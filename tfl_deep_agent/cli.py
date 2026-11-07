"""``tfl-deep`` command line.

    tfl-deep (TEXT | --file task.pdf) --save DIR [--live] [--auto-approve]

P0 ships the argument contract only; the agent run is wired in P2/P3. Without
``--live`` no request is ever sent to the Anthropic API.
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tfl-deep",
        description="TFL Deep Agent: solve a Theory of Formal Languages task (REG / CFL / DCFL / LL(k)).",
    )
    p.add_argument("text", nargs="?", help="task statement as text")
    p.add_argument("--file", help="task file: .txt / .md / .pdf / .png / .jpg / .webp")
    p.add_argument("--save", metavar="DIR", help="directory for result.{json,md,html}")
    p.add_argument("--template", default="full_proof",
                   choices=["full_proof", "exam_solution", "short_verdict", "lean_appendix"],
                   help="report template (default: full_proof)")
    p.add_argument("--live", action="store_true",
                   help="call the real Anthropic API (spends money; default is mock mode)")
    p.add_argument("--auto-approve", action="store_true",
                   help="approve both HITL points without asking")
    p.add_argument("--settings", metavar="JSON", help="settings file (default: tfl_deep_agent/settings.json)")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.text and not args.file:
        parser.error("give the task as TEXT or --file")
    if args.text and args.file:
        parser.error("give either TEXT or --file, not both")
    print("tfl-deep: the agent run is not implemented yet (phase P0 skeleton).", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
