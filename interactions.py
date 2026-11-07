"""吸収の確かめの道具 omonpakari_interactions。

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
     "Hurrell et al., Am J Clin Nutr 1999."),
    ("tea tannins", ("matcha", "抹茶", "green tea", "緑茶", "tea", "紅茶", "black tea",
                     "peppermint tea", "ペパーミントティー"), "inhibits",
     "Tea tannins strongly inhibit non-heme iron absorption (reported up to ~90% for matcha, "
     "79-94% for black tea in the source table).",
     "Interaction reference compiled from nutrition literature."),
    ("coffee", ("coffee", "コーヒー"), "inhibits",
     "Coffee polyphenols inhibit non-heme iron absorption.",
     "Hurrell et al., Am J Clin Nutr 1999."),
    ("soy protein", ("soy protein", "ソイプロテイン", "大豆プロテイン", "soy", "大豆"), "inhibits",
     "Soy protein inhibits non-heme iron absorption.",
     "Interaction reference compiled from nutrition literature."),
    ("calcium", ("calcium", "カルシウム"), "inhibits",
     "Calcium taken at the same time inhibits iron absorption.",
     "Hallberg et al., Am J Clin Nutr 1991."),
    ("magnesium", ("magnesium", "マグネシウム"), "inhibits",
     "Divalent minerals such as magnesium compete with iron absorption when taken together.",
     "Interaction reference compiled from nutrition literature."),
    ("zinc", ("zinc", "亜鉛"), "inhibits",
     "Zinc and iron compete for absorption when taken together in supplement form.",
     "Interaction reference compiled from nutrition literature."),
    ("fiber / phytate", ("fiber", "食物繊維", "phytate", "フィチン酸", "もち麦"), "inhibits",
     "Fiber and phytates can reduce non-heme iron absorption.",
     "Interaction reference compiled from nutrition literature."),
    ("vitamin C", ("vitamin c", "ビタミンc", "vitamin C", "ビタミンC"), "promotes",
     "Vitamin C (ascorbic acid) enhances non-heme iron absorption.",
     "Hallberg et al., Am J Clin Nutr 1989."),
    ("citric acid", ("citric acid", "クエン酸"), "promotes",
     "Citric acid can help non-heme iron absorption.",
     "Interaction reference compiled from nutrition literature."),
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


# 鉄以外の、一般の組み合わせの表。臓器は「担当外」と言わない。
# (組の名, 側Aの別名, 側Bの別名, 同じ物同士でも当てるか, 作用の言葉, 出典の言葉)
NSAID_ALIASES = ("nsaid", "ロキソニン", "loxoprofen", "ibuprofen", "イブプロフェン",
                 "aspirin", "アスピリン", "痛み止め", "解熱鎮痛")
ALCOHOL_ALIASES = ("alcohol", "お酒", "酒", "ビール", "beer", "ワイン", "wine", "日本酒", "焼酎")
ACETAMINOPHEN_ALIASES = ("acetaminophen", "アセトアミノフェン", "カロナール", "paracetamol")

PAIR_RULES = [
    ("NSAID + alcohol", NSAID_ALIASES, ALCOHOL_ALIASES, False,
     "NSAIDs taken with alcohol raise the risk of stomach irritation and bleeding, "
     "and add to the liver's load.",
     "FDA NSAID class labeling (alcohol warning)."),
    ("NSAID + NSAID", NSAID_ALIASES, NSAID_ALIASES, True,
     "Two NSAIDs close together stack the same mechanism: more stomach and kidney "
     "load without more benefit.",
     "FDA NSAID class labeling (do not combine NSAIDs)."),
    ("acetaminophen + alcohol", ACETAMINOPHEN_ALIASES, ALCOHOL_ALIASES, False,
     "Acetaminophen is cleared by the liver; combined with alcohol the liver load "
     "and toxicity risk rise.",
     "FDA acetaminophen labeling (liver warning with 3+ drinks)."),
]


def _pair_classify(label: str) -> Optional[str]:
    key = storage.label_key(label)
    for group, aliases in (("nsaid", NSAID_ALIASES), ("alcohol", ALCOHOL_ALIASES),
                           ("acetaminophen", ACETAMINOPHEN_ALIASES)):
        if _matches(key, aliases):
            return group
    return None


def scan(conn, now: int, days: int, window_h: float) -> list[dict]:
    """窓の中の衝突を、事実の並びとして返す。鉄の吸収も、それ以外の組み合わせも。"""
    start = now - days * 86400
    marks = ",".join("?" for _ in SCAN_KINDS)
    rows = conn.execute(
        f"SELECT id, kind, label, at_utc, tz_min FROM journal "
        f"WHERE kind IN ({marks}) AND at_utc BETWEEN ? AND ? ORDER BY at_utc",
        (*SCAN_KINDS, start, now + 300)).fetchall()
    entries = []
    pair_entries = []
    all_entries = []
    for entry_id, kind, label, at_utc, tz_min in rows:
        base = {"id": int(entry_id), "kind": kind, "label": label,
                "at_utc": int(at_utc), "tz_min": int(tz_min)}
        all_entries.append(base)
        info = classify(label)
        if info:
            entries.append({**base, **info})
        group = _pair_classify(label)
        if group:
            pair_entries.append({**base, "group": group})
    window_s = int(window_h * 3600)
    out = []

    def _item(effect: str, substance: str, fact: str, source: str,
              a: dict, b: dict, gap: int) -> dict:
        item = {
            "effect": effect,
            "substance": substance,
            "fact": fact,
            "source": source,
            "entries": [
                {"label": a["label"], "kind": a["kind"],
                 "at": storage.fmt_at(a["at_utc"], a["tz_min"])},
                {"label": b["label"], "kind": b["kind"],
                 "at": storage.fmt_at(b["at_utc"], b["tz_min"])},
            ],
            "minutes_apart": round(gap / 60),
            "window_h": window_h,
        }
        alternatives = life.alternatives_for(conn, a["label"], b["label"], substance)
        if alternatives:
            item["person_alternative"] = [
                {"topic": alt["topic"], "text": alt["text"]} for alt in alternatives]
        return item

    targets = [e for e in entries if e["role"] == "target"]
    others = [e for e in entries if e["role"] in ("inhibits", "promotes")]
    for other in others:
        for target in targets:
            gap = other["at_utc"] - target["at_utc"]
            if abs(gap) <= window_s:
                item = _item(other["role"], other["name"], other["fact"], other["source"],
                             target, other, gap)
                item["iron_entry"] = item["entries"][0]
                item["other_entry"] = item["entries"][1]
                out.append(item)
    for name, aliases_a, aliases_b, same_ok, fact, source in PAIR_RULES:
        side_a = [e for e in pair_entries if _matches(storage.label_key(e["label"]), aliases_a)]
        side_b = [e for e in pair_entries if _matches(storage.label_key(e["label"]), aliases_b)]
        for a in side_a:
            for b in side_b:
                if a["id"] == b["id"]:
                    continue
                if not same_ok and a["group"] == b["group"]:
                    continue
                if same_ok and a["id"] >= b["id"]:
                    continue  # 同じ組の二つは一度だけ
                gap = b["at_utc"] - a["at_utc"]
                if abs(gap) <= window_s:
                    out.append(_item("caution", name, fact, source, a, b, gap))
    # 未収載の組み合わせも、無言で通さない。近くで摂られた記録の組は
    # 「未収載」として返す。未収載であることは、安全の証明ではない。
    covered = set()
    for item in out:
        labels = tuple(sorted(storage.label_key(e["label"]) for e in item["entries"]))
        covered.add(labels)
    unique = all_entries
    meds = [e for e in unique if e["kind"] in ("medication", "supplement")]
    unlisted = []
    for i, a in enumerate(unique):
        for b in unique[i + 1:]:
            if a["kind"] not in ("medication", "supplement", "food"):
                continue
            if b["kind"] not in ("medication", "supplement", "food"):
                continue
            if not (a in meds or b in meds):
                continue
            gap = b["at_utc"] - a["at_utc"]
            if abs(gap) > window_s:
                continue
            labels = tuple(sorted((storage.label_key(a["label"]), storage.label_key(b["label"]))))
            if labels in covered:
                continue
            covered.add(labels)
            unlisted.append({
                "effect": "unlisted",
                "substance": f"{a['label']} + {b['label']}",
                "fact": "Taken close together; not in the reference table. "
                        "Unlisted means unchecked, not safe.",
                "source": "",
                "entries": [
                    {"label": a["label"], "kind": a["kind"],
                     "at": storage.fmt_at(a["at_utc"], a["tz_min"])},
                    {"label": b["label"], "kind": b["kind"],
                     "at": storage.fmt_at(b["at_utc"], b["tz_min"])},
                ],
                "minutes_apart": round(gap / 60),
                "window_h": window_h,
            })
            if len(unlisted) >= 20:
                break
        if len(unlisted) >= 20:
            break
    out.extend(unlisted)
    out.sort(key=lambda i: i["entries"][1]["at"])
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
                             for name, _aliases, effect, fact, source in REFERENCE],
            "pairs": [{"pair": name, "effect": "caution", "fact": fact, "source": source}
                      for name, _a, _b, _same, fact, source in PAIR_RULES]}


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
