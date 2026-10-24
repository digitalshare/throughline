import json
from pathlib import Path

import pytest

from throughline import audience, checks
from throughline.audience import CommentLink
from throughline.graph import load_series

SAMPLES = Path(__file__).parents[2] / "samples" / "neon_courier"

TIKTOK_POST = {
    "url": "https://www.tiktok.com/@neoncourier/video/1", "description": "Ep 4", "play_count": 1200, "digg_count": 90,
    "comment_count": 2, "share_count": 4, "hashtags": ["#AISeries", "cyberpunk"], "profile_username": "neoncourier",
}
COMMENTS = [
    {"comment_id": "c1", "comment_text": "wait how does Juno know her name??", "num_likes": 40},
    {"comment_id": "c2", "comment_text": "Mira's scarf is gone lol", "num_likes": 12},
    {"comment_id": "c3", "error": "comment unavailable"},
]


@pytest.fixture(scope="module")
def graph():
    try:
        g = load_series(SAMPLES, graph_name="test_audience", wipe=True)
    except Exception as exc:  # FalkorDB not running
        pytest.skip(f"FalkorDB unavailable: {exc}")
    checks.persist(g, checks.run_all(g))
    yield g
    g.delete()


def test_normalize_skips_errors_and_cleans_hashtags():
    post = audience.normalize_post(TIKTOK_POST)
    assert (post["platform"], post["views"], post["likes"]) == ("tiktok", 1200, 90)
    assert post["hashtags"] == ["aiseries", "cyberpunk"]
    assert audience.normalize_post({"url": "x", "error": "blocked"}) is None
    assert [c["id"] for c in map(audience.normalize_comment, COMMENTS) if c] == ["c1", "c2"]


def test_confused_comment_meets_the_open_finding_on_the_same_character(graph):
    comments = [c for c in map(audience.normalize_comment, COMMENTS) if c]
    links = [
        CommentLink(comment_id="c1", characters=["JUNO"], facts=["daughter_name"], topic="continuity", sentiment="neutral", confused=True),
        CommentLink(comment_id="c2", characters=["MIRA"], facts=[], topic="visual", sentiment="neutral", confused=True),
    ]
    audience.load_episode_post(graph, 4, audience.normalize_post(TIKTOK_POST), comments, links)
    flags = {f["comment"]: f for f in audience.report(graph)["viewer_flags"]}
    assert "leak:JUNO:daughter_name" in flags["wait how does Juno know her name??"]["matching_findings"]
    assert "wardrobe:MIRA:E4S1" in flags["Mira's scarf is gone lol"]["matching_findings"]


def test_trends_come_from_a_fresh_cache_without_calling_bright_data(graph, tmp_path):
    (tmp_path / "trends_aiseries.json").write_text(json.dumps([TIKTOK_POST]))

    class NoNetwork:
        def collect(self, *args, **kwargs):
            raise AssertionError("should have used the cache")

    posts = audience.sync_trends(graph, "#aiseries", tmp_path, client=NoNetwork())
    assert [p["author"] for p in posts] == ["neoncourier"]
    assert {"tag": "aiseries", "posts": 1, "views": 1200} in audience.report(graph)["niche_hashtags"]
