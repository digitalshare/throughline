"""Compiles each shot's video-generation prompt from the graph, with the source of every detail."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from falkordb import Graph


@dataclass
class Source:
    detail: str
    path: str  # human-readable graph path the detail came from


@dataclass
class ShotPrompt:
    shot: str
    scene: str
    line: int | None
    prompt: str
    sources: list[Source] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def active_outfit(g: Graph, cid: str, scene_order: int) -> tuple[str, str, str, int | None]:
    """The outfit a character wears in a scene, from their WEARS timeline: (id, description, since_scene, since_line)."""
    rows = g.query(
        """MATCH (c:Character {id: $cid})-[w:WEARS]->(o:Outfit) WHERE w.from_order < $order + 100
           RETURN o.id, o.description, w.scene, w.line ORDER BY w.from_order DESC LIMIT 1""",
        {"cid": cid, "order": scene_order},
    ).result_set
    return tuple(rows[0])


def _prop_state(g: Graph, pid: str, scene_order: int) -> tuple[str, str] | None:
    """The prop's last state before this scene, so the scene can't vouch for itself."""
    rows = g.query(
        """MATCH (:Prop {id: $pid})-[s:STATE]->(:Scene) WHERE s.order < $order
           RETURN s.state, s.scene ORDER BY s.order DESC LIMIT 1""",
        {"pid": pid, "order": scene_order},
    ).result_set
    return tuple(rows[0]) if rows else None


def compile_episode(g: Graph, episode: int) -> list[ShotPrompt]:
    style, fmt = g.query("MATCH (s:Series) RETURN s.style, s.format LIMIT 1").result_set[0]
    shots = g.query(
        """MATCH (:Episode {num: $ep})-[:HAS_SCENE]->(s:Scene)-[:HAS_SHOT]->(t:Shot), (s)-[:AT]->(l:Location)
           RETURN s.id, s.order, s.heading, l.id, l.look, t.id, t.description, t.line ORDER BY t.order""",
        {"ep": episode},
    ).result_set

    prompts: list[ShotPrompt] = []
    previous: tuple[str, str] | None = None  # (scene, description) of the last shot
    for scene, order, heading, loc, look, shot, desc, line in shots:
        sources = [
            Source(f"setting: {look}", f"Shot {shot} ← HAS_SHOT ← Scene {scene} → AT → Location {loc}"),
            Source(f"style: {style}, {fmt}", "Series bible"),
        ]
        corrections: list[str] = []
        people: list[str] = []
        cast = g.query(
            """MATCH (c:Character)-[i:IN_SHOT]->(:Shot {id: $shot})
               RETURN c.id, c.name, c.canonical_look, i.outfit ORDER BY c.id""",
            {"shot": shot},
        ).result_set
        for cid, name, canonical, scripted in cast:
            outfit, outfit_desc, since, since_line = active_outfit(g, cid, order)
            people.append(f"{name} ({canonical}, wearing {outfit_desc})")
            sources.append(Source(f"{name}'s look: {canonical}", f"Character {cid}.canonical_look (series bible)"))
            origin = "default outfit, series bible" if since == "bible" else f"since {since} line {since_line}"
            sources.append(Source(f"{name} wears {outfit_desc}", f"Character {cid} → WEARS → Outfit {outfit} ({origin})"))
            if scripted and scripted != outfit:
                corrections.append(f"Script dresses {name} as '{scripted}'; using '{outfit}' from the wardrobe timeline ({origin}).")

        props = []
        for (pid, pdesc) in g.query(
            "MATCH (:Shot {id: $shot})-[:FEATURES]->(p:Prop) RETURN p.id, p.description ORDER BY p.id", {"shot": shot}
        ).result_set:
            state = _prop_state(g, pid, order)
            if state and state[0] in ("destroyed", "lost"):
                corrections.append(f"{pid} was {state[0]} in {state[1]}; left out of the prompt.")
                continue
            props.append(pdesc)
            where = f" (last seen {state[0]} in {state[1]})" if state else ""
            sources.append(Source(f"prop: {pdesc}", f"Shot {shot} → FEATURES → Prop {pid}{where}"))

        parts = [desc + ".", f"Setting: {look}."]
        if people:
            parts.append("Characters: " + "; ".join(people) + ".")
        if props:
            parts.append("Props: " + "; ".join(props) + ".")
        if previous and previous[0] == scene:
            parts.append(f"Continues from the previous shot: {previous[1]}.")
            sources.append(Source("continuity with the previous shot", f"Shot {shot} follows the previous shot in story order"))
        parts.append(f"Style: {style}. Format: {fmt}.")
        prompts.append(ShotPrompt(shot=shot, scene=scene, line=line, prompt=" ".join(parts), sources=sources, corrections=corrections))
        previous = (scene, desc)
    return prompts
