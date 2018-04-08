import pickle
import re

from django.shortcuts import render
import json
from django.views.generic.base import View
from search.models import ArticleType
from django.http import HttpResponse
from datetime import datetime
from mtianyanSearch.utils.common import OrderedSet
import redis
from w3lib.html import remove_tags

# 为搜索界面进行准备
from elasticsearch import Elasticsearch
client = Elasticsearch(hosts=["127.0.0.1"])
# 使用redis实现top n排行榜
redis_cli = redis.StrictRedis()

# Create your views here.


class IndexView(View):
    # 首页

    def get(self, request):
        topn_search_clean = []
        topn_search = redis_cli.zrevrangebyscore(
            "search_keywords_set", "+inf", "-inf", start=0, num=5)
        for topn_key in topn_search:
            topn_key = str(topn_key, encoding="utf-8")
            topn_search_clean.append(topn_key)
        topn_search = topn_search_clean
        return render(request, "index.html", {"topn_search": topn_search})


class SearchSuggest(View):

    def get(self, request):
        key_words = request.GET.get('s', '')
        current_page = request.GET.get('s_type', '')
        if current_page == "article":
            re_datas = []
            if key_words:
                s = ArticleType.search()
                # fuzzy模糊搜索。fuzziness 编辑距离 prefix_length前面不变化的前缀长度
                s = s.suggest('my_suggest', key_words, completion={
                    "field": "suggest", "fuzzy": {
                        "fuzziness": 2
                    },
                    "size": 10
                })
                suggestions = s.execute_suggest()
                for match in suggestions.my_suggest[0].options[:10]:
                    source = match._source
                    re_datas.append(source["title"])
                    # re_datas.append(source["content"])
            return HttpResponse(
                json.dumps(re_datas),
                content_type="application/json")



class SearchView(View):

    def get(self, request):
        key_words = request.GET.get("q", "")

        # 通用部分
        # 实现搜索关键词keyword加1操作
        redis_cli.zincrby("search_keywords_set", key_words)
        # 获取topn个搜索词
        topn_search_clean = []
        topn_search = redis_cli.zrevrangebyscore(
            "search_keywords_set", "+inf", "-inf", start=0, num=5)
        for topn_key in topn_search:
            topn_key = str(topn_key, encoding="utf-8")
            topn_search_clean.append(topn_key)
        topn_search = topn_search_clean
        # 获取新闻数量

        jobbole_count = redis_cli.get("jobbole_count")
        if jobbole_count:
            jobbole_count = pickle.loads(jobbole_count)
        else:
            jobbole_count = 0
        job_count = redis_cli.get("job_count")
        if job_count:
            job_count = pickle.loads(job_count)
        else:
            job_count = 0
        zhihu_count = redis_cli.get("zhihu_count")
        if zhihu_count:
            zhihu_count = pickle.loads(zhihu_count)
        else:
            zhihu_count = 0

        # 当前要获取第几页的数据
        page = request.GET.get("p", "1")
        try:
            page = int(page)
        except BaseException:
            page = 1
        response = []
        start_time = datetime.now()
        s_type = request.GET.get("s_type", "")
        if s_type == "article":
            response = client.search(
                index="jobbole",
                request_timeout=60,
                body={
                    "query": {
                        "multi_match": {
                            "query": key_words,
                            "fields": ["tags", "title", "content"]
                        }
                    },
                    "from": (page - 1) * 10,
                    "size": 10,
                    "highlight": {
                        "pre_tags": ['<span class="keyWord">'],
                        "post_tags": ['</span>'],
                        "fields": {
                            "title": {},
                            "content": {},
                        }
                    }
                }
            )
        elif s_type == "job":
            response = client.search(
                index="lagou",
                request_timeout=60,
                body={
                    "query": {
                        "multi_match": {
                            "query": key_words,
                            "fields": [
                                "title",
                                "tags",
                                "job_desc",
                                "job_advantage",
                                "company_name",
                                "job_addr",
                                "job_city",
                                "degree_need"]}},
                    "from": (
                        page - 1) * 10,
                    "size": 10,
                    "highlight": {
                        "pre_tags": ['<span class="keyWord">'],
                        "post_tags": ['</span>'],
                        "fields": {
                            "title": {},
                            "job_desc": {},
                            "company_name": {},
                        }}})
        elif s_type == "question":
            response = client.search(
                index="zhihu",
                request_timeout=60,
                body={
                    "query": {
                        "multi_match": {
                            "query": key_words,
                            "fields": [
                                "topics",
                                "title",
                                "content",
                                "author_name"]}},
                    "from": (
                        page - 1) * 10,
                    "size": 10,
                    "highlight": {
                        "pre_tags": ['<span class="keyWord">'],
                        "post_tags": ['</span>'],
                        "fields": {
                            "title": {},
                            "content": {},
                            "author_name": {},
                        }}})

        end_time = datetime.now()
        last_seconds = (end_time - start_time).total_seconds()

        # 新闻具体的信息
        hit_list = []
        hit_dict = {}
        error_nums = 0
        if s_type == "article":
            for hit in response["hits"]["hits"]:
                hit_dict = {}
                try:
                    if "title" in hit["highlight"]:
                        hit_dict["title"] = "".join(hit["highlight"]["title"])
                    else:
                        hit_dict["title"] = hit["_source"]["title"]
                    if "content" in hit["highlight"]:
                        hit_dict["content"] = "".join(
                            hit["highlight"]["content"])
                    else:
                        hit_dict["content"] = hit["_source"]["content"]
                    hit_dict["create_date"] = hit["_source"]["create_date"]
                    hit_dict["url"] = hit["_source"]["url"]
                    hit_dict["score"] = hit["_score"]
                    if (hit_dict["url"].find("news.cslg.edu.cn") >= 0):
                        hit_dict["source_site"] = "常熟理工学院新闻网"
                    elif (hit_dict["url"].find("web.cse.cslg.cn") >= 0):
                        hit_dict["source_site"] = "计算机科学与工程学院新闻中心"
                    hit_list.append(hit_dict)
                except:
                    error_nums = error_nums + 1

        total_nums = int(response["hits"]["total"])

        # 计算出总页数
        if (page % 10) > 0:
            page_nums = int(total_nums / 10) + 1
        else:
            page_nums = int(total_nums / 10)
        return render(request, "result.html", {"page": page,
                                               "all_hits": hit_list,
                                               "key_words": key_words,
                                               "total_nums": total_nums,
                                               "page_nums": page_nums,
                                               "last_seconds": last_seconds,
                                               "topn_search": topn_search,
                                               "jobbole_count": jobbole_count,
                                               "s_type": s_type,
                                               "job_count": job_count,
                                               "zhihu_count": zhihu_count,
                                               })

    #
from django.views.generic.base import RedirectView
favicon_view = RedirectView.as_view(
    url='http://www.cslg.edu.cn/theme/red/images/logo.png', permanent=True)
