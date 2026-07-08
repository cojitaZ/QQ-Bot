import cloudscraper
from bs4 import BeautifulSoup
from . import Search
import codecs
import os
import re
from urllib.parse import urljoin
class Iqdb(Search):
    def __init__(self):
        super().__init__()
        self.name="Iqdb"
        self.Introduction="Multi-service image search,结果和猴戏没什么区别"
        self.special_intro="一般不会出错,可能是网卡了"
    def parse_result(self):
        html=self.html_data.decode("utf-8",errors="ignore") if isinstance(self.html_data,bytes) else str(self.html_data)
        if "\\n" in html or "\\'" in html:
            try:
                html=codecs.decode(html,"unicode_escape")
            except Exception:
                pass
        soup=BeautifulSoup(html,"html.parser")
        self.debug_info["html_title"] = soup.title.get_text(strip=True) if soup.title else ""
        self.debug_info["table_count"] = len(soup.select('#pages table'))

        for table in soup.select('#pages table'):
            header=table.select_one('th')
            if header and "Your image" in header.get_text(strip=True):
                continue

            table_text=table.get_text(" ",strip=True)
            sim_match=re.search(r'(\d+(?:\.\d+)?)\s*%\s*similarity',table_text)
            if not sim_match:
                continue

            similarity=float(sim_match.group(1))

            img=table.select_one('td.image img')
            img_url=urljoin("https://www.iqdb.org/",img.get('src')) if img else ''

            source_link=table.select_one('td.image a[href]')
            if source_link:
                origin_url=urljoin("https://www.iqdb.org/",source_link.get('href'))
            else:
                origin_url=''

            title=''
            if img:
                title=img.get('title') or img.get('alt') or ''
            if not title or title == "[IMG]":
                service_cell=table.select_one('tr:nth-of-type(3) td')
                title=service_cell.get_text(" ",strip=True) if service_cell else ''

            self.result_list.append({
                'similarity':similarity,
                'img_url':img_url,
                'title':title,
                'origin_url':origin_url
            })

        self.result_list.sort(key=lambda x: x["similarity"],reverse=True)
        if self.result_list:
            self.max_similarity=self.result_list[0]['similarity']
        else:
            self.max_similarity=0

    def search(self):
        lengs=os.path.getsize(self.img_url)
        base_url="https://www.iqdb.org/"
        base_header={
            "Host":"www.iqdb.org",
            "Referer":"https://www.iqdb.org",
            "Origin": "https://www.iqdb.org/",
            "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0",
            "Accept-Lauguage":"zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Encoding":"gzip, deflate, br, zstd",
            "connection":"keeping-alive",
            "cotent-length":str(lengs),
            "sec-ch-ua":"\"Chromium\";v=\"148\", \"Microsoft Edge\";v=\"148\", \"Not/A)Brand\";v=\"99\"",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "sec-ch-ua-mobile": "?0",
            "Upgrade-Insecure-Requests": "1",
            "sec-ch-ua-platform": "\"Windows\""

        }
        with open(self.img_url,"rb") as r:
            data = {"factor": (None, "1.2"), "file": ("image.jpg", r, "image/jpeg")}
            resp=cloudscraper.create_scraper().post(url=base_url,headers=base_header,files=data)
            self.debug_info["status_code"] = resp.status_code
            self.debug_info["final_url"] = resp.url
            self.html_data=resp.content
            self.parse_result()
