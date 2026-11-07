"""状態（信号）の層と、日常会話からの蓄積の試験。"""

from __future__ import annotations

from omonpakari import journal, states, waves


def _add(kind: str, label: str, day: str, time: str = "08:00"):
    result = journal.run({"action": "add", "kind": kind, "label": label,
                          "at": f"2027-01-{day}T{time}+00:00"})
    assert result["ok"], result


def test_寝不足が重なると眠りの信号が重くなる(settings):
    _add("note", "寝不足", "15")
    _add("note", "寝不足", "14")
    _add("note", "眠れない", "13")
    result = waves.compose(settings)
    assert result["states"]["sleep"]["signal"] == "heavy"


def test_粗食と抜きが重なると食の信号が重くなる(settings):
    _add("food", "菓子パン", "15")
    _add("note", "昼食べなかった", "14")
    _add("note", "夜食べなかった", "13")
    result = waves.compose(settings)
    assert result["states"]["food"]["signal"] == "heavy"


def test_記録が無ければ信号は無い(settings):
    result = waves.compose(settings)
    assert result["states"] == {}


def test_札に状態の行が入る(settings):
    _add("note", "寝不足", "15")
    _add("note", "寝不足", "14")
    _add("note", "寝不足", "13")
    from omonpakari import quiet_context
    import omonpakari.storage as storage
    from datetime import datetime, timezone
    now = int(datetime(2027, 1, 15, 8, 0, tzinfo=timezone.utc).timestamp())
    text, _fp = quiet_context.build(settings, now)
    assert text is not None
    assert "states:" in text and "sleep heavy" in text
