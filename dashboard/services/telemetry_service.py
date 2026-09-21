"""
dashboard/services/telemetry_service.py — one ai_operations row per AI call.

What this is for: Phase 3.6 sets every quota number in the product, and it is a
measurement step, not a guess. It cannot measure anything that was not recorded,
so the recording has to exist before the numbers are chosen — which is why this
lands with the agent boundary rather than with the calibration itself.

Two rules shape the whole module.

**Telemetry never fails the request it measures.** Every write is wrapped. A
dropped measurement costs a row in a calibration sample; an exception raised
from here would cost the visitor their answer, having already spent their
allowance on it. That trade is never worth making, so failures are logged and
swallowed.

**It is measured where it is metered.** `measure()` is called from the routes,
beside `@require_quota`, rather than from inside the services. The services take
`user_id` as an argument and never reach into request state; giving one of them
a hidden dependency on `flask.g` to emit telemetry would break that, and the
route is where the quota decision already happens, so the metered event and the
measured event are the same event by construction.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator

from repositories import lakebase

logger = logging.getLogger(__name__)


class Operation:
    """
    A single AI operation in flight.

    The caller fills in whatever it learns as it goes — how many LLM turns, how
    many tool calls, which model answered — and `measure()` writes the row when
    the block ends, however it ends.
    """

    __slots__ = ("metric", "tier", "user_id", "provider", "model",
                 "input_tokens", "output_tokens", "estimated_cost_usd",
                 "llm_turns", "tool_calls", "embedding_calls", "ok", "error")

    def __init__(self, metric: str, tier: str, user_id: str | None):
        self.metric = metric
        self.tier = tier
        self.user_id = user_id
        self.provider: str | None = None
        self.model: str | None = None
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.estimated_cost_usd: float | None = None
        self.llm_turns = 0
        self.tool_calls = 0
        self.embedding_calls = 0
        self.ok = True
        self.error: str | None = None


def record(metric: str, tier: str, latency_ms: int, **fields) -> None:
    """Write one row. Never raises."""
    try:
        lakebase.record_ai_operation(
            metric=metric, tier=tier, latency_ms=latency_ms, **fields
        )
    except Exception:
        # debug, not warning: a missing migration would otherwise produce one
        # line per search and drown the log it is meant to be diagnosed from.
        logger.debug("ai_operations write failed for metric=%s", metric, exc_info=True)


@contextmanager
def measure(metric: str, tier: str, user_id: str | None = None,
            **initial) -> Iterator[Operation]:
    """
    Time a block and record it, whether it succeeds or raises.

    A failed operation is recorded with `ok = false` and kept, because failures
    are part of the cost: a request that times out after twenty seconds consumed
    twenty seconds of capacity and has to appear in the latency distribution.
    The exception itself always propagates — this observes, it never handles.
    """
    op = Operation(metric=metric, tier=tier, user_id=user_id)
    for key, value in initial.items():
        setattr(op, key, value)

    started = time.perf_counter()
    try:
        yield op
    except Exception as exc:
        op.ok = False
        # Type and message only. The row is a cost record, not a crash report,
        # and a traceback here could carry a fragment of someone's question.
        op.error = f"{type(exc).__name__}: {exc}"[:500]
        raise
    finally:
        record(
            metric=op.metric,
            tier=op.tier,
            latency_ms=int((time.perf_counter() - started) * 1000),
            user_id=op.user_id,
            provider=op.provider,
            model=op.model,
            input_tokens=op.input_tokens,
            output_tokens=op.output_tokens,
            estimated_cost_usd=op.estimated_cost_usd,
            llm_turns=op.llm_turns,
            tool_calls=op.tool_calls,
            embedding_calls=op.embedding_calls,
            ok=op.ok,
            error=op.error,
        )
