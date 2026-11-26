import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

数据目录 = Path(os.environ.get("数据目录", Path(__file__).resolve().parent.parent / "data"))
夹具目录 = Path(os.environ.get("夹具目录", Path(__file__).resolve().parent.parent.parent / "fixtures"))
库路径 = 数据目录 / "灰度轨.db"


def 连接() -> sqlite3.Connection:
    数据目录.mkdir(parents=True, exist_ok=True)
    # timeout 放宽：截止扫描与回读写入并发时，后到者排队而非立刻报 locked
    c = sqlite3.connect(库路径, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


@contextmanager
def 写事务():
    """BEGIN IMMEDIATE 写事务：进事务即取写锁。

    截止扫描 / 回读写入 / 延期 / 晋升 共用——扫描与迟到确认并发时串行化，
    后到者读到已收口票态而拒绝，失败闭；扫描整体要么全落要么全回滚，不得半截。
    """
    conn = 连接()
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
