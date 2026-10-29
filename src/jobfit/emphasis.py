"""이력서 강조 포인트(스크립트, AI 없음) — report.md의 한 절.

- 위로 올릴 것: me/resume.yaml 항목 중 이 공고의 필수·우대·키워드와 많이 맞는 순. resume.yaml이 없으면 경험 카드 순위로.
- 표기 맞추기: 같은 기술을 공고와 다르게 적은 곳(예: 이력서 '스프링부트' ↔ 공고 'Spring Boot').
"""

from __future__ import annotations

from pathlib import Path

import yaml

from jobfit.clean.keywords import KeywordDict
from jobfit.match import CARD_POINTS, terms_of

TOP_RESUME = 5
TOP_CARDS = 3


def load_resume(path: Path) -> tuple[list[dict], list[str]]:
    if not path.exists():
        return [], []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return [], ["resume.yaml을 읽을 수 없습니다"]
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return [], ["resume.yaml은 items: 아래에 항목 목록이어야 합니다"]
    return [i for i in items if isinstance(i, dict) and (i.get("title") or i.get("text"))], []


def split_examples(items: list[dict], example_file: Path) -> tuple[list[dict], list[str]]:
    """(내 항목, me.example 에서 그대로 복사된 예시 항목의 제목). 제목과 내용이 둘 다 같아야 예시로 본다(고쳐 쓴 항목은 내 항목).
    예시 항목은 강조 포인트에 쓰지 않는다 — 가짜 이력서 항목을 '위로 올릴 것'으로 내놓지 않게."""
    examples, _ = load_resume(example_file)
    pairs = {_pair(i) for i in examples}
    mine = [i for i in items if _pair(i) not in pairs]
    return mine, [str(i.get("title") or i.get("text", ""))[:40] for i in items if _pair(i) in pairs]


def example_note(items: list[dict], example_titles: list[str]) -> str:
    """예시 항목이 남았을 때 사용자에게 알릴 한 줄. 없으면 빈 문자열."""
    if not example_titles:
        return ""
    if len(example_titles) == len(items):
        return "me/resume.yaml 이 예시 그대로입니다 — 강조 포인트는 경험 카드 순위로 대신합니다. 내 이력서 항목으로 바꾸거나 파일을 지우세요"
    shown = ", ".join(example_titles[:5]) + (f" 외 {len(example_titles) - 5}개" if len(example_titles) > 5 else "")
    return f"me/resume.yaml 에 예시 항목 {len(example_titles)}개가 남아 있습니다({shown}) — 강조 포인트에서 뺐습니다. 직접 지우세요"


def build_emphasis(match: dict, cards: list[dict], resume_items: list[dict], kd: KeywordDict, posting_text: str) -> dict:
    requirements = match.get("requirements", [])
    all_terms = list(dict.fromkeys(list(match.get("keywords", [])) + [k for r in requirements for k in r.get("terms", [])]))

    if resume_items:
        source = "resume"
        scored = []
        for item in resume_items:
            tags = kd.expand([kd.normalize(str(t)) for t in (item.get("tags") or [])])
            blob = _blob(item.get("title"), item.get("text"), " ".join(tags))
            terms = {t.lower() for t in terms_of(blob, kd, tags)} | {t.lower() for t in tags}
            hits = _hits(requirements, match, terms)
            score = sum(CARD_POINTS[k] * len(v) for k, v in hits.items())
            if score:
                scored.append({"section": str(item.get("section") or ""), "title": str(item.get("title") or item.get("text", ""))[:60], "score": score, **hits})
        top = sorted(scored, key=lambda x: -x["score"])[:TOP_RESUME]
        # 표기는 이력서에 실제로 보이는 글(제목·내용)에서만 본다 — 태그는 분류용이라 제외
        texts = [(str(i.get("title") or i.get("text", ""))[:40], _blob(i.get("title"), i.get("text"))) for i in resume_items]
    else:
        source = "cards"
        top = [
            {"section": "경험 카드", "title": c["title"] or c["id"], "score": c["score"], "must": c["must_hits"], "nice": c["nice_hits"], "keywords": c["keywords_hits"]}
            for c in match.get("cards", [])
            if c["score"] > 0
        ][:TOP_CARDS]
        by_id = {str(c.get("id")): c for c in cards}
        texts = [(c["title"] or c["id"], _blob(by_id.get(c["id"], {}).get("title"), by_id.get(c["id"], {}).get("summary"))) for c in match.get("cards", [])]

    wording = []
    seen = set()
    for canon in all_terms:
        if canon not in kd.variants:
            continue
        posting_forms = kd.forms(posting_text, canon)
        if not posting_forms:
            continue
        posting_form = max(posting_forms, key=posting_forms.get)
        for title, blob in texts:
            forms = kd.forms(blob, canon)
            if not forms:
                continue
            item_form = max(forms, key=forms.get)
            if item_form.lower() != posting_form.lower() and (item_form, posting_form) not in seen:
                seen.add((item_form, posting_form))
                wording.append({"item": title, "from": item_form, "to": posting_form})
    return {"source": source, "top": top, "wording": wording}


def _hits(requirements: list[dict], match: dict, terms: set[str]) -> dict[str, list[str]]:
    return {
        "must": [r["text"] for r in requirements if r["kind"] == "must" and {t.lower() for t in r["terms"]} & terms],
        "nice": [r["text"] for r in requirements if r["kind"] == "nice" and {t.lower() for t in r["terms"]} & terms],
        "keywords": [k for k in match.get("keywords", []) if k.lower() in terms],
    }


def _pair(item: dict) -> tuple[str, str]:
    return str(item.get("title") or "").strip(), str(item.get("text") or "").strip()


def _blob(*parts) -> str:
    return " ".join(str(p) for p in parts if p)
