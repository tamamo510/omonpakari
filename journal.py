"""記録の道具 omonpakari_log。

症状、薬やサプリ、ひと言の覚えを、使う人自身の言葉（label）で残す。
決まった症状の名も、決め打ちの薬の名も持たない。言葉の全文は溜めない。
書く時は網に出ず、溜めてある気圧のうち時刻のいちばん近い値を添える。
薬の上限は、使う人が決めた時だけ数として添える。警告の文は作らない。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from . import storage
from .settings import Settings

logger = logging.getLogger(__name__)

TOOL_NAME = "omonpakari_log"
KINDS = ("symptom", "medication", "note")
MAX_LABEL = 80
MAX_NOTE = 280
MAX_DOSE = 40
MAX_LIMIT = 200
MAX_PAST_DAYS = 3650         # これより昔の時刻は刻まない（見返しの days の上限とそろえる）
PRESSURE_MATCH_S = 90 * 60   # 前後90分以内の気圧を添える

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "The user's own health journal. action=add records a symptom, a medication or supplement, or a "
        "short note, in the user's own words, only when the user mentions it. action=list reads entries "
        "back (with counts against any limit the user set). action=delete removes one entry, "
        "action=set_limit stores a limit the user chose for a medication (omit max_count to clear it), "
        "action=purge erases everything (requires confirm=true). No reminders, no advice."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "list", "delete", "set_limit", "purge"],
                       "description": "What to do."},
            "kind": {"type": "string", "enum": list(KINDS),
                     "description": "Entry type for add; optional filter for list."},
            "label": {"type": "string", "description": "The user's own words for it, e.g. 'headache' "
                      "or a medication name (max 80 characters). Required for add and set_limit."},
            "severity": {"type": "integer", "minimum": 1, "maximum": 5,
                         "description": "Optional 1-5, only if the user said how strong it was."},
            "dose": {"type": "string", "description": "Optional dose as the user said it (max 40 characters)."},
            "note": {"type": "string", "description": "Optional short note (max 280 characters). "
                     "Do not paste the whole message."},
            "at": {"type": "string", "description": "When it happened, ISO 8601 with the user's UTC offset "
                   "(e.g. 2027-01-15T09:30-05:00); without an offset it is read in the time zone set for "
                   "Hermes (else the host's local time). Not in the future, at most 10 years back. "
                   "Defaults to now."},
            "since": {"type": "string", "description": "For list: only entries at or after this ISO 8601 time."},
            "days": {"type": "integer", "minimum": 1, "maximum": 3650, "default": 14,
                     "description": "For list without since: how many days back (default 14)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": 50,
                      "description": "For list: maximum entries, newest first (max 200)."},
            "id": {"type": "integer", "minimum": 1, "description": "Entry id for delete."},
            "max_count": {"type": "integer", "minimum": 1, "maximum": 100,
                          "description": "For set_limit: the most doses the user wants within window_h."},
            "window_h": {"type": "integer", "minimum": 1, "maximum": 720,
                         "description": "For set_limit: window in hours (default 24)."},
            "confirm": {"type": "boolean", "description": "Must be true for purge."},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


def _error(reason: str, **extra: Any) -> dict:
    return {"ok": False, "reason": reason, **extra}


def _text(value: Any, limit: int, name: str) -> tuple[Optional[str], Optional[dict]]:
    """任意の文字の欄を読む。長すぎる時は切らずに誤りを返す（使う人の言葉を勝手に削らない）。"""
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, _error(f"invalid_{name}")
    text = " ".join(value.split())
    if len(text) > limit:
        return None, _error(f"{name}_too_long", max=limit)
    return (text or None), None


def _int(value: Any, low: int, high: int) -> Optional[int]:
    """整数として読める時だけ、範囲の内の整数を返す（真偽値と端数は読まない）。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        value = int(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text.lstrip("-").isdigit():
            return None
        value = int(text)
    if not isinstance(value, int):
        return None
    return value if low <= value <= high else None


def _entry(row: tuple) -> dict:
    (entry_id, kind, label, severity, dose, note, at_utc, tz_min, pressure_hpa) = row
    out: dict[str, Any] = {"id": entry_id, "kind": kind, "label": label,
                           "at": storage.fmt_at(at_utc, tz_min)}
    if severity is not None:
        out["severity"] = severity
    if dose:
        out["dose"] = dose
    if note:
        out["note"] = note
    if pressure_hpa is not None:
        out["pressure_hpa"] = round(pressure_hpa, 1)
    return out


_ENTRY_COLUMNS = "id, kind, label, severity, dose, note, at_utc, tz_min, pressure_hpa"


def nearest_pressure(conn, at_utc: int) -> Optional[float]:
    """前後90分以内でいちばん近い気圧（実測を先に、無ければ予報）。網には出ない。"""
    for kinds in (("observed",), ("observed", "forecast")):
        marks = ",".join("?" for _ in kinds)
        row = conn.execute(
            f"""
            SELECT hpa FROM pressure_samples
            WHERE kind IN ({marks}) AND at_utc BETWEEN ? AND ?
            ORDER BY ABS(at_utc - ?), fetched_at_utc DESC LIMIT 1
            """,
            (*kinds, at_utc - PRESSURE_MATCH_S, at_utc + PRESSURE_MATCH_S, at_utc),
        ).fetchone()
        if row:
            return float(row[0])
    return None


def limit_counts(conn, now: int, label_keys: Optional[list[str]] = None) -> list[dict]:
    """使う人が決めた上限ごとに、窓の中の回数を数える。"""
    rows = conn.execute("SELECT label_key, label, max_count, window_h FROM med_limits ORDER BY label").fetchall()
    out = []
    for key, label, max_count, window_h in rows:
        if label_keys is not None and key not in label_keys:
            continue
        count = conn.execute(
            "SELECT COUNT(*) FROM journal WHERE kind = 'medication' AND label_key = ? AND at_utc > ? AND at_utc <= ?",
            (key, now - window_h * 3600, now),
        ).fetchone()[0]
        out.append({"label": label, "count_in_window": int(count), "max_count": int(max_count),
                    "window_h": int(window_h)})
    return out


def _add(conn, args: dict) -> dict:
    kind = args.get("kind")
    if kind not in KINDS:
        return _error("invalid_kind", allowed=list(KINDS))
    label, err = _text(args.get("label"), MAX_LABEL, "label")
    if err:
        return err
    if not label:
        return _error("label_required")
    note, err = _text(args.get("note"), MAX_NOTE, "note")
    if err:
        return err
    dose, err = _text(args.get("dose"), MAX_DOSE, "dose")
    if err:
        return err
    severity = None
    if args.get("severity") is not None:
        severity = _int(args.get("severity"), 1, 5)
        if severity is None:
            return _error("invalid_severity", allowed="1-5")
    now = storage.now_utc()
    if args.get("at"):
        try:
            at_utc, tz_min = storage.parse_at(args["at"])
        except (ValueError, TypeError, OverflowError, OSError):
            return _error("invalid_at")
        if at_utc > now + 300:
            return _error("at_in_future")
        if at_utc < now - MAX_PAST_DAYS * 86400:  # 見返しの窓（最長3650日）の外へは刻まない
            return _error("at_too_old", max_days=MAX_PAST_DAYS)
    else:
        at_utc, tz_min = now, storage.local_tz_min(now)

    pressure_hpa = nearest_pressure(conn, at_utc)
    key = storage.label_key(label)
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO journal
                (kind, label, label_key, severity, dose, note, at_utc, tz_min, pressure_hpa, created_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (kind, label, key, severity, dose, note, at_utc, tz_min, pressure_hpa, now),
        )
    entry_id = int(cursor.lastrowid)
    row = conn.execute(f"SELECT {_ENTRY_COLUMNS} FROM journal WHERE id = ?", (entry_id,)).fetchone()
    out: dict[str, Any] = {"ok": True, "entry": _entry(row)}
    if kind == "medication":
        limits = limit_counts(conn, now, [key])
        if limits:
            out["limit"] = limits[0]
    return out


def _list(conn, args: dict) -> dict:
    now = storage.now_utc()
    where, params = ["1 = 1"], []
    if args.get("since"):
        try:
            since, _ = storage.parse_at(args["since"])
        except (ValueError, TypeError, OverflowError, OSError):
            return _error("invalid_since")
    else:
        days = _int(args.get("days", 14), 1, 3650) or 14
        since = now - days * 86400
    where.append("at_utc >= ?")
    params.append(since)
    if args.get("kind") is not None:
        if args["kind"] not in KINDS:
            return _error("invalid_kind", allowed=list(KINDS))
        where.append("kind = ?")
        params.append(args["kind"])
    if args.get("label"):
        label, err = _text(args.get("label"), MAX_LABEL, "label")
        if err:
            return err
        where.append("label_key = ?")
        params.append(storage.label_key(label or ""))
    limit = _int(args.get("limit", 50), 1, MAX_LIMIT) or 50
    sql = (f"SELECT {_ENTRY_COLUMNS} FROM journal WHERE {' AND '.join(where)} "
           "ORDER BY at_utc DESC, id DESC LIMIT ?")
    rows = conn.execute(sql, (*params, limit)).fetchall()
    total = conn.execute(f"SELECT COUNT(*) FROM journal WHERE {' AND '.join(where)}", params).fetchone()[0]
    out: dict[str, Any] = {"ok": True, "entries": [_entry(r) for r in rows], "shown": len(rows),
                           "matching": int(total), "since": storage.fmt_at(since, storage.local_tz_min(since))}
    limits = limit_counts(conn, now)
    if limits:
        out["limits"] = limits
    return out


def _delete(conn, args: dict) -> dict:
    entry_id = _int(args.get("id"), 1, 2**62)
    if entry_id is None:
        return _error("id_required")
    with conn:
        cursor = conn.execute("DELETE FROM journal WHERE id = ?", (entry_id,))
    if cursor.rowcount == 0:
        return _error("not_found", id=entry_id)
    return {"ok": True, "deleted": entry_id}


def _set_limit(conn, args: dict) -> dict:
    label, err = _text(args.get("label"), MAX_LABEL, "label")
    if err:
        return err
    if not label:
        return _error("label_required")
    key = storage.label_key(label)
    if args.get("max_count") is None:
        with conn:
            cursor = conn.execute("DELETE FROM med_limits WHERE label_key = ?", (key,))
        return {"ok": True, "cleared": cursor.rowcount > 0, "label": label}
    max_count = _int(args.get("max_count"), 1, 100)
    if max_count is None:
        return _error("invalid_max_count", allowed="1-100")
    window_h = 24 if args.get("window_h") is None else _int(args.get("window_h"), 1, 720)
    if window_h is None:
        return _error("invalid_window_h", allowed="1-720")
    with conn:
        conn.execute(
            """
            INSERT INTO med_limits (label_key, label, max_count, window_h) VALUES (?, ?, ?, ?)
            ON CONFLICT (label_key) DO UPDATE SET label = excluded.label,
                max_count = excluded.max_count, window_h = excluded.window_h
            """,
            (key, label, max_count, window_h),
        )
    return {"ok": True, "limit": limit_counts(conn, storage.now_utc(), [key])[0]}


def _purge(conn, args: dict) -> dict:
    if args.get("confirm") is not True:
        return _error("confirm_required", hint="Only when the user clearly asked to erase everything.")
    counts = {}
    with conn:
        for table in ("journal", "med_limits", "pressure_samples"):
            counts[table] = conn.execute(f"DELETE FROM {table}").rowcount
    return {"ok": True, "erased": counts}


_ACTIONS = {"add": _add, "list": _list, "delete": _delete, "set_limit": _set_limit, "purge": _purge}


def run(args: dict) -> dict:
    args = args if isinstance(args, dict) else {}
    action = _ACTIONS.get(args.get("action"))
    if action is None:
        return _error("invalid_action", allowed=list(_ACTIONS))
    try:
        with storage.connect() as conn:
            return action(conn, args)
    except Exception as exc:
        logger.debug("omonpakari: journal storage error (%s)", type(exc).__name__)
        return _error("storage_unavailable")


def make_handler(settings: Settings) -> Callable[..., str]:
    def handle_log(args: dict, **kwargs: Any) -> str:
        try:
            return json.dumps(run(args), ensure_ascii=False)
        except Exception as exc:
            logger.debug("omonpakari: log tool failed (%s)", type(exc).__name__)
            return json.dumps({"ok": False, "reason": "internal_error"})

    return handle_log
