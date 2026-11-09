"""Module-level helpers for tests/test_tools_base.py.

``run_with_timeout(mode="process")`` pickles the function by reference, and the
spawned child imports this module, so it stays free of pytest and heavy imports.
"""

from __future__ import annotations

import time

from pydantic import BaseModel

from tfl_deep_agent.tools.base import ToolArgs, ToolResult, ToolUnsupported


def double(x: int) -> int:
    return 2 * x


def sleep_then(seconds: float, value: str = "late") -> str:
    time.sleep(seconds)
    return value


def raise_unsupported() -> None:
    raise ToolUnsupported("no oracle for this kind")


def raise_value_error() -> None:
    raise ValueError("bad grammar")


class SleepArgs(ToolArgs):
    seconds: float


def sleepy_tool(args: SleepArgs) -> ToolResult:
    """Sleep for the given number of seconds (test tool)."""
    time.sleep(args.seconds)
    return ToolResult.success({"slept": args.seconds}, trust="well_formed", reason="slept")


class Inner(BaseModel):
    word: str
