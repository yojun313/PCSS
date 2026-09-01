import asyncio
from datetime import datetime, timedelta, timezone
from app.libs.email import send_html_email, sender_address
from app.services.search_service import PCSSEARCHMongo
from app.db import subscription_col, papers_col, notifier_state_col
from app.core.templates import templates
from collections import defaultdict


class MockRequest:
    def __init__(self):
        self.headers = {}
        self.state = type("State", (), {"user": None})()
        self.query_params = {}
        self.url = "https://pcss.postech.ac.kr"
        self.base_url = "https://pcss.postech.ac.kr"
        self.cookies = {}
        self.client = None

    def url_for(self, name, **kwargs):
        return f"/{name}"


class SubscriptionProcessor(PCSSEARCHMongo):
    def __init__(self, target_papers: list, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_papers = target_papers

    async def _fetch_papers(self, conf_list: list) -> list:
        return [p for p in self.target_papers if p.get("conference") in conf_list]


NOTIFIER_STATE_ID = "subscription_notifier"


class SubscriptionNotifier:
    def __init__(self):
        self.mail_config = self._load_mail_config()
        self.option_map = {
            1: "1저자",
            2: "2저자",
            3: "마지막 저자",
            4: "기타 공저자",
        }

    def _load_mail_config(self):
        # 발송 설정은 app.libs.email이 MAIL_BACKEND에 따라 알아서 고른다
        try:
            return {"sender": sender_address()}
        except Exception as e:
            print(f"[Notifier] Mail config load failed: {e}")
            return None

    def _send_email_sync(self, receiver, title, html_body):
        if not self.mail_config:
            print(f"[Notifier] No mail config. Skipping email to {receiver}")
            return

        try:
            send_html_email(receiver, title, html_body)
            print(f"[Notifier] Sent to {receiver}")
        except Exception as e:
            print(f"[Notifier] Fail {receiver}: {e}")

    def _group_papers(self, papers_dict):
        grouped = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

        for pid, paper in papers_dict.items():
            authors = paper.get("target_author", [])
            year = paper.get("year", 0)
            conf = paper.get("conference", "Unknown")

            for author in authors:
                grouped[author][year][conf].append(paper)

        return grouped

    def _generate_result_html(
        self,
        python_result: dict,
        options: list,
        threshold: float,
        confs: list,
        manage_token: str,
    ):
        grouped_data = self._group_papers(python_result)

        selected_texts = []
        if options:
            for opt in options:
                try:
                    txt = self.option_map.get(int(opt))
                    if txt:
                        selected_texts.append(txt)
                except ValueError:
                    continue
        option_text = ", ".join(selected_texts) if selected_texts else "옵션 미선택"

        context = {
            "request": MockRequest(),
            "user": None,
            "options": {
                "options": options,
                "probability": threshold,
                "startyear": datetime.now().year,
                "endyear": datetime.now().year,
                "selectedConferences": confs,
            },
            "option_text": option_text,
            "grouped_papers": grouped_data,  # 원본 pythonResult 대신 그룹화된 데이터 전달
            "FASTAPI_BASE": "https://pcss.postech.ac.kr",
        }

        try:
            # 이메일 전용 템플릿 사용
            template = templates.env.get_template("subscribe/search_results.html")
            html_content = template.render(**context)

            return html_content

        except Exception as e:
            print(f"[Notifier] Template render error: {e}")
            import traceback

            traceback.print_exc()
            return "<h1>Error generating email</h1>"

    async def _advance_state(self, run_started_at: datetime):
        # 이번 실행이 어디까지 처리했는지 기록해서, 다음 실행이 같은 논문을
        # "최근 논문"으로 다시 잡아 중복 발송하지 않도록 함
        await asyncio.to_thread(
            notifier_state_col.update_one,
            {"_id": NOTIFIER_STATE_ID},
            {"$set": {"last_notified_at": run_started_at}},
            upsert=True,
        )

    async def run(self):
        print("[Notifier] Start")
        run_started_at = datetime.now(timezone.utc)

        state = await asyncio.to_thread(
            notifier_state_col.find_one, {"_id": NOTIFIER_STATE_ID}
        )
        # 최초 실행 등 상태가 없을 때만 32일 전으로 폴백
        cutoff_date = (
            state["last_notified_at"]
            if state and state.get("last_notified_at")
            else run_started_at - timedelta(days=32)
        )

        recent_papers_cursor = papers_col.find(
            {"created_at": {"$gte": cutoff_date}},
            {
                "_id": 0,
                "title": 1,
                "author_names": 1,
                "author_pids": 1,
                "conference": 1,
                "year": 1,
                "source": 1,
                "dblp_url": 1,
            },
        )
        recent_papers = await asyncio.to_thread(lambda: list(recent_papers_cursor))

        if not recent_papers:
            print("[Notifier] No new papers.")
            await self._advance_state(run_started_at)
            return

        subs = list(subscription_col.find({"is_enabled": True}))
        print(f"[Notifier] Papers: {len(recent_papers)}, Subs: {len(subs)}")

        for sub in subs:
            email = sub["email"]
            confs = sub.get("conferences", [])
            options = sub.get("options", [1])
            threshold = sub.get("threshold", 0.8)
            manage_token = sub.get("manage_token", "")

            if not confs:
                continue

            processor = SubscriptionProcessor(
                target_papers=recent_papers,
                options=options,
                threshold=threshold,
                startyear=0,
                endyear=0,
            )
            result_dict = await processor.run(confs)

            if result_dict:
                html_body = self._generate_result_html(
                    result_dict, options, threshold, confs, manage_token
                )
                title = f"[PCSS 구독] {len(result_dict)}개의 새로운 관심 논문이 도착했습니다."

                await asyncio.to_thread(self._send_email_sync, email, title, html_body)

        await self._advance_state(run_started_at)
