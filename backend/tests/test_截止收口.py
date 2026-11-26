"""回读截止收口：双夹具 + 并发失败闭 + 贯验协同。

剧本：两边缘一确认 → 拨截止 → 甲超时收口 / 乙书面延期物化 →
再灌迟到确认 → 甲不可复活可升 / 乙计入延期窗口；并发扫描不得半截。
"""

import threading

import pytest
from fastapi.testclient import TestClient

from app.db import 连接
from app.main import app
from app.services import 下发, 回读, 截止, 贯验

甲 = "票-甲-checkout.new_tax@4-gray20"
乙 = "票-乙-checkout.new_tax@4-gray20"


@pytest.fixture()
def 台():
    with TestClient(app) as c:
        yield c


def 装截止样例(台):
    r = 台.post("/api/装载/夹具", params={"名": "回读截止样例"})
    assert r.status_code == 200, r.text
    return r.json()["报告"]


def 票行(票号):
    conn = 连接()
    try:
        return dict(
            conn.execute("SELECT * FROM 下发票 WHERE 票号=?", (票号,)).fetchone()
        )
    finally:
        conn.close()


def 命运数(票号, 命运):
    conn = 连接()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM 命运账 WHERE 对象类型='下发票' AND 对象键=? AND 命运=?",
            (票号, 命运),
        ).fetchone()[0]
    finally:
        conn.close()


def 回读数(票号, 边缘键):
    conn = 连接()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM 回读证 WHERE 票号=? AND 边缘键=?", (票号, 边缘键)
        ).fetchone()[0]
    finally:
        conn.close()


def test_甲超时收口_乙延期物化(台):
    报告 = 装截止样例(台)
    assert 报告["扫描"]["收口"] == [甲]

    甲行 = 票行(甲)
    assert 甲行["状态"] == "超时收口"
    assert 甲行["回读截止"] == "2020-01-01T00:00:00Z"
    assert 命运数(甲, "超时收口") == 1  # 命运条同票

    乙行 = 票行(乙)
    assert 乙行["状态"] == "已下发"  # 书面延期为准：保持待确认
    assert 乙行["回读截止"] == "2020-01-01T00:00:00Z"
    assert 乙行["有效截止"] == "2099-01-02T00:00:00Z"  # 物化新截止
    延 = 台.get("/api/回读延期", params={"票号": 乙}).json()
    assert len(延) == 1 and 延[0]["延期截止"] == "2099-01-02T00:00:00Z"

    # 必须可区分点：过原截止、延期未耗尽——晋升拒绝，但票态不是超时收口
    证乙 = 台.get("/api/截止核证", params={"票号": 乙}).json()
    assert 证乙["票态"] == "已下发"
    assert 证乙["可否晋升"] is False  # 缺口 edge-d，原截止瞬间不得直接当可升
    assert 证乙["截止"] == "2020-01-01T00:00:00Z"
    assert 证乙["延期截止"] == "2099-01-02T00:00:00Z"
    assert 证乙["valid"] is True

    证甲 = 台.get("/api/截止核证", params={"票号": 甲}).json()
    assert 证甲["票态"] == "超时收口"
    assert 证甲["可否晋升"] is False
    assert 证甲["valid"] is True  # 收口+命运条齐，账实相符


def test_迟到确认_甲不可复活_乙计入窗口(台):
    装截止样例(台)
    r = 台.post("/api/装载/夹具", params={"名": "回读迟到样例"})
    assert r.status_code == 200, r.text
    结果 = {x["边缘键"]: x for x in r.json()["报告"]["结果"]}

    # 迟到确认脚本断言：甲（超时收口）被拒，不得复活可升
    assert 结果["edge-b"]["结果"] == "拒绝"
    assert "超时收口" in 结果["edge-b"]["错误"]
    assert 票行(甲)["状态"] == "超时收口"
    assert 回读数(甲, "edge-b") == 0

    # 乙在延期窗口内：迟到确认计入
    assert 结果["edge-d"]["结果"] == "确认"
    assert 回读数(乙, "edge-d") == 1

    # 直灌迟到确认同样失败闭
    r = 台.post("/api/回读", json={"票号": 甲, "边缘键": "edge-b"})
    assert r.status_code == 400

    # 已收口票不可延期（不可复活）
    r = 台.post(
        "/api/回读延期", json={"票号": 甲, "延期截止": "2099-01-03T00:00:00Z"}
    )
    assert r.status_code == 400

    # 乙齐套后可晋升；甲晋升拒绝
    证乙 = 台.get("/api/截止核证", params={"票号": 乙}).json()
    assert 证乙["齐套"] is True and 证乙["可否晋升"] is True
    r = 台.post("/api/晋升", json={"票号": 甲})
    assert r.status_code == 400
    r = 台.post("/api/晋升", json={"票号": 乙})
    assert r.status_code == 200 and r.json()["状态"] == "已晋升"
    assert 票行(乙)["状态"] == "已晋升"
    assert 命运数(乙, "晋升") == 1

    g = 台.post("/api/贯验").json()
    assert g["是否通过"] is True, g["说明"]


def test_并发扫描与迟到确认_失败闭(台):
    装截止样例(台)
    # 丙：已过期未扫描，扫描与迟到确认正面相撞
    下发.下一票(
        {
            "票号": "票-丙-并发",
            "版本键": "checkout.new_tax@4",
            "桶键": "checkout.new_tax-gray20",
            "回读截止": "2020-06-01T00:00:00Z",
            "边缘名册": ["edge-e"],
        }
    )

    异常 = []

    def 扫():
        try:
            截止.跑扫描({})
        except Exception as e:  # noqa: BLE001
            异常.append(("扫", e))

    def 迟(票号, 边缘):
        try:
            回读.记回读({"票号": 票号, "边缘键": 边缘})
        except Exception as e:  # noqa: BLE001
            异常.append((f"迟-{票号}-{边缘}", e))

    线程 = (
        [threading.Thread(target=扫) for _ in range(3)]
        + [threading.Thread(target=迟, args=("票-丙-并发", "edge-e")) for _ in range(3)]
        + [threading.Thread(target=迟, args=(甲, "edge-b")) for _ in range(2)]
        + [threading.Thread(target=迟, args=(乙, "edge-d")) for _ in range(2)]
    )
    for t in 线程:
        t.start()
    for t in 线程:
        t.join()

    # 迟到确认对过期/已收口票必须全部失败闭
    丙迟 = [e for k, e in 异常 if k == "迟-票-丙-并发-edge-e"]
    assert len(丙迟) == 3
    assert 回读数("票-丙-并发", "edge-e") == 0
    assert [e for k, e in 异常 if k == f"迟-{甲}-edge-b"]
    assert 回读数(甲, "edge-b") == 0

    # 并发扫描不得半截：收口票恰好一条命运条，票态一致
    assert 票行("票-丙-并发")["状态"] == "超时收口"
    assert 命运数("票-丙-并发", "超时收口") == 1
    assert 命运数(甲, "超时收口") == 1
    assert 票行(甲)["状态"] == "超时收口"

    # 乙的窗口内确认幂等落一条
    assert 回读数(乙, "edge-d") == 1

    g = 贯验.跑贯验()
    assert g["是否通过"] is True, g["说明"]


def test_贯验抓假已下发可升(台):
    装截止样例(台)  # 备版本与桶
    下发.下一票(
        {
            "票号": "票-丁-假升",
            "版本键": "checkout.new_tax@4",
            "桶键": "checkout.new_tax-gray20",
            "回读截止": "2020-01-01T00:00:00Z",
            "边缘名册": ["edge-x"],
        }
    )

    # 截止到了票还挂着已下发：贯验点名，核证 valid=False，晋升拒绝
    g = 台.post("/api/贯验").json()
    assert g["是否通过"] is False and "假已下发可升:票-丁-假升" in g["说明"]
    证 = 台.get("/api/截止核证", params={"票号": "票-丁-假升"}).json()
    assert 证["valid"] is False and 证["可否晋升"] is False
    r = 台.post("/api/晋升", json={"票号": "票-丁-假升"})
    assert r.status_code == 400

    # 扫描收口后：贯验恢复通过，核证 valid
    r = 台.post("/api/截止扫描")
    assert "票-丁-假升" in r.json()["收口"]
    g = 台.post("/api/贯验").json()
    assert g["是否通过"] is True, g["说明"]
    证 = 台.get("/api/截止核证", params={"票号": "票-丁-假升"}).json()
    assert 证["票态"] == "超时收口" and 证["valid"] is True


def test_延期耗尽才收口(台):
    装截止样例(台)
    下发.下一票(
        {
            "票号": "票-戊-耗尽",
            "版本键": "checkout.new_tax@4",
            "桶键": "checkout.new_tax-gray20",
            "回读截止": "2099-06-01T00:00:00Z",
            "边缘名册": ["edge-y"],
        }
    )
    截止.拨截止(["票-戊-耗尽"], "2020-01-01T00:00:00Z")

    # 书面延期一次：有效截止物化；次数到顶再延须拒
    r = 台.post(
        "/api/回读延期", json={"票号": "票-戊-耗尽", "延期截止": "2021-01-01T00:00:00Z"}
    )
    assert r.status_code == 200 and r.json()["有效截止"] == "2021-01-01T00:00:00Z"
    r = 台.post(
        "/api/回读延期", json={"票号": "票-戊-耗尽", "延期截止": "2022-01-01T00:00:00Z"}
    )
    assert r.status_code == 400 and "延期耗尽" in r.json()["detail"]

    # 延期截止不得倒退
    装截止样例(台)
    r = 台.post(
        "/api/回读延期", json={"票号": 乙, "延期截止": "2099-01-01T12:00:00Z"}
    )
    assert r.status_code == 400

    # 延期窗口自身也过期（耗尽）→ 夹具内扫描已收口；再扫幂等不重复
    assert 票行("票-戊-耗尽")["状态"] == "超时收口"
    assert 命运数("票-戊-耗尽", "超时收口") == 1
    r = 台.post("/api/截止扫描")
    assert "票-戊-耗尽" not in r.json()["收口"]
