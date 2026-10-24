"""MCP server that gives a TrueForge agent the series graph: checks, explanations, prompts, renders, audience."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from . import audience, checks, fountain, revise, video
from .graph import connect, load_series
from .prompts import compile_episode

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "samples" / "neon_courier"
SERIES_DIR = Path(os.environ.get("THROUGHLINE_SERIES", ROOT / "workspace" / "neon_courier"))
GRAPH = os.environ.get("THROUGHLINE_GRAPH", "neon_courier")

if not SERIES_DIR.exists() and SERIES_DIR.parent == ROOT / "workspace":
    shutil.copytree(SAMPLE, SERIES_DIR)  # work on a copy so demo edits never touch the committed sample

# TrueForge asks for approval before tools marked write/destructive and runs read-only ones on their own.
READ = ToolAnnotations(read_only_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False)
SPEND = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)

server = MCPServer(
    name="throughline",
    instructions=(
        "Tools over the series bible graph of an AI-generated video series. Run checks before anything is rendered. "
        "When you report a finding, cite its scene, line and the path from explain_finding. Never call generate_shot, "
        "fix_script_line or publish_episode without the user's go-ahead: they spend money or change the script."
    ),
)


@server.tool(annotations=WRITE)
def ingest_series(live: bool = False) -> dict:
    """Reload every episode script into the graph. live=True re-extracts all episodes with OpenAI (slow)."""
    g = load_series(SERIES_DIR, GRAPH, live=live)
    return dict(g.query("MATCH (n) RETURN labels(n)[0] AS label, count(n) ORDER BY label").result_set)


@server.tool(annotations=WRITE)
def run_checks(episode: int | None = None) -> list[dict]:
    """Run every continuity check (knowledge leaks, wardrobe jumps, prop resurrections, ghosts, dangling threads).
    Stores findings in the graph and returns them with severity, scene, line and proving path."""
    g = connect(GRAPH)
    findings = checks.run_all(g, episode)
    checks.persist(g, findings)
    return [f.to_dict() for f in findings]


@server.tool(annotations=READ)
def explain_finding(finding_id: str) -> dict:
    """The full graph path behind one finding, plus the script lines on that path."""
    g = connect(GRAPH)
    finding = next((f for f in checks.run_all(g) if f.id == finding_id), None)
    if finding is None:
        return {"error": f"{finding_id} is not open (it may be resolved)"}
    excerpts = []
    for edge in finding.edges:
        if edge.get("scene") and edge["scene"] != "bible" and edge.get("line"):
            excerpts.append(_excerpt(edge["scene"], edge["line"]) | {"edge": f"{edge['source']} -{edge['type']}-> {edge['target']}"})
    return finding.to_dict() | {"script_excerpts": excerpts}


def _excerpt(scene_id: str, line: int, context: int = 1) -> dict:
    episode = int(scene_id.split("S")[0].lstrip("E"))
    lines = fountain.parse_file(SERIES_DIR / "scripts" / f"ep{episode}.fountain").lines
    lo, hi = max(1, line - context), min(len(lines), line + context)
    return {"scene": scene_id, "line": line, "text": "\n".join(f"{n:>3}  {lines[n - 1]}" for n in range(lo, hi + 1))}


@server.tool(annotations=READ)
def read_script(episode: int) -> str:
    """The episode script with line numbers."""
    lines = (SERIES_DIR / "scripts" / f"ep{episode}.fountain").read_text().splitlines()
    return "\n".join(f"{n:>3}  {text}" for n, text in enumerate(lines, start=1))


@server.tool(annotations=SPEND)
def fix_script_line(episode: int, line: int, new_text: str) -> dict:
    """NEEDS APPROVAL. Replace one script line, re-extract the episode, reload the graph and re-run the checks.
    Returns which findings the fix resolved and any new ones it caused."""
    return revise.fix_line(SERIES_DIR, GRAPH, episode, line, new_text)


@server.tool(annotations=READ)
def compile_prompts(episode: int) -> list[dict]:
    """Video-generation prompt for every shot in an episode, built from the graph (canonical looks, current outfits,
    locations, live props), with the graph source of each detail and any corrections to the script."""
    return [p.to_dict() for p in compile_episode(connect(GRAPH), episode)]


@server.tool(annotations=SPEND)
def generate_shot(shot_id: str, seconds: int = 5, provider: str = "fal") -> dict:
    """NEEDS APPROVAL, spends credits (about $0.50 per 5 s shot). Render one shot (e.g. 'E4S1T1'): FLUX.2 keyframe
    conditioned on each character's reference image, then image-to-video. provider: 'fal' (Kling v3) or 'machgen'.
    seconds: 3-15."""
    return video.generate_shot(connect(GRAPH), SERIES_DIR / "renders", shot_id, seconds, provider)


@server.tool(annotations=READ)
def estimate_render(episode: int, seconds: int = 5, provider: str = "fal") -> dict:
    """What rendering an episode would cost: shots without clips yet, new reference images, and USD."""
    return video.estimate_cost(connect(GRAPH), SERIES_DIR / "renders", episode, seconds, provider)


@server.tool(annotations=SPEND)
def render_episode(episode: int, seconds: int = 5, provider: str = "fal") -> dict:
    """NEEDS APPROVAL, spends credits. Call estimate_render first and tell the user the price. Renders every shot of
    the episode that has no clip yet (FLUX.2 keyframe from each character's reference in their current outfit, then
    image-to-video), then assembles the episode video. Failed shots are listed; rerun to retry only those."""
    return video.render_episode(connect(GRAPH), SERIES_DIR / "renders", episode, seconds, provider)


@server.tool(annotations=WRITE)
def sync_trends(keyword: str, limit: int = 20, max_age_hours: float = 6) -> dict:
    """Pull top TikTok posts for a niche keyword or hashtag via Bright Data into the graph. A fresh scrape takes
    5-10 minutes, so results younger than max_age_hours come from the cache; pass max_age_hours=0 to force a scrape."""
    posts = audience.sync_trends(connect(GRAPH), keyword, SERIES_DIR / "audience", limit, max_age_hours)
    return {"keyword": keyword, "posts": len(posts), "top": sorted(posts, key=lambda p: -p["views"])[:5]}


@server.tool(annotations=WRITE)
def sync_episode_audience(episode: int, url: str, max_comments: int = 100) -> dict:
    """Pull a published episode's stats and comments (TikTok or YouTube URL) via Bright Data, and link each comment
    to the characters and plot facts it talks about."""
    return audience.sync_episode(connect(GRAPH), episode, url, SERIES_DIR / "audience", max_comments)


@server.tool(annotations=READ)
def audience_report() -> dict:
    """What viewers say, joined to the story: mentions per character, confused comments matched to open findings,
    and the niche's top hashtags and posts."""
    return audience.report(connect(GRAPH))


# ---------- REST + static viewer (same process, so the viewer always shows what the agent sees) ----------

WEB = ROOT / "web"


@server.custom_route("/", methods=["GET"])
async def viewer(request: Request) -> Response:
    return FileResponse(WEB / "index.html")


@server.custom_route("/api/episodes", methods=["GET"])
async def api_episodes(request: Request) -> Response:
    g = connect(GRAPH)
    title = g.query("MATCH (s:Series) RETURN s.title LIMIT 1").result_set[0][0]
    rows = g.query(
        """MATCH (e:Episode)-[:HAS_SCENE]->(s:Scene)
           WITH e, s ORDER BY s.num
           RETURN e.num, e.title, collect({id: s.id, heading: s.heading, line_start: s.line_start, line_end: s.line_end})
           ORDER BY e.num"""
    ).result_set
    return JSONResponse({"title": title, "episodes": [{"num": n, "title": t, "scenes": sc} for n, t, sc in rows]})


@server.custom_route("/api/script/{episode:int}", methods=["GET"])
async def api_script(request: Request) -> Response:
    path = SERIES_DIR / "scripts" / f"ep{request.path_params['episode']}.fountain"
    return JSONResponse({"lines": path.read_text().splitlines()})


@server.custom_route("/api/findings", methods=["GET"])
async def api_findings(request: Request) -> Response:
    return JSONResponse([f.to_dict() for f in checks.run_all(connect(GRAPH))])


@server.custom_route("/api/prompts/{episode:int}", methods=["GET"])
async def api_prompts(request: Request) -> Response:
    return JSONResponse([p.to_dict() for p in compile_episode(connect(GRAPH), request.path_params["episode"])])


@server.custom_route("/api/characters", methods=["GET"])
async def api_characters(request: Request) -> Response:
    rows = connect(GRAPH).query("MATCH (c:Character) RETURN c.id, c.name, c.canonical_look ORDER BY c.id").result_set
    avatars = SERIES_DIR / "renders" / "avatars"
    return JSONResponse([
        {"id": cid, "name": name, "look": look,
         "avatar": f"/media/avatars/{cid}.png" if video.avatar_path(avatars, cid).exists() else None}
        for cid, name, look in rows
    ])


@server.custom_route("/api/renders/{episode:int}", methods=["GET"])
async def api_renders(request: Request) -> Response:
    episode = request.path_params["episode"]
    renders = SERIES_DIR / "renders"
    g = connect(GRAPH)
    shots = {
        shot: {
            "keyframe": f"/media/keyframes/{shot}.png" if (renders / "keyframes" / f"{shot}.png").exists() else None,
            "clip": f"/media/clips/{shot}.mp4" if (renders / "clips" / f"{shot}.mp4").exists() else None,
        }
        for shot, _ in video.episode_shots(g, episode)
    }
    full = renders / "episodes" / f"ep{episode}.mp4"
    return JSONResponse({"episode": f"/media/episodes/ep{episode}.mp4" if full.exists() else None, "shots": shots})


@server.custom_route("/media/{kind}/{name}", methods=["GET"])
async def media(request: Request) -> Response:
    kind, name = request.path_params["kind"], request.path_params["name"]
    if kind not in ("keyframes", "clips", "episodes", "characters", "avatars") or "/" in name or name.startswith("."):
        return JSONResponse({"error": "not found"}, status_code=404)
    path = SERIES_DIR / "renders" / kind / name
    return FileResponse(path) if path.is_file() else JSONResponse({"error": "not found"}, status_code=404)


@server.custom_route("/api/audience", methods=["GET"])
async def api_audience(request: Request) -> Response:
    return JSONResponse(audience.report(connect(GRAPH)))


def main() -> None:
    server.run("streamable-http", host="127.0.0.1", port=int(os.environ.get("THROUGHLINE_MCP_PORT", 8770)))


if __name__ == "__main__":
    main()
