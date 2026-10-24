"""Loads the series bible and extracted episodes into FalkorDB. Every story edge keeps its provenance."""

from __future__ import annotations

import json
import os
from pathlib import Path

from falkordb import FalkorDB, Graph

from . import fountain
from .extract import load_or_extract
from .models import EpisodeExtract

# Story time is a single integer: episode, then scene, then event within the scene.
EPISODE_STRIDE = 10_000
SCENE_STRIDE = 100


def scene_order(episode: int, scene: int) -> int:
    return episode * EPISODE_STRIDE + scene * SCENE_STRIDE


def connect(graph_name: str = "neon_courier") -> Graph:
    db = FalkorDB(host=os.environ.get("FALKORDB_HOST", "localhost"), port=int(os.environ.get("FALKORDB_PORT", 6379)))
    return db.select_graph(graph_name)


def reset(g: Graph) -> None:
    try:
        g.delete()
    except Exception:  # graph does not exist yet
        pass


# Nodes that outlive a script revision: the agents' findings and everything pulled from the audience.
MEMORY_LABELS = ("Finding", "Comment", "Post", "TrendPost", "Hashtag")


def reset_story(g: Graph) -> None:
    """Drop the story so it can be reloaded, but keep memory nodes and the edges they own."""
    owned = " OR ".join(f"a:{label}" for label in MEMORY_LABELS)
    g.query("MATCH (n) WHERE n:Scene OR n:Shot DETACH DELETE n")
    g.query(f"MATCH (a)-[r]->() WHERE NOT ({owned}) DELETE r")


def load_bible(g: Graph, bible: dict) -> None:
    series = bible["series"]
    g.query("MERGE (s:Series {id: $id}) SET s.title = $title, s.format = $format, s.style = $style", series)
    for cid, c in bible["characters"].items():
        g.query(
            """MERGE (c:Character {id: $id})
               SET c.name = $name, c.canonical_look = $look, c.voice = $voice
               MERGE (o:Outfit {id: $outfit})
               MERGE (c)-[w:WEARS {from_order: 0}]->(o)
               SET w.scene = 'bible', w.quote = 'default outfit'""",
            {"id": cid, "name": c["name"], "look": c["canonical_look"], "voice": c["voice"], "outfit": c["default_outfit"]},
        )
    for oid, desc in bible["outfits"].items():
        g.query("MERGE (o:Outfit {id: $id}) SET o.description = $desc", {"id": oid, "desc": desc})
    for lid, look in bible["locations"].items():
        g.query("MERGE (l:Location {id: $id}) SET l.look = $look", {"id": lid, "look": look})
    for pid, desc in bible["props"].items():
        g.query("MERGE (p:Prop {id: $id}) SET p.description = $desc", {"id": pid, "desc": desc})


def load_episode(g: Graph, series_id: str, ep: EpisodeExtract, script: fountain.Script) -> None:
    g.query(
        """MATCH (s:Series {id: $series})
           MERGE (e:Episode {num: $num}) SET e.title = $title
           MERGE (s)-[:HAS_EP]->(e)""",
        {"series": series_id, "num": ep.episode, "title": ep.title},
    )
    for sc, parsed in zip(ep.scenes, script.scenes, strict=True):
        _load_scene(g, ep.episode, sc, parsed, script)


def _load_scene(g: Graph, episode: int, sc, parsed: fountain.Scene, script: fountain.Script) -> None:
    base = scene_order(episode, sc.num)
    scene_id = f"E{episode}S{sc.num}"
    g.query(
        """MATCH (e:Episode {num: $episode})
           MERGE (s:Scene {id: $id})
           SET s.episode = $episode, s.num = $num, s.order = $order, s.heading = $heading,
               s.line_start = $line_start, s.line_end = $line_end, s.flashback = $flashback
           MERGE (e)-[:HAS_SCENE]->(s)
           MERGE (l:Location {id: $location})
           MERGE (s)-[:AT]->(l)""",
        {
            "episode": episode, "id": scene_id, "num": sc.num, "order": base, "heading": sc.heading,
            "line_start": parsed.line_start, "line_end": parsed.line_end, "flashback": sc.flashback,
            "location": sc.location,
        },
    )
    if sc.num > 1:
        g.query(
            "MATCH (a:Scene {id: $prev}), (b:Scene {id: $id}) MERGE (a)-[:NEXT]->(b)",
            {"prev": f"E{episode}S{sc.num - 1}", "id": scene_id},
        )
    for cid in sc.characters:
        g.query(
            "MATCH (s:Scene {id: $scene}) MERGE (c:Character {id: $cid}) MERGE (c)-[:APPEARS_IN]->(s)",
            {"scene": scene_id, "cid": cid},
        )
    for shot in sc.shots:
        shot_id = f"{scene_id}T{shot.num}"
        g.query(
            """MATCH (s:Scene {id: $scene})
               MERGE (t:Shot {id: $id})
               SET t.num = $num, t.order = $order, t.description = $desc, t.line = $line, t.quote = $quote
               MERGE (s)-[:HAS_SHOT]->(t)""",
            {
                "scene": scene_id, "id": shot_id, "num": shot.num, "order": base + shot.num, "desc": shot.description,
                "line": fountain.find_line(script, shot.quote, parsed), "quote": shot.quote,
            },
        )
        for cast in shot.characters:
            # outfit stays null unless the script describes it; the WEARS timeline supplies the look otherwise
            g.query(
                """MATCH (t:Shot {id: $shot}) MERGE (c:Character {id: $cid})
                   MERGE (c)-[i:IN_SHOT]->(t) SET i.outfit = $outfit""",
                {"shot": shot_id, "cid": cast.character, "outfit": cast.outfit},
            )
        for pid in shot.props:
            g.query("MATCH (t:Shot {id: $shot}) MERGE (p:Prop {id: $pid}) MERGE (t)-[:FEATURES]->(p)", {"shot": shot_id, "pid": pid})
    for idx, ev in enumerate(sc.events, start=1):
        prov = {
            "order": base + idx, "scene": scene_id, "episode": episode,
            "line": fountain.find_line(script, ev.quote, parsed), "quote": ev.quote,
        }
        _load_event(g, ev, prov)


PROV = "order: $order, scene: $scene, episode: $episode, line: $line, quote: $quote"


def _load_event(g: Graph, ev, prov: dict) -> None:
    match ev.type:
        case "establishes":
            g.query(
                f"""MATCH (s:Scene {{id: $scene}})
                    MERGE (f:Fact {{id: $fact}}) SET f.kind = $kind, f.text = $text
                    MERGE (f)-[r:ESTABLISHED_IN]->(s) SET r += {{{PROV}}}""",
                {**prov, "fact": ev.fact, "kind": ev.kind, "text": ev.text},
            )
            for cid in ev.known_by:
                g.query(
                    f"""MATCH (f:Fact {{id: $fact}}) MERGE (c:Character {{id: $cid}})
                        CREATE (c)-[:LEARNS {{how: 'witnessed', {PROV}}}]->(f)""",
                    {**prov, "fact": ev.fact, "cid": cid},
                )
        case "references":
            g.query(
                f"""MERGE (c:Character {{id: $cid}}) MERGE (f:Fact {{id: $fact}})
                    CREATE (c)-[:REFERENCES {{{PROV}}}]->(f)""",
                {**prov, "fact": ev.fact, "cid": ev.character},
            )
        case "tells":
            # telling a fact also means the teller had to know it, so it counts as a reference too
            g.query(
                f"""MERGE (a:Character {{id: $cid}}) MERGE (b:Character {{id: $to}}) MERGE (f:Fact {{id: $fact}})
                    CREATE (a)-[:TELLS {{fact: $fact, {PROV}}}]->(b)
                    CREATE (a)-[:REFERENCES {{{PROV}}}]->(f)
                    CREATE (b)-[:LEARNS {{how: 'told', source: $cid, {PROV}}}]->(f)""",
                {**prov, "fact": ev.fact, "cid": ev.character, "to": ev.to},
            )
        case "resolves":
            g.query(
                f"""MATCH (s:Scene {{id: $scene}}) MERGE (f:Fact {{id: $fact}})
                    MERGE (f)-[r:RESOLVED_IN]->(s) SET r += {{{PROV}}}""",
                {**prov, "fact": ev.fact},
            )
        case "prop_state":
            g.query(
                f"""MATCH (s:Scene {{id: $scene}}) MERGE (p:Prop {{id: $prop}})
                    CREATE (p)-[:STATE {{state: $state, holder: $holder, {PROV}}}]->(s)""",
                {**prov, "prop": ev.prop, "state": ev.state, "holder": ev.holder},
            )
        case "outfit_change":
            g.query(
                f"""MERGE (c:Character {{id: $cid}}) MERGE (o:Outfit {{id: $outfit}})
                    CREATE (c)-[:WEARS {{from_order: $order, scene: $scene, episode: $episode, line: $line, quote: $quote}}]->(o)""",
                {**prov, "cid": ev.character, "outfit": ev.outfit},
            )
        case "dies":
            g.query(
                f"""MATCH (s:Scene {{id: $scene}}) MERGE (c:Character {{id: $cid}})
                    CREATE (c)-[:DIES_IN {{{PROV}}}]->(s)""",
                {**prov, "cid": ev.character},
            )


def link_meetings(g: Graph) -> None:
    """MET edges between every pair of characters who share a scene, so knowledge can travel along them."""
    g.query(
        """MATCH (a:Character)-[:APPEARS_IN]->(s:Scene)<-[:APPEARS_IN]-(b:Character)
           WHERE a.id <> b.id
           MERGE (a)-[m:MET {scene: s.id}]->(b) SET m.order = s.order, m.episode = s.episode"""
    )


def load_series(series_dir: Path, graph_name: str | None = None, live: bool = False, wipe: bool = False) -> Graph:
    """Load or reload the whole series. Memory (findings, audience data) survives unless `wipe` is set."""
    bible = json.loads((series_dir / "bible.json").read_text())
    g = connect(graph_name or bible["series"]["id"])
    reset(g) if wipe else reset_story(g)
    load_bible(g, bible)
    known_facts: dict[str, str] = {}
    for path in sorted((series_dir / "scripts").glob("ep*.fountain"), key=lambda p: int(p.stem[2:])):
        num = int(path.stem[2:])
        extracted = load_or_extract(series_dir, num, known_facts, live=live)
        load_episode(g, bible["series"]["id"], extracted, fountain.parse_file(path))
        for sc in extracted.scenes:
            known_facts.update({ev.fact: ev.text for ev in sc.events if ev.type == "establishes" and ev.fact})
    link_meetings(g)
    g.query("MATCH (f:Fact) WHERE NOT (f)--() DELETE f")  # facts a revision removed
    return g
