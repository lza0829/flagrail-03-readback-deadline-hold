"""回读截止收口：截止扫描 / 书面延期 / 截止核证 / 晋升闸。

口径（两分叉取并集，可区分点在「过原截止、延期未耗尽」）：
- 无书面延期 → 以超时收口为准：扫描把票打进「超时收口」，晋升拒绝，
  迟到确认不得把票拉回可升（回读写入侧同样失败闭）。
- 有书面延期且未耗尽 → 以书面延期为准：票保持「已下发」待确认，
  有效截止物化为延期截止；延期耗尽（有效截止也过期）才收口。
- 原截止瞬间不得直接当可升：晋升须 票态已下发 + 未过有效截止 + 边缘名册齐套。

硬约束：截止扫描、回读写入、命运条同票同事务（写事务 BEGIN IMMEDIATE）；
扫描与迟到确认并发失败闭；扫描整体原子，不得半截。
"""

import json
from datetime import datetime, timezone

from app.db import 写事务, 连接
from fastapi import HTTPException


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _站规(conn, 键: str, 默认: str) -> str:
    row = conn.execute("SELECT 值 FROM 站规 WHERE 键=?", (键,)).fetchone()
    return row["值"] if row else 默认


def _最新延期截止(conn, 票号: str) -> str:
    row = conn.execute(
        "SELECT MAX(延期截止) AS d FROM 回读延期 WHERE 票号=?", (票号,)
    ).fetchone()
    return (row["d"] or "") if row else ""


def _齐套(conn, 票号: str, 名册: list) -> list:
    """返回缺口边缘：名册中还没有「确认」回读的边缘。"""
    已确认 = {
        r["边缘键"]
        for r in conn.execute(
            "SELECT DISTINCT 边缘键 FROM 回读证 WHERE 票号=? AND 结果='确认'", (票号,)
        )
    }
    return [e for e in 名册 if e not in 已确认]


def 核证(conn, 票号: str) -> dict:
    """截止核证：返回 截止、延期截止、票态、可否晋升、valid。

    valid=False 即账实不符：已下发却已过有效截止（假已下发可升），
    或超时收口/已晋升却缺命运条（半截）。
    """
    t = conn.execute("SELECT * FROM 下发票 WHERE 票号=?", (票号,)).fetchone()
    if not t:
        raise HTTPException(404, f"下发票不存在:{票号}")
    回读截止 = t["回读截止"] or ""
    延期截止 = _最新延期截止(conn, 票号)
    有效截止 = max(回读截止, 延期截止) if (回读截止 or 延期截止) else ""
    try:
        名册 = json.loads(t["边缘名册"] or "[]")
    except Exception:
        名册 = []
    缺口 = _齐套(conn, 票号, 名册)
    齐套 = bool(名册) and not 缺口
    过期 = bool(有效截止) and 此刻() > 有效截止
    票态 = t["状态"]

    可否晋升 = 票态 == "已下发" and not 过期 and 齐套
    问题 = []
    if 票态 == "已下发" and 过期:
        问题.append("假已下发可升：已过有效截止仍未收口")
    if 票态 == "超时收口" and not conn.execute(
        "SELECT 1 FROM 命运账 WHERE 对象类型='下发票' AND 对象键=? AND 命运='超时收口'",
        (票号,),
    ).fetchone():
        问题.append("超时收口缺命运条（半截）")
    if 票态 == "已晋升" and not conn.execute(
        "SELECT 1 FROM 命运账 WHERE 对象类型='下发票' AND 对象键=? AND 命运='晋升'",
        (票号,),
    ).fetchone():
        问题.append("已晋升缺命运条（半截）")
    if not 名册:
        问题.append("未约定边缘名册")
    elif 缺口:
        问题.append(f"缺口边缘:{','.join(缺口)}")

    return {
        "票号": 票号,
        "截止": 回读截止,
        "延期截止": 延期截止,
        "有效截止": 有效截止,
        "票态": 票态,
        "齐套": 齐套,
        "缺口边缘": 缺口,
        "可否晋升": 可否晋升,
        "valid": not any("假已下发" in p or "半截" in p for p in 问题),
        "说明": "；".join(问题) if 问题 else "齐",
    }


def 核证票(票号: str) -> dict:
    conn = 连接()
    try:
        return 核证(conn, 票号)
    finally:
        conn.close()


def 跑扫描(体: dict | None = None) -> dict:
    """截止扫描：每张已下发票——过有效截止则收口（同票写命运条），
    有未耗尽书面延期则物化新有效截止。整体一个写事务，不得半截。"""
    现在 = 此刻()
    收口 = []
    物化 = []
    with 写事务() as conn:
        rows = conn.execute(
            "SELECT 票号, 回读截止, 有效截止 FROM 下发票 WHERE 状态='已下发'"
        ).fetchall()
        for r in rows:
            票号 = r["票号"]
            回读截止 = r["回读截止"] or ""
            if not 回读截止:
                continue  # 无计划截止的老票由贯验点名，扫描不替它决定
            延期截止 = _最新延期截止(conn, 票号)
            有效截止 = max(回读截止, 延期截止)
            if 现在 > 有效截止:
                # 以超时收口为准（延期耗尽亦走此处）：票态 + 命运条同票落
                conn.execute(
                    "UPDATE 下发票 SET 状态='超时收口', 有效截止=? WHERE 票号=?",
                    (有效截止, 票号),
                )
                conn.execute(
                    "INSERT INTO 命运账(对象类型, 对象键, 命运, 时刻, 说明) VALUES (?,?,?,?,?)",
                    ("下发票", 票号, "超时收口", 现在, "回读截止收口：过有效截止未齐套"),
                )
                收口.append(票号)
            elif 有效截止 != (r["有效截止"] or ""):
                # 以书面延期为准：票保持待确认，物化新截止
                conn.execute(
                    "UPDATE 下发票 SET 有效截止=? WHERE 票号=?", (有效截止, 票号)
                )
                物化.append({"票号": 票号, "有效截止": 有效截止})
    return {"扫描时": 现在, "收口": 收口, "物化": 物化, "扫描数": len(rows)}


def 写延期(体: dict) -> dict:
    """书面延期：落延期实体并物化有效截止。已收口/已回滚票不可延期（不可复活）。"""
    票号 = 体["票号"]
    延期截止 = 体["延期截止"]
    with 写事务() as conn:
        t = conn.execute("SELECT * FROM 下发票 WHERE 票号=?", (票号,)).fetchone()
        if not t:
            raise HTTPException(400, f"下发票不存在:{票号}")
        已有 = conn.execute(
            "SELECT 1 FROM 回读延期 WHERE 票号=? AND 延期截止=?", (票号, 延期截止)
        ).fetchone()
        if 已有 and t["状态"] == "已下发":
            # 幂等重放：不重复计次、不重复落命运，但仍把有效截止物化回来
            conn.execute(
                "UPDATE 下发票 SET 有效截止=MAX(有效截止, ?) WHERE 票号=?",
                (延期截止, 票号),
            )
            return {"票号": 票号, "延期截止": 延期截止, "有效截止": 延期截止, "幂等": True}
        if t["状态"] != "已下发":
            raise HTTPException(400, f"票状态不可延期:{t['状态']}")
        当前有效 = max(t["回读截止"] or "", _最新延期截止(conn, 票号))
        if 当前有效 and 延期截止 <= 当前有效:
            raise HTTPException(400, f"延期截止须晚于当前有效截止:{当前有效}")
        上限 = int(_站规(conn, "最大延期次数", "1"))
        已次数 = conn.execute(
            "SELECT COUNT(*) AS c FROM 回读延期 WHERE 票号=?", (票号,)
        ).fetchone()["c"]
        if 已次数 >= 上限:
            raise HTTPException(400, f"延期耗尽:{票号} 已{已次数}次/上限{上限}")
        现在 = 此刻()
        conn.execute(
            "INSERT INTO 回读延期(票号, 延期截止, 记时, 说明) VALUES (?,?,?,?)",
            (票号, 延期截止, 现在, 体.get("说明", "")),
        )
        conn.execute(
            "UPDATE 下发票 SET 有效截止=? WHERE 票号=?", (延期截止, 票号)
        )
        conn.execute(
            "INSERT INTO 命运账(对象类型, 对象键, 命运, 时刻, 说明) VALUES (?,?,?,?,?)",
            ("下发票", 票号, "延期", 现在, f"书面延期至{延期截止}"),
        )
    return {"票号": 票号, "延期截止": 延期截止, "有效截止": 延期截止, "次数": 已次数 + 1}


def 晋升票(体: dict) -> dict:
    """晋升闸：核证不过即拒（失败闭）。过了才同票落 票态+版本态+命运条。"""
    票号 = 体["票号"]
    with 写事务() as conn:
        t = conn.execute("SELECT * FROM 下发票 WHERE 票号=?", (票号,)).fetchone()
        if not t:
            raise HTTPException(400, f"下发票不存在:{票号}")
        if t["状态"] == "已晋升":
            return {"票号": 票号, "状态": "已晋升", "幂等": True}
        证 = 核证(conn, 票号)
        if not 证["可否晋升"]:
            raise HTTPException(400, f"不可晋升:{证['说明']}")
        现在 = 此刻()
        conn.execute("UPDATE 下发票 SET 状态='已晋升' WHERE 票号=?", (票号,))
        conn.execute(
            "UPDATE 开关版本 SET 状态='已晋升' WHERE 版本键=?", (t["版本键"],)
        )
        conn.execute(
            "INSERT INTO 命运账(对象类型, 对象键, 命运, 时刻, 说明) VALUES (?,?,?,?,?)",
            ("下发票", 票号, "晋升", 现在, 体.get("说明", "灰度晋升全量")),
        )
        conn.execute(
            "INSERT INTO 命运账(对象类型, 对象键, 命运, 时刻, 说明) VALUES (?,?,?,?,?)",
            ("开关版本", t["版本键"], "晋升", 现在, "随票晋升"),
        )
    return {"票号": 票号, "状态": "已晋升", "幂等": False}


def 列延期(票号: str = "") -> list:
    conn = 连接()
    try:
        if 票号:
            rows = conn.execute(
                "SELECT * FROM 回读延期 WHERE 票号=? ORDER BY 编号 DESC", (票号,)
            )
        else:
            rows = conn.execute("SELECT * FROM 回读延期 ORDER BY 编号 DESC LIMIT 200")
        return [dict(r) for r in rows]
    finally:
        conn.close()


def 拨截止(票号们: list, 到: str) -> dict:
    """夹具专用：把票的回读截止/有效截止拨到指定时刻，模拟「截止到了」。"""
    with 写事务() as conn:
        for 票号 in 票号们:
            conn.execute(
                "UPDATE 下发票 SET 回读截止=?, 有效截止=? WHERE 票号=?",
                (到, 到, 票号),
            )
    return {"票号": 票号们, "拨到": 到}


def 装截止夹具(体: dict) -> dict:
    """截止演练夹具：入轨→分桶→下发（带回读截止/边缘名册）→回读
    →拨截止→书面延期→截止扫描，一站跑完甲乙双票剧本。"""
    from app.services import 入轨, 分桶, 下发, 回读

    批次 = 体.get("批次编号") or f"截止-{此刻()}"
    报告 = {"类型": "截止演练"}
    if 体.get("版本"):
        报告["入轨"] = 入轨.入一批(
            {"批次编号": f"{批次}-入轨", "版本": 体["版本"]}
        )["报告"]
    if 体.get("分桶"):
        报告["分桶"] = [分桶.写桶(b) for b in 体["分桶"]]
    if 体.get("下发"):
        报告["下发"] = [下发.下一票(d) for d in 体["下发"]]
    if 体.get("回读"):
        outs = []
        for r0 in 体["回读"]:
            try:
                outs.append(回读.记回读(r0))
            except HTTPException as e:
                outs.append(
                    {
                        "票号": r0.get("票号", ""),
                        "边缘键": r0.get("边缘键", ""),
                        "结果": "拒绝",
                        "错误": str(e.detail),
                    }
                )
        报告["回读"] = outs
    if 体.get("拨截止"):
        拨 = 体["拨截止"]
        报告["拨截止"] = 拨截止(拨["票号"], 拨["到"])
    if 体.get("延期"):
        outs = []
        for e0 in 体["延期"]:
            try:
                outs.append(写延期(e0))
            except HTTPException as e:
                outs.append(
                    {"票号": e0.get("票号", ""), "结果": "拒绝", "错误": str(e.detail)}
                )
        报告["延期"] = outs
    if 体.get("扫描"):
        报告["扫描"] = 跑扫描({})
    conn = 连接()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO 装载批次(编号, 时刻, 报告) VALUES (?,?,?)",
            (批次, 此刻(), json.dumps(报告, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()
    return {"批次编号": 批次, "报告": 报告}
