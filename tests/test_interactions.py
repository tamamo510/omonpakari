"""吸収の確かめの試験。鉄の近くにある物を事実として拾い、遠い物は拾わない。"""

from __future__ import annotations

from omonpakari import interactions, journal, life


def _add(kind: str, label: str, at: str) -> dict:
    result = journal.run({"action": "add", "kind": kind, "label": label, "at": at})
    assert result["ok"], result
    return result


def test_鉄とココアの衝突を拾う():
    _add("supplement", "iron supplement", "2027-01-15T07:00+00:00")
    _add("food", "cocoa", "2027-01-15T07:30+00:00")
    result = interactions.run(_settings(), {"action": "check", "days": 1})
    assert result["ok"] and result["count"] == 1
    hit = result["collisions"][0]
    assert hit["effect"] == "inhibits"
    assert hit["substance"] == "cocoa"
    assert hit["minutes_apart"] == 30
    assert hit["source"]  # 出典が付いている


def test_窓の外は拾わない():
    _add("supplement", "iron supplement", "2027-01-15T04:00+00:00")
    _add("food", "coffee", "2027-01-15T07:30+00:00")  # 3時間半後、窓は2時間
    result = interactions.run(_settings(), {"action": "check", "days": 1})
    assert result["count"] == 0


def test_ビタミンCは助ける側で返る():
    _add("supplement", "鉄サプリ", "2027-01-15T07:00+00:00")
    _add("supplement", "ビタミンC", "2027-01-15T07:05+00:00")
    result = interactions.run(_settings(), {"action": "check", "days": 1})
    assert result["count"] == 1
    assert result["collisions"][0]["effect"] == "promotes"


def test_日本語の記録でも当たる():
    _add("medication", "鉄剤", "2027-01-15T07:00+00:00")
    _add("food", "抹茶ラテ", "2027-01-15T08:00+00:00")
    result = interactions.run(_settings(), {"action": "check", "days": 1})
    assert result["count"] == 1
    assert result["collisions"][0]["substance"] == "tea tannins"


def test_代替の覚えが添う():
    life.run({"action": "set", "topic": "朝の飲み物",
              "text": "温かいほうじ茶（ノンカフェイン）", "relates": "ココア"})
    _add("supplement", "iron supplement", "2027-01-15T07:00+00:00")
    _add("food", "ココア", "2027-01-15T07:20+00:00")
    result = interactions.run(_settings(), {"action": "check", "days": 1})
    hit = result["collisions"][0]
    assert hit["person_alternative"][0]["text"] == "温かいほうじ茶（ノンカフェイン）"


def test_痛み止めとお酒の組み合わせも拾う():
    _add("medication", "ロキソニン", "2027-01-15T06:00+00:00")
    _add("food", "ビール", "2027-01-15T07:00+00:00")
    result = interactions.run(_settings(), {"action": "check", "days": 1})
    names = {hit["substance"] for hit in result["collisions"]}
    assert "NSAID + alcohol" in names
    hit = next(h for h in result["collisions"] if h["substance"] == "NSAID + alcohol")
    assert hit["effect"] == "caution"
    assert hit["source"]


def test_未収載の組み合わせは未収載として返る():
    _add("supplement", "iron supplement", "2027-01-15T07:00+00:00")
    _add("food", "rice", "2027-01-15T07:15+00:00")
    result = interactions.run(_settings(), {"action": "check", "days": 1})
    effects = {hit["effect"] for hit in result["collisions"]}
    assert "unlisted" in effects  # 無言で通さない


def test_表そのものを返せる():
    result = interactions.run(_settings(), {"action": "table"})
    assert result["ok"]
    names = {row["substance"] for row in result["interactions"]}
    assert "cocoa" in names and "vitamin C" in names
    assert all(row["source"] for row in result["interactions"])


def _settings():
    from conftest import FakeCtx
    from omonpakari.settings import Settings
    return Settings(FakeCtx())
