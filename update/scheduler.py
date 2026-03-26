import os
import time
import asyncio
import traceback
import platform
from datetime import datetime
from dateutil.relativedelta import relativedelta
from app.data import name_dict, author_list
from app.db import name_col, authors_col
from update import update_authors
from update import update_papers 
from app.services.subscription_service import SubscriptionNotifier
from multiprocessing import Process
import asyncio

if platform.system() == 'Windows':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

def refresh_name_dict():
    print(f"[{datetime.now()}] name_dict 캐시 갱신 시작 (현재 크기: {len(name_dict)})")
    
    try:
        cursor = name_col.find({}, {"name": 1, "score": 1, "_id": 0})
        
        count = 0
        for doc in cursor:
            name = doc.get("name")
            score = doc.get("score")
            
            if name and score is not None:
                name_dict[name] = float(score)
                count += 1
                
        print(f"[{datetime.now()}] name_dict 갱신 완료 (최신 크기: {len(name_dict)}, 처리된 항목: {count})")
        
    except Exception as e:
        print(f"!!! name_dict 갱신 중 에러 발생: {e}")
        traceback.print_exc()
        
def refresh_author_list():
    print(f"[{datetime.now()}] author_list 캐시 갱신 시작 (현재 크기: {len(author_list)})")
    try:
        cursor = authors_col.find({}, {"_id": 0, "name": 1, "pid": 1})
        
        new_authors = list(cursor)
        author_list.clear()
        author_list.extend(new_authors)
        
        print(f"[{datetime.now()}] author_list 갱신 완료 (최신 크기: {len(author_list)})")
        
    except Exception as e:
        print(f"!!! author_list 갱신 중 에러 발생: {e}")
        traceback.print_exc()


def run_step_in_process(target_func, *args):
    """별도 프로세스에서 함수를 실행하여 종료 시 메모리를 완전히 회수함"""
    p = Process(target=target_func, args=args)
    p.start()
    p.join()  # 작업 완료까지 대기
    

def run_async_main(func, *args, **kwargs):
    """비동기 main 함수를 동기 프로세스 환경에서 실행하기 위한 래퍼"""
    asyncio.run(func(*args, **kwargs))

def run_job():
    xml_path = os.path.join(os.path.dirname(__file__), "dblp.xml")
    
    print(f"\n[{datetime.now()}] === 월간 업데이트 작업 시작 ===")
    
    try:
        # 1. 파일 정리
        if os.path.exists(xml_path):
            update_papers.cleanup_files(xml_path)

        # 2. 논문 추출 (기존 방식 유지)
        print(f"[{datetime.now()}] 논문 추출 시작 (프로세스 분리)...")
        run_step_in_process(update_papers.main)
        
        # 3. 저자 추출 및 판정 (수정된 부분)
        # DeepSeek 판정 로직은 async이므로 run_async_main을 거쳐 실행합니다.
        print(f"[{datetime.now()}] 저자 추출 및 DeepSeek 판정 시작...")
        run_step_in_process(run_async_main, update_authors.main, True) # True는 automatic 인자
        
        # 4. 캐시 갱신 및 알림
        print(f"[{datetime.now()}] 최신 저자 정보 메모리 로드 시작...")
        refresh_name_dict()
        refresh_author_list()
        
        print(f"[{datetime.now()}] 구독 알림 발송 서비스 시작...")
        notifier = SubscriptionNotifier()
        asyncio.run(notifier.run())
        
    except Exception as e:
        print(f"!!! 작업 중 에러 발생: {e}")
        traceback.print_exc()

    print(f"[{datetime.now()}] === 모든 작업 종료 ===\n")


def next_monthly_run(now: datetime) -> datetime:
    """
    다음 실행 시각 계산: 매달 1일 00:00:00
    """
    candidate = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # 이미 지났으면 다음 달 1일로 설정
    if candidate <= now:
        candidate = candidate + relativedelta(months=1)
    return candidate


def format_timedelta(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    return f"{days}일 {hours}시간 {minutes}분"


def main():
    print("=== DBLP Scheduler & Notifier Started ===")
    print("매달 1일 00:00에 데이터 갱신 및 구독 메일을 발송합니다.\n")
    
    run_job()

    while True:
        now = datetime.now()
        target_time = next_monthly_run(now)
        wait_seconds = int((target_time - now).total_seconds())

        print(f"현재 시각: {now.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"다음 실행: {target_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"대기 시간: {format_timedelta(wait_seconds)}")
        print("대기 중... (Ctrl+C로 종료)")

        # 긴 대기시간 동안 1분마다 체크하며 sleep
        while wait_seconds > 0:
            sleep_sec = min(60, wait_seconds)
            time.sleep(sleep_sec)
            wait_seconds -= sleep_sec

        # 대기 종료 후 작업 실행
        run_job()


if __name__ == "__main__":
    main()