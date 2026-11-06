"""部品：生理の波。

巡りの位相（どの時期か）と、次の窓までの距離を、波の状態として返す。
不順な巡りでは単発の日を言わない。範囲のまま持つ。
"""

from __future__ import annotations

from typing import Optional

from . import cycle

NAME = "cycle"


def state(settings, conn, now: int) -> Optional[dict]:
    marks = cycle.load_marks(conn)
    if not marks:
        return None
    summary = cycle.summarize(marks, now)
    if not summary.get("phase"):
        return None
    out = {"phase": summary["phase"], "days_since_start": summary.get("days_since_start")}
    window = summary.get("next_window")
    if window:
        out["next_window"] = window
    if summary.get("cycle_length_avg"):
        out["cycle_length_avg"] = summary["cycle_length_avg"]
    return out
