import json
import zipfile

import yaml
from openpyxl import Workbook

from jobfit import cli, pipeline
from jobfit.ai import exp_extract
from jobfit.ai.exp_extract import parse_cards
from jobfit.experiences import load_cards
from jobfit.paths import Project
from jobfit.state import State
from jobfit.textio import read_any
from test_analyze import GOOD, KEY, _ready_project

RESUME = """홍길동 · 010-1234-5678 · hong@example.com

주문 조회 API 개선 프로젝트 (2025-03 ~ 2025-05)
- N+1 조회를 fetch join으로 묶고 Redis 캐시를 붙였다
- p95 응답 시간 1.8초 → 0.4초
- 주문 1건당 쿼리 21회 → 1회

결제 알림 배치 개선 (2024-09 ~ 2024-12)
- 하루 3만 건 알림을 Spring Batch로 옮김
"""


def _pdf_bytes(text: str) -> bytes:
    """글자 층이 있는 한 쪽짜리 PDF (오프셋을 계산해 xref를 맞춘다)."""
    stream = f"BT /F1 11 Tf 20 120 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def test_read_any_formats(tmp_path):
    pptx = tmp_path / "deck.pptx"
    with zipfile.ZipFile(pptx, "w") as z:
        for n, text in ((10, "열번째"), (2, "두번째"), (1, "첫번째")):
            z.writestr(f"ppt/slides/slide{n}.xml", f'<p:sld xmlns:a="x"><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:sld>')
    assert read_any(pptx).text == "첫번째\n\n두번째\n\n열번째"  # 슬라이드 번호 순서, 슬라이드 사이는 빈 줄

    xlsx = tmp_path / "list.xlsx"
    wb = Workbook()
    wb.active.title = "프로젝트"
    wb.active.append(["이름", "기간"])
    wb.active.append(["주문 API", "2025-03"])
    wb.save(xlsx)
    assert read_any(xlsx).text == "# 프로젝트\n이름 | 기간\n주문 API | 2025-03"

    html = tmp_path / "page.html"
    html.write_text("<h1>포트폴리오</h1><ul><li>주문 API</li></ul><script>x()</script>", encoding="utf-8")
    assert "- 주문 API" in read_any(html).text and "x()" not in read_any(html).text

    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(_pdf_bytes("Order API p95 1.8s to 0.4s, queries 21 to 1 per order with Redis cache"))
    got = read_any(pdf)
    assert got.kind == "pdf" and "21 to 1" in got.text

    scanned = tmp_path / "scan.pdf"
    scanned.write_bytes(_pdf_bytes(""))
    assert read_any(scanned).kind == "scanned-pdf" and read_any(scanned).text is None

    (tmp_path / "a.png").write_bytes(b"\x89PNG")
    (tmp_path / "b.hwp").write_bytes(b"HWP")
    assert read_any(tmp_path / "a.png").kind == "image"
    assert read_any(tmp_path / "b.hwp").kind == "unsupported"


def test_parse_cards_variants():
    assert parse_cards("[]") == []
    assert parse_cards("네, 뽑았습니다.\n```yaml\n- id: exp-a\n  title: A\n```\n끝") == [{"id": "exp-a", "title": "A"}]
    assert parse_cards("- id: exp-a\n  title: A\n\n확인 부탁드립니다.") == [{"id": "exp-a", "title": "A"}]  # 코드 블록 없이 뒤에 설명
    for bad in ("카드가 없네요", "- id: exp-a\n  title: [깨진"):
        try:
            parse_cards(bad)
            raise AssertionError("ValueError 를 기대")
        except ValueError:
            pass


def _project(tmp_path) -> Project:
    assert cli.main(["--root", str(tmp_path), "init"]) == 0  # me.example 이 없으니 빈 me/
    project = Project(tmp_path)
    project.experiences_file.write_text(
        "- id: exp-order\n  title: 주문 조회 API 개선 프로젝트\n  tags: [Java]\n  summary: s\n  usable_for: [직무역량]\n  facts: []\n  verified: true\n",
        encoding="utf-8",
    )
    return project


def test_extract_flow(tmp_path, capsys):
    project = _project(tmp_path)
    assert project.sources_dir.is_dir()
    (project.sources_dir / "resume.txt").write_text(RESUME, encoding="utf-8")
    (project.sources_dir / "photo.png").write_bytes(b"\x89PNG")
    (project.sources_dir / "old.hwp").write_bytes(b"HWP")
    capsys.readouterr()
    assert cli.main(["--root", str(tmp_path), "exp", "extract"]) == 0
    out = capsys.readouterr().out
    assert "건너뜀: sources/old.hwp" in out and "조각 2개" in out
    items = exp_extract.pending_items(project)
    assert [i.source for i in items] == ["sources/photo.png", "sources/resume.txt"]
    photo_prompt = items[0].prompt.read_text(encoding="utf-8")
    assert "원본 파일 `sources/photo.png` 을 직접 열어" in photo_prompt
    resume_prompt = items[1].prompt.read_text(encoding="utf-8")
    assert resume_prompt.startswith("[jobfit · 경험 카드 뽑기]") and "p95 응답 시간 1.8초 → 0.4초" in resume_prompt
    assert "010-1234-5678" not in resume_prompt and "hong@example.com" not in resume_prompt  # 연락처는 가림
    assert "- exp-order: 주문 조회 API 개선 프로젝트" in resume_prompt and "{{" not in resume_prompt

    # next 와 기본 prep/accept 는 공고보다 경험 카드를 먼저 가리킨다
    cli.main(["--root", str(tmp_path), "next"])
    assert "경험 카드 뽑기: sources/photo.png" in capsys.readouterr().out

    # 1) 사진 — 원문 대조 불가: facts 는 남기고 경고
    photo_answer = tmp_path / "a1.txt"
    photo_answer.write_text("- id: exp-hackathon\n  title: 사내 해커톤 수상\n  tags: [협업]\n  summary: 해커톤\n  facts: [\"3팀 중 1위\"]\n  usable_for: [협업]\n", encoding="utf-8")
    assert cli.main(["--root", str(tmp_path), "accept", "--file", str(photo_answer)]) == 0
    out = capsys.readouterr().out
    assert "카드 1개를" in out and "원문과 대조하지 못했습니다" in out

    # 2) 이력서 — 원문에 없는 숫자는 빼고, 이미 있는 경험은 건너뛰고, 겹치는 id 는 바꾼다
    answer = tmp_path / "a2.txt"
    answer.write_text(
        """여기 있습니다.
```yaml
- id: exp-order
  title: 주문 조회 API 개선 프로젝트
  facts: ["p95 응답 시간 1.8초 → 0.4초"]
- id: exp-hackathon
  title: 결제 알림 배치 개선
  period: 2024-09 ~ 2024-12
  tags: [스프링 배치, 배치]
  summary: 알림 배치를 Spring Batch로 옮김
  star: {situation: 알림이 늦었다, task: "", action: Spring Batch로 옮김, result: ""}
  facts: ["하루 3만 건 알림", "처리 시간 70% 단축"]
  usable_for: [문제해결, 리더십]
```""",
        encoding="utf-8",
    )
    assert cli.main(["--root", str(tmp_path), "accept", "exp-extract", "--file", str(answer)]) == 0
    out = capsys.readouterr().out
    assert "이미 있는 카드와 같은 경험이라 건너뜀: 주문 조회 API 개선 프로젝트" in out
    assert "처리 시간 70% 단축" in out and "자료 원문에 없는 숫자" in out
    assert "'리더십'" in out and "모두 카드로 만들었습니다" in out

    cards, problems = load_cards(project.experiences_file)
    assert problems == []
    by_id = {c["id"]: c for c in cards}
    assert set(by_id) == {"exp-order", "exp-hackathon", "exp-hackathon-2"}
    batch = by_id["exp-hackathon-2"]
    assert batch["facts"] == ["하루 3만 건 알림"] and batch["tags"] == ["Spring Batch", "배치"]
    assert batch["usable_for"] == ["문제해결"] and batch["verified"] is False and batch["source"] == "sources/resume.txt"
    assert batch["star"] == {"situation": "알림이 늦었다", "task": "", "action": "Spring Batch로 옮김", "result": ""}
    text = project.experiences_file.read_text(encoding="utf-8")
    assert "- id: exp-order" in text.splitlines()[0]  # 원래 내용은 그대로 맨 앞에
    assert "  tags: [Spring Batch, 배치]\n" in text and "  usable_for: [문제해결]\n" in text  # me.example 과 같은 꼴
    assert "  facts:\n    - 하루 3만 건 알림\n" in text
    assert "sources/resume.txt 에서 AI가 뽑은 카드" in text
    assert len(list(project.backups_dir.glob("experiences_*.yaml"))) == 2

    # 다시 돌려도 같은 자료는 하지 않는다. 자료가 바뀌면 다시
    assert cli.main(["--root", str(tmp_path), "exp", "extract"]) == 0
    assert exp_extract.pending_items(project) == []
    (project.sources_dir / "resume.txt").write_text(RESUME + "\n추가 활동", encoding="utf-8")
    cli.main(["--root", str(tmp_path), "exp", "extract"])
    assert "바뀐 자료: sources/resume.txt" in capsys.readouterr().out
    assert [i.source for i in exp_extract.pending_items(project)] == ["sources/resume.txt"]


def test_long_source_is_split_and_run_prepares_prompts(tmp_path, capsys):
    project = _project(tmp_path)
    para = "프로젝트 경험 문단입니다. " * 40
    (project.sources_dir / "long.md").write_text("\n\n".join([para] * 12), encoding="utf-8")
    capsys.readouterr()
    cli.main(["--root", str(tmp_path), "run"])  # run 도 자료 프롬프트를 만들어 둔다
    assert "경험 카드: sources/ 자료 조각" in capsys.readouterr().out
    items = exp_extract.pending_items(project)
    assert len(items) >= 2 and all(i.parts == len(items) for i in items)
    for item in items:
        assert len(item.prompt.read_text(encoding="utf-8")) <= exp_extract.limit()
    # 조각마다 받아서 끝낸다
    empty = tmp_path / "e.txt"
    empty.write_text("[]", encoding="utf-8")
    for _ in items:
        assert cli.main(["--root", str(tmp_path), "accept", "--file", str(empty)]) == 0
    assert exp_extract.pending_items(project) == []


def test_bad_answer_keeps_item_pending(tmp_path, capsys):
    project = _project(tmp_path)
    (project.sources_dir / "r.txt").write_text(RESUME, encoding="utf-8")
    cli.main(["--root", str(tmp_path), "exp", "extract"])
    bad = tmp_path / "bad.txt"
    bad.write_text("죄송하지만 카드를 만들 수 없습니다", encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["--root", str(tmp_path), "accept", "exp-extract", "--file", str(bad)]) == 1
    assert "YAML 목록" in capsys.readouterr().out
    assert len(exp_extract.pending_items(project)) == 1
    assert yaml.safe_load(project.experiences_file.read_text(encoding="utf-8"))[0]["id"] == "exp-order"  # 카드 파일은 그대로


def test_append_to_empty_list_file(tmp_path):
    project = _project(tmp_path)
    project.experiences_file.write_text("# 내 카드\n[]\n", encoding="utf-8")
    card = {"id": "exp-a", "title": "A: 콜론", "period": "", "org": "", "role": "", "tags": ["C++"], "summary": "'따옴표'",
            "star": {"situation": "", "task": "", "action": "", "result": ""}, "facts": [], "usable_for": ["기타"],
            "verified": False, "source": "sources/a.txt"}
    exp_extract.append_cards(project, [card], "sources/a.txt")
    cards, problems = load_cards(project.experiences_file)
    assert problems == [] and cards == [card]  # 특수문자도 그대로 되읽힌다
    assert project.experiences_file.read_text(encoding="utf-8").startswith("# 내 카드\n")


def test_next_points_to_sources_without_jobs(tmp_path):
    project = _project(tmp_path)
    assert State.load(project.state_file).jobs == {}  # 공고 없이도 next 가 자료를 안내한다
    (project.sources_dir / "r.txt").write_text(RESUME, encoding="utf-8")
    action = pipeline.next_action(project, State.load(project.state_file))
    assert action.command == "jobfit exp extract"


def test_changed_source_drops_stale_answer(tmp_path, capsys):
    project = _project(tmp_path)
    src = project.sources_dir / "r.txt"
    src.write_text(RESUME, encoding="utf-8")
    cli.main(["--root", str(tmp_path), "exp", "extract"])
    item = exp_extract.pending_items(project)[0]
    assert project.rel(item.prompt).startswith("output/_exp/_ai/")  # 에이전트 쓰기 허용 규칙(output/**/_ai/*.response.*)에 맞는 자리
    item.response.write_text("- id: exp-old\n  title: 옛 답\n", encoding="utf-8")
    src.write_text(RESUME + "\n새 활동", encoding="utf-8")
    cli.main(["--root", str(tmp_path), "exp", "extract"])
    assert not item.response.exists()  # 바뀐 자료에 옛 답을 받아들이지 않는다
    capsys.readouterr()
    assert cli.main(["--root", str(tmp_path), "accept", "exp-extract"]) == 1
    assert "답 파일이 없습니다" in capsys.readouterr().out


def test_agent_accept_takes_the_piece_with_an_answer(tmp_path, capsys):
    project = _project(tmp_path)
    (project.sources_dir / "a.txt").write_text("동아리 회장 활동", encoding="utf-8")
    (project.sources_dir / "b.txt").write_text("교내 해커톤 1위", encoding="utf-8")
    cli.main(["--root", str(tmp_path), "exp", "extract"])
    a, b = exp_extract.pending_items(project)
    b.response.write_text('- id: exp-hack\n  title: 교내 해커톤\n  facts: ["1위"]\n', encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["--root", str(tmp_path), "accept", "exp-extract"]) == 0
    assert "sources/b.txt: 경험 카드 1개" in capsys.readouterr().out
    assert [i.source for i in exp_extract.pending_items(project)] == ["sources/a.txt"]


def test_paste_goes_to_the_prompt_shown_last(tmp_path, capsys):
    project = _ready_project(tmp_path)  # 공고 하나가 분석(AI) 단계에서 기다린다
    (project.sources_dir / "r.txt").write_text(RESUME, encoding="utf-8")
    cli.main(["--root", str(tmp_path), "exp", "extract"])
    assert cli.main(["--root", str(tmp_path), "prep", "--job", KEY]) == 0  # 공고 프롬프트를 마지막으로 봄
    analyze_answer = tmp_path / "an.txt"
    analyze_answer.write_text(json.dumps(GOOD, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["--root", str(tmp_path), "accept", "--file", str(analyze_answer)]) == 0
    assert "분석을 받아들였습니다" in capsys.readouterr().out
    assert len(exp_extract.pending_items(project)) == 1  # 경험 카드는 그대로 기다림
    # 이제 기다리는 공고가 없으니 단계 없이 받으면 경험 카드로
    card = tmp_path / "c.txt"
    card.write_text('- id: exp-batch\n  title: 결제 알림 배치 개선\n  facts: ["하루 3만 건 알림"]\n', encoding="utf-8")
    assert cli.main(["--root", str(tmp_path), "accept", "--file", str(card)]) == 0
    assert "경험 카드 1개" in capsys.readouterr().out
