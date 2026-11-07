"""長期パターンの試験。その人の普通が、蓄積からだけ見えること。"""

from __future__ import annotations

from omonpakari import cycle, journal, waves


def _add(kind: str, label: str, at: str, severity: int | None = None):
    args = {"action": "add", "kind": kind, "label": label, "at": at}
    if severity is not None:
        args["severity"] = severity
    result = journal.run(args)
    assert result["ok"], result


def test_生理の重さの型が見える(settings):
    cycle.run({"action": "mark", "mark": "start", "at": "2026-12-10T08:00+09:00"})
    cycle.run({"action": "mark", "mark": "start", "at": "2027-01-08T08:00+09:00"})
    _add("symptom", "生理痛", "2026-12-11T09:00+09:00", severity=5)
    _add("symptom", "頭痛", "2026-12-12T09:00+09:00", severity=4)
    _add("symptom", "生理痛", "2027-01-09T09:00+09:00", severity=5)
    _add("symptom", "だるさ", "2027-01-10T09:00+09:00", severity=4)
    result = waves.compose(settings)
    texts = [p["pattern"] for p in result.get("patterns", [])]
    assert any("runs heavy" in t for t in texts), result.get("patterns")


def test_周期が早まっている傾きが見える(settings):
    for d in ("2026-09-01", "2026-09-30", "2026-10-28", "2026-11-24", "2026-12-18", "2027-01-11"):
        cycle.run({"action": "mark", "mark": "start", "at": f"{d}T08:00+09:00"})
    result = waves.compose(settings)
    texts = [p["pattern"] for p in result.get("patterns", [])]
    assert any("shortening" in t for t in texts), result.get("patterns")


def test_蓄積が無ければ型は言わない(settings):
    result = waves.compose(settings)
    assert "patterns" not in result
