"""Minimal Fountain parser: splits a script into scenes and keeps line numbers for provenance."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SCENE_HEADING = re.compile(r"^(INT\.|EXT\.|EST\.|INT/EXT\.|I/E\.)\s", re.IGNORECASE)


@dataclass
class Scene:
    num: int
    heading: str
    line_start: int
    line_end: int
    lines: list[tuple[int, str]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(text for _, text in self.lines)


@dataclass
class Script:
    title: str
    scenes: list[Scene]
    lines: list[str]


def parse(source: str) -> Script:
    lines = source.splitlines()
    title = ""
    scenes: list[Scene] = []
    for lineno, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not scenes and line.lower().startswith("title:"):
            title = line.split(":", 1)[1].strip()
            continue
        if SCENE_HEADING.match(line):
            if scenes:
                scenes[-1].line_end = lineno - 1
            scenes.append(Scene(num=len(scenes) + 1, heading=line, line_start=lineno, line_end=lineno))
            continue
        if scenes and line:
            scenes[-1].lines.append((lineno, line))
    if scenes:
        scenes[-1].line_end = len(lines)
    return Script(title=title, scenes=scenes, lines=lines)


def parse_file(path: Path) -> Script:
    return parse(path.read_text())


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def find_line(script: Script, quote: str, scene: Scene | None = None) -> int | None:
    """Line number where `quote` appears, searching `scene` first and then the whole script."""
    needle = _normalize(quote)
    if not needle:
        return None
    if scene is not None:
        for lineno, text in scene.lines:
            if needle in _normalize(text):
                return lineno
    for lineno, text in enumerate(script.lines, start=1):
        if needle in _normalize(text):
            return lineno
    return None
