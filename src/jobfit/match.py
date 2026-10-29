"""매칭(스크립트, AI 없음) — 공고 분석 결과와 경험 카드를 맞춘다. output/<공고>/match.json.

1. 필수(must)·우대(nice) 항목마다 낱말(키워드 사전 + 카드 태그)을 뽑아, 그 낱말을 태그로 가진 카드를 찾는다.
   카드 태그는 data/implies.yaml 로 넓혀 본다 — Spring Boot 카드는 'Java' 요건에도 맞는다.
   낱말을 하나도 못 뽑은 항목("피드백에 따른 접근 변경" 같은)은 태그로 판단할 수 없어 점수에서 뺀다.
2. 점수 = 필수 반영률 60% + 우대 25% + 키워드 15%. 분모가 0인 항목의 가중치는 나머지에 나눠 준다.
3. 문항은 규칙으로 유형을 나누고(지원동기·직무역량·협업·문제해결·성장·기타), 카드 usable_for가 맞는 카드 중
   이 공고에 잘 맞는 순으로 주력 1개·보조 1개를 고른다. 한 카드는 한 문항에서만 주력이다.
4. 사용자가 match.json의 배정을 고쳤으면 다시 돌려도 그 배정을 지킨다.
부족한 역량 목록(갭 리포트)은 만들지 않는다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from jobfit.ai.analyze import FILE_RESULT as FILE_ANALYSIS
from jobfit.ai.promptfile import sha1
from jobfit.clean.keywords import KeywordDict, implies_file
from jobfit.clean.keywords import word_pattern
from jobfit.experiences import card_types, file_sha, is_verified, load_cards, match_tags
from jobfit.paths import Project
from jobfit.state import Job, now

FILE_MATCH = "match.json"
WEIGHTS = {"must": 0.6, "nice": 0.25, "keywords": 0.15}
CARD_POINTS = {"must": 3, "nice": 2, "keywords": 1}
ROLE_WORDS = {"백엔드", "프런트엔드", "풀스택", "서버 개발", "데이터 엔지니어"}
MATCH_RULES = "2"  # 매칭 코드의 규칙을 바꾸면 올린다 — 예전 규칙으로 만든 match.json 을 다시 만들게 (2: implies.yaml)
TOP_IN_USE = 3  # 리포트에서 앞세우는 상위 카드 수 — 확인 전이면 먼저 확인하라고 알린다

# 앞에서부터 처음 맞는 유형. 지원동기를 먼저 봐야 "지원 동기와 입사 후 목표"가 문제해결로 가지 않는다
TYPE_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("지원동기", re.compile(r"지원\s*동기|지원(한|하는|하게\s*된)\s*(이유|계기)|관심을\s*갖|입사\s*후|포부|우리\s*회사|당사")),
    ("협업", re.compile(r"협업|팀워크|갈등|동료|의견이\s*(다른|달랐)|소통|커뮤니케이션|함께\s*(일|결정)")),
    ("문제해결", re.compile(r"문제|어려움|어려운|난관|실패|극복|해결|개선|예상과\s*다른|목표")),
    ("성장", re.compile(r"성장|배움|배운|학습|노력|자기\s*계발")),
    ("직무역량", re.compile(r"직무|역량|기술|경험|프로젝트|전문성|강점|역할")),
]


def classify_question(text: str) -> str:
    for name, pat in TYPE_RULES:
        if pat.search(text):
            return name
    return "기타"


def load_analysis(project: Project, job: Job) -> dict:
    path = project.job_dir(job.key) / FILE_ANALYSIS
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def resolve_questions(job: Job, analysis: dict) -> list[dict]:
    """문항 시트에 사용자가 입력한 문항이 있으면 그것만, 없으면 공고에서 추출한 문항."""
    if job.user_questions:
        source, raw = "직접입력", job.user_questions
    else:
        source, raw = "공고추출", [{"text": q.get("text", ""), "limit": q.get("limit"), "count_spaces": True} for q in analysis.get("questions_found", [])]
    out = []
    for i, q in enumerate(raw, start=1):
        text = str(q.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "id": f"q{len(out) + 1}",
            "text": text,
            "limit": q.get("limit"),
            "count_spaces": q.get("count_spaces", True),
            "source": source,
            "type": classify_question(text),
        })
    return out


def inputs_sha(project: Project, job: Job) -> dict[str, str]:
    """매칭 결과를 바꾸는 입력들의 해시. 하나라도 바뀌면 매칭부터 다시 한다."""
    analysis_path = project.job_dir(job.key) / FILE_ANALYSIS
    questions = resolve_questions(job, load_analysis(project, job))
    return {
        "analysis_sha": file_sha(analysis_path),
        "cards_sha": file_sha(project.experiences_file),
        "questions_sha": sha1(json.dumps([[q["text"], q["limit"], q["count_spaces"]] for q in questions], ensure_ascii=False)),
        "rules_sha": rules_sha(project),
    }


def rules_sha(project: Project) -> str:
    """매칭 규칙과 사전(대표 표기·다른 표기·포함 관계). 사전을 고치거나 버전을 올리면 매칭을 다시 한다."""
    data = project.data_dir
    files = [data / "keywords.txt", data / "synonyms.yaml", implies_file(data)]
    return sha1(MATCH_RULES + "".join(f.read_text(encoding="utf-8") for f in files if f.exists()))


def terms_of(text: str, kd: KeywordDict, extra_tags: list[str]) -> list[str]:
    """글에서 대표 표기 낱말. 사전에 없는 카드 태그(예: '배치')도 글에 그대로 있으면 낱말로 친다.
    직무 이름(백엔드 등)은 거의 모든 공고·카드에 있어 매칭을 부풀리므로 뺀다."""
    found = [t for t in kd.find(text) if t not in ROLE_WORDS]
    lowered = {t.lower() for t in found}
    for tag in extra_tags:
        # 사전 낱말은 kd.find가 경계를 지켜 이미 찾았다 — 다시 부분 문자열로 찾으면 Java가 JavaScript에 걸린다
        if len(tag) < 2 or tag in kd.variants or tag in ROLE_WORDS or tag.lower() in lowered:
            continue
        if word_pattern(tag).search(text):
            found.append(tag)
            lowered.add(tag.lower())
    return found


def build_match(analysis: dict, cards: list[dict], questions: list[dict], kd: KeywordDict) -> dict:
    tags_by_card = {str(c.get("id")): {t.lower() for t in match_tags(c, kd)} for c in cards if c.get("id")}
    all_tags = list(dict.fromkeys(t for c in cards for t in match_tags(c, kd)))

    requirements = []
    for kind in ("must", "nice"):
        for text in analysis.get(kind, []):
            terms = terms_of(text, kd, all_tags)
            low = {t.lower() for t in terms}
            covered = [cid for cid, tags in tags_by_card.items() if low & tags]
            requirements.append({"kind": kind, "text": text, "terms": terms, "cards": covered})
    keywords = [k for k in analysis.get("keywords", []) if k not in ROLE_WORDS]
    union = set().union(*tags_by_card.values()) if tags_by_card else set()
    keyword_hits = [k for k in keywords if k.lower() in union]

    breakdown = {}
    for kind in ("must", "nice"):
        items = [r for r in requirements if r["kind"] == kind]
        judged = [r for r in items if r["terms"]]
        breakdown[kind] = {"covered": sum(1 for r in judged if r["cards"]), "judged": len(judged), "total": len(items)}
    breakdown["keywords"] = {"covered": len(keyword_hits), "judged": len(keywords), "total": len(keywords)}
    score = _score(breakdown)

    ranked = []
    for card in cards:
        cid = str(card.get("id") or "")
        if not cid:
            continue
        hits = {
            "must": [r["text"] for r in requirements if r["kind"] == "must" and cid in r["cards"]],
            "nice": [r["text"] for r in requirements if r["kind"] == "nice" and cid in r["cards"]],
            "keywords": [k for k in keywords if k.lower() in tags_by_card.get(cid, set())],
        }
        ranked.append({
            "id": cid,
            "title": str(card.get("title") or ""),
            "score": sum(CARD_POINTS[k] * len(v) for k, v in hits.items()),
            "verified": is_verified(card),
            "usable_for": card_types(card),
            **{f"{k}_hits": v for k, v in hits.items()},
        })
    ranked.sort(key=lambda c: -c["score"])  # 같은 점수는 파일 순서

    assigned = assign(questions, ranked)
    return {
        "score": score,
        "breakdown": breakdown,
        "requirements": requirements,
        "keywords": keywords,
        "keyword_hits": keyword_hits,
        "cards": ranked,
        "questions": assigned,
    }


def assign(questions: list[dict], ranked: list[dict]) -> list[dict]:
    """주력은 두 번에 나눠 고른다. 먼저 유형이 맞는 카드로 모든 문항을 채우고, 남은 문항만 남은 카드로 채운다.
    한 번에 고르면 앞 문항(지원동기 등)이 유형도 안 맞는 최고 카드를 가져가 뒤 문항이 비게 된다."""
    typed = {q["id"]: [c for c in ranked if q["type"] in c["usable_for"]] for q in questions}
    primary: dict[str, dict] = {}
    used: set[str] = set()
    for q in questions:
        pick = next((c for c in typed[q["id"]] if c["id"] not in used), None)
        if pick:
            primary[q["id"]] = pick
            used.add(pick["id"])
    for q in questions:
        if q["id"] in primary:
            continue
        pick = next((c for c in ranked if c["id"] not in used), None)  # 이 공고에 잘 맞는 남은 카드
        if pick:
            primary[q["id"]] = pick
            used.add(pick["id"])
    out = []
    for q in questions:
        picks = []
        p = primary.get(q["id"])
        if p:
            picks.append({"id": p["id"], "role": "주력", "type_match": q["type"] in p["usable_for"]})
        second = next((c for c in typed[q["id"]] if not p or c["id"] != p["id"]), None)  # 보조는 다른 문항과 겹쳐도 된다
        if second:
            picks.append({"id": second["id"], "role": "보조", "type_match": True})
        out.append({**q, "cards": picks})
    return out


def run_match(project: Project, job: Job, kd: KeywordDict | None = None) -> dict:
    """match.json을 쓴다. 사용자가 배정을 고친 흔적이 있으면 그 배정을 지킨다."""
    kd = kd or KeywordDict.load(project.data_dir)
    analysis = load_analysis(project, job)
    cards, _ = load_cards(project.experiences_file)
    questions = resolve_questions(job, analysis)
    result = build_match(analysis, cards, questions, kd)
    result["auto_sha"] = _assign_sha(result["questions"])

    path = project.job_dir(job.key) / FILE_MATCH
    previous = _load(path)
    if previous and _assign_sha(previous.get("questions", [])) != previous.get("auto_sha"):
        if [q.get("text") for q in previous.get("questions", [])] == [q["text"] for q in result["questions"]]:
            known = {c["id"] for c in result["cards"]}
            for new_q, old_q in zip(result["questions"], previous["questions"]):
                kept = [c for c in old_q.get("cards", []) if c.get("id") in known]
                new_q["cards"] = kept
            result["edited"] = True
            result["auto_sha"] = previous.get("auto_sha")  # 다음에도 고친 것으로 알아본다
    result["made_at"] = now()
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def load_match(project: Project, job: Job) -> dict:
    return _load(project.job_dir(job.key) / FILE_MATCH)


def cards_in_use(match: dict) -> list[str]:
    """리포트·초안에 실제로 쓰이는 카드 id — 문항에 배정된 카드와 매칭 상위 카드(점수 0은 빼고)."""
    ids = [c["id"] for q in match.get("questions", []) for c in q.get("cards", [])]
    ids += [c["id"] for c in match.get("cards", [])[:TOP_IN_USE] if c.get("score", 0) > 0]
    return list(dict.fromkeys(ids))


def _score(breakdown: dict) -> int | None:
    parts = {k: b["covered"] / b["judged"] for k, b in breakdown.items() if b["judged"]}
    if not parts:
        return None
    total_weight = sum(WEIGHTS[k] for k in parts)
    return round(100 * sum(WEIGHTS[k] * v for k, v in parts.items()) / total_weight)


def _assign_sha(questions: list[dict]) -> str:
    return sha1(json.dumps([[q.get("id"), [c.get("id") for c in q.get("cards", [])]] for q in questions]))


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
