"""吸収の照らしの道具 omonpakari_interactions。

出典のある一般的な相互作用の表を持ち、記録の時刻を突き合わせて、
「鉄の記録の近くに、吸収を妨げる物の記録がある」ことを事実として返す。
6月の事故（鉄とココアを同じ時間帯に提案した）の再発防止が、この道具の生まれ。
診断でも助言でもなく、確かめの仕組み。表は一般の栄養学の知見で組み、
個別の薬の名前は持たない。使う人が決めた間合い（初期値2時間）で見る。
衝突には、暮らしの記憶にあるその人の代替（あれば）を添える。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from . import life, storage
from .settings import Settings

logger = logging.getLogger(__name__)

TOOL_NAME = "omonpakari_interactions"
SCAN_KINDS = ("medication", "supplement", "food")
MAX_DAYS = 90

# 表：(対象の別名, 作用の別名, 向き, 作用の言葉, 出典の言葉)
# 向きは inhibits（妨げる）か promotes（助ける）。別名は label_key にそろえた形で当てる。
TARGETS = ("iron", "鉄", "鉄剤", "鉄サプリ", "ferrous", "ferrous fumarate", "フマル酸第一鉄")

REFERENCE = [
    ("cocoa", ("cocoa", "ココア"), "inhibits",
     "Cocoa polyphenols inhibit non-heme iron absorption (about 71% in a classic single-meal study).",
     "Hurrell et al., Am J Clin Nutr 1999; household interaction table, 2026-06-20."),
    ("tea tannins", ("matcha", "抹茶", "green tea", "緑茶", "tea", "紅茶", "black tea",
                     "peppermint tea", "ペパーミントティー"), "inhibits",
     "Tea tannins strongly inhibit non-heme iron absorption (reported up to ~90% for matcha, "
     "79-94% for black tea in the source table).",
     "Household interaction table, 2026-06-20 (compiled from nutrition literature)."),
    ("coffee", ("coffee", "コーヒー"), "inhibits",
     "Coffee polyphenols inhibit non-heme iron absorption.",
     "Hurrell et al., Am J Clin Nutr 1999."),
    ("soy protein", ("soy protein", "ソイプロテイン", "大豆プロテイン", "soy", "大豆"), "inhibits",
     "Soy protein inhibits non-heme iron absorption.",
     "Household interaction table, 2026-06-20."),
    ("calcium", ("calcium", "カルシウム"), "inhibits",
     "Calcium taken at the same time inhibits iron absorption.",
     "Hallberg et al., Am J Clin Nutr 1991."),
    ("magnesium", ("magnesium", "マグネシウム"), "inhibits",
     "Divalent minerals such as magnesium compete with iron absorption when taken together.",
     "Household interaction table, 2026-06-20."),
    ("zinc", ("zinc", "亜鉛"), "inhibits",
     "Zinc and iron compete for absorption when taken together in supplement form.",
     "Household interaction table, 2026-06-20."),
    ("fiber / phytate", ("fiber", "食物繊維", "phytate", "フィチン酸", "もち麦"), "inhibits",
     "Fiber and phytates can reduce non-heme iron absorption.",
     "Household interaction table, 2026-06-20."),
    ("vitamin C", ("vitamin c", "ビタミンc", "vitamin C", "ビタミンC"), "promotes",
     "Vitamin C (ascorbic acid) enhances non-heme iron absorption.",
     "Hallberg et al., Am J Clin Nutr 1989."),
    ("citric acid", ("citric acid", "クエン酸"), "promotes",
     "Citric acid can help non-heme iron absorption.",
     "Household interaction table, 2026-10-26."),
]

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Check the journal against a sourced table of absorption interactions, before suggesting "
        "food, drink or supplement combinations. action=check scans recent entries and reports, as "
        "facts, entries that fall within the person's spacing window (default 2 hours) of an iron "
        "entry and are known to inhibit or promote its absorption, with the person's own remembered "
        "alternative when one is set. action=table returns the reference table itself. "
        "Facts with sources; not medical advice."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["check", "table"],
                       "description": "What to do."},
            "days": {"type": "integer", "minimum": 1, "maximum": MAX_DAYS, "default": 7,
                     "description": "For check: how many days back to scan (default 7)."},
            "window_h": {"type": "number", "minimum": 0.5, "maximum": 12,
                         "description": "For check: spacing in hours that counts as 'near'. "
                         "Defaults to the configured interaction window (2 hours)."},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


def _error(reason: str, **extra: Any) -> dict:
    return {"ok": False, "reason": reason, **extra}


def _matches(label_key_text: str, aliases: tuple) -> bool:
    return any(storage.label_key(alias) in label_key_text for alias in aliases)


def classify(label: str) -> Optional[dict]:
    """記録の言葉が、対象（鉄）か、表のどれかに当たるかを返す。"""
    key = storage.label_key(label)
    if _matches(key, TARGETS):
        return {"role": "target", "name": "iron"}
    for name, aliases, effect, fact, source in REFERENCE:
        if _matches(key, aliases):
            return {"role": effect, "name": name, "fact": fact, "source": source}
    return None


def scan(conn, now: int, days: int, window_h: float) -> list[dict]:
    """窓の中の衝突を、事実の並びとして返す。"""
    start = now - days * 86400
    marks = ",".join("?" for _ in SCAN_KINDS)
    rows = conn.execute(
        f"SELECT id, kind, label, at_utc, tz_min FROM journal "
        f"WHERE kind IN ({marks}) AND at_utc BETWEEN ? AND ? ORDER BY at_utc",
        (*SCAN_KINDS, start, now + 300)).fetchall()
    entries = []
    for entry_id, kind, label, at_utc, tz_min in rows:
        info = classify(label)
        if info:
            entries.append({"id": int(entry_id), "kind": kind, "label": label,
                            "at_utc": int(at_utc), "tz_min": int(tz_min), **info})
    targets = [e for e in entries if e["role"] == "target"]
    others = [e for e in entries if e["role"] in ("inhibits", "promotes")]
    window_s = int(window_h * 3600)
    out = []
    for other in others:
        for target in targets:
            gap = other["at_utc"] - target["at_utc"]
            if abs(gap) <= window_s:
                item = {
                    "effect": other["role"],
                    "substance": other["name"],
                    "fact": other["fact"],
                    "source": other["source"],
                    "iron_entry": {"label": target["label"],
                                   "at": storage.fmt_at(target["at_utc"], target["tz_min"])},
                    "other_entry": {"label": other["label"], "kind": other["kind"],
                                    "at": storage.fmt_at(other["at_utc"], other["tz_min"])},
                    "minutes_apart": round(gap / 60),
                    "window_h": window_h,
                }
                alternatives = life.alternatives_for(conn, other["label"], other["name"],
                                                     target["label"])
                if alternatives:
                    item["person_alternative"] = [
                        {"topic": a["topic"], "text": a["text"]} for a in alternatives]
                out.append(item)
    out.sort(key=lambda i: i["other_entry"]["at"])
    return out


def _check(settings: Settings, args: dict) -> dict:
    days = args.get("days", 7)
    if isinstance(days, bool) or not isinstance(days, int) or not (1 <= days <= MAX_DAYS):
        return _error("invalid_days", allowed=f"1-{MAX_DAYS}")
    window = args.get("window_h")
    if window is None:
        window = settings.interaction_window_h()
    elif isinstance(window, bool) or not isinstance(window, (int, float)) or not (0.5 <= window <= 12):
        return _error("invalid_window_h", allowed="0.5-12")
    now = storage.now_utc()
    try:
        with storage.connect() as conn:
            collisions = scan(conn, now, days, float(window))
    except Exception as exc:
        logger.debug("omonpakari: interactions storage error (%s)", type(exc).__name__)
        return _error("storage_unavailable")
    return {"ok": True, "days": days, "window_h": float(window),
            "collisions": collisions, "count": len(collisions),
            "note": "Facts from a sourced reference table, matched by time. Not advice."}


def _table(args: dict) -> dict:
    return {"ok": True, "target": "iron (non-heme)",
            "interactions": [{"substance": name, "effect": effect, "fact": fact, "source": source}
                             for name, _aliases, effect, fact, source in REFERENCE]}


def run(settings: Settings, args: dict) -> dict:
    args = args if isinstance(args, dict) else {}
    if args.get("action") == "table":
        return _table(args)
    if args.get("action") == "check":
        return _check(settings, args)
    return _error("invalid_action", allowed=["check", "table"])


def make_handler(settings: Settings) -> Callable[..., str]:
    def handle_interactions(args: dict, **kwargs: Any) -> str:
        try:
            return json.dumps(run(settings, args), ensure_ascii=False)
        except Exception as exc:
            logger.debug("omonpakari: interactions tool failed (%s)", type(exc).__name__)
            return json.dumps({"ok": False, "reason": "internal_error"})

    return handle_interactions
