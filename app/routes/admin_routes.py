from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from datetime import datetime, timezone
from app.core.templates import templates
from app.libs.auth import _require_admin_or_redirect
from app.libs.email import config_summary, probe, send_mail
from app.db import admin_col

router = APIRouter()


@router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    # 관리자 권한 확인 로직 (기존 코드 패턴 유지)
    next_path = "/admin/dashboard"
    email, status_or_resp = _require_admin_or_redirect(request, next_path)

    # 1. 로그인 안 된 경우 -> 로그인 페이지로 리다이렉트
    if isinstance(status_or_resp, RedirectResponse):
        return status_or_resp

    # 2. 로그인은 됐으나 관리자가 아닌 경우 -> 에러 페이지 표시 (또는 403)
    if status_or_resp is None:
        return templates.TemplateResponse(
            request=request,
            name="admin/access_denied.html",
            context={"error": "관리자 권한이 없습니다."},
            status_code=403,
        )

    # 3. 관리자 인증 성공 -> 대시보드 렌더링
    return templates.TemplateResponse(
        request=request,
        name="admin/dashboard.html",
        context={"email": email},
    )


# 관리자 목록 페이지
@router.get("/manage/admins", response_class=HTMLResponse)
async def manage_admins_page(request: Request):
    email, resp = _require_admin_or_redirect(request, "/admin/manage/admins")
    if isinstance(resp, RedirectResponse) or resp is None:
        return resp

    # 현재 접속한 관리자 정보 조회하여 루트 권한 확인
    current_admin = admin_col.find_one({"email": email})
    is_root = current_admin.get("is_root", False) if current_admin else False

    admins = list(admin_col.find({}, {"_id": 0}).sort("name", 1))

    return templates.TemplateResponse(
        request=request,
        name="admin/admin_list.html",
        context={
            "email": email,
            "admins": admins,
            "is_root": is_root,
        },
    )


# 관리자 추가 (루트만 가능)
@router.post("/manage/admins/add")
async def add_admin(
    request: Request, new_email: str = Form(...), new_name: str = Form(...)
):
    email, resp = _require_admin_or_redirect(request, "/admin/manage/admins")
    if isinstance(resp, RedirectResponse) or resp is None:
        return resp

    # 권한 검증: 루트 계정이 아니면 추가 불가
    current_admin = admin_col.find_one({"email": email})
    if not current_admin or not current_admin.get("is_root", False):
        return RedirectResponse(url="/admin/manage/admins", status_code=302)

    target_email = new_email.strip().lower()
    target_name = new_name.strip()

    if not admin_col.find_one({"email": target_email}):
        admin_col.insert_one(
            {
                "email": target_email,
                "name": target_name,
                "is_root": False,  # 새로 추가되는 관리자는 기본적으로 일반 관리자
            }
        )

    return RedirectResponse(url="/admin/manage/admins", status_code=302)


# 관리자 삭제 (루트만 가능)
@router.post("/manage/admins/delete")
async def delete_admin(request: Request, target_email: str = Form(...)):
    current_user_email, resp = _require_admin_or_redirect(
        request, "/admin/manage/admins"
    )
    if isinstance(resp, RedirectResponse) or resp is None:
        return resp

    # 권한 검증: 루트 계정이 아니면 삭제 불가
    current_admin = admin_col.find_one({"email": current_user_email})
    if not current_admin or not current_admin.get("is_root", False):
        return RedirectResponse(url="/admin/manage/admins", status_code=302)

    target_email = target_email.strip().lower()

    # 자기 자신 삭제 불가
    if current_user_email == target_email:
        return RedirectResponse(url="/admin/manage/admins", status_code=302)

    # 삭제 대상이 루트 계정인 경우 삭제 불가 (안전 장치)
    target_admin = admin_col.find_one({"email": target_email})
    if target_admin and target_admin.get("is_root", False):
        return RedirectResponse(url="/admin/manage/admins", status_code=302)

    admin_col.delete_one({"email": target_email})

    return RedirectResponse(url="/admin/manage/admins", status_code=302)


# ---------------------------
# 메일 발송 테스트
# ---------------------------
def _mail_test_guard(request: Request):
    """(admin_email, error_response) 형태로 돌려준다."""
    email, status_or_resp = _require_admin_or_redirect(request, "/admin/mail-test")
    if isinstance(status_or_resp, RedirectResponse):
        return None, status_or_resp
    if status_or_resp is None:
        return None, templates.TemplateResponse(
            request=request,
            name="admin/access_denied.html",
            context={"error": "관리자 권한이 없습니다."},
            status_code=403,
        )
    return email, None


def _render_mail_test(request: Request, email: str, **extra):
    ctx = {
        "email": email,
        "config": config_summary(),
        "receiver": extra.pop("receiver", email),
    }
    ctx.update(extra)
    return templates.TemplateResponse(
        request=request, name="admin/mail_test.html", context=ctx
    )


@router.get("/mail-test", response_class=HTMLResponse)
async def mail_test_page(request: Request):
    email, err = _mail_test_guard(request)
    if err:
        return err
    return _render_mail_test(request, email)


@router.post("/mail-test/check", response_class=HTMLResponse)
async def mail_test_check(request: Request, receiver: str = Form(...)):
    email, err = _mail_test_guard(request)
    if err:
        return err

    receiver = (receiver or "").strip()
    if not receiver:
        return _render_mail_test(request, email, error="수신 주소를 입력해 주세요.")

    return _render_mail_test(
        request, email, receiver=receiver, probe_result=probe(receiver)
    )


@router.post("/mail-test/send", response_class=HTMLResponse)
async def mail_test_send(request: Request, receiver: str = Form(...)):
    email, err = _mail_test_guard(request)
    if err:
        return err

    receiver = (receiver or "").strip()
    if not receiver:
        return _render_mail_test(request, email, error="수신 주소를 입력해 주세요.")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    cfg = config_summary()
    try:
        send_mail(
            receiver,
            "[PCSS] 메일 발송 테스트",
            text=(
                "PCSS 메일 발송 테스트입니다.\n\n"
                f"보낸 시각: {now}\n"
                f"발송 경로: {cfg['backend']} ({cfg['host']}:{cfg['port']})\n"
                f"발신 주소: {cfg['sender']}\n"
                f"요청한 관리자: {email}\n\n"
                "이 메일이 보인다면 현재 설정으로 정상 발송되고 있습니다.\n"
            ),
        )
    except Exception as e:
        return _render_mail_test(
            request,
            email,
            receiver=receiver,
            error=f"발송 실패 — {type(e).__name__}: {e}",
        )

    return _render_mail_test(
        request,
        email,
        receiver=receiver,
        message=f"{receiver} 주소로 테스트 메일을 발송했습니다. 수신함을 확인해 주세요.",
    )
