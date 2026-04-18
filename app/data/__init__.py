from app.db import name_col, conf_col, authors_col
import os
import json


if os.getenv("LLM_NAME_SOURCE") == "local":
    with open(os.path.join(os.path.dirname(__file__), 'llm_names.json'), 'r', encoding='utf-8') as f:
        name_dict = json.load(f)
    name_dict = {
        doc["name"]: doc["score"]
        for doc in name_dict
    }
else:
    print("Loading LLM names from DB")
    name_dict = {
        doc["name"]: doc["score"]
        for doc in name_col.find({}, {"_id": 0, "name": 1, "score": 1})
    }
    print(f"Loaded {len(name_dict)} LLM names from DB")
author_list = list(authors_col.find({}, {"_id": 0, "name": 1, "pid": 1}))

print("Loaded conferences from DB")

_conf_docs = list(
    conf_col.find(
        {},
        {"_id": 0, "name": 1, "params": 1, "kind": 1, "urls": 1},
    )
)

def _normalize_params(d: dict) -> list[str]:
    ps = d.get("params")
    if isinstance(ps, list) and ps:
        return [str(x).strip() for x in ps if str(x).strip()]

    return []

# 유효 문서만 유지 + 정렬(선택)
_conf_docs = [d for d in _conf_docs if d.get("name") and _normalize_params(d)]
_conf_docs.sort(key=lambda d: (str(d.get("kind", "")), str(d.get("name", ""))))

# 기존 전역 값들(호환 유지)
conf_param_list: list[str] = []
conf_param_dict: dict[str, list[str]] = {}  # name -> params(list)
param_conf_dict: dict[str, str] = {}        # param -> name

for d in _conf_docs:
    name = d["name"]
    params = _normalize_params(d)

    conf_param_dict[name] = params
    conf_param_list.extend(params)

    for p in params:
        # param 중복이 생기면 마지막이 덮어씀 (원하면 여기서 raise로 막아도 됨)
        param_conf_dict[p] = name

def get_conferences_for_ui():
    # 프론트가 선택할 수 있도록 params를 내려줌
    return [
        {
            "kind": d.get("kind", ""),
            "conference": d.get("name", ""),
            "params": _normalize_params(d),
        }
        for d in _conf_docs
    ]