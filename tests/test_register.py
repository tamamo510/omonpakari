"""入口と家の形の試験。宣言と登録のそろい、自分から送る手が無いこと、鍵を直に読まないこと。"""

from __future__ import annotations

import ast
import json
import re

import pytest

from conftest import ROOT, FakeCtx
import omonpakari

CODE_FILES = sorted(p for p in ROOT.glob("*.py"))


def _manifest_list(key: str) -> list[str]:
    """plugin.yaml の上の段の並び（- の行）を読む。依りを持たない小さな読み方。"""
    items, inside = [], False
    for line in (ROOT / "plugin.yaml").read_text(encoding="utf-8").splitlines():
        if re.match(rf"^{key}:\s*$", line):
            inside = True
            continue
        if inside:
            match = re.match(r"^\s+-\s+(?:name:\s+)?([A-Za-z0-9_\-]+)\s*$", line)
            if match:
                items.append(match.group(1))
            elif line.strip() and not line.startswith(" "):
                break
    return items


def _manifest_block(key: str) -> list[str]:
    lines, inside = [], False
    for line in (ROOT / "plugin.yaml").read_text(encoding="utf-8").splitlines():
        if re.match(rf"^{key}:\s*$", line):
            inside = True
            continue
        if inside:
            if line.strip() and not line.startswith(" "):
                break
            lines.append(line)
    return lines


def test_plugin_yamlの宣言と実際の登録がそろう():
    ctx = FakeCtx()
    omonpakari.register(ctx)
    assert sorted(ctx.tools) == sorted(_manifest_list("provides_tools"))
    assert sorted(ctx.hooks) == sorted(_manifest_list("provides_hooks")) == ["pre_llm_call"]
    assert _manifest_list("requires_env") == ["OPENWEATHER_API_KEY"]
    assert all(len(callbacks) == 1 for callbacks in ctx.hooks.values())


def test_道具の形は上流の約束どおり():
    ctx = FakeCtx()
    omonpakari.register(ctx)
    for name, tool in ctx.tools.items():
        schema = tool["schema"]
        assert schema["name"] == name and name.startswith("omonpakari_")
        assert tool["toolset"] == "omonpakari" and tool["override"] is False
        assert schema["parameters"]["type"] == "object"
        assert schema["parameters"].get("additionalProperties") is False
        assert schema["description"].isascii()
        json.dumps(schema)  # JSON にできること
    assert ctx.tools["omonpakari_pressure"]["requires_env"] == ["OPENWEATHER_API_KEY"]
    # 鍵が無くても道具は隠さない（呼べば「鍵が要る」と静かに返す）
    assert all(tool["check_fn"] is None for tool in ctx.tools.values())


def test_道具の戻りはいつもJSONの文字列(hermes):
    ctx = FakeCtx()
    omonpakari.register(ctx)
    for tool in ctx.tools.values():
        for args in ({}, {"action": "list"}, {"lat": "x"}):
            raw = tool["handler"](args, task_id="t", session_id="s")
            assert isinstance(raw, str) and isinstance(json.loads(raw), dict)


def test_添えのスキルの紙がある():
    ctx = FakeCtx()
    omonpakari.register(ctx)
    skill = ctx.skills["omonpakari"]
    assert skill["path"].is_file()
    assert 0 < len(skill["description"]) <= 60  # 目録の説明は60字で切られる
    head = skill["path"].read_text(encoding="utf-8").split("---")[1]
    assert "name: omonpakari" in head and "description:" in head


def test_registerは設定も鍵も記録も読まず網に出ない(hermes):
    class Strict(FakeCtx):
        def get_config(self, key, default=None):
            raise AssertionError("register must not read config")

        def __getattr__(self, name):  # state、manifest ほかの呼べない属性に触れたら落ちる
            raise AttributeError(name)

    omonpakari.register(Strict())
    assert hermes.http.calls == [] and hermes.db.opened == 0


def test_座標の既定値を家に置かない():
    block = "\n".join(_manifest_block("config_schema"))
    for key in ("latitude", "longitude"):
        section = re.search(rf"^  {key}:\n((?:    .*\n?)+)", block + "\n", re.M).group(1)
        assert "default" not in section
    for path in CODE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                names = [t.id.lower() for t in node.targets if isinstance(t, ast.Name)]
                assert not any(n in ("default_lat", "default_lon", "lat", "lon") for n in names), path


FORBIDDEN_CALLS = {"inject_message", "post_message", "send_message", "spawn_task", "register_automation_blueprint",
                   "register_cron", "add_cron", "schedule", "notify", "push"}
FORBIDDEN_MODULES = {"sched", "croniter", "smtplib", "apscheduler", "schedule"}


@pytest.mark.parametrize("path", CODE_FILES, ids=lambda p: p.name)
def test_自分から送る手が家のどこにも無い(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            assert name not in FORBIDDEN_CALLS, f"{path.name}: {name}"
            assert "cron" not in name.lower() and "send" not in name.lower(), f"{path.name}: {name}"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            assert not any(m.split(".")[0] in FORBIDDEN_MODULES for m in modules), f"{path.name}: {modules}"
        if isinstance(node, ast.Dict):  # フックが手番を止める形を返さない
            for key in node.keys:
                if isinstance(key, ast.Constant) and key.value == "action":
                    values = [v for k, v in zip(node.keys, node.values, strict=True) if k is key]
                    assert not any(isinstance(v, ast.Constant) and v.value == "block" for v in values)


@pytest.mark.parametrize("path", CODE_FILES, ids=lambda p: p.name)
def test_鍵を環境から直に読まない(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert not (node.attr == "environ" and getattr(node.value, "id", "") == "os"), path.name
            assert node.attr not in ("getenv", "putenv"), path.name
        if isinstance(node, ast.Name):
            assert node.id not in ("getenv", "environ"), path.name
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open":
            raise AssertionError(f"{path.name}: 紙を直に開かない（.env も記録も上流の道から）")


def test_家に鍵の値らしき並びが無い():
    pattern = re.compile(r"\b[0-9a-f]{32}\b")
    for path in ROOT.rglob("*"):
        if path.is_file() and ".git" not in path.parts and path.suffix in {".py", ".md", ".yaml", ".txt", ".ini"}:
            assert not pattern.search(path.read_text(encoding="utf-8")), path
