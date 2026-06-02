from fastapi import APIRouter, HTTPException, Query, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from datetime import datetime, timezone
from app.core.templates import templates
from app.db import conf_col
from app.libs.auth import _require_admin_or_redirect
from app.schemas.conf import (
    AddParamsRequest,
    DeleteParamRequest,
    CreateConferenceRequest,
)

router = APIRouter()


def _now():
    return datetime.now(timezone.utc)


def _parse_list(text: str) -> list[str]:
    items = []
    for token in (text or "").replace("\r", "\n").replace(",", "\n").split("\n"):
        t = token.strip()
        if t:
            items.append(t)
    return list(dict.fromkeys(items))


def _normalize_params(doc: dict) -> list[str]:
    ps = doc.get("params")
    if isinstance(ps, list):
        out = [str(x).strip() for x in ps if str(x).strip()]
        if out:
            return out

    p = doc.get("param")
    if isinstance(p, str) and p.strip():
        return [p.strip()]

    return []


def _find_by_param(param: str) -> dict | None:
    return conf_col.find_one(
        {"$or": [{"params": param}, {"param": param}]},
        {"_id": 0},
    )


@router.get("/manage", response_class=HTMLResponse)
async def conf_manage_page(request: Request):
    next_path = request.url.path

    email, status_or_resp = _require_admin_or_redirect(request, next_path)
    if isinstance(status_or_resp, RedirectResponse):
        return status_or_resp

    if status_or_resp is None:
        return templates.TemplateResponse(
            request=request,
            name="admin/access_denied.html",
            context={
                "email": email,
                "error": "권한이 없습니다. admin에 등록된 이메일로 로그인하세요.",
                "success": None,
                "conferences": [],
            },
            status_code=403,
        )

    docs = list(conf_col.find({}, {"_id": 0}))
    for d in docs:
        d["params"] = _normalize_params(d)
        d.pop("param", None)
        d["kind"] = (d.get("kind") or "").strip()
        d["name"] = (d.get("name") or "").strip()

    docs.sort(
        key=lambda x: (
            x.get("kind", ""),
            x.get("name", ""),
            ",".join(x.get("params") or []),
        )
    )

    return templates.TemplateResponse(
        request=request,
        name="admin/admin_conferences.html",
        context={
            "email": email,
            "error": None,
            "success": None,
            "conferences": docs,
        },
    )


@router.post("/manage/create", response_class=HTMLResponse)
async def conf_manage_create(
    request: Request,
    kind: str = Form(...),
    name: str = Form(""),
    params_text: str = Form(...),
):
    next_path = request.url.path.rsplit("/", 1)[0]

    email, status_or_resp = _require_admin_or_redirect(request, next_path)
    if isinstance(status_or_resp, RedirectResponse):
        return status_or_resp
    if status_or_resp is None:
        return RedirectResponse(url=next_path, status_code=303)

    kind = (kind or "").strip()
    name = (name or "").strip()
    params = _parse_list(params_text)

    if not kind:
        return RedirectResponse(url=next_path, status_code=303)
    if not params:
        return RedirectResponse(url=next_path, status_code=303)

    exists = conf_col.find_one(
        {"$or": [{"params": {"$in": params}}, {"param": {"$in": params}}]}, {"_id": 1}
    )
    if exists:
        docs = list(conf_col.find({}, {"_id": 0}))
        for d in docs:
            d["params"] = _normalize_params(d)
            d.pop("param", None)
        return templates.TemplateResponse(
            request=request,
            name="admin/admin_conferences.html",
            context={
                "email": email,
                "error": "이미 사용 중인 param이 포함되어 있습니다.",
                "success": None,
                "conferences": docs,
            },
            status_code=409,
        )

    now = _now()
    conf_col.insert_one(
        {
            "kind": kind,
            "name": name,
            "params": params,
            "created_at": now,
            "updated_at": now,
            "meta": {"created_by": email},
        }
    )
    return RedirectResponse(url=next_path, status_code=303)


@router.post("/manage/{base_param:path}/params/add", response_class=HTMLResponse)
async def conf_manage_add_params(
    request: Request, base_param: str, params_text: str = Form(...)
):
    next_path = "/conferences/manage"
    email, status_or_resp = _require_admin_or_redirect(request, next_path)
    if isinstance(status_or_resp, RedirectResponse):
        return status_or_resp
    if status_or_resp is None:
        return RedirectResponse(url=next_path, status_code=303)

    new_params = _parse_list(params_text)
    if not new_params:
        return RedirectResponse(url=next_path, status_code=303)

    exists = conf_col.find_one(
        {"$or": [{"params": {"$in": new_params}}, {"param": {"$in": new_params}}]},
        {"_id": 1},
    )
    if exists:
        docs = list(conf_col.find({}, {"_id": 0}))
        for d in docs:
            d["params"] = _normalize_params(d)
            d.pop("param", None)
        return templates.TemplateResponse(
            request=request,
            name="admin/admin_conferences.html",
            context={
                "email": email,
                "error": "추가하려는 param 중 이미 사용 중인 값이 있습니다.",
                "success": None,
                "conferences": docs,
            },
            status_code=409,
        )

    conf_col.update_one(
        {"$or": [{"params": base_param}, {"param": base_param}]},
        {
            "$addToSet": {"params": {"$each": new_params}},
            "$set": {"updated_at": _now()},
        },
    )
    conf_col.update_one(
        {"$or": [{"params": base_param}, {"param": base_param}]},
        {"$unset": {"param": ""}},
    )
    return RedirectResponse(url=next_path, status_code=303)


@router.post("/manage/{base_param:path}/params/delete", response_class=HTMLResponse)
async def conf_manage_delete_param(
    request: Request, base_param: str, param: str = Form(...)
):
    next_path = "/conferences/manage"
    email, status_or_resp = _require_admin_or_redirect(request, next_path)
    if isinstance(status_or_resp, RedirectResponse):
        return status_or_resp
    if status_or_resp is None:
        return RedirectResponse(url=next_path, status_code=303)

    param = (param or "").strip()
    if not param:
        return RedirectResponse(url=next_path, status_code=303)

    doc = conf_col.find_one(
        {"$or": [{"params": base_param}, {"param": base_param}]},
        {"_id": 0, "params": 1, "param": 1},
    )
    if not doc:
        return RedirectResponse(url=next_path, status_code=303)

    params = _normalize_params(doc)
    if param not in params:
        return RedirectResponse(url=next_path, status_code=303)

    if len(params) <= 1:
        docs = list(conf_col.find({}, {"_id": 0}))
        for d in docs:
            d["params"] = _normalize_params(d)
            d.pop("param", None)
        return templates.TemplateResponse(
            request=request,
            name="admin/admin_conferences.html",
            context={
                "email": email,
                "error": "마지막 param은 삭제할 수 없습니다.",
                "success": None,
                "conferences": docs,
            },
            status_code=400,
        )

    conf_col.update_one(
        {"$or": [{"params": base_param}, {"param": base_param}]},
        {"$pull": {"params": param}, "$set": {"updated_at": _now()}},
    )
    return RedirectResponse(url=next_path, status_code=303)


@router.post("/manage/{base_param:path}/delete", response_class=HTMLResponse)
async def conf_manage_delete_conference(request: Request, base_param: str):
    next_path = "/conferences/manage"
    email, status_or_resp = _require_admin_or_redirect(request, next_path)
    if isinstance(status_or_resp, RedirectResponse):
        return status_or_resp
    if status_or_resp is None:
        return RedirectResponse(url=next_path, status_code=303)

    conf_col.delete_one({"$or": [{"params": base_param}, {"param": base_param}]})
    return RedirectResponse(url=next_path, status_code=303)


@router.get("/api/kinds")
async def get_kinds():
    kinds = conf_col.distinct("kind")
    return {"kinds": kinds}


@router.get("/api/by-kind/{kind}")
async def get_conferences_by_kind(kind: str):
    docs = list(conf_col.find({"kind": kind}, {"_id": 0}))
    if not docs:
        raise HTTPException(404, "No conferences for this kind")

    for d in docs:
        d["params"] = _normalize_params(d)
        d.pop("param", None)

    return docs


@router.get("/api/{param}")
async def get_conference(param: str):
    doc = _find_by_param(param)
    if not doc:
        raise HTTPException(404, "Conference not found")

    doc["params"] = _normalize_params(doc)
    doc.pop("param", None)
    return doc


@router.post("/api/")
async def create_conference(body: CreateConferenceRequest):
    data = body.dict()

    params = data.get("params")
    if params is None:
        p = data.get("param")
        if isinstance(p, str) and p.strip():
            params = [p.strip()]
        else:
            params = []
    if not isinstance(params, list) or not any(str(x).strip() for x in params):
        raise HTTPException(422, "params must be a non-empty list")

    params = [str(x).strip() for x in params if str(x).strip()]
    data["params"] = params
    data.pop("param", None)

    if conf_col.find_one({"$or": [{"params": {"$in": params}}]}):
        raise HTTPException(409, "one of params already exists")

    conf_col.insert_one(data)
    return {"status": "created", "params": params}


@router.delete("/api/{param}")
async def delete_conference(param: str):
    result = conf_col.delete_one({"$or": [{"params": param}, {"param": param}]})
    if result.deleted_count == 0:
        raise HTTPException(404, "Conference not found")
    return {"status": "deleted", "param": param}


@router.post("/api/{param}/params")
async def add_params_batch(param: str, body: AddParamsRequest):
    new_params = [p.strip() for p in body.params if p.strip()]
    if not new_params:
        raise HTTPException(422, "params is empty")

    exists = conf_col.find_one(
        {"$or": [{"params": {"$in": new_params}}, {"param": {"$in": new_params}}]},
        {"_id": 1},
    )
    if exists:
        raise HTTPException(409, "one of params already exists in another conference")

    result = conf_col.update_one(
        {"$or": [{"params": param}, {"param": param}]},
        {"$addToSet": {"params": {"$each": new_params}}},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Conference not found")

    conf_col.update_one(
        {"$or": [{"params": param}, {"param": param}]}, {"$unset": {"param": ""}}
    )
    return {"status": "added", "base": param, "params": new_params}


@router.delete("/api/{param}/params")
async def delete_param(param: str, body: DeleteParamRequest):
    del_param = body.param.strip()
    if not del_param:
        raise HTTPException(422, "param is empty")

    doc = conf_col.find_one(
        {"$or": [{"params": param}, {"param": param}]}, {"_id": 0, "params": 1}
    )
    if not doc:
        raise HTTPException(404, "Conference not found")

    params = doc.get("params") or []
    if del_param not in params:
        raise HTTPException(404, "param not in this conference")

    if len(params) <= 1:
        raise HTTPException(400, "cannot delete the last param")

    result = conf_col.update_one(
        {"$or": [{"params": param}, {"param": param}]},
        {"$pull": {"params": del_param}},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Conference not found")

    return {"status": "deleted", "base": param, "param": del_param}
