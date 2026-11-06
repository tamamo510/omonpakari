"""omonpakari（慮り）――急かさない体調の見守りの、Hermes のプラグイン。

道具は3つ（気圧、記録、相関）、フックは1つ（pre_llm_call）、添えのスキルが1つ。
記録は上流の plugin_db に置き、鍵は上流の get_secret で読む。

芯の決まり：使う人に、自分からは一度も声をかけない。定時の知らせも、催促も、通知も持たない。
フックは、使う人が声をかけた手番の前に、状態を静かに置くだけ。
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import correlate, journal, pressure, quiet_context
from .settings import API_KEY_NAME, Settings

logger = logging.getLogger(__name__)

TOOLSET = "omonpakari"
SKILL_NAME = "omonpakari"
SKILL_PATH = Path(__file__).resolve().parent / "skills" / SKILL_NAME / "SKILL.md"
SKILL_DESCRIPTION = "Quiet pressure and symptom journal; never nags."


def register(ctx) -> None:
    """Hermes が起動の時に一度だけ呼ぶ入口。

    ここでは登録だけをする。網に出ず、記録も開かず、設定も読まない
    （ctx.state のような呼べない属性にも触れない。照らしの器で落ちるため）。
    plugin.yaml の provides_tools と provides_hooks は、ここで登録するものと同じ名にそろえる。
    """
    settings = Settings(ctx)

    ctx.register_tool(
        name=pressure.TOOL_NAME, toolset=TOOLSET, schema=pressure.SCHEMA,
        handler=pressure.make_handler(settings), requires_env=[API_KEY_NAME],
        description=pressure.SCHEMA["description"], emoji="🌀",
    )
    ctx.register_tool(
        name=journal.TOOL_NAME, toolset=TOOLSET, schema=journal.SCHEMA,
        handler=journal.make_handler(settings),
        description=journal.SCHEMA["description"], emoji="📓",
    )
    ctx.register_tool(
        name=correlate.TOOL_NAME, toolset=TOOLSET, schema=correlate.SCHEMA,
        handler=correlate.make_handler(settings),
        description=correlate.SCHEMA["description"], emoji="📈",
    )
    ctx.register_hook("pre_llm_call", quiet_context.make_hook(settings))
    ctx.register_skill(SKILL_NAME, SKILL_PATH, description=SKILL_DESCRIPTION)
    logger.debug("omonpakari: registered 3 tools, 1 hook, 1 skill")
