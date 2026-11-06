"""波として見守る層 omonpakari_waves（合成役）。

体調は点ではなく波だ。気圧のうねり、生理の巡りの波、症状の寄せと引き、
眠り、鉄。その1つ1つが部品（part_*）で、約束は1つだけ：
state(settings, conn, now) が自分の波の状態か None を返す。
この層は部品を全部呼んで重ね、裏側で「今日」を合成して持つ。
表に出すための数値の読み上げではない。声かけは通知ではなく、
その人の一日に置かれる言葉として出すための材料。
部品は増やせる。新しい要素は、新しい part_* を置いて PARTS に足すだけ。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from . import part_cycle, part_iron, part_pressure, part_sleep, part_symptoms, states, storage
from .settings import Settings

logger = logging.getLogger(__name__)

TOOL_NAME = "omonpakari_waves"

# 部品の並び。順番が、そのまま合成の順番。
PARTS = (part_pressure, part_cycle, part_symptoms, part_sleep, part_iron)
PART_NAMES = [p.NAME for p in PARTS]

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "The composed wave states behind the scenes, one part per rhythm: pressure, cycle, "
        "symptoms, sleep (as the person has mentioned it), and iron. Each part returns its "
        "state and direction, or nothing when it has no ground to stand on. Pass part to read "
        "a single rhythm. States and directions, not verdicts. Use these to understand today "
        "before any topic - and if you speak first, speak from the waves in ordinary words "
        "fitted to the person's day, never as a notification or a readout of values."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "part": {"type": "string", "enum": PART_NAMES,
                     "description": "Optional: read only this part."},
        },
        "additionalProperties": False,
    },
}


def compose(settings: Settings, now: Optional[int] = None,
            only: Optional[str] = None) -> dict:
    now = storage.now_utc() if now is None else now
    waves: dict[str, Any] = {}
    with storage.connect() as conn:
        for part in PARTS:
            if only is not None and part.NAME != only:
                continue
            try:
                waves[part.NAME] = part.state(settings, conn, now)
            except Exception as exc:  # 部品1つの失敗で全体を落とさない
                logger.debug("omonpakari: wave part %s failed (%s)",
                             part.NAME, type(exc).__name__)
                waves[part.NAME] = None
        summary = None
        if only is None:
            try:
                summary = states.compute(settings, conn, now)
            except Exception as exc:
                logger.debug("omonpakari: states failed (%s)", type(exc).__name__)
    result: dict[str, Any] = {"ok": True, "waves": waves}
    if summary is not None:
        result["states"] = summary
    return {**result,
            "note": "Wave states for understanding today. If you speak, speak in ordinary "
                    "words fitted to the person's day - never as a notification or a readout."}


def run(settings: Settings, args: dict) -> dict:
    args = args if isinstance(args, dict) else {}
    only = args.get("part")
    if only is not None and only not in PART_NAMES:
        return {"ok": False, "reason": "invalid_part", "allowed": PART_NAMES}
    try:
        return compose(settings, only=only)
    except Exception as exc:
        logger.debug("omonpakari: waves failed (%s)", type(exc).__name__)
        return {"ok": False, "reason": "storage_unavailable"}


def make_handler(settings: Settings) -> Callable[..., str]:
    def handle_waves(args: dict, **kwargs: Any) -> str:
        try:
            return json.dumps(run(settings, args), ensure_ascii=False)
        except Exception as exc:
            logger.debug("omonpakari: waves tool failed (%s)", type(exc).__name__)
            return json.dumps({"ok": False, "reason": "internal_error"})

    return handle_waves
