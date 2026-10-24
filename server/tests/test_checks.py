from pathlib import Path

import pytest

from throughline import checks
from throughline.graph import load_series

SAMPLES = Path(__file__).parents[2] / "samples" / "neon_courier"


@pytest.fixture(scope="module")
def graph():
    try:
        g = load_series(SAMPLES, graph_name="test_neon_courier", wipe=True)
    except Exception as exc:  # FalkorDB not running
        pytest.skip(f"FalkorDB unavailable: {exc}")
    yield g
    g.delete()


def test_finds_exactly_the_planted_errors(graph):
    found = {(f.type, f.severity, f.scene) for f in checks.run_all(graph)}
    assert found == {
        ("knowledge_leak", "error", "E4S1"),       # Juno knows the name Elena
        ("knowledge_leak", "warning", "E4S2"),     # Kai knows the locket secret; Juno could have told him
        ("wardrobe_jump", "error", "E4S1"),        # Mira lost the red scarf
        ("prop_resurrection", "error", "E4S2"),    # motorbike exploded in E2S2
        ("ghost", "error", "E4S3"),                # Rook died in E3S2
        ("dangling_cliffhanger", "warning", "E2S3"),  # tower signal never resolved
    }


def test_leak_explains_who_knew_and_the_carrier_path(graph):
    by_id = {f.id: f for f in checks.run_all(graph)}
    elena = by_id["leak:JUNO:daughter_name"]
    assert "Director Voss" in elena.detail
    assert {"source": "VOSS", "target": "daughter_name", "type": "LEARNS", "scene": "E3S3", "line": None} in elena.edges
    locket = by_id["leak:KAI:locket_key"]
    assert any(e["type"] == "MET" and e["target"] == "KAI" for e in locket.edges)


def test_hearing_it_on_screen_is_not_a_leak(graph):
    # Juno also says "Voss has a daughter", which she heard from Rook in E3S2.
    assert "leak:JUNO:voss_has_daughter" not in {f.id for f in checks.run_all(graph)}


def test_persist_resolves_findings_that_stop_firing(graph):
    checks.persist(graph, checks.run_all(graph))
    graph.query("MATCH (c:Character {id: 'ROOK'})-[d:DIES_IN]->() DELETE d")
    checks.persist(graph, checks.run_all(graph))
    status = graph.query("MATCH (f:Finding {id: 'ghost:ROOK:E4S3'}) RETURN f.status").result_set
    assert status == [["resolved"]]
