"""暮らしの記憶の試験。教えられたことだけを持ち、言い換えずに返す。"""

from __future__ import annotations

from omonpakari import life


def test_覚えて見返せる():
    result = life.run({"action": "set", "topic": "朝の飲み物",
                       "text": "温かいほうじ茶", "relates": "ココア"})
    assert result["ok"]
    listed = life.run({"action": "list"})
    assert listed["notes"][0]["topic"] == "朝の飲み物"
    assert listed["notes"][0]["text"] == "温かいほうじ茶"
    assert listed["notes"][0]["relates"] == "ココア"


def test_同じ題は上書きされる():
    life.run({"action": "set", "topic": "朝の飲み物", "text": "白湯"})
    life.run({"action": "set", "topic": "朝の飲み物", "text": "温かいほうじ茶"})
    listed = life.run({"action": "list"})
    assert len(listed["notes"]) == 1
    assert listed["notes"][0]["text"] == "温かいほうじ茶"


def test_消せる():
    life.run({"action": "set", "topic": "朝の飲み物", "text": "白湯"})
    assert life.run({"action": "delete", "topic": "朝の飲み物"})["ok"]
    assert life.run({"action": "list"})["notes"] == []


def test_無い題を消すと誤り():
    assert life.run({"action": "delete", "topic": "無い題"})["reason"] == "not_found"


def test_長すぎる覚えは断る():
    result = life.run({"action": "set", "topic": "題", "text": "あ" * 201})
    assert result["ok"] is False
    assert result["reason"] == "text_too_long"
