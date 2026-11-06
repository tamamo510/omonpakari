"""omonpakari（慮り）――急かさない体調の見守りの、Hermes のプラグイン。

道具は6つ（気圧、記録、生理の巡り、吸収の照らし、暮らしの記憶、相関）、
フックは1つ（pre_llm_call）、添えのスキルが1つ。
記録は上流の plugin_db に置き、鍵は上流の get_secret で読む。

芯の決まり：催促はしない。やることを急かす知らせも、繰り返しの声かけも持たない。
ただし、慮りには「要る時に、自分から静かに声をかける」側面がある。気圧の急落や
周期の窓のように、その人にとって今がその時なら、声をかける。回数や型で決める
ものではなく、その人を知っている側の判断として。
フックは、使う人が声をかけた手番の前に「今日のその人」の絵を静かに置き、
会話の中で察して効かせるための材料にする。声をかける口そのものは宿主（Hermes）の側にあり、
このプラグインは、その一声の材料になる絵をいつでも組める形で持つ。
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import correlate, cycle, interactions, journal, life, pressure, quiet_context
from .settings import API_KEY_NAME, Settings

logger = logging.getLogger(__name__)

TOOLSET = "omonpakari"
SKILL_NAME = "omonpakari"
SKILL_PATH = Path(__file__).resolve().parent / "skills" / SKILL_NAME / "SKILL.md"
SKILL_DESCRIPTION = "Considerate health context; never nags."


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
        name=cycle.TOOL_NAME, toolset=TOOLSET, schema=cycle.SCHEMA,
        handler=cycle.make_handler(settings),
        description=cycle.SCHEMA["description"], emoji="🌙",
    )
    ctx.register_tool(
        name=interactions.TOOL_NAME, toolset=TOOLSET, schema=interactions.SCHEMA,
        handler=interactions.make_handler(settings),
        description=interactions.SCHEMA["description"], emoji="🍵",
    )
    ctx.register_tool(
        name=life.TOOL_NAME, toolset=TOOLSET, schema=life.SCHEMA,
        handler=life.make_handler(settings),
        description=life.SCHEMA["description"], emoji="🏠",
    )
    ctx.register_tool(
        name=correlate.TOOL_NAME, toolset=TOOLSET, schema=correlate.SCHEMA,
        handler=correlate.make_handler(settings),
        description=correlate.SCHEMA["description"], emoji="📈",
    )
    ctx.register_hook("pre_llm_call", quiet_context.make_hook(settings))
    ctx.register_skill(SKILL_NAME, SKILL_PATH, description=SKILL_DESCRIPTION)
    logger.debug("omonpakari: registered 6 tools, 1 hook, 1 skill")
