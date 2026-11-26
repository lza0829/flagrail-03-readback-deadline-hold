import os
import sys
import tempfile
from pathlib import Path

# 必须在 import app 之前落位：库路径在 app.db 导入时定死
os.environ.setdefault("数据目录", tempfile.mkdtemp(prefix="灰度轨测试-"))
os.environ.setdefault("夹具目录", str(Path(__file__).resolve().parents[2] / "fixtures"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app import seed  # noqa: E402
from app.db import 连接  # noqa: E402

所有表 = [
    "站规", "开关版本", "分桶规", "下发票", "回读证", "回读延期",
    "命运账", "装载批次", "轨贯验", "空桩",
]


@pytest.fixture(autouse=True)
def 净库():
    seed.初始化库()  # 先保表存在
    conn = 连接()
    try:
        for t in 所有表:
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
    finally:
        conn.close()
    seed.初始化库()  # 重灌站规/空桩
    yield
