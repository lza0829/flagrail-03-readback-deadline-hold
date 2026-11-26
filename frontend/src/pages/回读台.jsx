import React, { useEffect, useState } from "react";
import { get, post, postFixture } from "../api.js";

export default function 回读台() {
  const [rows, setRows] = useState([]);
  const [票们, set票们] = useState([]);
  const [讯, set讯] = useState("");
  const [证, set证] = useState(null);
  const 刷 = async () => {
    const [a, b] = await Promise.all([get("/回读证"), get("/下发票")]);
    setRows(a);
    set票们(b);
  };
  useEffect(() => { 刷().catch((e) => set讯(String(e))); }, []);
  const 灌 = async (名, 话) => {
    try {
      const r = await postFixture(名);
      const 结果 = r?.报告?.结果;
      const 拒 = 结果 ? 结果.filter((x) => x.结果 === "拒绝").length : 0;
      await 刷();
      set讯(拒 ? `${话}（拒 ${拒} 笔）` : 话);
    } catch (e) { set讯(String(e)); }
  };
  const 扫 = async () => {
    try {
      const r = await post("/截止扫描");
      await 刷();
      set讯(`扫描毕：收口 ${r.收口.length} 票，物化延期 ${r.物化.length} 票`);
    } catch (e) { set讯(String(e)); }
  };
  const 核 = async (票号) => {
    try { set证(await get(`/截止核证?票号=${encodeURIComponent(票号)}`)); }
    catch (e) { set讯(String(e)); }
  };
  const 徽章 = (s) =>
    s === "已下发" ? "信" : s === "已晋升" ? "通" : "败";
  return (
    <div>
      <h1 className="题">回读台</h1>
      <p className="述">边缘对下发票的确认。所见版本与票面不一致 → 记「偏差」；过有效截止的迟到确认失败闭，已收口票不可复活。</p>
      <div className="行">
        <button onClick={() => 灌("回读样例", "已灌回读样例")}>灌回读样例</button>
        <button onClick={() => 灌("回读截止样例", "已灌截止样例：甲收口·乙延期")}>灌截止样例</button>
        <button onClick={() => 灌("回读迟到样例", "已灌迟到确认")}>灌迟到确认</button>
        <button className="焰" onClick={扫}>跑截止扫描</button>
        <button className="次" onClick={() => 刷()}>刷新</button>
      </div>
      {讯 && <p className="讯">{讯}</p>}
      {证 && (
        <p className="讯">
          核证 {证.票号}：票态 {证.票态} · 截止 {证.截止 || "—"} · 延期截止 {证.延期截止 || "—"} ·
          有效截止 {证.有效截止 || "—"} · 可否晋升 {证.可否晋升 ? "可" : "否"} · valid {String(证.valid)} · {证.说明}
        </p>
      )}
      <h2 className="题">票截止与延期</h2>
      {!票们.length ? <div className="空态">尚无下发票</div> : (
        <table>
          <thead><tr><th>票号</th><th>票态</th><th>回读截止</th><th>延期截止</th><th>有效截止</th><th></th></tr></thead>
          <tbody>{票们.map((r) => (
            <tr key={r.票号}><td>{r.票号}</td>
            <td><span className={"徽章 " + 徽章(r.状态)}>{r.状态}</span></td>
            <td>{r.回读截止 || "—"}</td><td>{r.延期截止 || "—"}</td><td>{r.有效截止 || "—"}</td>
            <td><button className="次" onClick={() => 核(r.票号)}>核证</button></td></tr>
          ))}</tbody>
        </table>
      )}
      <h2 className="题">回读证</h2>
      {!rows.length ? <div className="空态">尚无回读证</div> : (
        <table>
          <thead><tr><th>证号</th><th>票号</th><th>边缘</th><th>所见版本</th><th>结果</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.证号}><td>{r.证号}</td><td>{r.票号}</td><td>{r.边缘键}</td>
            <td>{r.所见版本键}</td>
            <td><span className={"徽章 " + (r.结果 === "确认" ? "通" : "败")}>{r.结果}</span></td></tr>
          ))}</tbody>
        </table>
      )}
    </div>
  );
}
