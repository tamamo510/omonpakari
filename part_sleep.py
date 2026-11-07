"""部품：眠りの波。

センサーは持たない。本人が言葉で残した眠りの記録（寝不足、眠れない等）だけから、
今週と先週の寄せ引きと、最後に触れられた時を返す。語られていない眠りは推測しない。
"""

from __future__ import annotations

from typing import Optional

from . import storage
from .settings import Settings

NAME = "sleep"
RECENT_DAYS = 7
ALIASES = ("睡眠", "眠り", "寝不足", "眠れない", "不眠", "寝つき", "中途覚醒",
           "sleep", "insomnia", "眠気", "だるさ")


def _is_sleep(label: str) -> bool:
    key = storage.label_key(label)
    return any(storage.label_key(a) in key for a in ALIASES)


def state(settings: Settings, conn, now: int) -> Optional[dict]:
    rows = conn.execute(
        """
        SELECT label, at_utc FROM journal
        WHERE kind IN ('symptom', 'note') AND at_utc BETWEEN ? AND ? ORDER BY at_utc
        """,
        (now - 2 * RECENT_DAYS * 86400, now + 300)).fetchall()
    hits = [(label, int(at)) for label, at in rows if _is_sleep(label)]
    if not hits:
        return None
    recent = sum(1 for _l, at in hits if at > now - RECENT_DAYS * 86400)
    prior = len(hits) - recent
    last_label, last_at = hits[-1]
    if recent > prior:
        direction = "sleep has been mentioned more this week"
    elif recent < prior:
        direction = "sleep has settled compared with last week"
    else:
        direction = "sleep mentioned about as often as last week"
    return {"direction": direction, "mentions_this_week": recent,
            "mentions_last_week": prior, "last_mention": last_label,
            "last_mention_at": storage.fmt_at(last_at, storage.local_tz_min(last_at))}
