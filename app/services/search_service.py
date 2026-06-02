import asyncio
import copy
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from app.libs.llm import get_name_score, single_name_llm
from app.libs.logger import write_log
from app.data import name_dict
from app.db.mongo import get_papers_col


class PCSSEARCHMongo:
    """
    MongoDB 데이터셋에서 논문을 조회하여 동일한 형태로 결과를 만들어 반환.

    - option:
        1: 1저자
        2: 1저자 또는 2저자
        3: 마지막 저자
        4: 기타 공저자(중간 저자)
        else: 저자 중 한 명 이상
    - threshold: 한국인 판정 임계값
    - countOption: True면 저자 통계(본 MongoDB 데이터셋 기준)도 붙임_bu
    """

    def __init__(
        self,
        options: List[int],
        threshold: float,
        startyear: int,
        endyear: int,
        countOption: bool = False,
        job_id: Optional[str] = None,
        event_queue: Optional[Any] = None,
        cancel_check: Optional[Any] = None,
    ):
        self.options = options
        self.threshold = float(threshold)
        self.startyear = int(startyear)
        self.endyear = int(endyear)
        self.countOption = bool(countOption)

        self.job_id = job_id
        self.event_queue = event_queue
        self.cancel_check = cancel_check  # callable -> bool

        self.run_id = job_id  # 기존 write_log(run_id, ...) 패턴 유지

        # 진행상황 emit 튜닝
        self._last_emit_ts = 0.0
        self.emit_min_interval_sec = 0.05  # 50ms

        # 결과/캐시
        self.checkedNameList: set[str] = set()
        self.titleList: List[str] = []
        self.CrawlData: List[Dict[str, Any]] = []

        # 중복 title 방지
        self._titleSet: set[str] = set()

        self._score_cache: Dict[str, float] = {}
        self._korean_cache: Dict[str, bool] = {}

        # author 통계 캐시(중복 집계 방지)
        self._author_stats_cache: Dict[str, Dict[str, Any]] = {}

        # LLM 동시성 제한(너무 많이 때리면 느려지거나 제한 걸릴 수 있음)
        self._llm_sem = asyncio.Semaphore(int(os.getenv("LLM_CONCURRENCY", "12")))

        # Mongo collection
        self._collection = None

        # progress tracking
        self._total_docs: int = 0
        self._processed_docs: int = 0
        self._matched_docs: int = 0  # 필터 통과(결과 포함)된 논문 수

    # ---------------- cancel / emit ----------------

    def _should_cancel(self) -> bool:
        try:
            return bool(self.cancel_check and self.cancel_check())
        except Exception:
            return False

    def _emit(self, payload: dict) -> None:
        q = self.event_queue
        if not q:
            return
        try:
            if q.full():
                try:
                    q.get_nowait()
                except Exception:
                    pass
            q.put_nowait(payload)
        except Exception:
            pass

    def _emit_status_throttled(self, payload: dict) -> None:
        now = time.monotonic()
        if now - self._last_emit_ts < self.emit_min_interval_sec:
            return
        self._last_emit_ts = now
        self._emit(payload)

    def printStatus(self, msg: str = "", url: Optional[str] = None) -> None:
        try:
            total = int(self._total_docs or 0)
            done = int(self._processed_docs or 0)
            matched = int(self._matched_docs or 0)

            progress = None
            if total > 0:
                progress = round((done / total) * 100, 2)

            payload = {
                "type": "status",
                "ts": datetime.utcnow().isoformat() + "Z",
                "msg": msg,
                "url": url,
                "paper_count": len(self.CrawlData),
                "korean_authors": len(self.checkedNameList),
                "progress": {
                    "total": total,
                    "done": done,
                    "matched": matched,
                    "percent": progress,
                },
            }
            self._emit_status_throttled(payload)
        except Exception:
            pass

    # ---------------- LLM: korean check ----------------

    async def _llm_score(self, name: str, timeout_sec: float = 5.0) -> float:
        if self._should_cancel():
            raise asyncio.CancelledError()

        # 전역 캐시(name_dict)가 점수(float/int)일 때만 사용
        v = name_dict.get(name)
        if isinstance(v, (int, float)):
            return float(v)

        # 인스턴스 캐시
        if name in self._score_cache:
            return self._score_cache[name]

        try:
            async with self._llm_sem:
                score = await asyncio.wait_for(
                    asyncio.to_thread(single_name_llm, name),
                    timeout=timeout_sec,
                )
            score_f = float(score)
            self._score_cache[name] = score_f
            name_dict[name] = score_f  # 전역에는 점수만 저장
            return score_f
        except asyncio.TimeoutError:
            self._score_cache[name] = 0.0
            name_dict[name] = 0.0
            return 0.0
        except asyncio.CancelledError:
            raise
        except Exception:
            self._score_cache[name] = 0.0
            name_dict[name] = 0.0
            return 0.0

    async def checkKorean(self, name: str) -> bool:
        if self._should_cancel():
            raise asyncio.CancelledError()

        name = (name or "").strip()
        if not name:
            return False

        # 이미 판정된 값이면 그대로 반환
        if name in self._korean_cache:
            return self._korean_cache[name]

        score = await self._llm_score(name, timeout_sec=5.0)

        if self._should_cancel():
            raise asyncio.CancelledError()

        is_k = bool(score >= self.threshold)
        self._korean_cache[name] = is_k  # bool은 여기만

        if is_k:
            self.checkedNameList.add(name)

        return is_k

    # ---------------- Mongo fetch ----------------

    def _year_filter(self) -> Dict[str, Any]:
        years_str = [str(y) for y in range(self.startyear, self.endyear + 1)]
        years_int = list(range(self.startyear, self.endyear + 1))
        return {"$or": [{"year": {"$in": years_str}}, {"year": {"$in": years_int}}]}

    async def _fetch_papers(
        self,
        conf_list: List[str],
    ) -> List[Dict[str, Any]]:
        if self._should_cancel():
            raise asyncio.CancelledError()

        flt = {
            "conference": {"$in": conf_list},
            **self._year_filter(),
        }

        # 필요한 필드만
        proj = {
            "_id": 0,
            "title": 1,
            "author_names": 1,
            "author_pids": 1,
            "conference": 1,
            "year": 1,
            "source": 1,
            "dblp_url": 1,
        }

        cursor = self._collection.find(flt, proj, batch_size=2000)

        docs: List[Dict[str, Any]] = []
        async for d in cursor:
            if self._should_cancel():
                raise asyncio.CancelledError()
            docs.append(d)

        return docs

    # ---------------- option-based target selection ----------------

    @staticmethod
    def _clean_authors(authors: List[str]) -> List[str]:
        cleaned = []
        for a in authors:
            a2 = re.sub(r"\d+", "", (a or "")).strip()
            if a2:
                cleaned.append(a2)
        return cleaned

    async def _targets_for_option(self, authors: List[str]) -> List[str]:
        """
        self.options에 포함된 조건들의 합집합(OR)을 구함.
        - 1: 1저자 (Index 0)
        - 2: 2저자 (Index 1)
        - 3: 마지막 저자 (Index -1)
        - 4: 기타 공저자 (Index 2 ~ -2)
        """
        if not authors:
            return []

        found_targets = set()

        async def get_tag(name: str) -> str:
            return f"{name} ({get_name_score(name)})"

        # [Option 1] 1저자
        if 1 in self.options:
            if await self.checkKorean(authors[0]):
                found_targets.add(await get_tag(authors[0]))

        # [Option 2] 2저자 (저자가 2명 이상일 때만)
        if 2 in self.options:
            if len(authors) >= 2 and await self.checkKorean(authors[1]):
                found_targets.add(await get_tag(authors[1]))

        # [Option 3] 마지막 저자
        if 3 in self.options:
            if await self.checkKorean(authors[-1]):
                found_targets.add(await get_tag(authors[-1]))

        # [Option 4] 기타 공저자 (1, 2, Last 제외한 중간 저자들)
        if 4 in self.options:
            if len(authors) >= 4:
                for a in authors[2:-1]:
                    if await self.checkKorean(a):
                        found_targets.add(await get_tag(a))

        return list(found_targets)

    @staticmethod
    def _strip_score_suffix(s: str) -> str:
        # "Hanbin Hong (1.0)" -> "Hanbin Hong"
        return re.sub(r"\s*\(\d+(?:\.\d+)?\)\s*$", "", (s or "")).strip()

    async def authorNumCheckerMongo(self, target_author: str) -> Dict[str, Any]:
        if not target_author:
            return {"stats": "(0,0,0,0)", "total": 0}

        if target_author in self._author_stats_cache:
            return self._author_stats_cache[target_author]

        if self._should_cancel():
            raise asyncio.CancelledError()

        self.printStatus(f"{target_author} Paper Counting", url=target_author)

        pipeline = [
            {"$match": {"author_names": target_author}},
            {
                "$group": {
                    "_id": None,
                    "co_author": {"$sum": 1},
                    "first_author": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$eq": [
                                        {"$arrayElemAt": ["$author_name", 0]},
                                        target_author,
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                    "first_or_second_author": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$or": [
                                        {
                                            "$eq": [
                                                {"$arrayElemAt": ["$author_name", 0]},
                                                target_author,
                                            ]
                                        },
                                        {
                                            "$eq": [
                                                {"$arrayElemAt": ["$author_name", 1]},
                                                target_author,
                                            ]
                                        },
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                    "last_author": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$eq": [
                                        {"$arrayElemAt": ["$author_name", -1]},
                                        target_author,
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                }
            },
        ]

        try:
            agg = self._collection.aggregate(pipeline, allowDiskUse=True)
            row = None
            async for r in agg:
                row = r
                break

            if not row:
                result = {"stats": "(0,0,0,0)", "total": 0}
                self._author_stats_cache[target_author] = result
                return result

            fa = int(row.get("first_author", 0))
            fs = int(row.get("first_or_second_author", 0))
            la = int(row.get("last_author", 0))
            co = int(row.get("co_author", 0))

            result = {
                "stats": f"({fa},{fs},{la},{co})",
                "total": co,
            }
            self._author_stats_cache[target_author] = result
            return result

        except asyncio.CancelledError:
            raise
        except Exception as e:
            write_log(
                self.run_id, f"[authorNumCheckerMongo] {target_author} error: {e}"
            )
            result = {"stats": "(0,0,0,0)", "total": 0}
            self._author_stats_cache[target_author] = result
            return result

    async def _build_crawl_data(self, docs: List[Dict[str, Any]]) -> None:
        """
        Mongo docs -> 기존 CrawlData 형태로 필터링/저장
        """
        for idx, d in enumerate(docs, start=1):
            if self._should_cancel():
                raise asyncio.CancelledError()

            self._processed_docs = idx

            try:
                title = (d.get("title") or "").strip()
                if not title:
                    continue

                if title in self._titleSet:
                    continue
                self._titleSet.add(title)
                self.titleList.append(title)

                authors_origin = d.get("author_names") or []
                author_pids = d.get("author_pids") or []
                author_urls = [
                    f"https://dblp.org/pid/{pid}.html" for pid in author_pids
                ]

                if not isinstance(authors_origin, list) or not authors_origin:
                    continue
                if not isinstance(author_urls, list) or not author_urls:
                    continue

                author_names = self._clean_authors(authors_origin)
                if not author_names:
                    continue

                # option별 target 추출
                target = await self._targets_for_option(author_names)
                if not target:
                    continue

                conf = d.get("conference") or ""
                year_raw = d.get("year")
                try:
                    year = int(year_raw)
                except Exception:
                    continue

                src = d.get("source") or ""
                dblp_url = d.get("dblp_url") or None

                self.CrawlData.append(
                    {
                        "title": title,
                        "author_names": author_names,
                        "author_urls": author_urls,
                        "author_pids": author_pids,
                        "target_author": target,
                        "conference": conf,
                        "year": year,
                        "source": src,
                        "dblp_url": dblp_url,
                    }
                )

                self._matched_docs += 1

            except asyncio.CancelledError:
                raise
            except Exception as e:
                write_log(self.run_id, f"[_build_crawl_data] error: {e}")

            if idx == 1 or idx % 25 == 0 or idx == self._total_docs:
                self.printStatus(
                    msg=f"Filtering... ({idx}/{self._total_docs})",
                    url="",
                )

    async def _attach_author_stats(self) -> List[Dict[str, Any]]:
        resultData: List[Dict[str, Any]] = []

        async def authorCounter(data: Dict[str, Any]) -> None:
            if self._should_cancel():
                raise asyncio.CancelledError()

            data_copy = copy.deepcopy(data)
            new_authors: List[str] = []
            totals_by_author: Dict[str, int] = {}

            for author in data_copy.get("author_names", []):
                if await self.checkKorean(author):
                    stats = await self.authorNumCheckerMongo(author)
                    new_authors.append(author + stats["stats"])
                    totals_by_author[author] = int(stats["total"])
                else:
                    new_authors.append(author)

            data_copy["author_names"] = new_authors

            target_names = [
                self._strip_score_suffix(t) for t in data_copy.get("target_author", [])
            ]
            data_copy["total_papers"] = [
                totals_by_author.get(name, 0) for name in target_names
            ]

            resultData.append(data_copy)

        tasks = [authorCounter(d) for d in self.CrawlData]
        batch_size = int(os.getenv("AUTHOR_STATS_BATCH", "500"))
        for i in range(0, len(tasks), batch_size):
            if self._should_cancel():
                raise asyncio.CancelledError()
            await asyncio.gather(*tasks[i : i + batch_size])

        return resultData

    async def run(self, conf_list: List[str]) -> Dict[int, Dict[str, Any]]:
        if self._should_cancel():
            raise asyncio.CancelledError()

        if self._collection is None:
            self._collection = await get_papers_col()

        docs = await self._fetch_papers(conf_list)

        self._total_docs = len(docs)
        self._processed_docs = 0
        self._matched_docs = 0

        self.printStatus("Filtering...", url="")
        await self._build_crawl_data(docs)

        if self.countOption:
            self.printStatus("Author Stats Attaching...", url="")
            resultData = await self._attach_author_stats()
        else:
            resultData = self.CrawlData

        final_sorted = sorted(
            resultData,
            key=lambda x: (
                -int(x.get("year", 0)),  # year desc
                str(x.get("conference", "")),  # conference asc
                str(x.get("title", "")),  # title asc (optional)
            ),
        )

        final_dict = {i: el for i, el in enumerate(final_sorted)}

        self.printStatus("Done.", url="")
        return final_dict
