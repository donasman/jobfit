"""프로젝트 폴더 안의 경로 규칙.

사용자 파일(jobs.xlsx, me/)과 스크립트 파일(dashboard.xlsx, output/, .jobfit/)을
한 곳에서 정의한다. 경로를 문자열로 조립하는 코드는 여기 밖에 두지 않는다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

JOBS_XLSX = "jobs.xlsx"
DASHBOARD_XLSX = "dashboard.xlsx"

_UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]\s]+')


def package_data_dir() -> Path:
    """패키지에 동봉된 공용 사전(keywords.txt, synonyms.yaml) 폴더."""
    return Path(__file__).resolve().parent / "data"


def package_prompts_dir() -> Path:
    """패키지에 동봉된 프롬프트 템플릿 폴더."""
    return Path(__file__).resolve().parent / "prompts"


def key_to_dirname(key: str) -> str:
    """공고키를 Windows에서도 안전한 폴더 이름으로 바꾼다.

    saramin:55145352 -> saramin-55145352
    url:host/path?x  -> url-host-<해시8>   (경로가 길어서 해시로 줄인다)
    local:abcd1234   -> local-abcd1234
    """
    kind, _, rest = key.partition(":")
    if kind == "url":
        host = rest.split("/", 1)[0]
        digest = hashlib.sha1(rest.encode("utf-8")).hexdigest()[:8]
        return f"url-{_UNSAFE.sub('-', host)}-{digest}"
    return f"{kind}-{_UNSAFE.sub('-', rest)}".rstrip("-.")


@dataclass(frozen=True)
class Project:
    root: Path

    @classmethod
    def at(cls, root: str | Path | None) -> "Project":
        return cls(Path(root).resolve() if root else Path.cwd())

    # 사용자 파일
    @property
    def jobs_xlsx(self) -> Path:
        return self.root / JOBS_XLSX

    @property
    def me_dir(self) -> Path:
        return self.root / "me"

    @property
    def me_example_dir(self) -> Path:
        return self.root / "me.example"

    # 스크립트 파일
    @property
    def dashboard_xlsx(self) -> Path:
        return self.root / DASHBOARD_XLSX

    @property
    def output_dir(self) -> Path:
        return self.root / "output"

    @property
    def state_dir(self) -> Path:
        return self.root / ".jobfit"

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def usage_file(self) -> Path:
        return self.state_dir / "usage.jsonl"

    @property
    def backups_dir(self) -> Path:
        """`jobfit add`가 jobs.xlsx에 쓰기 직전 백업."""
        return self.state_dir / "backups"

    @property
    def cache_dir(self) -> Path:
        """프롬프트 해시별 AI 결과. 입력이 같으면 AI 단계를 건너뛴다."""
        return self.state_dir / "cache"

    @property
    def experiences_file(self) -> Path:
        return self.me_dir / "experiences.yaml"

    @property
    def resume_file(self) -> Path:
        return self.me_dir / "resume.yaml"

    @property
    def sources_dir(self) -> Path:
        """경험 카드를 뽑을 내 자료(이력서·포트폴리오·프로젝트 문서). me/ 밖이라 에이전트가 읽을 수 있다."""
        return self.root / "sources"

    @property
    def exp_dir(self) -> Path:
        """경험 카드 뽑기 프롬프트·답(output/_exp/_ai). 공고별 _ai/ 와 같은 꼴이라 에이전트 쓰기 허용 규칙이 그대로 맞는다."""
        return self.output_dir / "_exp" / "_ai"

    @property
    def prompts_dir(self) -> Path:
        """프로젝트 루트의 prompts/가 있으면 패키지 템플릿보다 우선한다."""
        local = self.root / "prompts"
        return local if local.is_dir() else package_prompts_dir()

    def ai_dir(self, key: str) -> Path:
        return self.job_dir(key) / "_ai"

    def rel(self, path: Path) -> str:
        """화면·프롬프트에 보일 프로젝트 기준 경로."""
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    @property
    def data_dir(self) -> Path:
        """프로젝트 루트의 data/가 있으면 패키지 사전보다 우선한다."""
        local = self.root / "data"
        return local if local.is_dir() else package_data_dir()

    def job_dir(self, key: str) -> Path:
        return self.output_dir / key_to_dirname(key)

    def is_initialized(self) -> bool:
        return self.jobs_xlsx.exists() and self.state_dir.is_dir()
