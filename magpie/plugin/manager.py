"""Plugin manager.

Loads plugins from the `Plugins/` directory (next to the executable when
frozen, or the repo `plugins/` dir in dev).  Each plugin is a plain Python
file that declares a `PLUGIN` dict and optionally implements event handlers
and OneBot custom actions:

    PLUGIN = {
        "name": "echo",
        "version": "1.0.0",
        "description": "Reply with the message content",
        "author": "",
        "events": ["message"],        # handled event types
        "actions": ["echo_ping"],     # OneBot actions this plugin provides
    }

    async def on_message(ctx):  # optional - event handler
        ...
        return [{"type": "text", "data": {"text": "..."}}]  # replies

    async def action_echo_ping(engine, **params):  # optional - OneBot action
        ...
        return {"ok": True}

Plugins are isolated (exceptions never break the host), can be reloaded at
runtime, and can be enabled/disabled individually.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def get_plugins_dir() -> Path:
    """Plugins directory: `Plugins/` beside the exe, or repo `plugins/` in dev."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "Plugins"
    return Path(__file__).resolve().parent.parent.parent / "plugins"


@dataclass
class PluginRecord:
    """A loaded plugin and its metadata."""
    name: str
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    path: str = ""
    events: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    settings: list[dict] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    missing_requires: list[str] = field(default_factory=list)
    enabled: bool = True
    loaded_at: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "path": self.path,
            "events": self.events,
            "actions": self.actions,
            "settings": self.settings,
            "requires": self.requires,
            "missing_requires": self.missing_requires,
            "enabled": self.enabled,
            "loaded_at": time.strftime("%H:%M:%S", time.localtime(self.loaded_at)) if self.loaded_at else "",
            "error": self.error,
        }


class PluginManager:
    """Scan, load, dispatch and manage plugins."""

    def __init__(self, engine, plugins_dir: Optional[Path] = None) -> None:
        self.engine = engine
        self.plugins_dir = Path(plugins_dir) if plugins_dir else get_plugins_dir()
        self.plugins: dict[str, PluginRecord] = {}
        self._handlers: dict[str, list[tuple[str, Any]]] = {}  # event -> [(name, fn)]
        self._actions: dict[str, tuple[str, Any]] = {}         # action -> (name, fn)

    # ------------------------------------------------------------ loading
    def load_plugins(self) -> list[str]:
        """Load all plugins from the plugins dir.

        Two layouts are supported (both can coexist):
        - single file:  Plugins/echo.py            (plugin name = file stem)
        - folder:       Plugins/leads_forwarder/__init__.py
                        Plugins/leads_forwarder/main.py
                        (plugin name = folder name; extra modules/resources
                         live in the same folder and can be imported)

        Returns the list of successfully loaded plugin names.
        """
        loaded: list[str] = []
        self._handlers.clear()
        self._actions.clear()

        if not self.plugins_dir.exists():
            self.plugins_dir.mkdir(parents=True, exist_ok=True)
            logger.info("插件目录不存在，已创建: %s", self.plugins_dir)

        # Make the plugins dir importable so folder plugins can use absolute
        # imports of sibling modules (import helper).
        plugins_root = str(self.plugins_dir)
        if plugins_root not in sys.path:
            sys.path.insert(0, plugins_root)

        # 1) folder plugins: Plugins/<name>/__init__.py or main.py
        for path in sorted(self.plugins_dir.iterdir()):
            if not path.is_dir() or path.name.startswith("_"):
                continue
            entry = path / "__init__.py"
            if not entry.exists():
                entry = path / "main.py"
            if not entry.exists():
                continue
            try:
                name = self._load_plugin_entry(entry, folder=path)
                if name:
                    loaded.append(name)
            except Exception as e:
                logger.exception("加载插件 %s 失败", path.name)
                self.plugins[path.name] = PluginRecord(
                    name=path.name, path=str(path), error=str(e)
                )

        # 2) single-file plugins: Plugins/xxx.py
        for path in sorted(self.plugins_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                name = self._load_plugin_entry(path, folder=None)
                if name:
                    loaded.append(name)
            except Exception as e:
                logger.exception("加载插件 %s 失败", path.name)
                self.plugins[path.stem] = PluginRecord(
                    name=path.stem, path=str(path), error=str(e)
                )

        # Re-register handlers/actions for enabled plugins
        for rec in self.plugins.values():
            if rec.enabled and not rec.error:
                self._register(rec)
        return loaded

    def _load_plugin_entry(self, entry: Path, folder: Optional[Path]) -> Optional[str]:
        """Load one plugin entry (a .py file or a folder's __init__/main)."""
        if folder is None:
            module_name = f"magpie_plugin_{entry.stem}"
            spec = importlib.util.spec_from_file_location(module_name, entry)
        else:
            module_name = f"magpie_plugin_{folder.name}"
            spec = importlib.util.spec_from_file_location(module_name, entry)
            if spec is not None:
                # folder plugins are packages: enable `from . import helper`
                spec.submodule_search_locations = [str(folder)]
        if not spec or not spec.loader:
            raise ImportError(f"无法加载 {entry}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        meta = getattr(module, "PLUGIN", None)
        if not isinstance(meta, dict) or not meta.get("name"):
            raise ValueError(f"{entry} 缺少 PLUGIN 元信息（需含 name）")

        display = folder.name if folder is not None else entry.stem
        rec = PluginRecord(
            name=str(meta.get("name", display)),
            version=str(meta.get("version", "1.0.0")),
            description=str(meta.get("description", "")),
            author=str(meta.get("author", "")),
            path=str(folder if folder is not None else entry),
            events=[str(e) for e in meta.get("events", [])],
            actions=[str(a) for a in meta.get("actions", [])],
            settings=[dict(s) for s in meta.get("settings", [])],
            enabled=bool(meta.get("enabled", True)),
            loaded_at=time.time(),
        )
        # declared third-party deps + missing ones (drives console warning)
        rec.requires = [str(r) for r in meta.get("requires", [])]
        rec.missing_requires = [
            r for r in rec.requires if importlib.util.find_spec(r) is None
        ]
        if rec.missing_requires:
            rec.error = f"缺少依赖: {', '.join(rec.missing_requires)}（请在程序目录安装或联系插件作者）"
            logger.warning("插件 %s 缺少依赖: %s", rec.name, ", ".join(rec.missing_requires))

        # stash the module so dispatch can call its handlers
        rec._module = module  # type: ignore[attr-defined]
        rec._name = module_name  # type: ignore[attr-defined]
        self.plugins[rec.name] = rec
        # Inject persisted settings into the module (PLUGIN_SETTINGS global)
        module.PLUGIN_SETTINGS = self._load_settings(rec.name, rec.settings)
        logger.info("插件已加载: %s v%s (%s)", rec.name, rec.version, rec.description or "无描述")
        return rec.name

    # ------------------------------------------------------------ settings
    def _settings_path(self, name: str) -> Path:
        cfg_dir = self.plugins_dir / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        return cfg_dir / f"{name}.json"

    def _load_settings(self, name: str, schema: list[dict]) -> dict:
        """Load persisted settings for a plugin, merged over schema defaults."""
        defaults = {s["key"]: s.get("default") for s in schema if "key" in s}
        stored: dict = {}
        path = self._settings_path(name)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    stored = json.load(f)
            except Exception:
                logger.exception("读取插件设置失败: %s", path)
        merged = {**defaults, **stored}
        return {k: v for k, v in merged.items() if v is not None}

    def get_plugin_settings(self, name: str) -> dict:
        """Return current settings values for a plugin."""
        rec = self.plugins.get(name)
        if rec is None:
            return {}
        if hasattr(rec, "_module") and hasattr(rec._module, "PLUGIN_SETTINGS"):
            return dict(rec._module.PLUGIN_SETTINGS)
        return self._load_settings(name, rec.settings)

    def save_plugin_settings(self, name: str, values: dict) -> dict:
        """Persist settings for a plugin and hot-apply to the loaded module."""
        rec = self.plugins.get(name)
        if rec is None:
            raise KeyError(f"插件不存在: {name}")
        schema = {s["key"]: s for s in rec.settings if "key" in s}
        cleaned = {}
        for key, spec in schema.items():
            if key in values and values[key] is not None:
                cleaned[key] = values[key]
        path = self._settings_path(name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, ensure_ascii=False, indent=2)
        # hot-apply to the module (no reload needed)
        merged = {**self._load_settings(name, rec.settings), **cleaned}
        if hasattr(rec, "_module"):
            rec._module.PLUGIN_SETTINGS = merged
        logger.info("插件设置已保存: %s -> %s", name, path)
        return merged

    def _register(self, rec: PluginRecord) -> None:
        """Register event handlers and OneBot actions of an enabled plugin."""
        module = getattr(rec, "_module", None)
        if module is None:
            return
        for event in rec.events:
            handler = getattr(module, f"on_{event}", None)
            if callable(handler):
                self._handlers.setdefault(event, []).append((rec.name, handler))
        for action in rec.actions:
            fn = getattr(module, f"action_{action}", None)
            if callable(fn):
                self._actions[action] = (rec.name, fn)

    # ------------------------------------------------------------ dispatch
    async def dispatch_event(self, event_type: str, ctx: dict, only: Optional[list[str]] = None) -> list[dict]:
        """Dispatch an event to plugins; returns collected reply segments.

        Args:
            event_type: the event name (e.g. "message").
            ctx: the event context dict.
            only: optional list of plugin names allowed to receive this event.
                  When None, all registered handlers are called.
        """
        replies: list[dict] = []
        allowed = set(only) if only is not None else None
        for name, handler in self._handlers.get(event_type, []):
            rec = self.plugins.get(name)
            if rec is None or not rec.enabled:
                continue
            if allowed is not None and name not in allowed:
                continue
            try:
                result = handler(ctx)  # may be coroutine
                if hasattr(result, "__await__"):
                    result = await result
                if isinstance(result, list):
                    replies.extend(result)
            except Exception:
                logger.exception("插件 %s 处理事件 %s 出错", name, event_type)
        return replies

    async def run_action(self, action: str, **params: Any) -> Any:
        """Execute a plugin-provided OneBot action."""
        entry = self._actions.get(action)
        if not entry:
            raise KeyError(f"插件 action 不存在: {action}")
        name, fn = entry
        rec = self.plugins.get(name)
        if rec is None or not rec.enabled:
            raise RuntimeError(f"插件 {name} 已禁用")
        result = fn(self.engine, **params)
        if hasattr(result, "__await__"):
            result = await result
        return result

    def has_action(self, action: str) -> bool:
        return action in self._actions

    # ------------------------------------------------------------ management
    def get_plugin_list(self) -> list[dict]:
        return [rec.to_dict() for rec in self.plugins.values()]

    def reload_plugins(self) -> dict:
        self.plugins.clear()
        loaded = self.load_plugins()
        return {"loaded": loaded, "count": len(self.plugins)}

    def set_enabled(self, name: str, enabled: bool) -> bool:
        rec = self.plugins.get(name)
        if not rec:
            return False
        rec.enabled = enabled
        # re-register handlers/actions (cheap: full rebuild)
        self._handlers.clear()
        self._actions.clear()
        for r in self.plugins.values():
            if r.enabled and not r.error:
                self._register(r)
        logger.info("插件 %s 已%s", name, "启用" if enabled else "禁用")
        return True
