"""插件系统测试：加载、事件分发、action、设置持久化。"""

import textwrap
from pathlib import Path

import pytest

from magpie.plugin.manager import PluginManager


@pytest.fixture()
def pm(tmp_path: Path) -> PluginManager:
    d = tmp_path / "plugins"
    d.mkdir()
    (d / "sample.py").write_text(
        textwrap.dedent(
            """
            PLUGIN = {
                "name": "sample",
                "version": "1.0.0",
                "events": ["message"],
                "actions": ["ping"],
                "settings": [{"key": "greeting", "type": "text", "default": "hi"}],
            }
            _REPLIES = []

            async def on_message(ctx):
                _REPLIES.append(ctx.get("message"))
                return [{"type": "text", "data": {"text": "pong:" + ctx.get("message", "")}}]

            def action_ping(engine, **params):
                return {"ok": True, "params": params}
            """
        ),
        encoding="utf-8",
    )
    return PluginManager(None, d)


class TestPluginManager:
    def test_load_and_metadata(self, pm):
        pm.load_plugins()
        assert "sample" in pm.plugins
        rec = pm.plugins["sample"]
        assert rec.version == "1.0.0"
        assert "message" in rec.events
        assert "ping" in rec.actions

    def test_dispatch_event(self, pm):
        pm.load_plugins()
        ctx = {"message": "你好"}
        import asyncio

        replies = asyncio.run(pm.dispatch_event("message", ctx))
        assert replies and replies[0]["data"]["text"] == "pong:你好"

    def test_action_routing(self, pm):
        pm.load_plugins()
        import asyncio

        assert pm.has_action("ping")
        result = asyncio.run(pm.run_action("ping", k=1))
        assert result == {"ok": True, "params": {"k": 1}}

    def test_settings_persist_and_hot_apply(self, pm):
        pm.load_plugins()
        merged = pm.save_plugin_settings("sample", {"greeting": "你好"})
        assert merged["greeting"] == "你好"
        # 未注册的键被白名单过滤
        merged2 = pm.save_plugin_settings("sample", {"evil": "x"})
        assert "evil" not in merged2
        # 设置文件落盘（人手可编辑，见 ADR-0002）
        assert (pm.plugins_dir / "config" / "sample.json").exists()

    def test_broken_plugin_isolated(self, tmp_path):
        d = tmp_path / "plugins2"
        d.mkdir()
        (d / "bad.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
        (d / "good.py").write_text(
            'PLUGIN = {"name": "good"}\n', encoding="utf-8"
        )
        pm = PluginManager(None, d)
        pm.load_plugins()
        assert "good" in pm.plugins
        assert "bad" in pm.plugins  # 记录错误而不是让宿主崩
        assert pm.plugins["bad"].error
