"""組み直した求心路の試験。今日の所見（周期・吸収・暮らし）が入り、旧い縛りの一文は無い。"""

from __future__ import annotations

from omonpakari import cycle, journal, life, quiet_context


def _seed(settings):
    cycle.run({"action": "mark", "mark": "start", "at": "2026-11-16T08:00+00:00"})
    cycle.run({"action": "mark", "mark": "start", "at": "2026-12-14T08:00+00:00"})
    cycle.run({"action": "mark", "mark": "start", "at": "2027-01-08T08:00+00:00"})
    journal.run({"action": "add", "kind": "supplement", "label": "iron supplement",
                 "at": "2027-01-15T07:00+00:00"})
    journal.run({"action": "add", "kind": "food", "label": "cocoa",
                 "at": "2027-01-15T07:30+00:00"})
    life.run({"action": "set", "topic": "morning drink",
              "text": "warm hojicha, non-caffeine", "relates": "cocoa"})
    return quiet_context.build(settings)


def test_求心路に今日の所見が入る(settings):
    text, _fp = _seed(settings)
    assert text is not None
    assert "cycle:" in text
    assert "next period window 2027-02-02 to 2027-02-05" in text
    assert "interactions today:" in text
    assert "cocoa" in text
    assert "life notes:" in text
    assert "warm hojicha" in text


def test_旧い縛りの一文は無い(settings):
    text, _fp = _seed(settings)
    assert "Do not bring this up on your own" not in text
    assert "mention journal entries only if" not in text.lower()
    # 結びは慮りの指示になっている
    assert "weigh it before suggesting" in text
    assert "prefer the person's own alternatives" in text
    assert text.endswith(quiet_context.FOOTER + "\n</omonpakari>")


def test_吸収の衝突には代替が添う(settings):
    text, _fp = _seed(settings)
    assert "person's alternative: warm hojicha, non-caffeine" in text
