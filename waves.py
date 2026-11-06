"""波として見守る層 omonpakari_waves。

体調は点ではなく波だ。気圧のうねり、生理の巡りの波、症状の寄せと引き、
そういうリズムが重なって「今日」ができている。この層は裏側で波の状態
（位相と向き）を合成して持つ。表に出すための数値の読み上げではない。
声かけは通知ではなく、その人の一日に置かれる言葉として出すための材料。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from . import cycle, pressure, storage
from .settings import Settings

logger = logging.getLogger(__name__)

TOOL_NAME = "omonpakari_waves"
RECENT_DAYS = 7
MAX_SYMPTOM_WAVES = 3

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "The composed wave states behind the scenes: pressure (level, direction, what is ahead), "
        "cycle (phase, distance to the next window), and symptom rhythms (whether a recurring "
        "symptom has been more or less present this week than last, with counts). States and "
        "directions, not verdicts. Use these to understand today before any topic - and if you "
        "speak first, speak from the waves in ordinary words fitted to the person's day, never "
        "as a notification or a readout of values."
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}


def pressure_wave(conn, settings: Settings, now: int) -> Optional[dict]:
    th = settings.thresholds()
    saved = settings.location()
    observed = storage.latest_observed(conn, storage.place_key(*saved) if saved else None)
    if observed is None or now - observed["fetched_at_utc"] > 72 * 3600:
        return None
    place = observed["place_key"]
    series = storage.load_series(conn, now - 6 * 3600, now + 6 * 3600, place=place)
    guess = storage.interpolate(series, now, pressure.MAX_GAP_H)
    now_hpa = observed["hpa"] if now - observed["at_utc"] <= 3 * 3600 else guess
    if now_hpa is None:
        return None
    forecast = storage.latest_forecast(conn, place, now)
    past = storage.load_series(conn, now - (th.window_h + 6) * 3600, now, place=place)
    result = pressure.assess(now, now_hpa, forecast, past, th)
    state = {"level": result.level, "trend": result.trend, "falling_fast": result.falling_fast}
    if result.ahead_delta is not None:
        state["ahead"] = ("falling" if result.ahead_delta < -0.5
                          else "rising" if result.ahead_delta > 0.5 else "steady")
        state["ahead_hours"] = result.ahead_span_h
    if result.recent_delta is not None:
        state["recent"] = ("fell" if result.recent_delta < -0.5
                            else "rose" if result.recent_delta > 0.5 else "steady")
    return state


def cycle_wave(conn, now: int) -> Optional[dict]:
    marks = cycle.load_marks(conn)
    if not marks:
        return None
    summary = cycle.summarize(marks, now)
    if not summary.get("phase"):
        return None
    state = {"phase": summary["phase"], "days_since_start": summary.get("days_since_start")}
    window = summary.get("next_window")
    if window:
        state["next_window"] = window
    if summary.get("cycle_length_avg"):
        state["cycle_length_avg"] = summary["cycle_length_avg"]
    return state


def symptom_waves(conn, now: int) -> list[dict]:
    """症状ごとに、今週と先週の寄せ引きを数える。向きだけを言う。"""
    rows = conn.execute(
        """
        SELECT label_key, label, severity, at_utc FROM journal
        WHERE kind = 'symptom' AND at_utc BETWEEN ? AND ? ORDER BY at_utc
        """,
        (now - 2 * RECENT_DAYS * 86400, now + 300)).fetchall()
    groups: dict[str, dict] = {}
    for key, label, severity, at_utc in rows:
        g = groups.setdefault(key, {"label": label, "recent": 0, "prior": 0,
                                    "recent_sev": [], "prior_sev": []})
        side = "recent" if at_utc > now - RECENT_DAYS * 86400 else "prior"
        g[side] += 1
        if severity is not None:
            g[side + "_sev"].append(severity)
    out = []
    for g in groups.values():
        total = g["recent"] + g["prior"]
        if total < 3:
            continue
        if g["recent"] > g["prior"]:
            direction = "more present this week"
        elif g["recent"] < g["prior"]:
            direction = "easing this week"
        else:
            direction = "about the same"
        entry = {"label": g["label"], "direction": direction,
                 "this_week": g["recent"], "last_week": g["prior"]}
        if g["recent_sev"]:
            entry["avg_severity_this_week"] = round(
                sum(g["recent_sev"]) / len(g["recent_sev"]), 1)
        out.append(entry)
    out.sort(key=lambda e: -(e["this_week"] + e["last_week"]))
    return out[:MAX_SYMPTOM_WAVES]


def compose(settings: Settings, now: Optional[int] = None) -> dict:
    now = storage.now_utc() if now is None else now
    with storage.connect() as conn:
        waves = {"pressure": pressure_wave(conn, settings, now),
                 "cycle": cycle_wave(conn, now),
                 "symptoms": symptom_waves(conn, now)}
    return {"ok": True, "waves": waves,
            "note": "Wave states for understanding today. If you speak, speak in ordinary "
                    "words fitted to the person's day - never as a notification or a readout."}


def run(settings: Settings, args: dict) -> dict:
    try:
        return compose(settings)
    except Exception as exc:
        logger.debug("omonpakari: waves failed (%s)", type(exc).__name__)
        return {"ok": False, "reason": "storage_unavailable"}


def make_handler(settings: Settings) -> Callable[..., str]:
    def handle_waves(args: dict, **kwargs: Any) -> str:
        try:
            return json.dumps(run(settings, args), ensure_ascii=False)
        except Exception as exc:
            logger.debug("omonpakari: waves tool failed (%s)", type(exc).__name__)
            return json.dumps({"ok": False, "reason": "internal_error"})

    return handle_waves
