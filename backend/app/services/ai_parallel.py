"""Run independent provider calls concurrently (fast reading, docs/v12/11-fast-reading.md).

A long read is OUTPUT-bound (≈150 tok/s on GLM-5.3-Flash): splitting it into field groups that run at the
same time makes the wall clock the slowest group instead of the sum. Calls are plain blocking functions
(the OpenAI/LangChain clients are thread-safe per call), so a small thread pool is enough — no asyncio.

Every call's outcome is returned, never raised: the caller decides whether one failed group sinks the
read (core) or only empties its section (technical).
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

#: Upper bound on simultaneous provider calls from ONE request.
MAX_PARALLEL_CALLS = 5


@dataclass
class GroupOutcome:
    """The result of one named call: ``value`` on success, ``error`` otherwise."""

    name: str
    value: Any = None
    error: BaseException | None = None
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None


def run_groups(
    calls: Mapping[str, Callable[[], Any]], *, max_workers: int = MAX_PARALLEL_CALLS
) -> dict[str, GroupOutcome]:
    """Run ``calls`` concurrently and return ``{name: GroupOutcome}`` in the input order.

    A single call runs inline (no pool) so tests and one-group reads stay simple.
    """

    def _timed(name: str, fn: Callable[[], Any]) -> GroupOutcome:
        started = time.monotonic()
        try:
            return GroupOutcome(name=name, value=fn(), seconds=time.monotonic() - started)
        except BaseException as exc:  # noqa: BLE001 - reported to the caller, never swallowed silently
            logger.warning("AI group %s failed after %.1fs: %s", name, time.monotonic() - started, exc)
            return GroupOutcome(name=name, error=exc, seconds=time.monotonic() - started)

    items = list(calls.items())
    if len(items) <= 1:
        return {name: _timed(name, fn) for name, fn in items}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as pool:
        futures = {name: pool.submit(_timed, name, fn) for name, fn in items}
        return {name: futures[name].result() for name, _ in items}
