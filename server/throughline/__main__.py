"""CLI: `python -m throughline ingest <series_dir>` and `python -m throughline check [--episode N]`."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import checks
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
    args = parser.parse_args()

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
