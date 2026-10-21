from __future__ import annotations

import json
import os
import uuid
from typing import Any


def _enabled() -> bool:
    value = (os.getenv("WALLETWISE_TRACE") or "true").strip().casefold()
    return value in {"1", "true", "yes", "on"}


def new_trace_id() -> str:
    return uuid.uuid4().hex[:8].upper()


def header(trace_id: str, title: str) -> None:
    if not _enabled():
        return
    print("\n" + "=" * 86, flush=True)
    print(f"[WALLY {trace_id}] {title}", flush=True)
    print("=" * 86, flush=True)


def line(trace_id: str, text: str = "") -> None:
    if not _enabled():
        return
    prefix = f"[WALLY {trace_id}] " if text else ""
    print(prefix + text, flush=True)


def section(trace_id: str, title: str, value: Any | None = None) -> None:
    if not _enabled():
        return
    print(f"\n[WALLY {trace_id}] --- {title} ---", flush=True)
    if value is None:
        return
    if isinstance(value, str):
        print(value, flush=True)
        return
    try:
        print(json.dumps(value, indent=2, ensure_ascii=False, default=str), flush=True)
    except Exception:
        print(str(value), flush=True)


def path(trace_id: str, *steps: str) -> None:
    if not _enabled():
        return
    print(f"\n[WALLY {trace_id}] PATH", flush=True)
    print("  " + "  ->  ".join(steps), flush=True)


def footer(trace_id: str, title: str = "REQUEST COMPLETE") -> None:
    if not _enabled():
        return
    print("-" * 86, flush=True)
    print(f"[WALLY {trace_id}] {title}", flush=True)
    print("=" * 86 + "\n", flush=True)
