"""omonpakari（慮り）――急かさない体調の見守りの、Hermes のプラグイン。

骨組みの段。道具（気圧の取り寄せ、症状と薬の記録、気圧との照らし合わせ）と、
声の前に静かに状態を置くフックと、添えのスキルは、これから1つずつ入れる。

芯の決まり：使う人に、自分からは一度も声をかけない。聞かれた時だけ一緒に考える。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    """Hermes が起動の時に一度だけ呼ぶ入口。

    骨組みの段では何も登録しない。plugin.yaml の provides_tools と provides_hooks は、
    ここで登録するものと同じ名にそろえる（そろわないとカタログの照らしで落ちる）。
    """
    logger.debug("omonpakari: 骨組みの段のため、登録するものはまだ無い")
