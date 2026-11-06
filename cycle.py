"""生理の巡りの道具 omonpakari_cycle。

始まりと終わりの印を刻み、巡りの長さと揺れを数え、次の窓を「範囲」で返す。
不順な巡りに単発の予定日を返すのは嘘になるので、平均と最短・最長から幅を持たせる。
時期（フェーズ）は見立てとして返し、断定の文は作らない。診断も警告もしない。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from . import storage
from .settings import Settings

logger = logging.getLogger(__name__)

TOOL_NAME = "omonpakari_cycle"
MAX_PAST_DAYS = 3650
DEFAULT_DURATION_DAYS = 5     # 終わりの印が無い時に使う、生理の長さの仮の値
MIN_CYCLES_FOR_RANGE = 2      # この数だけ巡りが見えて、初めて窓を範囲で言う

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "The user's menstrual cycle, in their own records. action=mark stores a period start or end "
        "the user mentioned. action=status returns the observed cycle lengths (average, shortest, "
        "longest), the next window as a RANGE of dates (never a single predicted day), and the "
        "current phase as a tentative estimate. action=delete removes one mark by id. "
        "Estimates only; not a diagnosis, not contraception advice."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["mark", "status", "delete"],
                       "description": "What to do."},
            "mark": {"type": "string", "enum": ["start", "end"],
                     "description": "For mark: which end of a period."},
            "at": {"type": "string", "description": "When, ISO 8601 with the user's UTC offset; "
                   "without an offset it is read in the time zone set for Hermes. Defaults to now."},
            "note": {"type": "string", "description": "Optional short note (max 280 characters)."},
            "id": {"type": "integer", "minimum": 1, "description": "Mark id for delete."},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


def _error(reason: str, **extra: Any) -> dict:
    return {"ok": False, "reason": reason, **extra}


def _day(at_utc: int, tz_min: int) -> int:
    """その人の帯での日数（epoch 日）。"""
    return (int(at_utc) + int(tz_min) * 60) // 86400


def _date_text(day: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(day * 86400, tz=timezone.utc).strftime("%Y-%m-%d")


def load_marks(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id, mark, at_utc, tz_min, note FROM cycle_marks ORDER BY at_utc, id").fetchall()
    return [{"id": int(r[0]), "mark": r[1], "at_utc": int(r[2]), "tz_min": int(r[3]),
             "note": r[4]} for r in rows]


def summarize(marks: list[dict], now: Optional[int] = None) -> dict:
    """印の並びから、巡りの数と時期の見立てを組む。純粋な計算（試験で直接確かめる）。"""
    now = storage.now_utc() if now is None else now
    starts = [m for m in marks if m["mark"] == "start"]
    out: dict[str, Any] = {"marks": len(marks), "periods_seen": len(starts)}
    if not starts:
        out["insufficient_data"] = True
        return out
    last = starts[-1]
    tz = last["tz_min"]
    today = _day(now, storage.local_tz_min(now))
    last_start_day = _day(last["at_utc"], last["tz_min"])
    out["last_start"] = _date_text(last_start_day)
    out["days_since_start"] = today - last_start_day

    # 巡りの長さ：連続する始まりの間隔。終わりの長さ：同じ巡りの終わりが次の始まりより前にあれば。
    lengths: list[int] = []
    durations: list[int] = []
    for i, s in enumerate(starts):
        sday = _day(s["at_utc"], s["tz_min"])
        if i + 1 < len(starts):
            lengths.append(_day(starts[i + 1]["at_utc"], starts[i + 1]["tz_min"]) - sday)
        for m in marks:
            if m["mark"] == "end" and m["at_utc"] > s["at_utc"]:
                if i + 1 < len(starts) and m["at_utc"] >= starts[i + 1]["at_utc"]:
                    break
                durations.append(_day(m["at_utc"], m["tz_min"]) - sday + 1)
                break
    if durations:
        out["duration_days_avg"] = round(sum(durations) / len(durations), 1)
    if not lengths:
        out["insufficient_data"] = True
        out["phase"] = phase_at(marks, now, out)
        return out
    avg = sum(lengths) / len(lengths)
    out["cycles_observed"] = len(lengths)
    out["cycle_length_avg"] = round(avg, 1)
    out["cycle_length_min"] = min(lengths)
    out["cycle_length_max"] = max(lengths)
    low, high = (min(lengths), max(lengths)) if len(lengths) >= MIN_CYCLES_FOR_RANGE else (
        round(avg) - 3, round(avg) + 3)
    out["next_window"] = {"earliest": _date_text(last_start_day + low),
                         "latest": _date_text(last_start_day + high),
                         "range_based_on": len(lengths)}
    out["phase"] = phase_at(marks, now, out)
    return out


def phase_at(marks: list[dict], at_utc: int, summary: Optional[dict] = None) -> Optional[str]:
    """時刻 t の時期の見立て。巡りが読めない時は None。"""
    starts = [m for m in marks if m["mark"] == "start"]
    if not starts:
        return None
    prior = [s for s in starts if s["at_utc"] <= at_utc]
    if not prior:
        return None
    last = prior[-1]
    tz = storage.local_tz_min(at_utc)
    days = _day(at_utc, tz) - _day(last["at_utc"], last["tz_min"])
    summary = summary if summary is not None else summarize(marks, at_utc)
    duration = summary.get("duration_days_avg") or DEFAULT_DURATION_DAYS
    if days < 0:
        return None
    if days < duration:
        return "menstrual (estimate)"
    avg = summary.get("cycle_length_avg")
    if avg is None:
        return "after period, cycle length unknown (estimate)"
    earliest_next = summary.get("next_window", {}).get("earliest")
    next_low_day = None
    if earliest_next:
        from datetime import datetime, timezone
        next_low_day = int(datetime.strptime(earliest_next, "%Y-%m-%d")
                           .replace(tzinfo=timezone.utc).timestamp() // 86400)
    today = _day(at_utc, tz)
    if next_low_day is not None and today >= next_low_day:
        return "next period window open (estimate)"
    if days < avg - 16:
        return "follicular (estimate)"
    if days <= avg - 12:
        return "around ovulation (estimate)"
    if next_low_day is not None and today >= next_low_day - 7:
        return "late luteal / premenstrual (estimate)"
    return "luteal (estimate)"


def status(conn, now: Optional[int] = None) -> dict:
    now = storage.now_utc() if now is None else now
    marks = load_marks(conn)
    out = summarize(marks, now)
    out["ok"] = True
    out["recent_marks"] = [
        {"id": m["id"], "mark": m["mark"], "at": storage.fmt_at(m["at_utc"], m["tz_min"]),
         **({"note": m["note"]} if m["note"] else {})} for m in marks[-6:]]
    return out


def _mark(conn, args: dict) -> dict:
    mark = args.get("mark")
    if mark not in ("start", "end"):
        return _error("invalid_mark", allowed=["start", "end"])
    note = args.get("note")
    if note is not None:
        if not isinstance(note, str):
            return _error("invalid_note")
        note = " ".join(note.split())[:280] or None
    now = storage.now_utc()
    if args.get("at"):
        try:
            at_utc, tz_min = storage.parse_at(args["at"])
        except (ValueError, TypeError, OverflowError, OSError):
            return _error("invalid_at")
        if at_utc > now + 300:
            return _error("at_in_future")
        if at_utc < now - MAX_PAST_DAYS * 86400:
            return _error("at_too_old", max_days=MAX_PAST_DAYS)
    else:
        at_utc, tz_min = now, storage.local_tz_min(now)
    with conn:
        cursor = conn.execute(
            "INSERT INTO cycle_marks (mark, at_utc, tz_min, note, created_utc) VALUES (?, ?, ?, ?, ?)",
            (mark, at_utc, tz_min, note, now))
    out = status(conn, now)
    out["marked"] = {"id": int(cursor.lastrowid), "mark": mark,
                     "at": storage.fmt_at(at_utc, tz_min)}
    return out


def _delete(conn, args: dict) -> dict:
    entry_id = args.get("id")
    if isinstance(entry_id, bool) or not isinstance(entry_id, int) or entry_id < 1:
        return _error("id_required")
    with conn:
        cursor = conn.execute("DELETE FROM cycle_marks WHERE id = ?", (entry_id,))
    if cursor.rowcount == 0:
        return _error("not_found", id=entry_id)
    return {"ok": True, "deleted": entry_id}


_ACTIONS = {"status": lambda conn, args: status(conn), "mark": _mark, "delete": _delete}


def run(args: dict) -> dict:
    args = args if isinstance(args, dict) else {}
    action = _ACTIONS.get(args.get("action"))
    if action is None:
        return _error("invalid_action", allowed=list(_ACTIONS))
    try:
        with storage.connect() as conn:
            return action(conn, args)
    except Exception as exc:
        logger.debug("omonpakari: cycle storage error (%s)", type(exc).__name__)
        return _error("storage_unavailable")


def make_handler(settings: Settings) -> Callable[..., str]:
    def handle_cycle(args: dict, **kwargs: Any) -> str:
        try:
            return json.dumps(run(args), ensure_ascii=False)
        except Exception as exc:
            logger.debug("omonpakari: cycle tool failed (%s)", type(exc).__name__)
            return json.dumps({"ok": False, "reason": "internal_error"})

    return handle_cycle
