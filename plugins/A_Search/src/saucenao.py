import cloudscraper
from bs4 import BeautifulSoup

from . import Search


class SauceNAO(Search):
    # 这个类的实现异常简单
    def __init__(self):
        super().__init__()
        self.name = "SauceNAO"
        self.Introduction = "搜索结果大部分在P站\n小部分在推"
        self.special_intro = "啊偶,无结果,估计是单个IP搜索限制20次导致"

    def parse_result(self):
        soup = BeautifulSoup(self.html_data, "html.parser")
        self.debug_info["html_title"] = soup.title.get_text(strip=True) if soup.title else ""
        self.debug_info["result_node_count"] = len(
            soup.select("div.result:not(#result-hidden-notification)")
        )

        for res in soup.select("div.result:not(#result-hidden-notification)"):
            similarity_node = res.select_one(".resultsimilarityinfo")
            similarity = similarity_node.get_text(strip=True) if similarity_node else 0
            img_url_node = res.select_one(".resultimage img")
            img_url = img_url_node.get("src") if img_url_node else "无"
            title_node = res.select_one(".resulttitle")
            title = title_node.get_text(strip=True) if title_node else "无"
            origin_url = res.select_one(".resultcontentcolumn a")
            if origin_url:
                self.result_list.append(
                    {
                        "similarity": similarity,
                        "img_url": img_url,
                        "title": title,
                        "origin_url": origin_url.get("href"),
                    }
                )
            else:
                self.result_list.append(
                    {
                        "similarity": similarity,
                        "img_url": img_url,
                        "title": title,
                        "origin_url": "无法提取出结果,网页源如下:\n"
                        + str(res.select_one(".resultcontentcolumn")),
                    }
                )

        self.result_list.sort(
            key=lambda x: self._normalize_similarity(x["similarity"]), reverse=True
        )
        if not self.result_list == []:
            self.max_similarity = self.result_list[0]["similarity"]
        else:
            self.max_similarity = 0

    def search(self):

        base_url = "https://saucenao.com/search.php"
        base_header = {
            "Referer": "https://saucenao.com",
            "Origin": "https://saucenao.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0",
            "Accept-Lauguage": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Encoding": "gzip, deflate, br, zstd",
        }
        with open(self.img_url, "rb") as r:
            data = {"factor": (None, "1.2"), "file": ("image.jpg", r, "image/jpeg")}
            resp = cloudscraper.create_scraper().post(url=base_url, headers=base_header, files=data)
            self.debug_info["status_code"] = resp.status_code
            self.debug_info["final_url"] = resp.url
            self.html_data = resp.content
            self.parse_result()
