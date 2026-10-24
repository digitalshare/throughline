"""Applies a script fix, re-extracts only the edited scene and reports which findings it closed."""

from __future__ import annotations

import json
from pathlib import Path

from . import checks, fountain
from .extract import extract_scene
from .graph import connect, load_series
from .models import EpisodeExtract


def fix_line(series_dir: Path, graph_name: str, episode: int, line: int, new_text: str) -> dict:
    before = {f.id for f in checks.run_all(connect(graph_name))}
    script_path = series_dir / "scripts" / f"ep{episode}.fountain"
    lines = script_path.read_text().splitlines()
    if not 1 <= line <= len(lines):
        raise ValueError(f"ep{episode} has {len(lines)} lines, not {line}")
    old = lines[line - 1]
    if fountain.SCENE_HEADING.match(old.strip()) or fountain.SCENE_HEADING.match(new_text.strip()):
        raise ValueError("scene headings can't be edited with a line fix")
    lines[line - 1] = new_text
    script_path.write_text("\n".join(lines) + "\n")

    script = fountain.parse_file(script_path)
    scene = next(s for s in script.scenes if s.line_start <= line <= s.line_end)
    cache = series_dir / "extracted" / f"ep{episode}.json"
    extracted = EpisodeExtract.model_validate_json(cache.read_text())
    bible = json.loads((series_dir / "bible.json").read_text())
    known = dict(connect(graph_name).query("MATCH (f:Fact) RETURN f.id, f.text").result_set)
    extracted.scenes[scene.num - 1] = extract_scene(script, episode, scene.num, extracted.scenes[scene.num - 1], bible, known)
    cache.write_text(extracted.model_dump_json(indent=2) + "\n")

    g = load_series(series_dir, graph_name)
    after = checks.run_all(g)
    checks.persist(g, after)
    return {
        "changed": {"episode": episode, "scene": f"E{episode}S{scene.num}", "line": line, "old": old, "new": new_text},
        "resolved": sorted(before - {f.id for f in after}),
        "new": sorted({f.id for f in after} - before),
        "open": len(after),
    }
