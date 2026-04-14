"""Application entry point for the Feishu Codex Bot."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import sys

from feishu_codex_bot import __version__
from feishu_codex_bot.config import ConfigError
from feishu_codex_bot.runtime import run_application_sync


def build_parser() -> argparse.ArgumentParser:
    """Create the top-level CLI parser for the application."""
    parser = argparse.ArgumentParser(
        prog="feishu-codex-bot",
        description="Bridge Feishu conversations to a local Codex app server.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        help="Dump all raw Codex callbacks to var/dump.json for debugging.",
    )
    return parser


def run(*, enable_dump: bool = False) -> int:
    """Load runtime configuration and run the application."""
    try:
        return run_application_sync(enable_dump=enable_dump)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and run the application."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return run(enable_dump=args.dump)


if __name__ == "__main__":
    raise SystemExit(main())
