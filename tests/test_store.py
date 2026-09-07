"""统一消息库测试：收发同表、WAL 读写、导出、旧数据迁移（ADR-0002）。"""

import sqlite3
from pathlib import Path

import pytest

from magpie.core.store import MessageStore


@pytest.fixture()
def store(tmp_path: Path) -> MessageStore:
    return MessageStore(tmp_path / "data" / "magpie.db")


class TestCrud:
    def test_send_receive_same_table(self, store):
        sid = store.log_send("群A", "text", "你好", status="pending")
        rid = store.log_receive("群A", "张三", "text", "收到")
        assert sid and rid
        rows = store.get_recent(limit=10, contact="群A")
        assert len(rows) == 2
        directions = {r["direction"] for r in rows}
        assert directions == {"outgoing", "incoming"}

    def test_update_status(self, store):
        mid = store.log_send("群A", "text", "hi", status="pending")
        store.update_status(mid, "success")
        assert store.get_recent(1)[0]["status"] == "success"

    def test_stats_and_contacts(self, store):
        store.log_send("群A", "text", "a", status="success")
        store.log_receive("群A", "u", "text", "b")
        store.log_send("群B", "text", "c", status="failed")
        stats = store.get_stats()
        assert stats["total_messages"] == 3
        assert stats["success"] == 2
        assert stats["failed"] == 1
        contacts = {c["contact"] for c in store.list_contacts()}
        assert contacts == {"群A", "群B"}

    def test_delete_and_clear(self, store):
        m1 = store.log_send("群A", "text", "a")
        store.log_send("群A", "text", "b")
        store.log_send("群B", "text", "c")
        assert store.delete_by_ids([m1]) == 1
        assert store.delete_by_contact("群B") == 1
        assert store.clear_all() == 1

    def test_export_csv_bom(self, store, tmp_path):
        store.log_send("群A", "text", "内容甲")
        store.log_receive("群A", "张三", "text", "内容乙")
        paths = store.export_csv(tmp_path / "exports")
        assert len(paths) == 1
        raw = paths[0].read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM：Excel 直开不乱码
        text = raw.decode("utf-8-sig")
        assert "内容甲" in text and "收到" in text


class TestMigration:
    def test_legacy_db_migrated(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        old = data_dir / "messages.db"
        conn = sqlite3.connect(old)
        conn.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " direction TEXT, contact TEXT, content_type TEXT, content TEXT,"
            " status TEXT, error TEXT, timestamp REAL,"
            " created_at TEXT DEFAULT (datetime('now')))"
        )
        conn.execute(
            "INSERT INTO messages(direction, contact, content_type, content, status, timestamp)"
            " VALUES('outgoing','旧群','text','旧消息','success', 1700000000)"
        )
        conn.commit()
        conn.close()

        store = MessageStore(tmp_path / "data" / "magpie.db")
        rows = store.get_recent(contact="旧群")
        assert len(rows) == 1
        assert rows[0]["content"] == "旧消息"
        assert not old.exists()  # 迁移后改名保留
        assert old.with_suffix(".db.migrated").exists()

    def test_legacy_home_csv_migrated(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        csv_dir = home / "MagpieBridge" / "data" / "messages"
        csv_dir.mkdir(parents=True)
        (csv_dir / "老群.csv").write_text(
            "\ufeff时间,联系人,发送者,类型,内容\n"
            "2026-09-07 10:00:00,老群,李四,text,历史回复1\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("magpie.core.store.Path.home", lambda: home)

        store = MessageStore(tmp_path / "data" / "magpie.db")
        rows = store.get_recent(contact="老群")
        assert len(rows) == 1
        assert rows[0]["sender"] == "李四"
        assert rows[0]["direction"] == "incoming"
