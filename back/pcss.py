from langchain.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_ollama import OllamaLLM
import platform
import aiofiles
from bs4 import BeautifulSoup
from user_agent import generate_navigator
import requests
import random
import traceback
import urllib3
import warnings
import pandas as pd
import re
import os
import aiohttp
import copy
from datetime import datetime
import asyncio
import json
import sys
from pymongo import MongoClient
from dotenv import load_dotenv
from bson import ObjectId

load_dotenv()

TIMEOUT = 10
TRYNUM = 10

com = 'cluster'

if sys.platform == 'darwin':
    com = 'z8'
    
if com == 'z8':
    LLM_SERVER = '121.152.225.232'
    PORT = "3333"
elif com == 'cluster':
    LLM_SERVER = '141.223.16.196'
    PORT = "8089"
    
    
MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = "pcss"
COLLECTION_NAME = "llm_names"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

class PCSSEARCH:
    def __init__(self, option, threshold, startyear, endyear, countOption=True):

        self.option         = option
        self.threshold      = threshold
        self.startyear      = int(startyear)
        self.endyear        = int(endyear)       
        self.countOption    = countOption             

        self.proxy_option   = False
        self.proxy_path     = "C:/Users/magel/Documents/아이피샵(유동프록시).txt"
        self.speed          = 3
        self.current_year   = 2025
        
        # PCSSEARCH 클래스 내부에 추가
        self.mongo_client = MongoClient(MONGO_URI)
        self.mongo_db = self.mongo_client[DB_NAME]
        self.mongo_col = self.mongo_db[COLLECTION_NAME]
        self.errors_col = self.mongo_db["errors"]
        self.run_id = None

        # MongoDB에서 모든 이름-스코어 가져오기
        self.name_dict = {
            doc["name"]: doc["score"]
            for doc in self.mongo_col.find({}, {"_id": 0, "name": 1, "score": 1})
        }

        self.llm_api_option = True
        self.api_url = f"http://{LLM_SERVER}:{PORT}/api/process"

        self.llm_model = 'llama3.3:70b-instruct-q8_0'
        if self.llm_api_option == False:
            self.llm = OllamaLLM(model=self.llm_model)
        self.checkedNameList = []
        self.titleList = []

        self.conf_df = pd.read_csv(os.path.join(os.path.dirname(__file__), 'data', 'conf.csv'))
        self.conf_param_list = self.conf_df['param'].tolist()
        self.conf_param_dict = self.conf_df.set_index('conference')['param'].to_dict()
        self.CrawlData = []
        self.FinalData = {}

        self.db_path = os.path.join(os.path.dirname(__file__), 'db')
        
        if self.proxy_option == True:
            self.init_proxy()

    def init_proxy(self):
        with open(self.proxy_path, "r", encoding="utf-8") as f:
            self.proxy_list = [line.strip() for line in f]  # strip()을 사용하여 개행 문자 제거 (필요한 경우)
    
    def async_proxy(self):
        proxy_server = random.choice(self.proxy_list)
        if self.proxy_option == True:
            return 'http://' + str(proxy_server)
        else:
            return None
    
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
                response = await self.asyncRequester(f"https://dblp.org/db/conf/{conf}/index.html", session=session)
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
            param = self.conf_param_dict[conf]
            
            edited_url = re.sub(r'[^\w\-_]', '_', url) + ".html"
            edited_url = edited_url.replace('https___', '').replace('_html', '')
            
            record_path = os.path.join(self.db_path, param, edited_url)
            
            # 비동기 파일 읽기: 파일이 존재하면 aiofiles로 읽음
            if os.path.exists(record_path):
                async with aiofiles.open(record_path, "r", encoding="utf-8") as file:
                    response = await file.read()
            else:
                response = await self.asyncRequester(url, session=session)
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
                            if idx < len(authors) and self.koreanChecker(authors[idx]):
                                # 이미 name_dict에 값이 있을 것이므로 가져오기
                                target_authors.append(
                                    authors[idx] + f' ({self.name_dict[authors[idx]]})'
                                )
                        return target_authors

                    if self.option == 1:
                        # 1저자
                        if self.koreanChecker(authors[0]):
                            self.CrawlData.append({
                                'title': title,
                                'author_name': authors,
                                'author_url': authors_url,
                                'target_author': [authors[0] + f' ({self.name_dict[authors[0]]})'],
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
                        if self.koreanChecker(authors[-1]):
                            self.CrawlData.append({
                                'title': title, 
                                'author_name': authors,
                                'author_url': authors_url,
                                'target_author': [authors[-1] + f' ({self.name_dict[authors[-1]]})'],
                                'conference': conf,
                                'year': year,
                                'source': url
                            })
                    elif self.option == 4:
                        # 1저자 또는 마지막 저자
                        target = []
                        if self.koreanChecker(authors[0]):
                            target.append(authors[0] + f'({self.name_dict[authors[0]]})')
                        if len(authors) > 1 and self.koreanChecker(authors[-1]):
                            target.append(authors[-1] + f' ({self.name_dict[authors[-1]]})')
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
                            if self.koreanChecker(auth):
                                target.append(auth + f' ({self.name_dict[auth]})')
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
                    conf_param = self.conf_param_dict[conf_name]
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
                    multi_name_option = False  # 필요에 따라 True로 변경 가능
                    
                    if not multi_name_option:
                        for index, author in enumerate(data_copy["author_name"]):
                            if not self.koreanChecker(author):
                                new_authors.append(author)
                                continue
                            result = await self.authorNumChecker(author, data['author_url'][index], session)
                            new_authors.append(author + result['stats'])
                            totals_by_author[author] = result["total"]
                    else:
                        llm_result = self.multi_name_llm(data_copy['author_name'])
                        for index, (author, result) in enumerate(llm_result.items()):
                            if not result:
                                new_authors.append(author)
                                continue
                            stats = await self.authorNumChecker(author, data['author_url'][index], session)
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
            self.FinalData = sorted(self.resultData, key=lambda x: (x["conference"], -x["year"]))
            self.FinalData = {index: element for index, element in enumerate(self.FinalData)}

            json_path = os.path.join(os.path.dirname(__file__), 'res', f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.json")
            with open(json_path, 'w', encoding='utf-8') as json_file:
                json.dump(self.FinalData, json_file, ensure_ascii=False, indent=4)

            self.clear_console()
            print(f" PATH={json_path}")
            self.result_json_path = json_path
            return self.result_json_path

        except Exception as e:
            print(" PATH=ERROR", e)
            self.write_log(traceback.format_exc())

    # 메인 함수
    def main(self, conf_list):
        try:
            self.target_conf_list = conf_list
            result_json_path = asyncio.run(self.MultiConfCollector(conf_list))

            return result_json_path
        except:
            self.write_log(traceback.format_exc())


    def koreanChecker(self, name, multi=False):
        if multi == False:
            self.printStatus(msg="LLM Checking Korean... ", url=name)
            if float(self.single_name_llm(name)) > self.threshold:
                if name not in self.checkedNameList:
                    self.checkedNameList.append(name)
                return True

            return False
        else:
            self.printStatus(msg="LLM Checking Korean... ", url=', '.join(name))
            result = self.multi_name_llm(name)
            
            result_dict = {}
            for key, value in result.items():
                if float(value) > self.threshold:
                    if name not in self.checkedNameList:
                        self.checkedNameList.append(name)
                    result_dict[key] = True
                else:
                    result_dict[key] = False
                    
            return result_dict
        # if self.possible == True:
        #     if name.split()[-1] in self.last_name_list and name.split()[0] in self.first_name_list and float(self.single_name_llm(name)) > self.possible_stat:
            
        # else:
        #     if name.split()[-1] in self.last_name_list and name.split()[0] in self.first_name_list and float(self.single_name_llm(name)) > 0.5:
            
        # return False


    def single_name_llm(self, name):
        
        try:
            return self.name_dict[name]
        except KeyError:
            pass
        
        if self.llm_api_option == False:
            template = f"Express the likelihood of this {name} being Korean using only a number between 0~1. You need to say number only"

            prompt = PromptTemplate.from_template(template=template)
            chain = prompt | self.llm | StrOutputParser()

            result = chain.invoke({"name", name})

        else:
            result = self.llm_api_answer(
                query = f"Express the likelihood of this {name} being Korean using only a number between 0~1. You need to say number only",
                model = self.llm_model
            )

        # 🔹 숫자만 추출 (지수 표기법 방지)
        match = re.findall(r"\d+\.\d+|\d+", result)
        if not match:
            return "0.0"  # 예외 처리: 결과가 없을 경우 기본값

        value = float(match[0])  # 🔹 문자열을 float으로 변환

        # 🔹 숫자 범위 고정 (0.0 ~ 1.0)
        value = max(0.0, min(1.0, value))

        # 🔹 소수점 1자리까지 포맷팅
        formatted_value = "{:.1f}".format(value)

        self.name_dict[name] = formatted_value
        self.mongo_col.update_one(
            {"name": name},
            {"$set": {"score": formatted_value}},
            upsert=True
        )

        return formatted_value  # 🔹 결과 반환 (0.0 ~ 1.0)


    def multi_name_llm(self, names):
        result_dict = {}
        remaining_names = [name for name in names if name not in self.name_dict]

        # 기존에 저장된 값 추가
        for name in names:
            if name in self.name_dict:
                result_dict[name] = self.name_dict[name]

        if remaining_names:  # 새로운 값을 조회할 필요가 있는 경우만 실행
            query = f"""
                Given a list of names, express the likelihood of each name being Korean using only a number between 0 and 1.
                Return the results **only as numbers in the same order as the input**, separated by spaces.

                Do not include any additional text, explanations, or formatting. Here are the names: {', '.join(remaining_names)}.
            """

            if not self.llm_api_option:
                template = PromptTemplate.from_template(template=query)
                chain = template | self.llm | StrOutputParser()
                results = chain.invoke({"names": remaining_names})
            else:
                results = self.llm_api_answer(query, model=self.llm_model)

            results = results.split()

            for index, result in enumerate(results):
                # 🔹 숫자만 추출 (지수 표기법 방지)
                match = re.findall(r"\d+\.\d+|\d+", result)
                if not match:
                    value = 0.0  # 예외 처리: 결과가 없을 경우 기본값
                else:
                    value = float(match[0])  # 🔹 문자열을 float으로 변환
                    value = max(0.0, min(1.0, value))  # 🔹 범위 조정 (0.0 ~ 1.0)

                # 🔹 소수점 1자리까지 포맷팅
                formatted_value = "{:.1f}".format(value)
                result_dict[remaining_names[index]] = formatted_value

        return {name: result_dict.get(name, "0.0") for name in names}  # 원래 순서 유지

    def llm_api_answer(self, query, model):
        # 전송할 데이터
        data = {
            "model": model,
            "prompt": query
        }

        try:
            # POST 요청 보내기
            response = requests.post(self.api_url, json=data)

            # 응답 확인
            if response.status_code == 200:
                result = response.json()['response']
                result = result.replace('<think>', '').replace('</think>', '').replace('\n\n', '')
                return result
            else:
                return f"Failed to get a valid response: {response.status_code} {response.text}"

        except requests.exceptions.RequestException as e:
            return "Error communicating with the server: {e}"


    def save_name_dict(self):
        """ name_dict를 JSON 파일로 저장 """
        with open(self.json_filename, "w", encoding="utf-8") as file:
            json.dump(self.name_dict, file, ensure_ascii=False, indent=4) 


    def load_name_dict(self):
        """ JSON 파일에서 name_dict 불러오기 """
        if os.path.exists(self.json_filename):
            with open(self.json_filename, "r", encoding="utf-8") as file:
                return json.load(file)
        return {}  # 파일이 없으면 빈 딕셔너리 반환


    async def authorNumChecker(self, target_author, url, session):
        try:
            stats = {
                "first_author": 0,
                "first_or_second_author": 0,
                "last_author": 0,
                "co_author": 0,
            }

            self.printStatus(f"{target_author} Paper Counting", url)
            res = await self.asyncRequester(url, session=session)
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
                    if paper.has_attr('id') and paper['id'].split('/')[1] in self.conf_param_list:
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


    def random_heador(self):
        navigator = generate_navigator()
        navigator = navigator['user_agent']
        return {"User-Agent": navigator}


    def random_proxy(self):
        proxy_server = random.choice(self.proxy_list)
        if self.proxy_option == True:
            return {"http": 'http://' + proxy_server, 'https': 'http://' + proxy_server}
        else:
            return None


    def write_log(self, message):
        try:
            # 문서가 없으면 새로 생성
            if self.run_id is None:
                self.run_id = ObjectId()
                self.errors_col.insert_one({
                    "_id": self.run_id,
                    "started_at": datetime.utcnow(),
                    "errors": [{
                        "timestamp": datetime.utcnow(),
                        "message": message
                    }]
                })
            else:
                self.errors_col.update_one(
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

    def Requester(self, url, headers={}, params={}, proxies={}, cookies={}):
        try:
            if headers == {}:
                headers = self.random_heador()

            if self.proxy_option == True:
                trynum = 0
                while True:
                    proxies = self.random_proxy()
                    try:
                        main_page = requests.get(url, proxies=proxies, headers=headers, params=params,
                                                 cookies=cookies, verify=False, timeout=TIMEOUT)
                        return main_page
                    except Exception as e:
                        if trynum >= TRYNUM:
                            return ("ERROR", traceback.format_exc())
                        trynum += 1
            else:
                return requests.get(url, headers=headers, params=params, verify=False)

        except Exception as e:
             return ("ERROR", traceback.format_exc())
    

    async def asyncRequester(self, url, headers={}, params={}, proxies='', cookies={}, session=None):
        timeout = aiohttp.ClientTimeout(total=TIMEOUT)
        trynum = 0
        while True:
            try:
                if self.proxy_option:
                    proxies = self.async_proxy()
                headers = self.random_heador()
                async with session.get(url, headers=headers, params=params, proxy=proxies, cookies=cookies,
                                       ssl=False, timeout=TIMEOUT) as response:
                    return await response.text()
            except (aiohttp.ClientError, asyncio.TimeoutError, Exception) as e:
                if trynum >= TRYNUM:
                    self.write_log(traceback.format_exc())
                trynum += 1


    def clear_console(self):
        if platform.system() == "Windows":
            os.system("cls")
        else:
            os.system("clear")

if __name__ == "__main__":
    pcssearch_obj = PCSSEARCH(1, 0.5, 2024, 2024, False)
    conf_list = ['CCS']
    pcssearch_obj.main(conf_list)
    
    
