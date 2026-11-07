"""部品：栄養の波。

栄養は1つの栄養素だけでは見ない。食の粗さと抜き、サプリの行き渡り、
鉄の摂り方と吸収の衝突を、3つの筋として一緒に持つ。鉄はその中の一本。
どの筋も、根拠（回数と日付）が無ければ何も言わない。
"""

from __future__ import annotations

from typing import Optional

from . import interactions, states, storage
from .settings import Settings

NAME = "nutrition"
RECENT_DAYS = 7


def _food_strand(rows, now: int) -> Optional[dict]:
    since = now - RECENT_DAYS * 86400
    meals = sum(1 for kind, _l, at in rows if kind == "food" and at > since)
    skipped = states._count(rows, states.SKIPPED_MEAL, since)
    rough = states._count(rows, states.ROUGH_FOOD, since)
    if meals == 0 and skipped == 0 and rough == 0:
        return None
    return {"meals_logged_this_week": meals, "skipped_mentions": skipped,
            "rough_mentions": rough}


def _supplements_strand(rows, now: int) -> Optional[dict]:
    since = now - RECENT_DAYS * 86400
    entries = [(label, at) for kind, label, at in rows
               if kind == "supplement" and at > since
               and (interactions.classify(label) or {}).get("role") != "target"]
    skipped = states._count(rows, states.SKIPPED_DOSE, since)
    if not entries and skipped == 0:
        return None
    return {"entries_this_week": len(entries),
            "days_covered": len({at // 86400 for _l, at in entries}),
            "skipped_mentions": skipped,
            "labels": sorted({label for label, _a in entries})[:5]}


def _iron_strand(settings: Settings, conn, now: int) -> Optional[dict]:
    rows = conn.execute(
        """
        SELECT label, at_utc, tz_min FROM journal
        WHERE kind IN ('medication', 'supplement') AND at_utc BETWEEN ? AND ?
        ORDER BY at_utc
        """,
        (now - 2 * RECENT_DAYS * 86400, now + 300)).fetchall()
    hits = [(label, int(at), int(tz)) for label, at, tz in rows
            if (interactions.classify(label) or {}).get("role") == "target"]
    if not hits:
        return None
    recent = sum(1 for _l, at, _t in hits if at > now - RECENT_DAYS * 86400)
    prior = len(hits) - recent
    _label, at, tz = hits[-1]
    collisions = [h for h in interactions.scan(conn, now, RECENT_DAYS,
                                               settings.interaction_window_h())
                  if h["effect"] == "inhibits"]
    return {"entries_this_week": recent, "entries_last_week": prior,
            "last_taken": storage.fmt_at(at, tz),
            "absorption_collisions_this_week": len(collisions)}


def state(settings: Settings, conn, now: int) -> Optional[dict]:
    rows = states._rows(conn, now)
    out = {"food": _food_strand(rows, now),
           "supplements": _supplements_strand(rows, now),
           "iron": _iron_strand(settings, conn, now)}
    if all(v is None for v in out.values()):
        return None
    return out
