"""CLI: `python -m throughline ingest <series_dir>`, `check [--episode N]`, `render --episode N` and `avatars`."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import checks, video
from .graph import connect, load_series

ICONS = {"error": "✖", "warning": "!", "info": "·"}


def main() -> None:
    parser = argparse.ArgumentParser(prog="throughline")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ingest = sub.add_parser("ingest", help="load a series folder into FalkorDB")
    ingest.add_argument("series_dir", type=Path)
    ingest.add_argument("--graph")
    ingest.add_argument("--live", action="store_true", help="re-extract every episode with OpenAI instead of the cache")
    ingest.add_argument("--wipe", action="store_true", help="also forget findings and audience data")
    check = sub.add_parser("check", help="run continuity checks and store findings in the graph")
    check.add_argument("--graph", default="neon_courier")
    check.add_argument("--episode", type=int)
    check.add_argument("--json", action="store_true")
    render = sub.add_parser("render", help="render an episode's shots and assemble the episode video")
    render.add_argument("--graph", default="neon_courier")
    render.add_argument("--episode", type=int, required=True)
    render.add_argument("--renders", type=Path, default=Path(__file__).resolve().parents[2] / "workspace" / "neon_courier" / "renders")
    render.add_argument("--seconds", type=int, default=5)
    render.add_argument("--provider", choices=["fal", "machgen"], default="fal")
    render.add_argument("--force", action="store_true", help="re-render shots that already have clips")
    render.add_argument("--yes", action="store_true", help="spend the money; without it, only print the estimate")
    avatars = sub.add_parser("avatars", help="make a profile portrait for every character that lacks one")
    avatars.add_argument("--graph", default="neon_courier")
    avatars.add_argument("--renders", type=Path, default=Path(__file__).resolve().parents[2] / "workspace" / "neon_courier" / "renders")
    avatars.add_argument("--yes", action="store_true", help="spend the money; without it, only print the estimate")
    args = parser.parse_args()

    if args.cmd == "avatars":
        g = connect(args.graph)
        missing = [cid for (cid,) in g.query("MATCH (c:Character) RETURN c.id ORDER BY c.id").result_set
                   if not video.avatar_path(args.renders / "avatars", cid).exists()]
        print(f"{len(missing)} portraits to make ({', '.join(missing) or 'none'}), about ${len(missing) * video.IMAGE_COST:.2f} "
              "plus any missing reference images")
        if args.yes:
            for cid in missing:
                try:
                    print(cid, video.character_avatar(g, cid, args.renders))
                except video.RenderError as exc:  # e.g. a provider moderation false positive; the others still run
                    print(cid, "FAILED:", exc)
        elif missing:
            print("dry run; pass --yes to generate")
        return

    if args.cmd == "render":
        g = connect(args.graph)
        estimate = video.estimate_cost(g, args.renders, args.episode, args.seconds, args.provider, args.force)
        print(f"episode {args.episode}: {len(estimate['shots_to_render'])} shots, "
              f"{estimate['new_reference_images']} new reference images, about ${estimate['usd']}")
        if not args.yes:
            print("dry run; pass --yes to render")
            return
        print(json.dumps(video.render_episode(g, args.renders, args.episode, args.seconds, args.provider, args.force), indent=2))
        return

    if args.cmd == "ingest":
        g = load_series(args.series_dir, args.graph, live=args.live, wipe=args.wipe)
        counts = g.query("MATCH (n) RETURN labels(n)[0] AS label, count(n) ORDER BY label").result_set
        print("loaded", g.name, dict(counts))
        return

    g = connect(args.graph)
    findings = checks.run_all(g, args.episode)
    checks.persist(g, findings)
    if args.json:
        print(json.dumps([f.to_dict() for f in findings], indent=2))
        return
    for f in findings:
        print(f"{ICONS[f.severity]} {f.scene} line {f.line}  {f.title}\n    {f.detail}")
    print(f"\n{len(findings)} findings")


if __name__ == "__main__":
    main()
