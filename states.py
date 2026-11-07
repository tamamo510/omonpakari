"""カテゴリごとの状態（信号） states。

蓄積された記録から、カテゴリごとに今の状態を1つに要約する内部の層。
信号は3段：settled（青、落ち着いている）／watch（黄、気にかける）／
heavy（赤、重く見る）。これは内側の把握のためのもので、本人への表示や
通知のためのものではない。状態があるから、声の掛け方が変わる。
根拠（basis）は必ず事実（回数と日付）で持つ。推測で信号は作らない。
"""

from __future__ import annotations

from typing import Optional

from . import interactions, part_cycle, part_nutrition, part_pressure, part_symptoms, storage
from .settings import Settings

WEEK_S = 7 * 86400

POOR_SLEEP = ("寝不足", "眠れない", "不眠", "寝つき", "中途覚醒", "insomnia", "poor sleep")
GOOD_SLEEP = ("ぐっすり", "よく寝た", "熟睡", "well slept", "slept well")
SKIPPED_MEAL = ("食べなかった", "食欲ない", "食欲がない", "抜いた", "skipped meal", "no appetite")
ROUGH_FOOD = ("粗食", "偏り", "偏食", "インスタント", "菓子パン", "同じもの", "カップ麺",
              "コンビニ", "instant", "same thing")
SKIPPED_DOSE = ("飲み忘れ", "飲んでない", "飲んでいない", "サボ", "forgot", "skipped dose")


def _matches(label: str, words: tuple[str, ...]) -> bool:
    key = storage.label_key(label)
    return any(storage.label_key(w) in key for w in words)


def _rows(conn, now: int, days: int = 14):
    return conn.execute(
        "SELECT kind, label, at_utc FROM journal WHERE at_utc BETWEEN ? AND ? ORDER BY at_utc",
        (now - days * 86400, now + 300)).fetchall()


def _count(rows, words, since: int) -> int:
    return sum(1 for _k, label, at in rows if at > since and _matches(label, words))


def sleep_state(rows, now: int) -> Optional[dict]:
    since = now - WEEK_S
    poor = _count(rows, POOR_SLEEP, since)
    good = _count(rows, GOOD_SLEEP, since)
    if poor == 0 and good == 0:
        return None
    if poor >= 3:
        return {"signal": "heavy", "basis": f"poor sleep mentioned {poor} times this week"}
    if poor >= 1:
        return {"signal": "watch", "basis": f"poor sleep mentioned {poor} time(s) this week"}
    return {"signal": "settled", "basis": "sleep mentioned without trouble this week"}


def food_state(rows, now: int) -> Optional[dict]:
    since = now - WEEK_S
    meals = sum(1 for kind, _l, at in rows if kind == "food" and at > since)
    skipped = _count(rows, SKIPPED_MEAL, since)
    rough = _count(rows, ROUGH_FOOD, since)
    if meals == 0 and skipped == 0 and rough == 0:
        return None
    if skipped >= 2 or rough >= 3:
        return {"signal": "heavy",
                "basis": f"meals look rough or skipped (skipped {skipped}, rough {rough}, logged {meals} this week)"}
    if skipped >= 1 or rough >= 1:
        return {"signal": "watch",
                "basis": f"some roughness in eating this week (skipped {skipped}, rough {rough}, logged {meals})"}
    return {"signal": "settled", "basis": f"{meals} meals logged this week, nothing rough mentioned"}


def supplements_state(rows, now: int) -> Optional[dict]:
    since = now - WEEK_S
    entries = [(label, at) for kind, label, at in rows
               if kind == "supplement" and at > since
               and (interactions.classify(label) or {}).get("role") != "target"]
    skipped = _count(rows, SKIPPED_DOSE, since)
    if not entries and skipped == 0:
        return None
    days_covered = len({at // 86400 for _l, at in entries})
    labels = sorted({label for label, _a in entries})
    if skipped >= 2:
        return {"signal": "heavy", "basis": f"supplements skipped or forgotten {skipped} times this week"}
    if skipped >= 1 or (entries and days_covered <= 2):
        return {"signal": "watch",
                "basis": f"supplement rhythm is patchy ({len(entries)} entries over {days_covered} day(s) this week)"}
    if entries:
        return {"signal": "settled",
                "basis": f"supplements taken on {days_covered} day(s) this week ({', '.join(labels[:3])})"}
    return None


def iron_state(settings: Settings, conn, now: int) -> Optional[dict]:
    part = part_nutrition._iron_strand(settings, conn, now)
    if not part:
        return None
    collisions = part["absorption_collisions_this_week"]
    if collisions >= 2:
        return {"signal": "heavy", "basis": f"{collisions} absorption collisions this week"}
    if collisions == 1:
        return {"signal": "watch", "basis": "one absorption collision this week"}
    if part["entries_this_week"] == 0 and part["entries_last_week"] > 0:
        return {"signal": "watch", "basis": "iron not taken this week though it was last week"}
    if part["entries_this_week"] >= 1:
        return {"signal": "settled", "basis": f"iron taken {part['entries_this_week']} time(s) this week"}
    return None


def cycle_state(settings: Settings, conn, now: int) -> Optional[dict]:
    part = part_cycle.state(settings, conn, now)
    if not part:
        return None
    phase = part["phase"]
    if phase in ("menstrual", "late luteal"):
        return {"signal": "watch", "basis": f"cycle phase: {phase}"}
    return {"signal": "settled", "basis": f"cycle phase: {phase}"}


def pressure_state(settings: Settings, conn, now: int) -> Optional[dict]:
    part = part_pressure.state(settings, conn, now)
    if not part:
        return None
    if part.get("falling_fast") or part["level"] in ("extreme", "very low"):
        return {"signal": "heavy", "basis": f"pressure {part['level']}, {part['trend']}"}
    if part["level"] == "low" or part["trend"] == "falling":
        return {"signal": "watch", "basis": f"pressure {part['level']}, {part['trend']}"}
    return {"signal": "settled", "basis": f"pressure {part['level']}, {part['trend']}"}


def symptoms_state(settings: Settings, conn, now: int) -> Optional[dict]:
    part = part_symptoms.state(settings, conn, now)
    if not part:
        return None
    rising = [w for w in part["waves"] if w["direction"] == "more present this week"]
    if any(w["this_week"] >= 5 for w in rising):
        names = ", ".join(w["label"] for w in rising)
        return {"signal": "heavy", "basis": f"symptoms gathering: {names}"}
    if rising:
        names = ", ".join(w["label"] for w in rising)
        return {"signal": "watch", "basis": f"symptoms gathering: {names}"}
    return {"signal": "settled", "basis": "no symptom is gathering this week"}


def compute(settings: Settings, conn, now: int) -> dict:
    rows = _rows(conn, now)
    states = {
        "sleep": sleep_state(rows, now),
        "food": food_state(rows, now),
        "supplements": supplements_state(rows, now),
        "iron": iron_state(settings, conn, now),
        "cycle": cycle_state(settings, conn, now),
        "pressure": pressure_state(settings, conn, now),
        "symptoms": symptoms_state(settings, conn, now),
    }
    return {name: state for name, state in states.items() if state is not None}
