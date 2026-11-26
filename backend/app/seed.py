from datetime import datetime, timedelta, timezone

from app.db import 连接

建表 = """
CREATE TABLE IF NOT EXISTS 站规 (
  键 TEXT PRIMARY KEY,
  值 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 开关版本 (
  版本键 TEXT PRIMARY KEY,
  开关键 TEXT NOT NULL,
  版本号 INTEGER NOT NULL,
  载荷 TEXT NOT NULL DEFAULT '{}',
  状态 TEXT NOT NULL DEFAULT '已入轨',
  入轨时 TEXT NOT NULL,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 分桶规 (
  桶键 TEXT PRIMARY KEY,
  开关键 TEXT NOT NULL,
  桶类型 TEXT NOT NULL,
  规则 TEXT NOT NULL DEFAULT '{}',
  状态 TEXT NOT NULL DEFAULT '生效',
  更新时 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 下发票 (
  票号 TEXT PRIMARY KEY,
  开关键 TEXT NOT NULL,
  版本键 TEXT NOT NULL,
  桶键 TEXT NOT NULL,
  状态 TEXT NOT NULL DEFAULT '待下发',
  下发时 TEXT NOT NULL DEFAULT '',
  世代 INTEGER NOT NULL DEFAULT 1,
  说明 TEXT NOT NULL DEFAULT '',
  回读截止 TEXT NOT NULL DEFAULT '',
  有效截止 TEXT NOT NULL DEFAULT '',
  边缘名册 TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS 回读证 (
  证号 TEXT PRIMARY KEY,
  票号 TEXT NOT NULL,
  边缘键 TEXT NOT NULL,
  所见版本键 TEXT NOT NULL DEFAULT '',
  结果 TEXT NOT NULL,
  回读时 TEXT NOT NULL,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 回读延期 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  票号 TEXT NOT NULL,
  延期截止 TEXT NOT NULL,
  记时 TEXT NOT NULL,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 命运账 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  对象类型 TEXT NOT NULL,
  对象键 TEXT NOT NULL,
  命运 TEXT NOT NULL,
  时刻 TEXT NOT NULL,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 装载批次 (
  编号 TEXT PRIMARY KEY,
  时刻 TEXT NOT NULL,
  报告 TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS 轨贯验 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  版本数 INTEGER NOT NULL,
  下发数 INTEGER NOT NULL,
  回读数 INTEGER NOT NULL,
  命运数 INTEGER NOT NULL,
  是否通过 INTEGER NOT NULL,
  说明 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 空桩 (
  名 TEXT PRIMARY KEY,
  状态 TEXT NOT NULL DEFAULT '空桩'
);
"""

默认站规 = {
    "站名": "灰度轨",
    "领域": "基础设施",
    "子方向": "中间件",
    "默认灰度比": "10",
    "回读齐套策略": "全边缘确认才可晋升",
    "回读截止秒": "86400",
    "最大延期次数": "1",
    "骨架": "开关版本入轨→分桶灰度下发→回读确认→失效/回滚互证",
}


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _迁移下发票(conn) -> None:
    """老库补列：回读计划截止字段 + 边缘名册；已下发票回填截止，禁止无截止挂着。"""
    已有 = {r["name"] for r in conn.execute("PRAGMA table_info(下发票)")}
    if "回读截止" not in 已有:
        conn.execute("ALTER TABLE 下发票 ADD COLUMN 回读截止 TEXT NOT NULL DEFAULT ''")
    if "有效截止" not in 已有:
        conn.execute("ALTER TABLE 下发票 ADD COLUMN 有效截止 TEXT NOT NULL DEFAULT ''")
    if "边缘名册" not in 已有:
        conn.execute("ALTER TABLE 下发票 ADD COLUMN 边缘名册 TEXT NOT NULL DEFAULT '[]'")
    秒 = int(默认站规["回读截止秒"])
    for r in conn.execute(
        "SELECT 票号, 下发时 FROM 下发票 WHERE 回读截止='' AND 下发时!=''"
    ).fetchall():
        try:
            止 = datetime.strptime(r["下发时"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            ) + timedelta(seconds=秒)
            止s = 止.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
        conn.execute(
            "UPDATE 下发票 SET 回读截止=?, 有效截止=? WHERE 票号=?", (止s, 止s, r["票号"])
        )
    conn.execute("UPDATE 下发票 SET 有效截止=回读截止 WHERE 有效截止='' AND 回读截止!=''")


def 初始化库() -> None:
    conn = 连接()
    conn.executescript(建表)
    _迁移下发票(conn)
    for k, v in 默认站规.items():
        conn.execute("INSERT OR IGNORE INTO 站规(键, 值) VALUES (?, ?)", (k, v))
    for name in [
        "分桶比例冲突", "名单与百分比互斥", "回读超时旁置", "半截下发回滚",
        "双版本同键并发", "回读偏差拒升全量", "环境桶隔离", "失效后幽灵回读",
        "下发票幂等键", "回滚窗互斥", "变体载荷契约", "边缘时钟漂移",
        "分桶重切", "全量晋升闸", "影子轨对照", "配置世代戳",
        "回读仲裁策略", "失效写闸", "跨环境串扰", "轨贯验三联",
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO 空桩(名, 状态) VALUES (?, '空桩')", (name,)
        )
    # 本桩已落地：回读截止收口
    conn.execute(
        "INSERT OR IGNORE INTO 空桩(名, 状态) VALUES (?, '已落地')", ("回读截止收口",)
    )
    conn.commit()
    conn.close()
