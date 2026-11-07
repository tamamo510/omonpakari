"""長期のパターン patterns。

蓄積が数週間から数ヶ月に届くと、その人自身の「普通」が見えてくる。
夜型だとか、生理は重いほうだとか、サプリは夜に飲む人だとか。
合わせる基準は世間の平均ではなく、その人の蓄積から見えたその人の普通。
ここは週単位の波や信号より遅い層で、根拠（何件の記録からか）を必ず添える。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from . import cycle, storage

WINDOW_DAYS = 90
PERIOD_WORDS = ("生理", "経血", "月経", "period", "menstrual", "生理痛")
SLEEP_WORDS = ("寝不足", "眠れない", "不眠", "寝つき", "中途覚醒", "insomnia", "poor sleep")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _matches(label: str, words: tuple[str, ...]) -> bool:
    key = storage.label_key(label)
    return any(storage.label_key(w) in key for w in words)


def cycle_heaviness(conn, now: int) -> dict | None:
    """生理の時期に重なった症状の重さから、その人の生理の重さの型を読む。"""
    marks = cycle.load_marks(conn)
    starts = sorted(m["at_utc"] for m in marks if m["mark"] == "start")
    if len(starts) < 2:
        return None
    rows = conn.execute(
        "SELECT label, severity, at_utc FROM journal WHERE kind = 'symptom' AND at_utc > ?",
        (now - WINDOW_DAYS * 86400,)).fetchall()
    severities = []
    for label, severity, at in rows:
        if severity is None:
            continue
        phase = cycle.phase_at(marks, at) or ""
        if phase.startswith(("menstrual", "late luteal")) or _matches(label, PERIOD_WORDS):
            severities.append(severity)
    if len(severities) < 3:
        return None
    avg = sum(severities) / len(severities)
    tone = "runs heavy" if avg >= 4 else "moderate" if avg >= 2.5 else "runs light"
    return {"pattern": f"their periods {tone}",
            "basis": f"average symptom severity {avg:.1f}/5 across {len(severities)} entries in menstrual phases, {len(starts)} cycles observed"}


def supplement_hour(conn, now: int) -> dict | None:
    """サプリや薬を摂る時刻の型（その人のリズム）。"""
    rows = conn.execute(
        "SELECT at_utc, tz_min FROM journal WHERE kind IN ('supplement', 'medication') AND at_utc > ?",
        (now - WINDOW_DAYS * 86400,)).fetchall()
    hours = []
    for at, tz in rows:
        local = datetime.fromtimestamp(at + int(tz) * 60, tz=timezone.utc)
        hours.append(local.hour)
    if len(hours) < 5:
        return None
    hours.sort()
    median = hours[len(hours) // 2]
    if median >= 20 or median < 4:
        when = "in the evening / night"
    elif median < 10:
        when = "in the morning"
    else:
        when = "around midday"
    return {"pattern": f"supplements and medication are usually taken {when}",
            "basis": f"median local hour {median}:00 across {len(hours)} entries"}


def sleep_weekday(conn, now: int) -> dict | None:
    """眠りの不調が寄る曜日の型。"""
    rows = conn.execute(
        "SELECT label, at_utc, tz_min FROM journal WHERE kind IN ('symptom', 'note') AND at_utc > ?",
        (now - WINDOW_DAYS * 86400,)).fetchall()
    counts: Counter = Counter()
    for label, at, tz in rows:
        if not _matches(label, SLEEP_WORDS):
            continue
        local = datetime.fromtimestamp(at + int(tz) * 60, tz=timezone.utc)
        counts[local.weekday()] += 1
    if sum(counts.values()) < 4:
        return None
    day, n = counts.most_common(1)[0]
    if n < 3:
        return None
    return {"pattern": f"poor sleep is mentioned most on {WEEKDAYS[day]}s",
            "basis": f"{n} of {sum(counts.values())} poor-sleep mentions"}


def cycle_trend(conn, now: int) -> dict | None:
    """巡りの長さの傾き。早まっている／遅くなっているを、観測した長さだけで言う。"""
    marks = cycle.load_marks(conn)
    starts = sorted((m for m in marks if m["mark"] == "start"),
                    key=lambda m: m["at_utc"])
    lengths = []
    for prev, nxt in zip(starts, starts[1:]):
        days = round((nxt["at_utc"] - prev["at_utc"]) / 86400)
        if 15 <= days <= 60:
            lengths.append(days)
    if len(lengths) < 4:
        return None
    half = len(lengths) // 2
    earlier = sum(lengths[:half]) / half
    recent = sum(lengths[half:]) / (len(lengths) - half)
    diff = recent - earlier
    if diff <= -2:
        direction = "have been shortening"
    elif diff >= 2:
        direction = "have been lengthening"
    else:
        return None
    return {"pattern": f"their cycles {direction}",
            "basis": f"observed lengths {lengths} (earlier average {earlier:.0f} days, recent {recent:.0f})"}


def compose(conn, now: int) -> list[dict]:
    out = []
    for fn in (cycle_heaviness, cycle_trend, supplement_hour, sleep_weekday):
        try:
            item = fn(conn, now)
        except Exception:
            item = None
        if item:
            out.append(item)
    return out
