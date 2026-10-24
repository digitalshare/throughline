# Throughline

A graph-powered series bible for AI-generated video series. Episode scripts become a FalkorDB graph. Continuity checks run across every episode before you spend credits rendering, and each finding comes with the graph path that proves it.

See [IDEAS.md](IDEAS.md) for the pitch and the track plan.

## Run it

```sh
docker compose up -d                      # FalkorDB on :6379 (browser UI :3000) + FalkorDB MCP on :8080
cd server && uv sync && uv run pytest

# Throughline MCP server + web viewer on http://127.0.0.1:8770 (works on a copy in workspace/)
uv run python -m throughline.mcp_server

# TrueForge on http://localhost:8790, allowed to reach the local MCP servers (and nothing else private)
OUTBOUND_URL_ALLOWED_HOSTS='["127.0.0.1"]' npx @truefoundry/trueforge@latest

# Register models, MCP servers, sandbox, skills and the agent (reads keys from the environment)
uv run --project server python trueforge/setup.py
```

Then open TrueForge, pick the **throughline** agent and ask "Check episode 4 before we render it." Fixes and renders pause for approval.

CLI without the agent:

```sh
uv run python -m throughline ingest ../samples/neon_courier
uv run python -m throughline check --episode 4
uv run python -m throughline render --episode 1          # prints the cost estimate only
uv run python -m throughline render --episode 1 --yes    # renders missing shots, assembles workspace/.../episodes/ep1.mp4
```

Renders keep characters consistent: each character gets one FLUX.2 reference image per outfit (a new outfit is drawn from their existing reference), each shot's keyframe uses the outfit the wardrobe timeline says they wear in that scene, and the keyframe is animated by Kling v3 on fal (or MachGen). Re-running a render only redoes shots without a clip.

To reset the demo, delete `workspace/` and restart the MCP server.

| Env var | Used for |
|---|---|
| `OPENAI_API_KEY` | extraction (`gpt-5.5`), comment linking, the TrueForge agent |
| `BFL_API_KEY` | FLUX.2 character reference images and shot keyframes |
| `FAL_KEY` | image hosting and image-to-video (Kling v3 by default) |
| `MACHGEN_API_KEY` | alternative video provider (`provider="machgen"`) |
| `BRIGHTDATA_API_TOKEN` | TikTok/YouTube posts, comments and trends; Bright Data MCP in TrueForge |
| `DAYTONA_API_KEY` | TrueForge sandbox (needed for skills) |
| `THROUGHLINE_SKILLS_REPO` | GitHub URL holding `skills/`, so TrueForge can load them |

`ingest --live` re-extracts every episode with OpenAI (needs `OPENAI_API_KEY`, model set by `THROUGHLINE_MODEL`, default `gpt-5.5`). Without it, the cached extraction in `samples/*/extracted/` is used, so the demo is deterministic.

## How it fits together

- `server/throughline/fountain.py`, `extract.py`: script → scenes → OpenAI structured extraction (cached per episode; a fix re-extracts only the edited scene)
- `graph.py`: the series bible in FalkorDB, with provenance (episode, scene, line, quote) on every story edge; findings and audience data survive reloads
- `checks.py`: the continuity checks, each returning the path that proves it
- `prompts.py`, `video.py`: shot prompts compiled from the graph with sources; renders: FLUX.2 keyframes from character references, then fal or MachGen image-to-video
- `audience.py`: Bright Data trigger/poll/download client, comment linking, trends, report
- `mcp_server.py`: the agent's tools (fixes and renders are annotated destructive, so TrueForge asks first), REST API and viewer
- `trueforge/`: agent instructions and the setup script; `skills/`: the agent's skills

## Checks

| Check | Example from the sample series |
|---|---|
| Knowledge leak (multi-hop, time-ordered `MET` paths) | Juno knows the name "Elena", which only Voss ever heard |
| Wardrobe jump | Mira lost the red scarf she has worn since E2S1 |
| Prop resurrection | The motorbike exploded in E2S2 and is back in E4S2 |
| Ghost | Rook died in E3S2 and walks into E4S3 |
| Dangling cliffhanger / setup | The tower signal from E2S3 is never resolved |
