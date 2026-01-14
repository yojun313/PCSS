import platform
import aiofiles
from bs4 import BeautifulSoup
import traceback
import urllib3
import warnings
import re
import os
import aiohttp
import copy
from datetime import datetime
import asyncio
from dotenv import load_dotenv
from bson import ObjectId
from app.db import errors_col
from libs.req import asyncRequester
from app.data import conf_param_dict, conf_param_list
from libs.llm import get_name_score, single_name_llm
from typing import List, Dict, Any
import httpx

load_dotenv()

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

class PCSSEARCH:
    def __init__(self, option, threshold, startyear, endyear, countOption=True):

        self.option         = option
        self.threshold      = threshold
        self.startyear      = int(startyear)
        self.endyear        = int(endyear)       
        self.countOption    = countOption             

        self.speed          = 3
        self.current_year   = 2025        
        self.run_id = None

        self.checkedNameList = set()
        self.titleList = []
        self.CrawlData = []
        self.FinalData = {}

        self.db_path = os.path.join(os.path.dirname(__file__), 'db')    
    
    # 한 Conference에 대한 연도별 url 크롤링 함수
    async def conf_crawl(self, conf, session, conf_name):
        try:
            self.printStatus(f"{conf_name} Loading...", url=f"https://dblp.org/db/conf/{conf}/index.html")
            filtered_urls = []
            urls = []
            
            folder_path = os.path.join(os.path.dirname(__file__), 'data', 'urls')
            file_path = os.path.join(folder_path, f"{conf_name}.txt")
            
            if os.path.exists(file_path) and self.endyear != self.current_year:
                # 이미 파일이 있다면, 해당 내용 사용
                async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                    async for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        urls.append(line)
            else:
                response = await asyncRequester(f"https://dblp.org/db/conf/{conf}/index.html", session=session)
                if isinstance(response, tuple) == True:
                    return response
                self.printStatus(f"{conf_name} URL Crawling...", url=f"https://dblp.org/db/conf/{conf}/index.html")

                soup = BeautifulSoup(response, "lxml")

                links = soup.find_all('a', class_='toc-link')
                urls = [link['href'] for link in links if link['href']]
                
                with open(file_path, 'w', encoding='utf-8') as f:
                    for url in urls:
                        f.write(url + '\n')

            for url in urls:
                match = re.search(r'\d{4}', url)  # 4자리 숫자 찾기
                if match:
                    year_str = match.group()
                    if year_str.isdigit():  
                        year = int(year_str)
                        if self.startyear <= year <= self.endyear:
                            filtered_urls.append((url, year))
                            
            return filtered_urls
        except:
            self.write_log(traceback.format_exc())
            return []

    # 한 개의 Paper에 대한 크롤링 함수
    async def paper_crawl(self, conf, url, year, session):
        try:
            self.printStatus(f"{year} {conf} Loading...", url=url)
            param = conf_param_dict[conf]
            
            edited_url = re.sub(r'[^\w\-_]', '_', url) + ".html"
            edited_url = edited_url.replace('https___', '').replace('_html', '')
            
            record_path = os.path.join(self.db_path, param, edited_url)
            
            # 비동기 파일 읽기: 파일이 존재하면 aiofiles로 읽음
            if os.path.exists(record_path):
                async with aiofiles.open(record_path, "r", encoding="utf-8") as file:
                    response = await file.read()
            else:
                response = await asyncRequester(url, session=session)
                if year != 2025:
                    async with aiofiles.open(record_path, "w", encoding="utf-8") as file:
                        await file.write(response)
                
            
            if isinstance(response, tuple):
                return response         

            # CPU 바운드 파싱 작업은 별도 스레드에서 실행
            soup = await asyncio.to_thread(BeautifulSoup, response, "lxml")
            
            # li.entry.inproceedings 태그를 한 번에 select로 가져옵니다.
            papers = soup.select('li.entry.inproceedings')

            self.printStatus(f"{year} {conf} Crawling...", url=url)
            
            # titleList를 집합으로도 관리(초기화)
            if not hasattr(self, "_titleSet"):
                self._titleSet = set(self.titleList)
            
            for paper in papers:
                try:
                    # 제목 추출
                    title_tag = paper.select_one('span.title')
                    title = title_tag.get_text(strip=True) if title_tag else 'No title found'

                    # 중복 체크
                    if title in self._titleSet:
                        continue
                    self._titleSet.add(title)
                    self.titleList.append(title)

                    # 저자 추출
                    authors_origin = []
                    authors_url = []

                    # 저자 정보를 한 번에 select
                    author_tags = paper.select('span[itemprop="author"] > a[href]')
                    if not author_tags:
                        # 저자가 하나도 없거나 a[href]가 아예 없는 경우
                        continue

                    for a in author_tags:
                        author_name_tag = a.select_one('span[itemprop="name"]')
                        if author_name_tag:
                            authors_origin.append(author_name_tag.get_text(strip=True))
                        authors_url.append(a['href'])

                    # authors_origin 있고, authors_url이 하나도 없는 경우는 skip
                    if authors_origin and not authors_url:
                        continue

                    if not authors_origin:
                        continue

                    # 조건별 필터링/저장
                    # ----------------------------------------------------
                    authors = authors_origin
                    authors = [re.sub(r'\d+', '', name).strip() for name in authors]
                    def store_if_korean(idx_list):
                        """idx_list에 해당하는 저자가 한국인이면 저장"""
                        target_authors = []
                        for idx in idx_list:
                            if idx < len(authors) and self.checkKorean(authors[idx]):
                                # 이미 name_dict에 값이 있을 것이므로 가져오기
                                target_authors.append(
                                    authors[idx] + f' ({get_name_score(authors[idx])})'
                                )
                        return target_authors

                    if self.option == 1:
                        # 1저자
                        if self.checkKorean(authors[0]):
                            self.CrawlData.append({
                                'title': title,
                                'author_name': authors,
                                'author_url': authors_url,
                                'target_author': [authors[0] + f' ({get_name_score(authors[0])})'],
                                'conference': conf,
                                'year': year,
                                'source': url
                            })
                    elif self.option == 2:
                        # 1저자 또는 2저자
                        target = store_if_korean([0, 1])  # 0,1인덱스
                        if target:
                            self.CrawlData.append({
                                'title': title,
                                'author_name': authors,
                                'author_url': authors_url,
                                'target_author': target,
                                'conference': conf,
                                'year': year,
                                'source': url
                            })
                    elif self.option == 3:
                        # 마지막 저자
                        if self.checkKorean(authors[-1]):
                            self.CrawlData.append({
                                'title': title, 
                                'author_name': authors,
                                'author_url': authors_url,
                                'target_author': [authors[-1] + f' ({get_name_score(authors[-1])})'],
                                'conference': conf,
                                'year': year,
                                'source': url
                            })
                    elif self.option == 4:
                        # 1저자 또는 마지막 저자
                        target = []
                        if self.checkKorean(authors[0]):
                            target.append(authors[0] + f'({get_name_score(authors[0])})')
                        if len(authors) > 1 and self.checkKorean(authors[-1]):
                            target.append(authors[-1] + f' ({get_name_score(authors[-1])})')
                        if target:
                            self.CrawlData.append({
                                'title': title,
                                'author_name': authors,
                                'author_url': authors_url,
                                'target_author': target,
                                'conference': conf,
                                'year': year,
                                'source': url
                            })
                    else:
                        # 저자 중 한 명 이상이 한국인
                        target = []
                        for auth in authors:
                            if self.checkKorean(auth):
                                target.append(auth + f' ({get_name_score(auth)})')
                        if target:
                            self.CrawlData.append({
                                'title': title,
                                'author_name': authors,
                                'author_url': authors_url,
                                'target_author': target,
                                'conference': conf,
                                'year': year,
                                'source': url
                            })
                    # ----------------------------------------------------
                except:
                    self.write_log(traceback.format_exc())

        except:
            self.write_log(traceback.format_exc())

    # 한 Conference에 대한 병렬 Paper 크롤링 함수
    async def MultiPaperCollector(self, conf_urls, conf_name, session):
        try:
            tasks = []
            for conf_url in conf_urls:
                try:
                    url = conf_url[0]
                    year = int(conf_url[1])
                    tasks.append(self.paper_crawl(conf_name, url, year, session))
                except:
                    self.write_log(f"{conf_url[1]}")
            results = await asyncio.gather(*tasks)
        except:
            self.write_log(traceback.format_exc())


    async def MultiConfCollector(self, conf_list):
        try:
            # 하나의 세션을 재사용하며 관리 (async with 사용)
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=self.speed)) as session:
                # 각 컨퍼런스에 대해 동시 크롤링 수행
                async def process_conference(conf):
                    conf_name = conf
                    conf_param = conf_param_dict[conf_name]
                    conf_urls = await self.conf_crawl(conf_param, session, conf_name)
                    await self.MultiPaperCollector(conf_urls, conf_name, session)

                # 컨퍼런스 크롤링 작업들을 병렬 실행
                tasks = [process_conference(conf) for conf in conf_list]
                await asyncio.gather(*tasks, return_exceptions=True)

                # 첫 번째 단계 완료 후, 결과를 저장할 리스트 초기화
                self.resultData = []

                # 저자 통계 처리 비동기 함수
                async def authorCounter(data):
                    data_copy = copy.deepcopy(data)
                    new_authors = []
                    totals_by_author = {}
                    
                    for index, author in enumerate(data_copy["author_name"]):
                        if not self.checkKorean(author):
                            new_authors.append(author)
                            continue
                        result = await self.authorNumChecker(author, data['author_url'][index], session)
                        new_authors.append(author + result['stats'])
                        totals_by_author[author] = result["total"]

                    data_copy["author_name"] = new_authors
                    # --- target_author 순서대로 논문 수 리스트 생성 ---
                    def strip_score(s: str) -> str:
                        """'Hanbin Hong (1.0)' -> 'Hanbin Hong'"""
                        return re.sub(r'\s*\(\d+(?:\.\d+)?\)\s*$', '', s).strip()

                    target_names = [strip_score(t) for t in data_copy.get("target_author", [])]

                    total_list = [totals_by_author.get(name, 0) for name in target_names]
                    data_copy["total_papers"] = total_list
                    # ---------------------------------------------------

                    self.resultData.append(data_copy)

                if self.countOption:
                    # 각 데이터에 대해 저자 처리 작업들을 병렬 실행
                    tasks = [authorCounter(data) for data in self.CrawlData]
                    await asyncio.gather(*tasks, return_exceptions=True)
                else:
                    self.resultData = self.CrawlData

            # 세션이 종료된 후에 최종 데이터를 정렬 및 JSON 파일로 저장
            FinalData = sorted(self.resultData, key=lambda x: (x["conference"], -x["year"]))
            FinalData = {index: element for index, element in enumerate(FinalData)}

            return FinalData

        except Exception as e:
            print(" PATH=ERROR", e)
            self.write_log(traceback.format_exc())

    # 메인 함수
    def main(self, conf_list):
        try:
            self.target_conf_list = conf_list
            result_data = asyncio.run(self.MultiConfCollector(conf_list))

            return result_data
        except:
            self.write_log(traceback.format_exc())


    def checkKorean(self, name):
        self.printStatus(msg="LLM Checking Korean... ", url=name)
        if float(single_name_llm(name)) > self.threshold:
            if name not in self.checkedNameList:
                self.checkedNameList.add(name)
            return True

        return False


    async def authorNumChecker(self, target_author, url, session):
        try:
            stats = {
                "first_author": 0,
                "first_or_second_author": 0,
                "last_author": 0,
                "co_author": 0,
            }

            self.printStatus(f"{target_author} Paper Counting", url)
            res = await asyncRequester(url, session=session)
            if isinstance(res, tuple):
                # 오류 상황 처리: 로그 기록 또는 기본값 반환
                self.write_log("asyncRequester returned an error: " + str(res))
                return stats
            soup = BeautifulSoup(res, "lxml")

            publ_lists = soup.find_all('ul', class_='publ-list')
            
            trynum = 1
            while True:
                publ_lists = soup.find_all('ul', class_='publ-list')
                if publ_lists is None or len(publ_lists) == 0:
                    trynum += 1
                    if trynum == 10:
                        break
                    continue
                break
            
            papers = []
            for publ_list in publ_lists:
                publ_list = publ_list.find_all("li", class_=re.compile(r"entry"))  
                for paper in publ_list:
                    if paper.has_attr('id') and paper['id'].split('/')[1] in conf_param_list:
                        conf = paper['id'].split('/')[1]
                        pass
                    else:
                        continue
                    
                    title = paper.find('span', 'title')
                    if title is not None:
                        title = title.text
                        middle = paper.find('cite', 'data tts-content')
                        authors = middle.select('span[itemprop="name"]:not(.title)')
                        author_list = [author.get_text(strip=True) for author in authors]
                        author_list.pop()

                        papers.append({
                            'title': title,
                            'authors': author_list,
                            'conf': conf
                        })
            
            paperCnt = 0
            for paper in papers:
                authors = paper["authors"]
                if target_author in authors:
                    paperCnt += 1
                    if authors[0] == target_author:
                        stats["first_author"] += 1
                        stats["first_or_second_author"] += 1  # 1저자도 2저자 조건에 포함됨
                    elif len(authors) > 1 and authors[1] == target_author:
                        stats["first_or_second_author"] += 1
                    elif authors[-1] == target_author:
                        stats["last_author"] += 1
                    stats["co_author"] += 1

            return {
                "stats": f"({stats['first_author']},{stats['first_or_second_author']},{stats['last_author']},{stats['co_author']})",
                "total": paperCnt
            }
        except Exception as e:
            self.write_log(traceback.format_exc())
            return stats


    def printStatus(self, msg='', url=None):
        try:
            print(f'\r{msg} | {url} | paper: {len(self.CrawlData)} | Korean Authors: {len(self.checkedNameList)}', end='')
        except:
            pass


    def write_log(self, message):
        try:
            # 문서가 없으면 새로 생성
            if self.run_id is None:
                self.run_id = ObjectId()
                errors_col.insert_one({
                    "_id": self.run_id,
                    "started_at": datetime.utcnow(),
                    "errors": [{
                        "timestamp": datetime.utcnow(),
                        "message": message
                    }]
                })
            else:
                errors_col.update_one(
                    {"_id": self.run_id},
                    {"$push": {
                        "errors": {
                            "timestamp": datetime.utcnow(),
                            "message": message
                        }
                    }}
                )
        except Exception as e:
            pass


    def clear_console(self):
        if platform.system() == "Windows":
            os.system("cls")
        else:
            os.system("clear")

def compute_author_stats(
    html: str,
    target_author: str,
    max_retry: int = 10
) -> Dict[str, Any]:
    stats = {
        "first_author": 0,
        "first_or_second_author": 0,
        "last_author": 0,
        "co_author": 0,
    }

    soup = BeautifulSoup(html, "lxml")

    trynum = 1
    publ_lists = soup.find_all("ul", class_="publ-list")
    while (publ_lists is None or len(publ_lists) == 0) and trynum < max_retry:
        publ_lists = soup.find_all("ul", class_="publ-list")
        trynum += 1

    papers: List[Dict[str, Any]] = []

    for publ_list in publ_lists:

        current_year = None

        for li in publ_list.find_all("li", recursive=False):

            # 연도 업데이트
            if "year" in li.get("class", []):
                current_year = li.get_text(strip=True)
                continue

            # entry 처리
            if not re.search(r"entry", " ".join(li.get("class", []))):
                continue  # year도 entry도 아닌 li는 무시

            if current_year is None:
                # year 이전 entry는 무시
                continue

            conf = None
            if li.has_attr("id"):
                parts = li["id"].split("/")
                if len(parts) > 1:
                    conf = parts[1]

            # conf 필터링
            if conf_param_list is not None:
                if conf is None or conf not in conf_param_list:
                    continue

            conf = conf_param_list[conf]

            # -------- title --------
            title_tag = li.find("span", class_="title")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)

            # -------- authors --------
            middle = li.find("cite", class_="data tts-content")
            if not middle:
                middle = li  # fallback

            authors = middle.select('span[itemprop="name"]:not(.title)')
            author_list = [a.get_text(strip=True) for a in authors]

            if len(author_list) > 0:
                # 기존 코드의 마지막 요소 제거 로직 유지
                author_list.pop()

            if not author_list:
                continue

            papers.append(
                {
                    "title": title,
                    "authors": author_list,
                    "conf": f"{conf} {current_year}",
                }
            )
            
    # 통계 집계
    paperCnt = 0
    for paper in papers:
        authors = paper["authors"]
        if target_author in authors:
            paperCnt += 1
            if len(authors) >= 1 and authors[0] == target_author:
                stats["first_author"] += 1
                stats["first_or_second_author"] += 1  # 1저자는 1or2 저자에도 포함
            elif len(authors) > 1 and authors[1] == target_author:
                stats["first_or_second_author"] += 1
            elif len(authors) >= 1 and authors[-1] == target_author:
                stats["last_author"] += 1

            stats["co_author"] += 1

    result = {
        "stats": f"({stats['first_author']},{stats['first_or_second_author']},{stats['last_author']},{stats['co_author']})",
        "total": paperCnt,
        "papers": papers,
    }
    return result

async def fetch_html(url: str, timeout_sec: float) -> str:
    async with httpx.AsyncClient(timeout=timeout_sec, headers={"User-Agent": "Mozilla/5.0"}) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


if __name__ == "__main__":
    pcssearch_obj = PCSSEARCH(1, 0.5, 2024, 2024, False)
    conf_list = ['CCS']
    pcssearch_obj.main(conf_list)
    
    
