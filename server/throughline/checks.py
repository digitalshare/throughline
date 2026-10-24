"""Continuity checks over the series graph. Each finding carries the graph path that proves it."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from falkordb import Graph


@dataclass
class Finding:
    id: str
    type: str
    severity: str  # error | warning | info
    title: str
    detail: str
    scene: str
    line: int | None
    quote: str
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _node(label: str, id: str, name: str | None = None) -> dict:
    return {"label": label, "id": id, "name": name or id}


def _edge(source: str, target: str, type: str, scene: str | None = None, line: int | None = None) -> dict:
    return {"source": source, "target": target, "type": type, "scene": scene, "line": line}


def knowledge_leaks(g: Graph) -> list[Finding]:
    """A character references a fact before learning it. Searches for anyone who could have told them."""
    rows = g.query(
        """MATCH (c:Character)-[r:REFERENCES]->(f:Fact)
           OPTIONAL MATCH (c)-[l:LEARNS]->(f)
           WITH c, r, f, [x IN collect(l) WHERE x.order < r.order] AS prior
           WHERE size(prior) = 0
           MATCH (f)-[e:ESTABLISHED_IN]->(es:Scene)
           RETURN c.id, c.name, f.id, f.text, r.order, r.scene, r.line, r.quote, es.id"""
    ).result_set
    findings = []
    for cid, cname, fid, ftext, order, scene, line, quote, est_scene in rows:
        knowers = g.query(
            """MATCH (k:Character)-[l:LEARNS]->(:Fact {id: $fact}) WHERE l.order < $order
               RETURN k.id, k.name, l.scene, l.how""",
            {"fact": fid, "order": order},
        ).result_set
        carrier = g.query(
            """MATCH (k:Character)-[l:LEARNS]->(:Fact {id: $fact}) WHERE l.order < $order AND k.id <> $cid
               MATCH p = (k)-[:MET*1..3]->(:Character {id: $cid})
               WHERE all(i IN range(0, length(p) - 1)
                         WHERE relationships(p)[i].order > l.order AND relationships(p)[i].order < $order
                           AND (i = 0 OR relationships(p)[i].order >= relationships(p)[i - 1].order))
               RETURN [n IN nodes(p) | n.id], [n IN nodes(p) | n.name], [r IN relationships(p) | r.scene], length(p) AS hops
               ORDER BY hops LIMIT 1""",
            {"fact": fid, "order": order, "cid": cid},
        ).result_set

        nodes = [_node("Character", cid, cname), _node("Fact", fid, ftext), _node("Scene", est_scene)]
        edges = [_edge(cid, fid, "REFERENCES", scene, line), _edge(fid, est_scene, "ESTABLISHED_IN", est_scene)]
        for kid, kname, lscene, how in knowers:
            nodes.append(_node("Character", kid, kname))
            edges.append(_edge(kid, fid, "LEARNS", lscene))
        who_knew = ", ".join(k[1] for k in knowers) or "nobody"

        if carrier:
            ids, names, scenes, _ = carrier[0]
            for a, b, s in zip(ids, ids[1:], scenes):
                edges.append(_edge(a, b, "MET", s))
            chain = " → ".join(f"{n} ({s})" for n, s in zip(names, scenes)) + f" → {cname}"
            severity = "warning"
            detail = (
                f"{cname} references \"{ftext}\" in {scene} but never learns it on screen. "
                f"Plausible off-screen route: {chain}. Add a line that shows it, or the audience will ask."
            )
        else:
            severity = "error"
            detail = (
                f"{cname} references \"{ftext}\" in {scene}, but only {who_knew} knew it, "
                f"and no chain of meetings connects them to {cname} before {scene}."
            )
        findings.append(
            Finding(
                id=f"leak:{cid}:{fid}", type="knowledge_leak", severity=severity,
                title=f"{cname} knows something they never learned", detail=detail,
                scene=scene, line=line, quote=quote, nodes=nodes, edges=edges,
            )
        )
    return findings


def wardrobe_jumps(g: Graph) -> list[Finding]:
    """A scene describes a character in a different outfit from the one they last changed into."""
    rows = g.query(
        """MATCH (c:Character)-[i:IN_SHOT]->(t:Shot)<-[:HAS_SHOT]-(s:Scene)
           WHERE i.outfit IS NOT NULL
           MATCH (c)-[w:WEARS]->(:Outfit) WHERE w.from_order < s.order + 100
           WITH c, i, t, s, max(w.from_order) AS latest
           MATCH (c)-[w:WEARS {from_order: latest}]->(o:Outfit)
           WHERE o.id <> i.outfit
           MATCH (shown:Outfit {id: i.outfit})
           WITH c, s, o, w, shown, collect(t.id) AS shots, min(t.line) AS line, collect(t.quote)[0] AS quote
           RETURN c.id, c.name, s.id, shots, line, quote, o.id, o.description, w.scene, w.line, shown.description"""
    ).result_set
    return [
        Finding(
            id=f"wardrobe:{cid}:{scene}", type="wardrobe_jump", severity="error",
            title=f"{cname}'s outfit jumps in {scene}",
            detail=(
                f"{', '.join(shots)} {'shows' if len(shots) == 1 else 'show'} {cname} in \"{shown_desc}\", "
                f"but since {since_scene} they wear \"{active_desc}\" and no scene changes it back. "
                f"The video model will render the wrong look."
            ),
            scene=scene, line=line, quote=quote,
            nodes=[_node("Character", cid, cname), *(_node("Shot", s) for s in shots), _node("Outfit", active, active_desc), _node("Scene", since_scene)],
            edges=[*(_edge(cid, s, "IN_SHOT", scene, line) for s in shots), _edge(cid, active, "WEARS", since_scene, since_line)],
        )
        for cid, cname, scene, unsorted_shots, line, quote, active, active_desc, since_scene, since_line, shown_desc in rows
        for shots in [sorted(unsorted_shots)]
    ]


def prop_resurrections(g: Graph) -> list[Finding]:
    """A prop shows up again after it was destroyed or lost, with no recovery in between."""
    rows = g.query(
        """MATCH (p:Prop)-[d:STATE]->(:Scene) WHERE d.state IN ['destroyed', 'lost']
           MATCH (p)-[a:STATE]->(:Scene) WHERE a.order > d.order AND a.state IN ['present', 'held']
           OPTIONAL MATCH (p)-[rc:STATE {state: 'recovered'}]->(:Scene)
           WITH p, d, a, [x IN collect(rc) WHERE x.order > d.order AND x.order < a.order] AS recoveries
           WHERE size(recoveries) = 0
           RETURN p.id, p.description, d.state, d.scene, d.line, a.scene, a.line, a.quote"""
    ).result_set
    return [
        Finding(
            id=f"prop:{pid}:{scene}", type="prop_resurrection", severity="error",
            title=f"The {pid} is back after being {gone_state}",
            detail=f"{desc} was {gone_state} in {gone_scene} (line {gone_line}) but appears in {scene} with no scene recovering it.",
            scene=scene, line=line, quote=quote,
            nodes=[_node("Prop", pid, desc), _node("Scene", gone_scene), _node("Scene", scene)],
            edges=[_edge(pid, gone_scene, f"STATE:{gone_state}", gone_scene, gone_line), _edge(pid, scene, "STATE:present", scene, line)],
        )
        for pid, desc, gone_state, gone_scene, gone_line, scene, line, quote in rows
    ]


def ghosts(g: Graph) -> list[Finding]:
    """A character appears after their death, outside a flashback."""
    rows = g.query(
        """MATCH (c:Character)-[d:DIES_IN]->(ds:Scene)
           MATCH (c)-[:APPEARS_IN]->(s:Scene) WHERE s.order > ds.order AND NOT s.flashback
           OPTIONAL MATCH (c)-[:IN_SHOT]->(t:Shot)<-[:HAS_SHOT]-(s)
           RETURN c.id, c.name, ds.id, d.line, s.id, min(t.line), s.heading"""
    ).result_set
    return [
        Finding(
            id=f"ghost:{cid}:{scene}", type="ghost", severity="error",
            title=f"{cname} is back from the dead",
            detail=f"{cname} died in {death_scene} (line {death_line}) but appears in {scene} ({heading}). Mark it as a flashback or explain the return.",
            scene=scene, line=line, quote=heading,
            nodes=[_node("Character", cid, cname), _node("Scene", death_scene), _node("Scene", scene)],
            edges=[_edge(cid, death_scene, "DIES_IN", death_scene, death_line), _edge(cid, scene, "APPEARS_IN", scene, line)],
        )
        for cid, cname, death_scene, death_line, scene, line, heading in rows
    ]


def dangling_threads(g: Graph, max_gap: int = 2) -> list[Finding]:
    """Cliffhangers left open for `max_gap`+ episodes, and setups that never come back."""
    latest = g.query("MATCH (e:Episode) RETURN max(e.num)").result_set[0][0]
    rows = g.query(
        """MATCH (f:Fact)-[e:ESTABLISHED_IN]->(s:Scene)
           WHERE f.kind IN ['cliffhanger', 'setup'] AND NOT (f)-[:RESOLVED_IN]->(:Scene)
           OPTIONAL MATCH (:Character)-[r:REFERENCES]->(f)
           WITH f, e, s, count(r) AS refs
           WHERE (f.kind = 'cliffhanger' AND $latest - s.episode >= $gap) OR (f.kind = 'setup' AND refs = 0)
           RETURN f.id, f.kind, f.text, s.id, s.episode, e.line, e.quote""",
        {"latest": latest, "gap": max_gap},
    ).result_set
    findings = []
    for fid, kind, text, scene, episode, line, quote in rows:
        if kind == "cliffhanger":
            title = f"Cliffhanger from episode {episode} is still open"
            detail = f"\"{text}\" ({scene}) has gone {latest - episode} episodes without a payoff. Viewers notice."
            severity = "warning"
        else:
            title = "Setup never pays off"
            detail = f"\"{text}\" is set up in {scene} and never mentioned again."
            severity = "info"
        findings.append(
            Finding(
                id=f"thread:{fid}", type=f"dangling_{kind}", severity=severity, title=title, detail=detail,
                scene=scene, line=line, quote=quote,
                nodes=[_node("Fact", fid, text), _node("Scene", scene)],
                edges=[_edge(fid, scene, "ESTABLISHED_IN", scene, line)],
            )
        )
    return findings


CHECKS = [knowledge_leaks, wardrobe_jumps, prop_resurrections, ghosts, dangling_threads]
SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


def run_all(g: Graph, episode: int | None = None) -> list[Finding]:
    findings = [f for check in CHECKS for f in check(g)]
    if episode is not None:
        findings = [f for f in findings if f.scene.startswith(f"E{episode}S")]
    return sorted(findings, key=lambda f: (SEVERITY_RANK[f.severity], f.scene, f.id))


def persist(g: Graph, findings: list[Finding]) -> None:
    """Store findings in the graph so every agent sees them. Findings that no longer fire are marked resolved."""
    g.query("MATCH (f:Finding) SET f.status = 'resolved'")
    for f in findings:
        g.query(
            """MERGE (n:Finding {id: $id})
               SET n.type = $type, n.severity = $severity, n.title = $title, n.detail = $detail,
                   n.scene = $scene, n.line = $line, n.status = 'open'
               WITH n MATCH (s:Scene {id: $scene}) MERGE (n)-[:ABOUT]->(s)""",
            {k: v for k, v in f.to_dict().items() if k not in ("nodes", "edges", "quote")},
        )
        for node in f.nodes:
            if node["label"] in ("Character", "Fact", "Prop"):
                g.query(
                    f"MATCH (n:Finding {{id: $id}}), (m:{node['label']} {{id: $target}}) MERGE (n)-[:ABOUT]->(m)",
                    {"id": f.id, "target": node["id"]},
                )
