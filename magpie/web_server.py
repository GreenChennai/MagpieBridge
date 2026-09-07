"""FastAPI web server for management interface and frontend serving.

安全基线（ADR-0005）：管理面板只面向本机。CORS 全开 + credentials 的组合
已移除（前端与 API 同源部署，根本不需要 CORS）；所有 /api 请求只接受
回环地址来源 —— 改 host 对外监听时，外部网段拿到的是 403 而不是控制权。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .core.config import AppConfig
from .core.ui_engine import UIEngine
from .monitor.listener import WeChatListener
from .core.store import MessageStore
from .monitor.status import StatusMonitor

try:
    from . import __version__ as APP_VERSION
except Exception:  # pragma: no cover - import guard for odd packaging
    APP_VERSION = "5.0.0"

logger = logging.getLogger(__name__)


def _get_web_dir() -> Path:
    """Get web frontend directory, handling PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = Path(meipass) / "magpie" / "web"
            if bundled.exists():
                return bundled
        exe_dir = Path(sys.executable).parent
        local = exe_dir / "magpie" / "web"
        if local.exists():
            return local
        return bundled if meipass else local
    return Path(__file__).parent / "web"


class SendMessageRequest(BaseModel):
    contact: str
    message: str


class SendAtRequest(BaseModel):
    contact: str
    at_name: str
    message: str


class SendImageRequest(BaseModel):
    contact: str
    file_path: str


class MonitorContactRequest(BaseModel):
    contact: str


class SaveConfigRequest(BaseModel):
    onebot: dict[str, Any] | None = None
    wechat: dict[str, Any] | None = None
    web: dict[str, Any] | None = None
    human_sim: dict[str, Any] | None = None
    admin: dict[str, Any] | None = None
    monitor: dict[str, Any] | None = None
    alert: dict[str, Any] | None = None


class AdminCommandRequest(BaseModel):
    command: str


class DeleteMessagesRequest(BaseModel):
    """批量删除消息：ids 必填；或按 contact 删除该会话全部记录。"""
    ids: list[int] | None = None
    contact: str | None = None


class ClearMessagesRequest(BaseModel):
    """清理消息：contact 为空 = 清空全部；否则清空该会话。"""
    contact: str | None = None


def create_app(
    config: AppConfig,
    engine: UIEngine,
    msg_logger: MessageStore,
    monitor: StatusMonitor,
    listener: WeChatListener | None = None,
    plugin_manager=None,
) -> FastAPI:
    app = FastAPI(title="MagpieBridge", version=APP_VERSION)

    @app.middleware("http")
    async def loopback_only(request: Request, call_next):
        """管理 API 只允许本机回环访问（见模块 docstring 安全基线）。"""
        client = request.client.host if request.client else ""
        if client not in ("127.0.0.1", "::1", "localhost"):
            return JSONResponse({"detail": "管理接口仅限本机访问"}, status_code=403)
        return await call_next(request)

    @app.get("/api/status")
    async def get_status() -> dict[str, Any]:
        return {
            "engine": engine.get_status(),
            "monitor": monitor.get_status(),
        }

    @app.get("/api/messages")
    async def get_messages(limit: int = 50, contact: str | None = None) -> list[dict]:
        return msg_logger.get_recent(limit=limit, contact=contact)

    @app.get("/api/messages/stats")
    async def get_message_stats() -> dict[str, Any]:
        return msg_logger.get_stats()

    @app.get("/api/messages/contacts")
    async def get_message_contacts() -> dict[str, Any]:
        """按会话分组的消息统计（左侧群列表）。"""
        contacts = msg_logger.list_contacts()
        return {"count": len(contacts), "contacts": contacts}

    @app.post("/api/messages/delete")
    async def delete_messages(req: DeleteMessagesRequest) -> dict[str, Any]:
        """批量删除：ids 数组删除指定记录；contact 删除某会话全部记录。"""
        try:
            if req.ids:
                deleted = msg_logger.delete_by_ids(req.ids)
                logger.info("批量删除消息 %d 条", deleted)
                return {"success": True, "deleted": deleted}
            if req.contact is not None:
                deleted = msg_logger.delete_by_contact(req.contact)
                logger.info("删除会话 %s 消息 %d 条", req.contact, deleted)
                return {"success": True, "deleted": deleted}
            return {"success": False, "deleted": 0, "error": "ids 或 contact 至少提供一个"}
        except Exception as e:
            logger.exception("删除消息失败")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/messages/clear")
    async def clear_messages(req: ClearMessagesRequest) -> dict[str, Any]:
        """清理消息：contact 为空清空全部；否则只清空该会话。"""
        try:
            if req.contact is not None:
                deleted = msg_logger.delete_by_contact(req.contact)
                logger.info("清空会话 %s 消息 %d 条", req.contact, deleted)
            else:
                deleted = msg_logger.clear_all()
                logger.info("清空全部消息 %d 条", deleted)
            return {"success": True, "deleted": deleted}
        except Exception as e:
            logger.exception("清理消息失败")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/export/csv")
    async def export_csv(contact: str | None = None) -> dict[str, Any]:
        """导出消息为 Excel 友好 CSV（UTF-8 BOM）到 data/exports/。

        用户查看/留档数据的入口（ADR-0002）：contact 缺省按会话各导一个文件。
        """
        try:
            out_dir = msg_logger.db_path.parent / "exports"
            paths = msg_logger.export_csv(out_dir, contact=contact)
            return {
                "success": True,
                "count": len(paths),
                "files": [str(p) for p in paths],
            }
        except Exception as e:
            logger.exception("导出 CSV 失败")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/screen")
    async def screen_power(req: dict[str, Any]) -> dict[str, Any]:
        """息屏控制（ADR-0003）：{"action": "off"|"on"|"toggle"|"status"}。"""
        from .core import screen_off

        action = str(req.get("action", "status")).lower()
        if action == "off":
            ok = screen_off.turn_off_display()
            return {"success": ok, "screen_off": screen_off.is_screen_off()}
        if action == "on":
            ok = screen_off.wake_display(reason="api")
            return {"success": ok, "screen_off": screen_off.is_screen_off()}
        if action == "toggle":
            return {"success": True, "screen_off": screen_off.toggle()}
        return {"success": True, "screen_off": screen_off.is_screen_off()}

    @app.post("/api/send/text")
    async def send_text(req: SendMessageRequest) -> dict[str, Any]:
        msg_id = msg_logger.log_send(req.contact, "text", req.message, status="sending")
        try:
            success = await engine.send_text(req.contact, req.message)
            status = "success" if success else "failed"
            msg_logger.update_status(msg_id, status)
            monitor.record_message()
            return {"success": success, "message_id": msg_id}
        except Exception as e:
            msg_logger.update_status(msg_id, "error", str(e))
            monitor.record_error()
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/send/image")
    async def send_image(req: SendImageRequest) -> dict[str, Any]:
        msg_id = msg_logger.log_send(req.contact, "image", req.file_path, status="sending")
        try:
            success = await engine.send_image_file(req.contact, req.file_path)
            status = "success" if success else "failed"
            msg_logger.update_status(msg_id, status)
            monitor.record_message()
            return {"success": success, "message_id": msg_id}
        except Exception as e:
            msg_logger.update_status(msg_id, "error", str(e))
            monitor.record_error()
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/chat/list")
    async def get_chat_list() -> list[dict[str, str]]:
        return engine.get_chat_list()

    @app.post("/api/engine/initialize")
    async def initialize_engine() -> dict[str, Any]:
        success = await engine.initialize()
        if success:
            monitor.set_status("running")
        return {"success": success}

    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        return {
            "onebot": config.onebot.__dict__,
            "wechat": config.wechat.__dict__,
            "web": config.web.__dict__,
            "human_sim": config.human_sim.__dict__,
            "admin": {
                "enabled": config.admin.enabled,
                "admin_contacts": config.admin.admin_contacts,
                "check_interval_sec": config.admin.check_interval_sec,
                "command_prefix": config.admin.command_prefix,
            },
            "monitor": {
                "enabled": config.monitor.enabled,
                "poll_interval_sec": config.monitor.poll_interval_sec,
                "poll_interval_std_sec": config.monitor.poll_interval_std_sec,
                "scroll_pages": config.monitor.scroll_pages,
                "react_delay_ms": config.monitor.react_delay_ms,
                "chat_x1": config.monitor.chat_x1,
                "chat_y1": config.monitor.chat_y1,
                "chat_x2": config.monitor.chat_x2,
                "chat_y2": config.monitor.chat_y2,
                "listen_contacts": config.monitor.listen_contacts,
            },
            "alert": {
                "enabled": config.alert.enabled,
                "smtp_host": config.alert.smtp_host,
                "smtp_port": config.alert.smtp_port,
                "use_ssl": config.alert.use_ssl,
                "smtp_user": config.alert.smtp_user,
                "auth_code": config.alert.masked_auth_code(),  # 打码，不暴露明文
                "receiver": config.alert.receiver,
                "subject_prefix": config.alert.subject_prefix,
                "cooldown_sec": config.alert.cooldown_sec,
                "auth_code_set": bool(config.alert.auth_code),
            },
        }

    @app.post("/api/config")
    async def save_config(req: SaveConfigRequest) -> dict[str, Any]:
        try:
            if req.onebot is not None:
                for k, v in req.onebot.items():
                    if hasattr(config.onebot, k):
                        setattr(config.onebot, k, v)

            if req.wechat is not None:
                for k, v in req.wechat.items():
                    if hasattr(config.wechat, k):
                        setattr(config.wechat, k, v)

            if req.web is not None:
                for k, v in req.web.items():
                    if hasattr(config.web, k):
                        setattr(config.web, k, v)

            if req.human_sim is not None:
                for k, v in req.human_sim.items():
                    if hasattr(config.human_sim, k):
                        setattr(config.human_sim, k, v)

            if req.admin is not None:
                if "enabled" in req.admin:
                    config.admin.enabled = req.admin["enabled"]
                if "admin_contacts" in req.admin:
                    config.admin.admin_contacts = req.admin["admin_contacts"]
                if "check_interval_sec" in req.admin:
                    config.admin.check_interval_sec = req.admin["check_interval_sec"]
                if "command_prefix" in req.admin:
                    config.admin.command_prefix = req.admin["command_prefix"]

            if req.monitor is not None:
                for k, v in req.monitor.items():
                    if hasattr(config.monitor, k):
                        setattr(config.monitor, k, v)

            # 邮件告警配置：授权码打码值/空串视为「不修改」，保留原值
            if req.alert is not None:
                for k, v in req.alert.items():
                    if k == "auth_code":
                        raw = str(v or "")
                        if raw and not raw.startswith("****"):
                            config.alert.auth_code = raw
                        # 空串或打码值 -> 保留原授权码
                    elif hasattr(config.alert, k):
                        setattr(config.alert, k, v)

            config.save()
            logger.info("配置已保存")
            # 热应用邮件告警配置（无需重启）
            try:
                from .core.alerter import get_alerter
                get_alerter().update_config(config.alert)
            except Exception:
                logger.exception("热应用邮件告警配置失败")
            return {"success": True, "message": "Config saved. Restart to apply changes."}
        except Exception as e:
            logger.exception("Failed to save config")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/alert/test")
    async def alert_test() -> dict[str, Any]:
        """发送测试邮件，验证 SMTP / 授权码配置是否正确。"""
        try:
            from .core.alerter import get_alerter
            ok, msg = await get_alerter().send_test()
            return {"success": ok, "message": msg}
        except Exception as e:
            logger.exception("测试邮件接口异常")
            return {"success": False, "message": f"发送失败: {e}"}

    @app.post("/api/admin/command")
    async def execute_admin_command(req: AdminCommandRequest) -> dict[str, Any]:
        try:
            from .admin.command_handler import parse_command

            cmd = parse_command(req.command)
            if not cmd:
                return {"success": False, "error": "Invalid command format. Use: /text 内容->目标"}

            if cmd.content_type == "text":
                success = await engine.send_text(cmd.target, cmd.content)
                msg_logger.log_send(cmd.target, "text", cmd.content, status="success" if success else "failed")
                monitor.record_message()
                return {"success": success, "target": cmd.target, "type": "text"}

            elif cmd.content_type == "image":
                success = await engine.send_image_file(cmd.target, cmd.content)
                msg_logger.log_send(cmd.target, "image", cmd.content, status="success" if success else "failed")
                monitor.record_message()
                return {"success": success, "target": cmd.target, "type": "image"}

            return {"success": False, "error": "Unknown command type"}
        except Exception as e:
            logger.exception("Failed to execute admin command")
            monitor.record_error()
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/send/at")
    async def send_at(req: SendAtRequest) -> dict[str, Any]:
        """Send a message with @mention in a group chat."""
        msg_id = msg_logger.log_send(req.contact, "at", f"@{req.at_name} {req.message}", status="sending")
        try:
            success = await engine.send_at_message(req.contact, req.at_name, req.message)
            status = "success" if success else "failed"
            msg_logger.update_status(msg_id, status)
            monitor.record_message()
            return {"success": success, "message_id": msg_id, "at_name": req.at_name}
        except Exception as e:
            msg_logger.update_status(msg_id, "error", str(e))
            monitor.record_error()
            raise HTTPException(status_code=500, detail=str(e))

    # ------------------------------------------------------------ monitor API
    @app.get("/api/monitor/list")
    async def monitor_list() -> dict[str, Any]:
        if not listener:
            raise HTTPException(status_code=503, detail="监听引擎未启用")
        return {
            "running": listener.running,
            "contacts": listener.get_listen_list(),
            "poll_interval_sec": config.monitor.poll_interval_sec,
            "poll_interval_std_sec": config.monitor.poll_interval_std_sec,
            "scroll_pages": config.monitor.scroll_pages,
        }

    @app.post("/api/monitor/add")
    async def monitor_add(req: MonitorContactRequest) -> dict[str, Any]:
        if not listener:
            raise HTTPException(status_code=503, detail="监听引擎未启用")
        ok = listener.add_listen(req.contact)
        config.save()
        return {"success": ok, "contact": req.contact, "contacts": listener.get_listen_list()}

    @app.post("/api/monitor/remove")
    async def monitor_remove(req: MonitorContactRequest) -> dict[str, Any]:
        if not listener:
            raise HTTPException(status_code=503, detail="监听引擎未启用")
        ok = listener.remove_listen(req.contact)
        config.save()
        return {"success": ok, "contact": req.contact, "contacts": listener.get_listen_list()}

    @app.post("/api/monitor/start")
    async def monitor_start() -> dict[str, Any]:
        if not listener:
            raise HTTPException(status_code=503, detail="监听引擎未启用")
        await listener.start()
        return {"success": True, "running": listener.running}

    @app.post("/api/monitor/stop")
    async def monitor_stop() -> dict[str, Any]:
        if not listener:
            raise HTTPException(status_code=503, detail="监听引擎未启用")
        await listener.stop()
        return {"success": True, "running": listener.running}

    @app.get("/api/monitor/messages")
    async def monitor_messages(limit: int = 50, since: int = 0) -> dict[str, Any]:
        if not listener:
            raise HTTPException(status_code=503, detail="监听引擎未启用")
        msgs = listener.get_messages(limit=limit, since=since)
        return {"count": len(msgs), "messages": msgs}

    @app.get("/api/audit/log")
    async def audit_log(limit: int = 100) -> dict[str, Any]:
        """Latest operation audit entries (risk-control / anti-detection tracing)."""
        from .core.audit import get_audit_log
        entries = get_audit_log(limit=limit)
        return {"count": len(entries), "entries": entries}

    # ------------------------------------------------------------ browse toggle
    @app.get("/api/browse")
    async def browse_get() -> dict[str, Any]:
        """Read the simulate-web-browsing toggle state (default off)."""
        from .tui import bus

        return {"enabled": bus.is_browse_enabled()}

    @app.post("/api/browse")
    async def browse_set(req: dict[str, Any]) -> dict[str, Any]:
        """Set the simulate-web-browsing toggle."""
        from .tui import bus

        enabled = bool(req.get("enabled", False))
        bus.set_browse_enabled(enabled)
        logger.info("网页浏览开关（后台）-> %s", "开" if enabled else "关")
        return {"success": True, "enabled": bus.is_browse_enabled()}

    # ------------------------------------------------------------ plugin API
    @app.get("/api/plugins")
    async def plugins_list() -> dict[str, Any]:
        if plugin_manager is None:
            raise HTTPException(status_code=503, detail="插件系统未启用")
        return {"count": len(plugin_manager.get_plugin_list()), "plugins": plugin_manager.get_plugin_list()}

    @app.post("/api/plugins/reload")
    async def plugins_reload() -> dict[str, Any]:
        if plugin_manager is None:
            raise HTTPException(status_code=503, detail="插件系统未启用")
        result = plugin_manager.reload_plugins()
        return {"success": True, **result}

    @app.post("/api/plugins/{name}/enable")
    async def plugin_enable(name: str) -> dict[str, Any]:
        if plugin_manager is None:
            raise HTTPException(status_code=503, detail="插件系统未启用")
        ok = plugin_manager.set_enabled(name, True)
        return {"success": ok, "name": name}

    @app.post("/api/plugins/{name}/disable")
    async def plugin_disable(name: str) -> dict[str, Any]:
        if plugin_manager is None:
            raise HTTPException(status_code=503, detail="插件系统未启用")
        ok = plugin_manager.set_enabled(name, False)
        return {"success": ok, "name": name}

    @app.get("/api/plugins/{name}/settings")
    async def plugin_settings_get(name: str) -> dict[str, Any]:
        """Plugin settings: schema + current values (drives the console form)."""
        if plugin_manager is None:
            raise HTTPException(status_code=503, detail="插件系统未启用")
        rec = plugin_manager.plugins.get(name)
        if rec is None:
            raise HTTPException(status_code=404, detail=f"插件不存在: {name}")
        return {
            "name": name,
            "schema": rec.settings,
            "values": plugin_manager.get_plugin_settings(name),
        }

    @app.post("/api/plugins/{name}/settings")
    async def plugin_settings_save(name: str, req: dict[str, Any]) -> dict[str, Any]:
        """Save plugin settings (partial patch: only provided keys are kept)."""
        if plugin_manager is None:
            raise HTTPException(status_code=503, detail="插件系统未启用")
        try:
            merged = plugin_manager.save_plugin_settings(name, req)
            return {"success": True, "name": name, "values": merged}
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))

    web_dir = _get_web_dir()
    static_dir = web_dir / "dist"
    logger.info("Web 前端目录: %s（存在: %s）", static_dir, static_dir.exists())

    if static_dir.exists():
        assets_dir = static_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/")
        async def serve_root() -> FileResponse:
            return FileResponse(str(static_dir / "index.html"))

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str) -> FileResponse:
            file_path = static_dir / full_path
            if file_path.exists() and file_path.is_file():
                return FileResponse(str(file_path))
            return FileResponse(str(static_dir / "index.html"))
    else:
        logger.warning("未找到 Web 前端目录: %s", static_dir)

    return app
