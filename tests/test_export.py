import csv

from jobfit import cli
from jobfit.paths import Project


def test_export_notion_csv(tmp_path, capsys):
    assert cli.main(["--root", str(tmp_path), "init"]) == 0
    assert cli.main(["--root", str(tmp_path), "add", "https://www.wanted.co.kr/wd/1", "--company", "원티드사", "--title", "서버", "--deadline", "2026-12-01"]) == 0
    assert cli.main(["--root", str(tmp_path), "add", "--company", "수기회사", "--title", "백엔드"]) == 0
    capsys.readouterr()
    assert cli.main(["--root", str(tmp_path), "export", "notion"]) == 0
    out = capsys.readouterr().out
    assert "output/notion-jobs.csv (2건)" in out
    path = Project(tmp_path).output_dir / "notion-jobs.csv"
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")  # 엑셀에서도 한글이 깨지지 않게
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    assert [r["회사명"] for r in rows] == ["원티드사", "수기회사"]  # 마감 있는 것 먼저
    assert rows[0]["공고키"] == "wanted:1" and rows[0]["결정"] == "미정" and rows[0]["마감일"] == "2026-12-01"
    assert cli.main(["--root", str(tmp_path), "export", "notion", "--api"]) == 2
