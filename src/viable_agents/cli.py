"""Command line entry point.

``viable-agents demo --verify`` runs the self-verifying Phase 1 demo that closes
the phase's exit criteria and gates the v0.1 tag.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from viable_agents import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="viable-agents",
        description="A multi-agent LLM orchestrator structured as Beer's Viable System Model.",
    )
    parser.add_argument("--version", action="version", version=f"viable-agents {__version__}")
    sub = parser.add_subparsers(dest="command")
    demo = sub.add_parser("demo", help="run the Phase 1 two-agent demo")
    demo.add_argument(
        "--verify",
        action="store_true",
        help="assert the exit criteria and return non-zero if any fails",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "demo":
        from viable_agents.demo import run_demo  # noqa: PLC0415 - lazy: keep --version fast

        return run_demo(verify=args.verify)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
