import base64
import re
import time

import cloudscraper

from . import Search


class Soutubot(Search):
    def __init__(self):
        super().__init__()
        self.name = "Soutubot"
        self.Introduction = "搜图bot,返回值通常在n站和e站"
        self.special_intro = "通常不会出错....可能是服务器的网络问题？"
        self._m = None

    def _fetch_m(self, scraper):
        """从主页提取 window.GLOBAL.m 值，用于 X-API-KEY 计算"""
        home_resp = scraper.get("https://soutubot.moe/")
        self.debug_info["home_status"] = home_resp.status_code

        match = re.search(r"m:\s*(\d+)", home_resp.text)
        if match:
            self._m = int(match.group(1))
        else:
            self._m = 1971847850625

    def generate_x_api_key(self, user_agent: str) -> str:
        """公式: base64(str(ts^2 + ua_len^2 + m)).reverse().strip('=')"""
        ts = int(time.time())
        combined = str(ts * ts + len(user_agent) ** 2 + (self._m or 1971847850625))
        b64 = base64.b64encode(combined.encode()).decode()
        return b64[::-1].rstrip("=")

    def parse_result(self):
        raw_results = self.data.get("data") or []
        self.debug_info["raw_result_count"] = len(raw_results)
        for result in raw_results:
            if result["source"] == "nhentai":
                origin_url = f"https://{result['source']}.net{result['pagePath']}"
            elif result["source"] == "ehentai":
                origin_url = f"https://e-hentai.org{result['pagePath']}"
            else:
                origin_url = None
            if origin_url:
                self.result_list.append(
                    {
                        "title": result["title"],
                        "img_url": result["previewImageUrl"],
                        "origin_url": origin_url,
                        "similarity": result["similarity"],
                    }
                )
        self.result_list.sort(key=lambda x: x["similarity"], reverse=True)
        if self.result_list:
            self.max_similarity = self.result_list[0]["similarity"]
        else:
            self.max_similarity = 0

    def search(self):
        """url默认https://soutubot.moe/api/search"""
        scraper = cloudscraper.create_scraper()

        url = "https://soutubot.moe/api/search"

        self._fetch_m(scraper)

        agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"
        headers = {
            "Accept": "application/json, text/plain, */*",
            # 只保留 gzip, deflate，去掉 br/zstd 避免 requests 无法解压
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": "https://soutubot.moe",
            "Referer": "https://soutubot.moe/",
            "User-Agent": agent,
            "X-API-KEY": self.generate_x_api_key(user_agent=agent),
            "X-Requested-With": "XMLHttpRequest",
        }

        with open(self.img_url, "rb") as f:
            data = {"factor": (None, "1.2"), "file": ("image.jpg", f, "image/jpeg")}
            resp = scraper.post(url, headers=headers, files=data)  # 返回一个json文件
            self.debug_info["api_status"] = resp.status_code
            self.data = resp.json()
            self.debug_info["json_keys"] = (
                list(self.data.keys()) if isinstance(self.data, dict) else []
            )
            self.parse_result()
