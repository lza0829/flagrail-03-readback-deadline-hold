import json
from datetime import datetime, timezone

from app.db import 写事务, 连接
from fastapi import HTTPException


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def 记回读(体: dict) -> dict:
    # 写事务：与截止扫描串行化——扫描先落收口则此处读到「超时收口」拒绝，
    # 迟到确认不得把票拉回可升（失败闭）。
    with 写事务() as conn:
        票号 = 体["票号"]
        ticket = conn.execute("SELECT * FROM 下发票 WHERE 票号=?", (票号,)).fetchone()
        if not ticket:
            raise HTTPException(400, f"下发票不存在:{票号}")
        if ticket["状态"] not in ("已下发", "回滚中"):
            raise HTTPException(400, f"票状态不可回读:{ticket['状态']}")
        有效截止 = ticket["有效截止"] or ticket["回读截止"] or ""
        if 有效截止 and 此刻() > 有效截止:
            raise HTTPException(400, f"回读已截止:{票号} 有效截止:{有效截止}")
        证号 = 体.get("证号") or f"证-{票号}-{体['边缘键']}"
        所见 = 体.get("所见版本键") or ticket["版本键"]
        结果 = 体.get("结果", "确认")
        if 结果 == "确认" and 所见 != ticket["版本键"]:
            结果 = "偏差"
        conn.execute(
            """INSERT INTO 回读证(证号, 票号, 边缘键, 所见版本键, 结果, 回读时, 说明)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(证号) DO UPDATE SET
                 所见版本键=excluded.所见版本键, 结果=excluded.结果,
                 回读时=excluded.回读时, 说明=excluded.说明""",
            (证号, 票号, 体["边缘键"], 所见, 结果, 此刻(), 体.get("说明", "")),
        )
    return {"证号": 证号, "票号": 票号, "边缘键": 体["边缘键"], "结果": 结果}


def 装回读夹具(体: dict) -> dict:
    批次 = 体.get("批次编号") or f"回读-{此刻()}"
    结果 = []
    for item in 体.get("回读") or []:
        try:
            结果.append(记回读(item))
        except HTTPException as e:
            # 迟到/拒收也入报告：超时收口不可复活要在台面上看得见
            结果.append(
                {
                    "票号": item.get("票号", ""),
                    "边缘键": item.get("边缘键", ""),
                    "结果": "拒绝",
                    "错误": str(e.detail),
                }
            )
    conn = 连接()
    try:
        报告 = {"类型": "回读", "结果": 结果}
        conn.execute(
            "INSERT OR REPLACE INTO 装载批次(编号, 时刻, 报告) VALUES (?,?,?)",
            (批次, 此刻(), json.dumps(报告, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()
    return {"批次编号": 批次, "报告": 报告}
