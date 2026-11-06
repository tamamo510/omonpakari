"""部品：症状の波。

症状ごとに、今週と先週の寄せ引きを数え、向きとして返す。
1回の記録は点だが、重なりは波だ。3件に満たない症状は波と呼ばない。
"""

from __future__ import annotations

from typing import Optional

from .settings import Settings

NAME = "symptoms"
RECENT_DAYS = 7
MAX_WAVES = 3


def state(settings: Settings, conn, now: int) -> Optional[dict]:
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
        if g["recent"] + g["prior"] < 3:
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
    return {"waves": out[:MAX_WAVES]} if out else None
