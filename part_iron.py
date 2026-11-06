"""部品：鉄の波。

鉄の記録のリズム（今週と先週の回数、最後に摂った時）と、
今週の吸収の衝突の数を返す。鉄は温子の家の中心課題だが、
部品としては誰の鉄にも使える形で持つ。
"""

from __future__ import annotations

from typing import Optional

from . import interactions, storage
from .settings import Settings

NAME = "iron"
RECENT_DAYS = 7


def state(settings: Settings, conn, now: int) -> Optional[dict]:
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
    label, at, tz = hits[-1]
    collisions = [h for h in interactions.scan(conn, now, RECENT_DAYS,
                                               settings.interaction_window_h())
                  if h["effect"] == "inhibits"]
    return {"entries_this_week": recent, "entries_last_week": prior,
            "last_taken": storage.fmt_at(at, tz),
            "absorption_collisions_this_week": len(collisions)}
