"""Command line entry point.

Phase 0 ships the version command only. Phase 1 adds `demo --verify`, the
self-verifying run that closes Phase 1's exit criteria and gates the v0.1 tag.
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
    parser.add_subparsers(dest="command")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
