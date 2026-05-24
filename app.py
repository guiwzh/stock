"""A股价值+技术选股 · 网页界面

启动：  streamlit run app.py
"""
import net_patch  # noqa: F401  最先导入
import datetime
import pandas as pd
import streamlit as st
import screener

st.set_page_config(page_title="A股选股助手", page_icon="📈", layout="wide")

st.title("📈 A股选股助手 · 价值 + 技术 + 估值分位")
st.caption("综合分 = 价值面 + 技术面 + 估值分位（PB 历史百分位），权重随「投资风格」切换："
           "短线波段 0.15/0.30/0.55，长线价值 0.57/0.31/0.12（技术分统一用反转式，经回测样本外验证）。"
           "数据来自东方财富 / 腾讯 / baostock，仅供研究参考，不构成投资建议。")

with st.expander("📐 选股逻辑详解"):
    st.markdown("""
### 🧮 综合评分公式

**综合分 = w₁ × 价值分 + w₂ × 技术分 + w₃ × 估值分位**，权重随侧边栏「投资风格」切换：

| 风格 | 价值 | 技术 | 估值分位 | 适用 |
|------|------|------|----------|------|
| **短线波段** | 0.15 | 0.30 | **0.55** | ≤3 个月持有，估值低位 + 反转技术 |
| **长线价值** | **0.57** | 0.31 | 0.12 | 长期持有，基本面主导（反转式技术）|

> 💡 **短线权重由回测校准**：在 A 股主板 ~3 个月维度，「估值便宜」最有效、「反转技术」（超跌/超卖）为辅，
> 而追涨式动量反而与未来收益负相关。年报基本面 3 个月内几乎不变，故价值面仅留小权重当质量底。
> 详见 `backtest.py`。

---

### 📊 价值面（内部权重）— 含现金流质量调节

| 指标 | 权重 | 评分逻辑 |
|------|------|----------|
| **ROE** | 28% | 分段线性：0%→10分 / 5%→45分 / 10%→65分 / 15%→82分 / 20%→95分 / 30%→100分 |
| **PE（动态）** | 20% | 亏损→5分；8倍→100分 / 15倍→92分 / 25倍→78分 / 40倍→58分 / 60倍→38分 |
| **净利润同比** | 16% | 分段线性：-30%→8分 / 0%→55分 / 15%→78分 / 30%→92分 / 60%→100分 |
| **PB** | 14% | 0.8倍→95分 / 2倍→88分 / 4倍→70分 / 7倍→48分 / 12倍→25分 |
| **毛利率** | 12% | 5%→35分 / 15%→58分 / 25%→75分 / 40%→92分 / 60%→100分 |
| **营收同比** | 10% | 同净利润同比标准 |

> ⚠️ **现金流质量乘数**：价值分 × (经营现金流/净利润比)
> - 现金流为负 → ×0.55（大雷，利润无现金支撑）
> - 严重不足 → ×0.75 / 偏弱 → ×0.90 / 正常 → ×1.0 / 优秀 → ×1.05

---

### 📈 技术面

**快照层（全市场）**：60日动量 45% + 年初至今 30% + 换手率 25%。
> ⚠️ 东财 push2 不通时，快照拿不到动量字段（按中性 50），技术面会失真。

**真实层（仅入围前 100 名）**：用 **baostock 前复权日线**算出真实技术分，覆盖快照值。

- **统一使用反转式技术**（经样本外验证）：奖励**超跌 / 超卖 / 低位**——
  动量(20/60) 52% + RSI 30% + 距MA20乖离 6% + 均线状态 12%（乖离率降权，因训练/检验期方向反转）。
  动量越低/RSI 越超卖/越在均线下方 → 分越高（极端暴跌轻微让分以防接飞刀）。

> 为何短线要反转？回测显示 A 股主板 ~3 个月维度强均值回归，追涨式动量与未来收益**负相关**（详见 `backtest.py`）。

---

### 💹 估值分位（权重 12%）— 防追高

用 **baostock** 取个股近 **5 年** PB(pbMRQ) 日线历史，算当前 PB 处于历史的百分位：

| PB 历史分位 | 评分 | 含义 |
|------|------|------|
| 0%（历史最低） | 100 | 相对自身历史极便宜 |
| 20% | 85 | 偏低 |
| 40% | 68 | 中等偏低 |
| 60% | 50 | 中性 |
| 80% | 32 | 偏高 |
| 100%（历史最高） | 12 | 相对自身历史极贵，追高风险大 |

> ⚙️ **性能取舍**：baostock 取历史为「按单只查询」，故仅对「价值+技术」**初排前 100 名**的入围候选计算分位并折进综合分后重排；其余股票该项按中性 50 计。
> 用意：同样基本面，一只 PB 处于历史 95% 高位的票会被压低，避免买在估值顶部。

---

### 🏷️ 建议分级

| 综合分 | 建议 |
|--------|------|
| ≥ 80 | 🟢 强烈关注 |
| ≥ 72 | 🟢 值得买入 |
| ≥ 63 | 🟡 可关注 |
| ≥ 50 | 🟠 观望 |
| < 50 | 🔴 暂不推荐 |

---

### 📡 数据来源

- **行情快照**：东方财富 push2 → 降级「baostock 清单 + 腾讯行情」→ 兜底新浪财经（PE/PB/市值/换手率）
- **业绩报表**：东方财富 datacenter（ROE/净利润/营收/毛利率/EPS/经营现金流；ROE 按报告期年化）
- **估值分位**：baostock 个股近 5 年 PB 历史百分位（仅入围前 100 名计算）
- **个股历史**：优先东方财富 → 降级新浪 daily（均线/RSI）
- **缺失处理**：降级时缺失的「量比/动量60/年初至今」字段按中性 50 分计算
""")


@st.cache_data(ttl=1800, show_spinner=False)
def load_stock(code):
    h = screener.analyze_stock(code)
    return h


_PROFILE_MAP = {"短线波段 (≤3月)": "短线波段", "长线价值": "长线价值"}
VAL_TOP = 100  # 入围精算的候选数量（按价值分取前 N）

with st.sidebar:
    st.header("① 数据")
    go = st.button("🚀 拉取 / 刷新全市场数据", type="primary", use_container_width=True)
    st.caption("拉一次即可；下面改条件**即时重算**，不再联网。")

    st.header("② 风格与筛选")
    profile_label = st.radio(
        "投资风格", list(_PROFILE_MAP.keys()), index=0,
        help="短线波段：估值分位主导 + 反转式技术（价值0.15/技术0.30/估值0.55，经回测校准），"
             "适合 ≤3 个月持有，奖励超跌/低估、规避追高；"
             "长线价值：基本面主导（价值0.57/技术0.31/估值0.12，反转式技术）。")
    profile = _PROFILE_MAP[profile_label]
    exclude_st = st.checkbox("排除 ST / 退市股", value=True)
    exclude_loss = st.checkbox("排除亏损股（PE/EPS≤0）", value=True)
    min_mktcap = st.slider("最小总市值（亿元）", 0, 1000, 50, step=10)
    min_roe = st.slider("最低 ROE（%，已年化）", -10, 30, 8, step=1)
    max_pe = st.slider("最高 PE（动态）", 5, 200, 80, step=5)

    st.header("③ 短线风控")
    exclude_halt = st.checkbox("剔除停牌 / 无成交", value=True)
    avoid_limit = st.checkbox("规避当日涨/跌停", value=True,
                              help="涨跌停次日可能买不进/卖不出，短线宜回避。")
    min_amount = st.slider("最小成交额（亿元）", 0.0, 20.0, 1.0, step=0.5,
                           help="成交额过低 3 个月内难进出，设流动性下限。")

    st.header("④ 展示")
    only_buy = st.checkbox("只看「值得买入」及以上", value=True)
    topn = st.number_input("展示数量", 10, 500, 50, step=10)

ss = st.session_state
ss.setdefault("loaded", False)
ss.setdefault("enrich", {})
ss.setdefault("enrich_tried", set())

if go:
    status = st.status("正在拉取全市场数据…", expanded=True)
    try:
        def progress_cb(step, detail, pct):
            status.update(label=f"📡 {step} ({pct}%)", state="running")
            st.write(f"{detail}")

        market = screener.fetch_market(progress_cb=progress_cb)
        ss.market = market
        ss.report_date = market.attrs.get("report_date")
        ss.enrich = {}          # 新数据 → 清空入围缓存
        ss.enrich_tried = set()
        ss.loaded = True
        status.update(label=f"✅ 全市场 {len(market)} 只已就绪，可即时筛选", state="complete")
    except Exception as e:
        status.update(label="❌ 数据获取失败", state="error")
        st.error(f"数据获取失败：{e}")

if ss.loaded:
    rd = ss.report_date
    # —— 即时筛选（纯本地）——
    filtered = screener.apply_filters(
        ss.market, exclude_st=exclude_st, exclude_loss=exclude_loss,
        min_mktcap_yi=min_mktcap, min_roe=float(min_roe), max_pe=float(max_pe),
        exclude_halt=exclude_halt, avoid_limit=avoid_limit, min_amount_yi=min_amount)

    # —— 入围精算：仅对尚未算过的代码查 baostock（per-code 缓存）——
    shortlist = filtered.sort_values("价值分", ascending=False).head(VAL_TOP)["代码"].tolist()
    missing = [c for c in shortlist if c not in ss.enrich_tried]
    if missing:
        with st.spinner(f"精算入围股 {len(missing)} 只（估值分位 + 真实短线技术，baostock）…"):
            rows = filtered[filtered["代码"].isin(missing)][["代码", "换手率"]]
            ss.enrich.update(screener.enrich_shortlist(rows))
            ss.enrich_tried.update(missing)

    df = screener.rescore(filtered, profile, ss.enrich)

    view = df[df["综合分"] >= 72] if only_buy else df
    view = view.head(int(topn))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("财报报告期", rd)
    c2.metric("筛选后股票数", len(df))
    c3.metric("达到买入线", int((df["综合分"] >= 72).sum()))
    c4.metric("更新时间", datetime.datetime.now().strftime("%H:%M"))

    st.subheader("📋 推荐清单")
    st.caption(f"风格：{profile_label}　|　入围精算覆盖：{len(ss.enrich)} 只")
    show_cols = ["代码", "名称", "行业", "最新价", "PE", "PB", "ROE",
                 "净利润同比", "毛利率", "价值分",
                 "技术分", "动量60", "RSI", "均线", "估值分位", "综合分", "建议"]
    show = view[show_cols].reset_index(drop=True)

    st.dataframe(
        show.style.background_gradient(subset=["综合分"], cmap="RdYlGn", vmin=40, vmax=100)
        .format({"最新价": "{:.2f}", "PE": "{:.1f}", "PB": "{:.2f}",
                 "ROE": "{:.1f}", "净利润同比": "{:.1f}", "毛利率": "{:.1f}",
                 "价值分": "{:.1f}", "技术分": "{:.1f}", "动量60": "{:.1f}%",
                 "RSI": "{:.0f}", "估值分位": "{:.0f}%", "综合分": "{:.1f}"}, na_rep="—"),
        use_container_width=True, height=560)

    st.download_button("⬇️ 下载完整结果 CSV",
                       df.to_csv(index=False).encode("utf-8-sig"),
                       file_name="选股结果.csv", mime="text/csv")

    # 个股技术详情
    st.subheader("🔍 个股技术详情")
    options = (view["代码"] + " " + view["名称"]).tolist()
    if options:
        pick = st.selectbox("选择一只股票查看走势 / 均线 / RSI", options)
        code = pick.split()[0]
        with st.spinner("拉取个股历史…"):
            h = load_stock(code)
        if h is None or len(h) == 0:
            st.warning("未取到该股历史数据")
        else:
            st.info("技术面：" + screener.trend_note(h))
            hi = h.set_index("date")
            st.line_chart(hi[["close", "MA20", "MA60"]], height=280)
            st.line_chart(hi[["RSI"]], height=160)
else:
    st.info("👈 先点左上角「🚀 拉取 / 刷新全市场数据」（首次需联网，约 40~90 秒）。"
            "拉取后，调整筛选条件 / 风格 / 风控都会**即时重算**，无需重新联网。")
