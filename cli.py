"""CLI wrapper around reward_core — used by the Claude Code `/reward-suggest`
skill. Prints the report JSON to stdout.

    python cli.py --team SLS --from 2026-04-21 --to 2026-05-21 [--score]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

import reward_core as rc


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Reward data for a team/interval")
    ap.add_argument("--team", required=True)
    ap.add_argument("--from", dest="date_from", default=None, help="YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", default=None, help="YYYY-MM-DD")
    ap.add_argument("--project", dest="project_hint", default=rc.DEFAULT_PROJECT)
    ap.add_argument("--score", action="store_true",
                    help="add pure-output score/rank/priority/flags (off by "
                         "default; turn on once weights are decided)")
    a = ap.parse_args()
    out = asyncio.run(rc.report(a.team, a.date_from, a.date_to,
                                a.score, a.project_hint))
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
