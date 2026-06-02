import requests
import sys
import json

# 서버 주소 설정 (환경에 맞게 변경하세요)
API_BASE_URL = "https://pcss.r-e.kr/api/conf"


def print_header(title):
    print("\n" + "=" * 40)
    print(f" {title}")
    print("=" * 40)


def get_user_input(prompt):
    return input(f"{prompt}: ").strip()


def get_user_selection(options, label_key=None):
    """
    리스트를 출력하고 사용자에게 번호 선택을 요청하는 헬퍼 함수
    options: 선택지 리스트 (문자열 리스트 또는 딕셔너리 리스트)
    label_key: 딕셔너리 리스트인 경우 화면에 표시할 키 이름
    """
    if not options:
        print(">> 선택 가능한 항목이 없습니다.")
        return None

    print("-" * 30)
    for idx, item in enumerate(options):
        display_text = item if label_key is None else item.get(label_key, str(item))
        print(f"{idx + 1}. {display_text}")
    print("0. 취소")
    print("-" * 30)

    while True:
        choice = input("번호를 선택하세요: ")
        if not choice.isdigit():
            print("숫자를 입력해주세요.")
            continue

        choice = int(choice)
        if choice == 0:
            return None
        if 1 <= choice <= len(options):
            return options[choice - 1]
        print("유효하지 않은 번호입니다.")


# ==========================================
# API 호출 래퍼 함수들
# ==========================================


def api_get_kinds():
    try:
        resp = requests.get(f"{API_BASE_URL}/kinds")
        resp.raise_for_status()
        return resp.json().get("kinds", [])
    except Exception as e:
        print(f"Error fetching kinds: {e}")
        return []


def api_get_conferences_by_kind(kind):
    try:
        resp = requests.get(f"{API_BASE_URL}/by-kind/{kind}")
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Error fetching conferences: {e}")
        return []


def api_create_conference(data):
    try:
        resp = requests.post(f"{API_BASE_URL}/", json=data)
        if resp.status_code == 409:
            print(">> [실패] 이미 존재하는 파라미터입니다.")
            return False
        resp.raise_for_status()
        print(f">> [성공] 생성 완료: {resp.json()}")
        return True
    except Exception as e:
        print(f"Error creating conference: {e}")
        return False


def api_delete_conference(param):
    try:
        resp = requests.delete(f"{API_BASE_URL}/{param}")
        resp.raise_for_status()
        print(">> [성공] 컨퍼런스 삭제 완료")
    except Exception as e:
        print(f"Error deleting conference: {e}")


def api_add_url(param, url):
    try:
        # Schema: AddUrlRequest(url=...)
        resp = requests.post(f"{API_BASE_URL}/{param}/urls", json={"url": url})
        resp.raise_for_status()
        print(">> [성공] URL 추가 완료")
    except Exception as e:
        print(f"Error adding URL: {e}")


def api_delete_url(param, url):
    try:
        # Delete param으로 url 전달
        resp = requests.delete(f"{API_BASE_URL}/{param}/urls", params={"url": url})
        resp.raise_for_status()
        print(">> [성공] URL 삭제 완료")
    except Exception as e:
        print(f"Error deleting URL: {e}")


def api_add_params(base_param, new_params):
    """
    new_params: List[str]
    """
    try:
        resp = requests.post(
            f"{API_BASE_URL}/{base_param}/params/batch", json={"params": new_params}
        )
        if resp.status_code == 409:
            print(
                ">> [실패] 입력한 파라미터 중 일부가 이미 다른 컨퍼런스에서 사용 중입니다."
            )
            return False
        resp.raise_for_status()
        print(">> [성공] 별칭(Param) 여러 개 추가 완료")
        return True
    except Exception as e:
        print(f"Error adding params: {e}")
        # 디버깅용
        try:
            print(resp.text)
        except:
            pass
        return False


def api_delete_param(base_param, target_param):
    try:
        # Schema: DeleteParamRequest(param=...) -> DELETE body는 requests에서 data/json 사용 가능
        # FastAPI DELETE body 지원함
        resp = requests.delete(
            f"{API_BASE_URL}/{base_param}/params", json={"param": target_param}
        )
        if resp.status_code == 400:
            print(">> [실패] 마지막 파라미터는 삭제할 수 없습니다.")
            return
        resp.raise_for_status()
        print(">> [성공] 별칭(Param) 삭제 완료")
    except Exception as e:
        print(f"Error deleting param: {e}")


# ==========================================
# 사용자 흐름 (Flow) 함수들
# ==========================================


def select_target_conference():
    """
    Kind 선택 -> Conference 선택 과정을 거쳐 특정 컨퍼런스 객체를 반환
    """
    print("\n[단계 1] 분류(Kind) 선택")
    kinds = api_get_kinds()
    selected_kind = get_user_selection(kinds)
    if not selected_kind:
        return None

    print(f"\n[단계 2] {selected_kind} 목록에서 컨퍼런스 선택")
    confs = api_get_conferences_by_kind(selected_kind)

    # 리스트 보여줄 때 보기 좋게 포맷팅
    # params 리스트의 첫 번째 요소를 대표 이름으로 사용
    display_list = []
    for c in confs:
        c["_display"] = f"{c.get('name', 'No Title')} (Params: {c.get('params')})"
        display_list.append(c)

    selected_conf = get_user_selection(display_list, label_key="_display")
    return selected_conf


def flow_create_conference():
    print_header("새 컨퍼런스 생성")
    kind = get_user_input("분류(Kind) 입력 (예: conf, journal)")
    title = get_user_input("제목(Title) 입력")
    param_str = get_user_input("기본 파라미터(ID) 입력 (여러 개면 콤마로 구분)")

    params = [p.strip() for p in param_str.split(",") if p.strip()]
    if not params:
        print(">> 파라미터는 필수입니다.")
        return

    data = {"kind": kind, "name": title, "params": params, "urls": []}
    api_create_conference(data)


def flow_view_details():
    print_header("컨퍼런스 상세 조회")
    conf = select_target_conference()
    if conf:
        print("\n--- 상세 정보 ---")
        print(json.dumps(conf, indent=2, ensure_ascii=False))


def flow_manage_urls():
    print_header("URL 관리")
    conf = select_target_conference()
    if not conf:
        return

    # 식별자로 사용할 param 하나 가져오기
    base_param = conf["params"][0]

    while True:
        print(f"\n현재 선택된 컨퍼런스: {conf.get('name')}")
        print(f"URL 목록: {conf.get('urls', [])}")
        print("1. URL 추가")
        print("2. URL 삭제")
        print("0. 뒤로 가기")

        choice = input("선택: ")
        if choice == "0":
            break
        elif choice == "1":
            new_url = get_user_input("추가할 URL 입력")
            if new_url:
                api_add_url(base_param, new_url)
                # 갱신된 정보 다시 가져오기 (간이 구현: 로컬 업데이트)
                conf["urls"] = conf.get("urls", []) + [new_url]
        elif choice == "2":
            urls = conf.get("urls", [])
            target = get_user_selection(urls)
            if target:
                api_delete_url(base_param, target)
                # 로컬 업데이트
                if target in conf["urls"]:
                    conf["urls"].remove(target)


def flow_manage_params():
    print_header("별칭(Param) 관리")
    conf = select_target_conference()
    if not conf:
        return

    base_param = conf["params"][0]  # 현재 접속용 파라미터

    while True:
        print(f"\n현재 선택된 컨퍼런스: {conf.get('name')}")
        print(f"파라미터 목록: {conf.get('params', [])}")
        print("1. 파라미터 추가 (여러 개 가능, 콤마 구분)")
        print("2. 파라미터 삭제")
        print("0. 뒤로 가기")

        choice = input("선택: ")
        if choice == "0":
            break
        elif choice == "1":
            param_str = get_user_input("추가할 파라미터 입력 (여러 개면 콤마로 구분)")
            new_params = [p.strip() for p in param_str.split(",") if p.strip()]
            if new_params:
                ok = api_add_params(base_param, new_params)
                if ok:
                    for p in new_params:
                        if p not in conf["params"]:
                            conf["params"].append(p)
        elif choice == "2":
            params = conf.get("params", [])
            target = get_user_selection(params)
            if target:
                api_delete_param(base_param, target)
                if target in conf["params"]:
                    conf["params"].remove(target)
                # 만약 방금 base_param을 지웠다면 base_param을 갱신해야 함
                if target == base_param and conf["params"]:
                    base_param = conf["params"][0]


def flow_delete_conference():
    print_header("컨퍼런스 삭제")
    conf = select_target_conference()
    if not conf:
        return

    print(f"\n정말로 삭제하시겠습니까? Name: {conf.get('name')}")
    confirm = input("삭제하려면 'yes'를 입력하세요: ")
    if confirm.lower() == "yes":
        # 삭제 시에는 params 중 아무거나 하나 쓰면 됨
        api_delete_conference(conf["params"][0])
    else:
        print(">> 삭제 취소됨")


# ==========================================
# 메인 메뉴
# ==========================================


def main_menu():
    while True:
        print("\n" + "=" * 30)
        print(" 컨퍼런스 관리 시스템 (CLI)")
        print("=" * 30)
        print("1. 컨퍼런스 조회 (상세)")
        print("2. 새 컨퍼런스 생성")
        print("3. URL 추가/삭제")
        print("4. 파라미터(별칭) 추가/삭제")
        print("5. 컨퍼런스 전체 삭제")
        print("0. 종료")
        print("-" * 30)

        choice = input("메뉴 선택: ")

        if choice == "1":
            flow_view_details()
        elif choice == "2":
            flow_create_conference()
        elif choice == "3":
            flow_manage_urls()
        elif choice == "4":
            flow_manage_params()
        elif choice == "5":
            flow_delete_conference()
        elif choice == "0":
            print("프로그램을 종료합니다.")
            sys.exit()
        else:
            print("올바른 번호를 선택해주세요.")


if __name__ == "__main__":
    # 서버 연결 테스트
    try:
        requests.get(API_BASE_URL + "/docs")
    except requests.exceptions.ConnectionError:
        print(
            f"오류: 서버({API_BASE_URL})에 연결할 수 없습니다. FastAPI 서버를 먼저 실행해주세요."
        )
        sys.exit(1)

    main_menu()
