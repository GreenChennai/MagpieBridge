"""统一消息库（ADR-0002）：SQLite WAL 单库，收发同表，人读导出。

设计：
- **写连接**单个、由 RLock 串行化（消息量级 ~每秒几条，锁足够，免线程队列复杂度）；
- **读连接**独立（WAL 模式下读写互不阻塞，用户在 Web 后台翻消息不影响写入）；
- `busy_timeout=5s` + `synchronous=NORMAL`：断电最多丢最后事务，不损坏库；
- 用户不直接编辑 .db —— Web 后台改/删，或导出 CSV（UTF-8 BOM，Excel 直开）。

迁移：启动时把旧版两处数据一次性并入——
1. 旧 `messages.db`（只有发出方向）→ 附库拷贝后改名 `.migrated` 保留；
2. 旧按联系人 CSV（`~/MsgPushWechat/data/messages/*.csv`，收到方向）
   → 逐行并入，原文件不动。
"""

from __future__ import annotations

import csv
import logging
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  direction TEXT NOT NULL CHECK(direction IN ('outgoing','incoming')),
  contact TEXT NOT NULL,
  sender TEXT NOT NULL DEFAULT '',
  content_type TEXT NOT NULL DEFAULT 'text',
  content TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ok',
  error TEXT,
  timestamp REAL NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_messages_contact_ts ON messages(contact, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(timestamp DESC);
"""

_TIME_COL_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?$")


def _ts(value: str) -> float:
    """CSV 里的时间列 → unix 秒；解析失败用当前时间。"""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value.strip(), fmt).timestamp()
        except Exception:
            continue
    return datetime.now().timestamp()


class MessageStore:
    """收发消息唯一持久层。接口刻意收窄：记、查、删、导出，仅此而已。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._wconn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._wconn.row_factory = sqlite3.Row
        self._rconn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._rconn.row_factory = sqlite3.Row
        for conn in (self._wconn, self._rconn):
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
        self._wconn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._wconn.executescript(_SCHEMA)
            self._wconn.commit()
        self._migrate_legacy()

    # ---------------------------------------------------------------- 写
    def log_send(
        self,
        contact: str,
        content_type: str,
        content: str,
        status: str = "pending",
        error: str | None = None,
    ) -> int:
        return self._insert("outgoing", contact, "", content_type, content, status, error)

    def log_receive(
        self, contact: str, sender: str, content_type: str, content: str
    ) -> int:
        return self._insert("incoming", contact, sender, content_type, content, "received", None)

    def _insert(
        self,
        direction: str,
        contact: str,
        sender: str,
        content_type: str,
        content: str,
        status: str,
        error: str | None,
    ) -> int:
        with self._lock:
            cur = self._wconn.execute(
                "INSERT INTO messages(direction, contact, sender, content_type, content,"
                " status, error, timestamp) VALUES(?,?,?,?,?,?,?,?)",
                (direction, contact, sender, content_type, content, status, error, time_now()),
            )
            self._wconn.commit()
            return int(cur.lastrowid or 0)

    def update_status(self, message_id: int, status: str, error: str | None = None) -> None:
        with self._lock:
            self._wconn.execute(
                "UPDATE messages SET status=?, error=? WHERE id=?", (status, error, message_id)
            )
            self._wconn.commit()

    # ---------------------------------------------------------------- 查
    def get_recent(self, limit: int = 100, contact: str | None = None) -> list[dict]:
        with self._lock:
            if contact:
                rows = self._rconn.execute(
                    "SELECT * FROM messages WHERE contact=? ORDER BY timestamp DESC LIMIT ?",
                    (contact, limit),
                ).fetchall()
            else:
                rows = self._rconn.execute(
                    "SELECT * FROM messages ORDER BY timestamp DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        with self._lock:
            row = self._rconn.execute(
                "SELECT COUNT(*) AS total,"
                " SUM(CASE WHEN status IN ('success','received') THEN 1 ELSE 0 END) AS ok,"
                " SUM(CASE WHEN status IN ('failed','error') THEN 1 ELSE 0 END) AS failed"
                " FROM messages"
            ).fetchone()
            contacts = self._rconn.execute(
                "SELECT COUNT(DISTINCT contact) AS n FROM messages"
            ).fetchone()
        return {
            "total_messages": row["total"] or 0,
            "success": row["ok"] or 0,
            "failed": row["failed"] or 0,
            "contacts": contacts["n"] or 0,
        }

    def list_contacts(self) -> list[dict]:
        with self._lock:
            rows = self._rconn.execute(
                "SELECT contact, COUNT(*) AS count, MAX(timestamp) AS last_ts"
                " FROM messages GROUP BY contact ORDER BY last_ts DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------------------------------------------------------------- 删
    def delete_by_ids(self, ids: list[int]) -> int:
        if not ids:
            return 0
        with self._lock:
            cur = self._wconn.execute(
                f"DELETE FROM messages WHERE id IN ({','.join('?' * len(ids))})", ids
            )
            self._wconn.commit()
            return cur.rowcount

    def delete_by_contact(self, contact: str) -> int:
        with self._lock:
            cur = self._wconn.execute("DELETE FROM messages WHERE contact=?", (contact,))
            self._wconn.commit()
            return cur.rowcount

    def clear_all(self, contact: str | None = None) -> int:
        with self._lock:
            if contact:
                cur = self._wconn.execute("DELETE FROM messages WHERE contact=?", (contact,))
            else:
                cur = self._wconn.execute("DELETE FROM messages")
            self._wconn.commit()
            return cur.rowcount

    # ---------------------------------------------------------------- 导出
    def export_csv(self, out_dir: str | Path, contact: str | None = None) -> list[Path]:
        """导出为 Excel 友好 CSV（UTF-8 BOM）。返回生成的文件路径列表。

        contact=None 时按会话各导一个文件（与旧版按联系人 CSV 的使用习惯一致）。
        """
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d")
        paths: list[Path] = []
        with self._lock:
            if contact:
                groups = [(contact, self._rows(contact))]
            else:
                groups = [(c["contact"], self._rows(c["contact"])) for c in self.list_contacts()]
        for name, rows in groups:
            safe = re.sub(r'[\\/:*?"<>|]', "_", name) or "all"
            path = out / f"{safe}_{stamp}.csv"
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["时间", "方向", "联系人", "发送者", "类型", "内容", "状态"])
                for r in rows:
                    w.writerow(
                        [
                            datetime.fromtimestamp(r["timestamp"]).strftime("%Y-%m-%d %H:%M:%S"),
                            "发出" if r["direction"] == "outgoing" else "收到",
                            r["contact"],
                            r["sender"],
                            r["content_type"],
                            r["content"],
                            r["status"],
                        ]
                    )
            paths.append(path)
        if not paths:
            paths = []
        logger.info("导出 CSV 完成：%d 个文件 → %s", len(paths), out)
        return paths

    def _rows(self, contact: str) -> list[dict]:
        rows = self._rconn.execute(
            "SELECT * FROM messages WHERE contact=? ORDER BY timestamp ASC", (contact,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- 迁移
    def _migrate_legacy(self) -> None:
        """旧 messages.db / 旧按联系人 CSV → 本库（一次性，幂等）。"""
        legacy_db = self.db_path.parent / "messages.db"
        if legacy_db.exists():
            try:
                n = self._import_legacy_db(legacy_db)
                if n:
                    logger.info("已从旧 messages.db 迁移 %d 条发出消息", n)
                legacy_db.rename(legacy_db.with_suffix(".db.migrated"))
            except Exception:
                logger.exception("旧 messages.db 迁移失败（保留原文件）")

        for home_dir in (
            Path.home() / "MagpieBridge" / "data" / "messages",
            Path.home() / "MsgPushWechat" / "data" / "messages",
        ):
            if not home_dir.is_dir():
                continue
            n = self._import_legacy_csv_dir(home_dir)
            if n:
                logger.info("已从 %s 迁移 %d 条收到消息", home_dir, n)

    def _import_legacy_db(self, path: Path) -> int:
        with self._lock:
            self._wconn.execute("ATTACH DATABASE ? AS legacy", (str(path),))
            try:
                cols = {
                    r["name"]
                    for r in self._wconn.execute("PRAGMA legacy.table_info(messages)").fetchall()
                }
                if "sender" not in cols:
                    self._wconn.execute(
                        "INSERT INTO messages(direction, contact, sender, content_type, content,"
                        " status, error, timestamp, created_at)"
                        " SELECT direction, contact, '', content_type, content, status, error,"
                        " timestamp, created_at FROM legacy.messages"
                    )
                else:
                    self._wconn.execute(
                        "INSERT INTO messages(direction, contact, sender, content_type, content,"
                        " status, error, timestamp, created_at)"
                        " SELECT direction, contact, sender, content_type, content, status, error,"
                        " timestamp, created_at FROM legacy.messages"
                    )
                n = self._wconn.execute("SELECT changes()").fetchone()[0]
                self._wconn.commit()
                return int(n)
            finally:
                self._wconn.execute("DETACH DATABASE legacy")

    def _import_legacy_csv_dir(self, directory: Path) -> int:
        n = 0
        with self._lock:
            existing = {
                (r["contact"], r["content"], int(r["timestamp"]))
                for r in self._rconn.execute(
                    "SELECT contact, content, timestamp FROM messages WHERE direction='incoming'"
                ).fetchall()
            }
        for f in sorted(directory.glob("*.csv")):
            contact = f.stem
            try:
                with open(f, newline="", encoding="utf-8-sig", errors="replace") as fh:
                    reader = csv.reader(fh)
                    header = next(reader, None)
                    if not header:
                        continue
                    idx = {name: i for i, name in enumerate(header)}
                    for row in reader:
                        try:
                            ts = _ts(row[idx["时间"]])
                            content = row[idx["内容"]]
                            sender = row[idx.get("发送者", 2)] if "发送者" in idx else ""
                            ctype = row[idx.get("类型", 3)] if "类型" in idx else "text"
                        except (IndexError, KeyError):
                            continue
                        if (contact, content, int(ts)) in existing:
                            continue
                        self._insert("incoming", contact, sender, ctype or "text", content, "received", None)
                        n += 1
            except Exception:
                logger.exception("旧 CSV 迁移失败: %s", f)
        return n

    def close(self) -> None:
        for conn in (self._wconn, self._rconn):
            try:
                conn.close()
            except Exception:
                pass


def time_now() -> float:
    from time import time

    return time()
