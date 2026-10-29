import zipfile

from jobfit import cli
from jobfit.paths import Project
from jobfit.style import compute_stats, forbidden_expressions, read_sample, stats_lines

SAMPLE = "저는 문제를 먼저 정의합니다. 그래서 로그부터 봤습니다.\n\n그 결과 원인을 찾았습니다. 이후 팀에 공유했습니다."


def test_compute_stats():
    s = compute_stats([SAMPLE])
    assert s["samples"] == 1 and s["sentences"] == 4 and s["paragraphs"] == 2
    assert s["special"]["·"] == 0 and s["commas_per_sentence"] == 0
    assert list(s["endings"])[0] in ("했습니다", "습니다(기타)", "합니다")
    assert "그래서" in s["connectives"] and "그 결과" in s["connectives"]
    lines = stats_lines(s)
    assert lines[0].startswith("- 문장 길이") and any("쓰지 않는 기호" in x for x in lines)


def test_forbidden_expressions_parsing():
    rules = "# 문체 규칙\n## 쓰는 방식\n- 결론부터\n## 피하는 표현\n- \"열심히 노력했습니다\" — 추상적\n- '최선을 다해' — 다짐형\n- 흥미를 느꼈다 (감상)\n## 대표 문단\n문단"
    assert forbidden_expressions(rules) == ["열심히 노력했습니다", "최선을 다해", "흥미를 느꼈다"]
    assert forbidden_expressions("# 문체 규칙\n## 쓰는 방식\n- x") == []


def test_read_docx_and_hwpx(tmp_path):
    docx = tmp_path / "a.docx"
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="x"><w:body><w:p><w:r><w:t>첫 문단 &amp; 끝</w:t></w:r></w:p><w:p><w:r><w:t xml:space="preserve">둘째 </w:t></w:r><w:r><w:t>문단</w:t></w:r></w:p></w:body></w:document>')
    assert read_sample(docx) == "첫 문단 & 끝\n둘째 문단"
    hwpx = tmp_path / "b.hwpx"
    with zipfile.ZipFile(hwpx, "w") as z:
        z.writestr("Contents/section0.xml", '<hs:sec xmlns:hp="y"><hp:p><hp:run><hp:t>한글 문단</hp:t></hp:run></hp:p></hs:sec>')
    assert read_sample(hwpx) == "한글 문단"
    bad = tmp_path / "c.docx"
    bad.write_bytes(b"not a zip")
    assert read_sample(bad) == ""


def test_style_stats_and_rules_commands(tmp_path, capsys):
    assert cli.main(["--root", str(tmp_path), "init"]) == 0
    project = Project(tmp_path)
    assert cli.main(["--root", str(tmp_path), "style", "stats"]) == 1  # 샘플 없음
    (project.me_dir / "samples").mkdir()
    (project.me_dir / "samples" / "s1.txt").write_text(SAMPLE, encoding="utf-8")
    assert cli.main(["--root", str(tmp_path), "style", "stats"]) == 0
    assert (project.me_dir / "style" / "style_stats.json").exists()
    assert "5개 미만" in capsys.readouterr().out

    assert cli.main(["--root", str(tmp_path), "prep", "style-rules"]) == 0
    prompt = (project.output_dir / "_style" / "style-rules.prompt.md").read_text(encoding="utf-8")
    assert prompt.startswith("[jobfit · 문체 규칙]") and "저는 문제를 먼저 정의합니다" in prompt and "{{" not in prompt
    answer = tmp_path / "rules.txt"
    answer.write_text("네 정리했어요.\n```markdown\n# 문체 규칙\n## 쓰는 방식\n- 결론부터\n## 피하는 표현\n- \"열심히\" — 추상적\n## 대표 문단\n문단입니다.\n```", encoding="utf-8")
    assert cli.main(["--root", str(tmp_path), "accept", "style-rules", "--file", str(answer)]) == 0
    rules = (project.me_dir / "style" / "style_rules.md").read_text(encoding="utf-8")
    assert rules.startswith("<!-- AI가 쓴 초안입니다") and "# 문체 규칙" in rules and "네 정리했어요" not in rules
    assert cli.main(["--root", str(tmp_path), "accept", "style-rules", "--file", str(answer)]) == 0
    assert (project.me_dir / "style" / "style_rules.new.md").exists()  # 확정본은 덮어쓰지 않는다
    bad = tmp_path / "bad.txt"
    bad.write_text("규칙은 이렇습니다", encoding="utf-8")
    assert cli.main(["--root", str(tmp_path), "accept", "style-rules", "--file", str(bad)]) == 1
