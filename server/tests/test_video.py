from pathlib import Path

import pytest

from throughline import video
from throughline.graph import load_series

SAMPLES = Path(__file__).parents[2] / "samples" / "neon_courier"


@pytest.fixture(scope="module")
def graph():
    try:
        g = load_series(SAMPLES, graph_name="test_video", wipe=True)
    except Exception as exc:  # FalkorDB not running
        pytest.skip(f"FalkorDB unavailable: {exc}")
    yield g
    g.delete()


def test_references_follow_the_wardrobe_timeline(graph):
    # Mira takes Juno's scarf in E2S1, so episode 1 must use her plain-jacket reference.
    assert ("MIRA", "Mira", "mira_jacket") in video.shot_cast(graph, "E1S1T1")
    assert ("MIRA", "Mira", "mira_jacket_scarf") in video.shot_cast(graph, "E2S2T1")
    # E4S1's script says "black jacket", but the timeline still says scarf.
    assert ("MIRA", "Mira", "mira_jacket_scarf") in video.shot_cast(graph, "E4S1T1")


def test_estimate_counts_only_missing_clips_and_references(graph, tmp_path):
    (tmp_path / "clips").mkdir()
    (tmp_path / "clips" / "E2S1T1.mp4").write_bytes(b"done")
    (tmp_path / "characters").mkdir()
    (tmp_path / "characters" / "MIRA__mira_jacket_scarf.png").write_bytes(b"ref")
    estimate = video.estimate_cost(graph, tmp_path, 2, seconds=5)
    assert estimate["shots_to_render"] == ["E2S2T1", "E2S2T2", "E2S3T1"]
    assert estimate["new_reference_images"] == 1  # Kai's coat; Mira's scarf look exists
    assert estimate["usd"] == round(3 * (video.IMAGE_COST + 5 * 0.084) + video.IMAGE_COST, 2)


def test_render_episode_skips_done_shots_and_keeps_going_after_a_failure(graph, tmp_path, monkeypatch):
    (tmp_path / "clips").mkdir()
    (tmp_path / "clips" / "E2S1T1.mp4").write_bytes(b"done")
    calls = []

    def fake_generate(g, render_dir, shot, seconds, provider):
        calls.append(shot)
        if shot == "E2S2T1" and calls.count(shot) == 1:  # transient: fails once, then works
            raise video.RenderError("provider timeout")
        (render_dir / "clips" / f"{shot}.mp4").write_bytes(b"clip")

    monkeypatch.setattr(video, "generate_shot", fake_generate)
    monkeypatch.setattr(video, "assemble_episode", lambda *a, **k: pytest.fail("must not assemble with a failed shot"))
    result = video.render_episode(graph, tmp_path, 2)
    assert calls == ["E2S2T1", "E2S2T2", "E2S3T1"]
    assert result["already_rendered"] == ["E2S1T1"]
    assert result["failed"] == {"E2S2T1": "provider timeout"}

    monkeypatch.setattr(video, "assemble_episode", lambda g, d, ep: d / "episodes" / f"ep{ep}.mp4")
    retry = video.render_episode(graph, tmp_path, 2)
    assert retry["rendered"] == ["E2S2T1"] and retry["episode_video"].endswith("ep2.mp4")
