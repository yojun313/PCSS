"""DBLP 개인 페이지(www / "Home Page") 레코드에서 소속 · 홈페이지 링크를 뽑는다.

update_papers(월간 XML 파싱)와 backfill_affiliations(기존 dblp 컬렉션)가 같은 규칙을 쓰도록
dict 형태(extract_dblp.elem_to_dict 와 같은 모양) 하나만 받는다.
"""

from typing import Any, Dict, List


def _as_list(v: Any) -> List[Any]:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def profile_from_www(rec: Dict[str, Any]) -> Dict[str, List[Any]]:
    """
    rec["note"]: {"type": "affiliation", "label": "former", "text": "..."} 또는 그 리스트
      - label 이 없으면 현재 소속, 있으면("former", "PhD 2014" 등) 이전 소속
    rec["url"]: 문자열 또는 {"type": ..., "text": ...} 의 리스트
    """
    affiliations = []
    for note in _as_list(rec.get("note")):
        if not isinstance(note, dict) or note.get("type") not in (
            "affiliation",
            "affil",
        ):
            continue
        text = (note.get("text") or "").strip()
        if not text:
            continue
        item = {"text": text}
        label = (note.get("label") or "").strip()
        if label:
            item["label"] = label
        affiliations.append(item)

    urls = []
    for u in _as_list(rec.get("url")):
        text = (u.get("text") if isinstance(u, dict) else u) or ""
        text = text.strip()
        if text.startswith("http") and text not in urls:
            urls.append(text)

    return {"affiliations": affiliations, "urls": urls}


def current_affiliation(doc: Dict[str, Any]) -> str:
    """authors 문서에서 대표 소속 하나 (label 없는 것 우선)."""
    affs = doc.get("affiliations") or []
    for a in affs:
        if not a.get("label"):
            return a.get("text", "")
    return ""
