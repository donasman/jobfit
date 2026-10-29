"""키워드 사전 매칭 — data/keywords.txt, data/synonyms.yaml 기준으로 공고 글자에서 기술·직무 낱말을 찾는다.

canonical(대표 표기) 하나에 여러 표기(동의어)가 붙는다. 결과는 대표 표기로 정규화한다.
영문 토큰은 단어 경계로, 한글 토큰은 부분 문자열로 찾는다. 긴 표기를 먼저 찾고 그 자리를 가린다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class KeywordDict:
    canonical: list[str] = field(default_factory=list)
    variants: dict[str, list[str]] = field(default_factory=dict)  # canonical -> 표기들(대표 포함)
    _ordered: list[tuple[str, str]] | None = field(default=None, repr=False, compare=False)  # (표기, 대표) 긴 것부터

    @classmethod
    def load(cls, data_dir: Path) -> "KeywordDict":
        """같은 폴더·같은 파일 수정 시각이면 한 번 읽은 사전을 다시 쓴다."""
        files = [data_dir / "keywords.txt", data_dir / "synonyms.yaml"]
        stamp = (str(data_dir.resolve()), *(f.stat().st_mtime_ns if f.exists() else 0 for f in files))
        if stamp not in _load_cache:
            _load_cache[stamp] = cls._read(data_dir)
        return _load_cache[stamp]

    @classmethod
    def _read(cls, data_dir: Path) -> "KeywordDict":
        kd = cls()
        kw_file = data_dir / "keywords.txt"
        if kw_file.exists():
            for line in kw_file.read_text(encoding="utf-8").splitlines():
                s = line.split("#", 1)[0].strip()
                if s and s not in kd.canonical:
                    kd.canonical.append(s)
        syn_file = data_dir / "synonyms.yaml"
        synonyms: dict[str, list[str]] = {}
        if syn_file.exists():
            loaded = yaml.safe_load(syn_file.read_text(encoding="utf-8")) or {}
            for canon, alts in loaded.items():
                alts = alts if isinstance(alts, list) else [alts]
                synonyms[str(canon)] = [str(a) for a in alts if a]
                if str(canon) not in kd.canonical:
                    kd.canonical.append(str(canon))
        for c in kd.canonical:
            kd.variants[c] = [c, *synonyms.get(c, [])]
        return kd

    def normalize(self, token: str) -> str:
        """어떤 표기든 대표 표기로. 사전에 없으면 그대로."""
        t = token.strip()
        low = t.lower()
        for canon, alts in self.variants.items():
            if any(low == a.lower() for a in alts):
                return canon
        return t

    def scan(self, text: str) -> list[tuple[str, str]]:
        """글에서 찾은 (대표 표기, 실제 표기) 목록. 긴 표기부터 찾고 그 자리를 가려서,
        '스프링부트' 안의 '스프링'이나 'Spring Boot' 안의 'Spring'을 따로 세지 않는다."""
        if self._ordered is None:
            pairs = [(alt, canon) for canon, alts in self.variants.items() for alt in alts]
            self._ordered = sorted(pairs, key=lambda p: (-len(p[0]), p[0].lower()))
        found: list[tuple[str, str]] = []
        s = text
        for alt, canon in self._ordered:
            matches = list(_pattern(alt).finditer(s))
            if not matches:
                continue
            for m in reversed(matches):
                found.append((canon, m.group(0)))
                s = s[: m.start()] + " " * (m.end() - m.start()) + s[m.end() :]
        return found

    def forms(self, text: str, canonical: str) -> dict[str, int]:
        """그 대표 표기의 여러 표기 중 글에 실제로 쓰인 것과 횟수. 이력서 표현을 공고 표기로 맞출 때 쓴다."""
        out: dict[str, int] = {}
        lowered = {alt.lower(): alt for alt in self.variants.get(canonical, [canonical])}
        for canon, surface in self.scan(text):
            if canon == canonical:
                key = lowered.get(surface.lower(), surface)
                out[key] = out.get(key, 0) + 1
        return out

    def find(self, text: str) -> dict[str, int]:
        """대표 표기 -> 등장 횟수. 0회는 빼고 많이 나온 순."""
        counts: dict[str, int] = {}
        for canon, _ in self.scan(text):
            counts[canon] = counts.get(canon, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


_pattern_cache: dict[str, re.Pattern[str]] = {}
_load_cache: dict[tuple, KeywordDict] = {}


def word_pattern(token: str) -> re.Pattern[str]:
    """사전 밖의 낱말(카드 태그 등)을 사전과 같은 경계 규칙으로 찾을 때."""
    return _pattern(token)


def _pattern(token: str) -> re.Pattern[str]:
    if token in _pattern_cache:
        return _pattern_cache[token]
    esc = re.escape(token)
    if re.search(r"[A-Za-z0-9]$", token) and re.match(r"^[A-Za-z0-9.+#/_ -]+$", token):
        # 영문·숫자 토큰: 앞이 영문·숫자·#·+·. 이 아니고(JS != Node.js), 뒤가 영문·숫자·#·+ 가 아니어야 한다
        # (Java != JavaScript, C != C# != C++). 뒤의 '.'은 문장 끝일 수 있어 막지 않는다
        # 한 글자 토큰(C, R)은 'C동'·'A급'처럼 한글이 바로 붙은 것도 뺀다
        tail = r"(?![A-Za-z0-9#+가-힣])" if len(token) == 1 else r"(?![A-Za-z0-9#+])"
        pat = re.compile(rf"(?<![A-Za-z0-9#+.]){esc}{tail}", re.I)
    else:
        pat = re.compile(esc, re.I)
    _pattern_cache[token] = pat
    return pat
