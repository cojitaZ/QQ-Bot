import re
import requests
from pathlib  import Path
import asyncio
import os
from urllib.parse import urlparse


class Search:
    def __init__(self):
        self.name="" # 引擎的名字
        self.Introduction="" # 引擎的简要介绍
        self.special_intro="" # 引擎无结果时的介绍
        self.temp_del = False #是否在搜索完成后删除文件
        self.max_similarity = 0 # 最高置信度
        self.result_list:list = []
        # 元素结构需要是 {"similarity":float,"title":str,"img_url":str ,"origin_url":str}，排序应该是置信度高到低,可以根据不同搜索引擎添加更多的信息
        # 这个列表内的img_url应该是preview_url，但是为了习惯还是就这样写吧
        # 嗯还是加上origin_url比较好吧
        self.img_url:str = "" # 这个是你的图片的路径,我需要写个函数确保兼容网页url和本地地址
        self.last_error = ""
        self.debug_info = {}

    def reset_results(self):
        self.result_list = []
        self.max_similarity = 0
        self.last_error = ""
        self.debug_info = {}

    def unified_url_structure(self):
        #这个函数用于统一格式为本地，如果是网页url就先下载到本地再将img_url改成本地格式，同时承担报错的问题
        if re.match(r"https?://",self.img_url):
            self.ajax_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64; rv:52.0) Gecko/20100101 Firefox/52.0",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.8,en-US;q=0.5,en;q=0.3",
            "Accept-Encoding": "",
            "Connection": "keep-alive",
            }
            data=requests.get(self.img_url,headers=self.ajax_headers).content
            temp_name=Path(urlparse(self.img_url).path).name or "image.png"
            temp_dir=Path(__file__).resolve().parent / "temp"
            temp_dir.mkdir(parents=True,exist_ok=True)
            self.img_url=os.path.join(temp_dir / temp_name)
            with open(self.img_url,"wb") as w:
                w.write(data)
            self.temp_del=True


    def clean_temp(self):
        os.remove(self.img_url)
    def parse_result(self):
        pass
    def search(self):
        pass
    async def async_search(self):
        await asyncio.to_thread(self.search)
        return self.engine_result()

    def normalized_results(self):
        return [self._normalize_result(result) for result in self.result_list]

    def engine_result(self):
        error = getattr(self, "last_error", "")
        return {
            "engine": self.name,
            "ok": not bool(error),
            "error": error,
            "special_intro": self._special_intro_with_status(error),
            "max_similarity": self._normalize_similarity(self.max_similarity),
            "results": self.normalized_results(),
            "debug": self.debug_info,
        }

    def _special_intro_with_status(self, error: str = ""):
        intro = str(getattr(self, "special_intro", "") or "")
        debug_info = getattr(self, "debug_info", {})
        no_result_reason = ""
        if isinstance(debug_info, dict):
            no_result_reason = str(debug_info.get("no_result_reason", "") or "")

        status_parts = []
        if error:
            status_parts.append(f"错误: {error}")
        if no_result_reason:
            status_parts.append(f"原因: {no_result_reason}")

        if not status_parts:
            return "" if self.result_list else intro

        status_intro = "；".join(status_parts)
        return f"{intro}；{status_intro}" if intro else status_intro

    def _normalize_result(self, result: dict):
        normalized = {
            "engine": self.name,
            "similarity": self._normalize_similarity(result.get("similarity", 0)),
            "title": str(result.get("title", "")),
            "img_url": str(result.get("img_url", "")),
            "origin_url": str(result.get("origin_url", "")),
        }
        for key, value in result.items():
            if key not in normalized:
                normalized[key] = value
        return normalized

    def _normalize_similarity(self, value):
        if isinstance(value, (int, float)):
            return float(value)
        match = re.search(r"\d+(?:\.\d+)?", str(value))
        return float(match.group(0)) if match else 0.0

from .soutubot import Soutubot
from .saucenao import SauceNAO
from .google import Google
from .iqdb import Iqdb
class search_api(Search):
    def __init__(self, enable_google: bool = True):
        super().__init__()
        self.img_url=""
        self.soutubot=Soutubot()
        self.saucenao=SauceNAO()
        self.iqdb=Iqdb()
        # 这里只创建 Google 引擎对象，不启动浏览器；是否参与搜索由 enable_google 控制。
        self.google=Google(start_browser=enable_google)
        self.engines=[self.soutubot,self.saucenao,self.iqdb]
        if enable_google:
            self.google.start_browser()
            self.engines.append(self.google)
        self.results_by_engine={}

    async def search(self, img_url=None):
        # 全部执行，顺便简单化函数
        if img_url is not None:
            self.img_url=str(img_url)
        self.unified_url_structure()
        print(f"开始搜索:{self.img_url}")

        engine_results=await asyncio.gather(
            *(self._run_engine(engine) for engine in self.engines)
        )
        self.results_by_engine={result["engine"]:result for result in engine_results}
        self.result_list=[
            result
            for engine_result in engine_results
            for result in engine_result["results"]
        ]
        self.result_list.sort(key=lambda x:x["similarity"],reverse=True)
        self.max_similarity=self.result_list[0]["similarity"] if self.result_list else 0

        print("搜索全部完成")
        if self.temp_del:
            self.clean_temp()
        return self.results_by_engine

    async def _run_engine(self, engine):
        engine.reset_results()
        engine.img_url=self.img_url
        try:
            result=await engine.async_search()
        except Exception as exc:
            engine.last_error=str(exc)
            result=engine.engine_result()
        print(f"{engine.name}完成搜索")
        return result

    def close(self):
        for engine in self.engines:
            close=getattr(engine,"close",None)
            if close:
                close()
