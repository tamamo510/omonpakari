"""気圧との相関の道具 omonpakari_correlate。

溜めた気圧の点（実測を先に、無ければその時刻の予報）から1時間ごとの線を組み、
各時間に「低い」「下がっている」の札を付ける。

- 母の数：線が覆えた全部の時間のうち、それぞれの札が付いた時間の割合。
- 出来事の割合：記録のうち、その札の時間に入った割合。
- 二つの比（lift）と、出来事の前の変化の平均と、全時間の変化の平均。

元の数をそのまま返す。医の見立ての文は付けない。数が足りない時はそう札を返す。
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Callable, Optional

from . import cycle, storage
from .journal import KINDS, MAX_LABEL
from .pressure import MAX_GAP_H
from .settings import Settings

logger = logging.getLogger(__name__)

TOOL_NAME = "omonpakari_correlate"
MIN_EVENTS = 5
MIN_EVENT_DAYS = 3        # 一度の重い日に固まった記録だけで比を言わない
MIN_HOURS = 72
SMALL_SAMPLE_DAYS = 10    # これより少ない日数の比は、早い見えとして札を付ける
MIN_DROP_HPA = 0.5
MAX_DROP_HPA = 50.0
MAX_LABELS = 8

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Compare the user's journal entries with barometric pressure, only when the user asks. "
        "Counts how often entries fell in low-pressure or falling-pressure hours versus how common "
        "those hours were overall (baseline), with raw counts. Returns insufficient_data when there "
        "is too little history. Numbers only; not a diagnosis."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "label": {"type": "string", "description": "Only entries with this label (the user's own words)."},
            "kind": {"type": "string", "enum": [*KINDS, "any"], "default": "symptom",
                     "description": "Which entries to compare (default symptom)."},
            "days": {"type": "integer", "minimum": 7, "maximum": 365, "default": 60,
                     "description": "How many days back to look (7-365, default 60)."},
            "window_h": {"type": "integer", "minimum": 1, "maximum": 72, "default": 12,
                         "description": "Hours before each moment used to measure a pressure change (default 12)."},
            "drop_hpa": {"type": "number", "minimum": 0.5, "maximum": 50,
                         "description": "A drop of at least this many hPa within window_h counts as falling. "
                         "Defaults to the configured drop scaled to window_h."},
            "observed_only": {"type": "boolean", "default": False,
                              "description": "Use only measured pressure, not past forecast points."},
        },
        "additionalProperties": False,
    },
}


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _share(part: int, whole: int) -> Optional[float]:
    return round(part / whole, 3) if whole else None


def _ratio(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or not b:
        return None
    return round(a / b, 2)


def _mean(values: list[float]) -> Optional[float]:
    return round(sum(values) / len(values), 2) if values else None


def _tally(series, times: list[int], low_hpa: float, window_s: int, drop: float,
           tz_mins: Optional[list[int]] = None) -> dict:
    """時刻の並びに札を付けて数える。tz_mins を渡せば、気圧の見えた記録の地の日付も数える。"""
    out = {"with_pressure": 0, "in_low": 0, "change_known": 0, "in_falling": 0, "_deltas": [],
           "_days": set()}
    for i, t in enumerate(times):
        p = storage.interpolate(series, t, MAX_GAP_H)
        if p is None:
            continue
        out["with_pressure"] += 1
        if tz_mins is not None:
            out["_days"].add((t + int(tz_mins[i] or 0) * 60) // 86400)
        if p < low_hpa:
            out["in_low"] += 1
        q = storage.interpolate(series, t - window_s, MAX_GAP_H)
        if q is None:
            continue
        out["change_known"] += 1
        out["_deltas"].append(p - q)
        if p - q <= -drop:
            out["in_falling"] += 1
    return out


def run(settings: Settings, args: dict) -> dict:
    args = args if isinstance(args, dict) else {}
    th = settings.thresholds()
    days = _clamp_int(args.get("days"), 60, 7, 365)
    window_h = _clamp_int(args.get("window_h"), 12, 1, 72)
    window_s = window_h * 3600
    kind = args.get("kind") or "symptom"
    if kind not in (*KINDS, "any"):
        return {"ok": False, "reason": "invalid_kind", "allowed": [*KINDS, "any"]}
    drop_arg = args.get("drop_hpa")
    if drop_arg is not None and not isinstance(drop_arg, bool):
        try:
            drop = float(drop_arg)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "invalid_drop_hpa"}
        if not math.isfinite(drop) or drop <= 0:
            return {"ok": False, "reason": "invalid_drop_hpa"}
        drop = max(MIN_DROP_HPA, min(MAX_DROP_HPA, drop))
    else:
        # 短い窓に割り戻すと、計りの揺れほどの差まで「下がり」と数えるので、床を置く
        drop = max(MIN_DROP_HPA, round(th.drop_hpa * window_h / th.window_h, 2))
    label = args.get("label")
    if label is not None and (not isinstance(label, str) or len(label) > MAX_LABEL):
        return {"ok": False, "reason": "invalid_label", "max": MAX_LABEL}
    observed_only = args.get("observed_only") is True

    now = storage.now_utc()
    start = now - days * 86400
    try:
        with storage.connect() as conn:
            series = storage.load_series(conn, start - window_s - int(MAX_GAP_H * 3600), now, observed_only)
            where, params = ["at_utc BETWEEN ? AND ?"], [start, now]
            if kind != "any":
                where.append("kind = ?")
                params.append(kind)
            if label and label.strip():
                where.append("label_key = ?")
                params.append(storage.label_key(label))
            events = conn.execute(
                f"SELECT at_utc, label_key, label, tz_min FROM journal WHERE {' AND '.join(where)} "
                "ORDER BY at_utc", params).fetchall()
    except Exception as exc:
        logger.debug("omonpakari: correlate storage error (%s)", type(exc).__name__)
        return {"ok": False, "reason": "storage_unavailable"}

    first_hour = -(-start // 3600) * 3600
    hours = list(range(first_hour, now + 1, 3600))
    base = _tally(series, hours, th.low_hpa, window_s, drop)
    ev = _tally(series, [int(e[0]) for e in events], th.low_hpa, window_s, drop,
                tz_mins=[int(e[3] or 0) for e in events])
    used = [p for p in series if p[0] >= start]
    entry_days = len(ev["_days"])
    counts = {
        "entries": len(events),
        "entries_with_pressure": ev["with_pressure"],
        "entry_days_with_pressure": entry_days,
        "hours": len(hours),
        "hours_with_pressure": base["with_pressure"],
        "observed_points": sum(1 for p in used if p[2] == "observed"),
        "forecast_points": sum(1 for p in used if p[2] == "forecast"),
    }
    settings_used = {"kind": kind, "label": label, "days": days, "window_h": window_h,
                     "drop_hpa": drop, "low_hpa": th.low_hpa, "observed_only": observed_only}
    if (ev["with_pressure"] < MIN_EVENTS or entry_days < MIN_EVENT_DAYS
            or base["with_pressure"] < MIN_HOURS):
        return {"ok": True, "insufficient_data": True, "counts": counts,
                "needed": {"entries_with_pressure": MIN_EVENTS, "entry_days_with_pressure": MIN_EVENT_DAYS,
                           "hours_with_pressure": MIN_HOURS},
                "settings": settings_used}

    # 変化の見えた数が少ない側の割合は出さない（1件のうち1件を 100% と言わない）
    falling_known = ev["change_known"] >= MIN_EVENTS and base["change_known"] >= MIN_HOURS
    baseline = {"low_share": _share(base["in_low"], base["with_pressure"]),
                "falling_share": _share(base["in_falling"], base["change_known"]) if falling_known else None,
                "change_known_hours": base["change_known"]}
    entries = {"in_low": ev["in_low"], "in_falling": ev["in_falling"], "change_known": ev["change_known"],
               "low_share": _share(ev["in_low"], ev["with_pressure"]),
               "falling_share": _share(ev["in_falling"], ev["change_known"]) if falling_known else None}
    out: dict[str, Any] = {
        "ok": True,
        "insufficient_data": False,
        "small_sample": entry_days < SMALL_SAMPLE_DAYS,
        "counts": counts,
        "baseline": baseline,
        "entries": entries,
        "lift": {"low": _ratio(entries["low_share"], baseline["low_share"]),
                 "falling": _ratio(entries["falling_share"], baseline["falling_share"])},
        "mean_change_hpa": {"before_entries": _mean(ev["_deltas"]), "all_hours": _mean(base["_deltas"])},
        "settings": settings_used,
        "note": "Counts only. Small samples are noisy, and pressure is one factor among many.",
    }
    # 生理の時期ごとの数も添える（巡りが2回以上見えている時だけ）。
    try:
        with storage.connect() as conn:
            marks = cycle.load_marks(conn)
    except Exception:
        marks = []
    summary = cycle.summarize(marks, now) if marks else {}
    if summary.get("cycles_observed", 0) >= 2:
        phase_counts: dict[str, int] = {}
        for at_utc, _key, _text, _tz in events:
            phase = cycle.phase_at(marks, int(at_utc))
            if phase:
                phase_counts[phase] = phase_counts.get(phase, 0) + 1
        day_counts: dict[str, int] = {}
        total_days = 0
        day = start
        while day <= now:
            phase = cycle.phase_at(marks, day + 12 * 3600)
            if phase:
                day_counts[phase] = day_counts.get(phase, 0) + 1
                total_days += 1
            day += 86400
        if phase_counts and total_days:
            total_events = sum(phase_counts.values())
            out["by_phase"] = {
                phase: {"entries": count,
                        "share_of_entries": _share(count, total_events),
                        "share_of_days": _share(day_counts.get(phase, 0), total_days)}
                for phase, count in sorted(phase_counts.items(), key=lambda kv: -kv[1])}
    if kind == "any" or not (label and label.strip()):
        groups: dict[str, dict] = {}
        for at_utc, key, text, _tz in events:
            groups.setdefault(key, {"label": text, "times": []})["times"].append(int(at_utc))
        ranked = sorted(groups.values(), key=lambda g: -len(g["times"]))[:MAX_LABELS]
        out["by_label"] = []
        for group in ranked:
            tally = _tally(series, group["times"], th.low_hpa, window_s, drop)
            out["by_label"].append({"label": group["label"], "entries": len(group["times"]),
                                    "with_pressure": tally["with_pressure"], "in_low": tally["in_low"],
                                    "in_falling": tally["in_falling"], "change_known": tally["change_known"]})
    return out


def make_handler(settings: Settings) -> Callable[..., str]:
    def handle_correlate(args: dict, **kwargs: Any) -> str:
        try:
            return json.dumps(run(settings, args), ensure_ascii=False)
        except Exception as exc:
            logger.debug("omonpakari: correlate tool failed (%s)", type(exc).__name__)
            return json.dumps({"ok": False, "reason": "internal_error"})

    return handle_correlate
