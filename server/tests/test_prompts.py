from pathlib import Path

import pytest

from throughline.graph import load_series
from throughline.prompts import compile_episode

SAMPLES = Path(__file__).parents[2] / "samples" / "neon_courier"


@pytest.fixture(scope="module")
def prompts():
    try:
        g = load_series(SAMPLES, graph_name="test_prompts", wipe=True)
    except Exception as exc:  # FalkorDB not running
        pytest.skip(f"FalkorDB unavailable: {exc}")
    yield {p.shot: p for p in compile_episode(g, 4)}
    g.delete()


def test_outfit_comes_from_the_wardrobe_timeline(prompts):
    street = prompts["E4S1T1"]
    assert "red scarf" in street.prompt
    assert any("since E2S1" in s.path for s in street.sources)
    assert any("mira_jacket_scarf" in c for c in street.corrections)


def test_destroyed_prop_is_left_out(prompts):
    highway = prompts["E4S2T1"]
    assert "Props:" not in highway.prompt
    assert any("motorbike was destroyed in E2S2" in c for c in highway.corrections)


def test_previous_shot_continuity_stays_inside_the_scene(prompts):
    assert "Continues from the previous shot" not in prompts["E4S2T1"].prompt
