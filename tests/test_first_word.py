"""first_word_turns（宿主の定時の回でも求心路を置く設定）と、未収載の行の試験。"""

from __future__ import annotations

from omonpakari import journal, quiet_context

H = 3600


def _hook(settings, **kwargs):
    payload = {"session_id": "s", "is_first_turn": True, "platform": "cli"}
    payload.update(kwargs)
    return quiet_context.make_hook(settings)(**payload)


def _add(kind: str, label: str, at: str = "2027-01-15T06:00+00:00"):
    result = journal.run({"action": "add", "kind": kind, "label": label, "at": at})
    assert result["ok"], result


def test_既定ではcronの回に求心路を置かない(settings):
    _add("note", "寝不足")
    assert _hook(settings, platform="cron") is None


def test_開いている時だけcronの回にも求心路を置く(settings, ctx):
    _add("note", "寝不足")
    ctx.config["first_word_turns"] = True
    placed = _hook(settings, platform="cron")
    assert placed is not None and "states:" in placed["context"]


def test_cronでも他の出どころが重なれば置かない(settings, ctx):
    _add("note", "寝不足")
    ctx.config["first_word_turns"] = True
    assert _hook(settings, platform="subagent") is None


def test_未収載の組は数だけ求心路に載る(settings):
    _add("supplement", "iron supplement", "2027-01-15T06:00+00:00")
    _add("food", "rice", "2027-01-15T06:15+00:00")
    placed = _hook(settings, platform="telegram")
    assert placed is not None
    assert "unchecked pairs today: 1" in placed["context"]
