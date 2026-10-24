"""Registers Throughline on a local TrueForge: OpenAI models, MCP servers, Daytona sandbox, skills and the agent.

Idempotent (every call is a PUT or an upsert). Secrets come from the environment and are never written to disk:
OPENAI_API_KEY (required), BRIGHTDATA_API_TOKEN, DAYTONA_API_KEY, THROUGHLINE_SKILLS_REPO (https GitHub URL).

    uv run --project server python trueforge/setup.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

TRUEFORGE = os.environ.get("TRUEFORGE_URL", "http://localhost:8790")
THROUGHLINE_MCP = os.environ.get("THROUGHLINE_MCP_URL", "http://127.0.0.1:8770/mcp")
FALKORDB_MCP = os.environ.get("FALKORDB_MCP_URL", "http://127.0.0.1:8080/mcp")
MODEL = os.environ.get("THROUGHLINE_AGENT_MODEL", "gpt-5.5")
AGENT = "throughline"
INSTRUCTIONS = (Path(__file__).parent / "instructions.md").read_text()

api = httpx.Client(base_url=f"{TRUEFORGE}/api/v1", timeout=60)
if token := os.environ.get("TRUEFORGE_API_KEY"):
    api.headers["Authorization"] = f"Bearer {token}"


def call(method: str, path: str, body: dict | None = None) -> dict:
    response = api.request(method, path, json=body)
    if response.is_error:
        sys.exit(f"{method} {path} failed with {response.status_code}: {response.text[:500]}")
    return response.json() if response.content else {}


def model_provider() -> str:
    catalog = call("GET", "/catalogs/model-providers")["data"]
    openai = next(p for p in catalog if p["type"] == "openai")
    models = [m for m in openai["models"] if m["model_id"] in (MODEL, "gpt-5.4-mini")]
    call("PUT", "/settings/model-providers", {"manifest": {"type": "openai", "auth": {"api_key": os.environ["OPENAI_API_KEY"]}, "models": models}})
    name = next(m["name"] for m in models if m["model_id"] == MODEL)
    print(f"model provider: openai ({', '.join(m['model_id'] for m in models)})")
    return f"openai/{name}"


def mcp_servers() -> list[dict]:
    servers = [
        {
            "type": "remote", "name": "throughline", "url": THROUGHLINE_MCP,
            "description": "Series bible graph: continuity checks with proving paths, shot prompts, FLUX + fal/MachGen renders, audience data.",
        },
        {
            "type": "remote", "name": "falkordb", "url": FALKORDB_MCP,
            "description": "Raw Cypher access to the FalkorDB series graph (graph name: neon_courier).",
        },
    ]
    if token := os.environ.get("BRIGHTDATA_API_TOKEN"):
        servers.append({
            "type": "remote", "name": "brightdata", "url": "https://mcp.brightdata.com/mcp",
            "auth": {"type": "header", "headers": {"Authorization": f"Bearer {token}"}},
            "description": "Bright Data: search the web and scrape TikTok/YouTube posts and comments.",
        })
    for manifest in servers:
        call("PUT", "/settings/mcp-servers", {"manifest": manifest})
        tools = call("GET", f"/mcp-servers/{manifest['name']}/tools")
        names = [t.get("name") for t in tools.get("data", tools if isinstance(tools, list) else [])]
        print(f"mcp server: {manifest['name']} ({len(names)} tools)")
    approvals = {"throughline": ["fix_script_line", "generate_shot", "render_episode"], "falkordb": ["@write", "@destructive"], "brightdata": []}
    return [
        {"name": s["name"], "enable_tools": ["@all"], "require_approval_for_tools": approvals[s["name"]], "preload": s["name"] == "throughline"}
        for s in servers
    ]


def sandbox() -> bool:
    key = os.environ.get("DAYTONA_API_KEY")
    if not key:
        print("sandbox: skipped (DAYTONA_API_KEY not set)")
        return False
    call("PUT", "/settings/sandbox-providers", {"manifest": {
        "type": "daytona", "auth": {"api_key": key}, "exec_timeout_ms": 300_000,
        "auto_stop_interval_in_minutes": 15, "auto_archive_interval_in_minutes": 60, "auto_delete_interval_in_minutes": 120,
    }})
    print("sandbox: daytona")
    return True


def skills(enabled: bool) -> list[dict]:
    repo = os.environ.get("THROUGHLINE_SKILLS_REPO")
    if not (repo and enabled):
        print("skills: skipped (needs THROUGHLINE_SKILLS_REPO and the sandbox)")
        return []
    names = {
        "series-style": "House style and prompt rules for rendering shots (FLUX.2 keyframes, fal or MachGen video).",
        "episode-structure": "Structure rules for 60-90s vertical episodes; reviewing scripts; planning the next episode.",
    }
    for name, description in names.items():
        call("PUT", "/settings/skills", {"manifest": {
            "type": "git", "name": name, "url": repo, "path": f"skills/{name}", "ref": os.environ.get("THROUGHLINE_SKILLS_REF", "main"),
            "description": description,
        }})
    print(f"skills: {', '.join(names)}")
    return [{"name": n} for n in names]


def agent(model: str, servers: list[dict], skill_refs: list[dict], sandbox_on: bool) -> None:
    body = {
        "name": AGENT,
        "description": "Throughline for an AI-generated video series: checks continuity across episodes, compiles shot prompts, renders shots, reads the audience.",
        "manifest": {
            "model": {"name": model, "params": {"reasoning_effort": "medium"}},
            "instructions": INSTRUCTIONS,
            "mcp_servers": servers,
            "skills": skill_refs,
            "config": {"sandbox": {"enabled": sandbox_on}, "dynamic_sub_agents": {"enabled": True}},
        },
    }
    existing = next((a for a in call("GET", "/agents").get("data", []) if a.get("name") == AGENT), None)
    if existing:
        call("PUT", f"/agents/{existing['id']}", {k: v for k, v in body.items() if k != "name"})  # name is fixed at creation
        print(f"agent: updated {AGENT} ({existing['id']})")
    else:
        created = call("POST", "/agents", body)
        print(f"agent: created {AGENT} ({created.get('data', created).get('id')})")


if __name__ == "__main__":
    model = model_provider()
    servers = mcp_servers()
    sandbox_on = sandbox()
    agent(model, servers, skills(sandbox_on), sandbox_on)
    print(f"\nOpen {TRUEFORGE} and pick the '{AGENT}' agent.")
