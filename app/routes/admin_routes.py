from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from app.core.templates import templates
from app.libs.auth import _require_admin_or_redirect
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
