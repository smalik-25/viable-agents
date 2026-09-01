"""Command line entry point.

``viable-agents demo --verify`` runs the self-verifying Phase 1 demo that closes
the phase's exit criteria and gates the v0.1 tag. ``viable-agents run`` is Phase
2's equivalent: three S1 agents over a live or synthetic CI event stream.
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

    run = sub.add_parser("run", help="run the S1 fleet over a live or synthetic CI event stream")
    run.add_argument("--source", choices=["synthetic", "live"], default="synthetic")
    run.add_argument("--events", type=int, default=500, help="synthetic mode only")
    run.add_argument("--scenario", default="normal-load", help="synthetic mode only")
    run.add_argument(
        "--live-llm",
        action="store_true",
        help="use the real Anthropic client instead of the free scripted classifier",
    )
    run.add_argument("--postgres", action="store_true", help="write rows to Postgres")
    run.add_argument(
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
    if args.command == "run":
        from viable_agents.run import run_fleet  # noqa: PLC0415 - lazy: keep --version fast

        return run_fleet(
            source=args.source,
            events=args.events,
            scenario=args.scenario,
            live_llm=args.live_llm,
            postgres=args.postgres,
            verify=args.verify,
        )
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
