"""A股价值+技术选股 · 网页界面

启动：  streamlit run app.py
"""
import net_patch  # noqa: F401  最先导入
import datetime
import pandas as pd
import streamlit as st
import screener

st.set_page_config(page_title="A股选股助手", page_icon="📈", layout="wide")

# ═══════════════════════════════════════════
#  全局自定义样式
# ═══════════════════════════════════════════
st.markdown("""
<style>
/* ===== 全局基础 ===== */
html, body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
}

/* ===== 主背景 ===== */
[data-testid="stAppViewContainer"] {
    background: linear-gradient(135deg, #f8fafc 0%, #eef1f5 100%);
}

/* ===== 顶部标题栏 ===== */
[data-testid="stHeader"] {
    background: rgba(255,255,255,0.7);
    backdrop-filter: blur(8px);
    border-bottom: 1px solid rgba(0,0,0,0.05);
}

/* ===== 主区容器：收紧上下留白、限制最大宽度更聚焦 ===== */
.block-container {
    padding-top: 2.4rem !important;
    padding-bottom: 2.5rem !important;
    max-width: 1500px !important;
}

/* ===== 主标题 ===== */
h1 {
    font-size: 1.9rem !important;
    font-weight: 700 !important;
    color: #1a1a2e !important;
    padding-bottom: 0.4rem !important;
    border-bottom: 3px solid #667eea;
    margin-bottom: 0.3rem !important;
}

/* ===== 主区小标题：左侧靛蓝强调条 ===== */
.block-container h2, .block-container h3 {
    color: #1a1a2e !important;
    font-weight: 700 !important;
    border-left: 4px solid #667eea;
    padding-left: 0.6rem !important;
    margin: 0.6rem 0 0.2rem 0 !important;
    line-height: 1.3 !important;
}

/* ===== 侧边栏 ===== */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%) !important;
}
/* 侧边栏所有文字默认浅色 */
[data-testid="stSidebar"] {
    color: #e2e8f0 !important;
}
[data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
    color: #667eea !important;
}
[data-testid="stSidebar"] p, [data-testid="stSidebar"] label,
[data-testid="stSidebar"] .stCaption, [data-testid="stSidebar"] small {
    color: #cbd5e1 !important;
}
/* 输入框：深底白字 */
[data-testid="stSidebar"] input,
[data-testid="stSidebar"] [data-baseweb="input"] input,
[data-testid="stSidebar"] [data-baseweb="select"] [role="combobox"],
[data-testid="stSidebar"] [data-baseweb="select"] input {
    background: rgba(255,255,255,0.1) !important;
    color: #fff !important;
    border-color: rgba(255,255,255,0.2) !important;
    border-radius: 8px !important;
}
/* 下拉菜单文本 */
[data-testid="stSidebar"] [data-baseweb="select"] [role="combobox"] {
    color: #fff !important;
}
/* 数字输入框：容器白底会盖住深色规则 → 强制深底白字（含 baseweb 包裹层）*/
[data-testid="stSidebar"] [data-testid="stNumberInput"] [data-baseweb="input"],
[data-testid="stSidebar"] [data-testid="stNumberInput"] [data-baseweb="base-input"],
[data-testid="stSidebar"] [data-testid="stNumberInput"] input {
    background: rgba(255,255,255,0.12) !important;
    color: #ffffff !important;
    -webkit-text-fill-color: #ffffff !important;
    border-color: rgba(255,255,255,0.25) !important;
}
[data-testid="stSidebar"] [data-testid="stNumberInput"] input::placeholder {
    color: #cbd5e1 !important;
}
/* 数字输入框 +/- 步进按钮：浅色图标 + 透明底，hover 微亮 */
[data-testid="stSidebar"] [data-testid="stNumberInput"] button {
    background: rgba(255,255,255,0.06) !important;
    color: #e2e8f0 !important;
}
[data-testid="stSidebar"] [data-testid="stNumberInput"] button:hover {
    background: rgba(255,255,255,0.18) !important;
}
[data-testid="stSidebar"] button[tabindex="-1"] {
    color: #e2e8f0 !important;
}
/* 成功的提示背景深 */
[data-testid="stSidebar"] .stAlert {
    background: rgba(255,255,255,0.08);
    border-color: rgba(255,255,255,0.15);
}
/* 按钮 */
[data-testid="stSidebar"] .stButton > button {
    background: linear-gradient(135deg, #667eea, #764ba2) !important;
    color: #fff !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
    cursor: pointer !important;
    transition: all 0.15s ease !important;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background: linear-gradient(135deg, #764ba2, #667eea) !important;
    transform: translateY(-1px);
}
[data-testid="stSidebar"] .stButton > button:active {
    transform: translateY(0);
    opacity: 0.9;
}
/* expander 展开面板 */
[data-testid="stSidebar"] .stExpander details {
    background: rgba(255,255,255,0.06);
    border-radius: 10px;
    border: 1px solid rgba(255,255,255,0.1);
}
/* expander 标题：必须比正文更醒目（覆盖上面通用 p 的浅灰色）*/
[data-testid="stSidebar"] .stExpander summary,
[data-testid="stSidebar"] details summary,
[data-testid="stSidebar"] details summary p,
[data-testid="stSidebar"] details summary span,
[data-testid="stSidebar"] [data-testid="stExpander"] summary * {
    color: #f8fafc !important;
    font-weight: 700 !important;
    font-size: 0.95rem !important;
}
/* 展开/收起箭头也用浅色，避免发灰看不清 */
[data-testid="stSidebar"] details summary svg {
    fill: #cbd5e1 !important;
}
/* 标题栏背景：透明贴合深色侧栏（默认不再纯白），仅 hover 时微亮 */
[data-testid="stSidebar"] .stExpander summary,
[data-testid="stSidebar"] details summary,
[data-testid="stSidebar"] .streamlit-expanderHeader {
    background: transparent !important;
    border: none !important;
}
[data-testid="stSidebar"] details summary:hover,
[data-testid="stSidebar"] .streamlit-expanderHeader:hover {
    background: rgba(255,255,255,0.10) !important;
}
/* 侧边栏右上角「折叠」按钮：深色侧栏上默认看不清，强制浅色 */
[data-testid="stSidebarCollapseButton"] button,
[data-testid="stSidebarCollapseButton"] svg,
[data-testid="stSidebarCollapseButton"] span,
[data-testid="stSidebar"] [data-testid="stBaseButton-headerNoPadding"],
[data-testid="stSidebar"] [data-testid="stBaseButton-headerNoPadding"] svg,
[data-testid="stSidebar"] [kind="header"] svg,
[data-testid="stSidebar"] header button svg {
    color: #e2e8f0 !important;
    fill: #e2e8f0 !important;
    opacity: 1 !important;
}
[data-testid="stSidebarCollapseButton"] button:hover {
    background: rgba(255,255,255,0.12) !important;
    border-radius: 8px !important;
}
/* 选中的 radio 选项更醒目 */
[data-testid="stSidebar"] .stRadio [aria-checked="true"] + div,
[data-testid="stSidebar"] .stRadio label[data-checked="true"] {
    color: #fff !important;
    font-weight: 600 !important;
}
/* divider */ 
[data-testid="stSidebar"] hr {
    border-color: rgba(255,255,255,0.15) !important;
}
/* radio / checkbox 文字 */
[data-testid="stSidebar"] .stRadio label,
[data-testid="stSidebar"] .stCheckbox label,
[data-testid="stSidebar"] .stToggle label {
    color: #e2e8f0 !important;
}
/* slider 数值和标签 */
[data-testid="stSidebar"] .stSlider [data-testid="stThumbValue"],
[data-testid="stSidebar"] .stSlider label,
[data-testid="stSidebar"] .stSlider p {
    color: #e2e8f0 !important;
}
/* number input 标签 */
[data-testid="stSidebar"] .stNumberInput label {
    color: #e2e8f0 !important;
}
/* selectbox 当前值 */
[data-testid="stSidebar"] .stSelectbox label {
    color: #e2e8f0 !important;
}
/* info / success 提示框 */
[data-testid="stSidebar"] .stAlert [data-testid="stNotification"] {
    color: #e2e8f0 !important;
}

/* ===== Metric 指标卡片 ===== */
[data-testid="stMetric"] {
    background: #fff;
    border-radius: 14px;
    padding: 0.9rem 1.1rem;
    border: 1px solid #e8ecf2;
    border-left: 4px solid #667eea;
    box-shadow: 0 2px 10px rgba(0,0,0,0.05);
    transition: transform .15s ease, box-shadow .15s ease;
}
[data-testid="stMetric"]:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 20px rgba(102,126,234,0.15);
}
[data-testid="stMetric"] label {
    font-size: 0.75rem !important;
    color: #64748b !important;
    font-weight: 500 !important;
}
[data-testid="stMetric"] [data-testid="stMetricValue"] {
    font-size: 1.6rem !important;
    font-weight: 700 !important;
    color: #1a1a2e !important;
}

/* ===== 表格（st.dataframe）===== */
[data-testid="stDataFrame"] {
    border-radius: 12px;
    overflow: hidden;
    border: 1px solid #e2e8f0;
}
[data-testid="stDataFrame"] thead th {
    background: #1e293b !important;
    color: #e2e8f0 !important;
    font-weight: 600 !important;
    font-size: 0.8rem !important;
    padding: 0.6rem 0.5rem !important;
    text-align: center !important;
}
[data-testid="stDataFrame"] tbody td {
    text-align: center !important;
    padding: 0.4rem 0.5rem !important;
    font-size: 0.82rem;
    border-bottom: 1px solid #f1f5f9;
}
[data-testid="stDataFrame"] tbody tr:hover {
    background: #f1f5f9 !important;
}

/* ===== 按钮（主区域）===== */
.stButton > button:not([data-testid="stSidebar"] *) {
    background: linear-gradient(135deg, #667eea, #764ba2) !important;
    color: #fff !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
    transition: all 0.3s ease !important;
}

/* ===== 展开面板 ===== */
.streamlit-expanderHeader {
    background: #fff;
    border-radius: 10px !important;
    border: 1px solid #e2e8f0;
    font-weight: 600 !important;
    color: #1a1a2e !important;
}

/* ===== 底部 ===== */
footer { visibility: hidden; }

/* ===== 滚动条 ===== */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 10px; }
</style>
""", unsafe_allow_html=True)

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

ss = st.session_state
ss.setdefault("loaded", False)
ss.setdefault("enrich", {})
ss.setdefault("enrich_tried", set())

with st.sidebar:
    go = st.button("🚀 拉取 / 刷新全市场数据", type="primary", use_container_width=True)
    if ss.get("loaded"):
        st.caption(f"📅 报告期：{ss.report_date}")

    st.divider()

    # —— 风格（始终可见）——
    profile_label = st.radio(
        "💰 投资风格", list(_PROFILE_MAP.keys()), index=0,
        help="短线波段：估值分位主导 + 反转式技术（价值0.15/技术0.30/估值0.55，经回测校准）；"
             "长线价值：基本面主导（价值0.57/技术0.31/估值0.12，反转式技术）。")
    profile = _PROFILE_MAP[profile_label]

    # —— 基本面筛选（折叠）——
    with st.expander("📊 基本面筛选", expanded=True):
        exclude_st = st.checkbox("排除 ST / 退市股", value=True)
        exclude_loss = st.checkbox("排除亏损股（PE/EPS≤0）", value=True)
        min_mktcap = st.slider("最小总市值（亿元）", 0, 1000, 50, step=10)
        min_roe = st.slider("最低 ROE（%，已年化）", -10, 30, 8, step=1)
        max_pe = st.slider("最高 PE（动态）", 5, 200, 80, step=5)

    # —— 短线风控（折叠）——
    with st.expander("🛡️ 短线风控", expanded=False):
        exclude_halt = st.checkbox("剔除停牌 / 无成交", value=True)
        avoid_limit = st.checkbox("规避当日涨/跌停", value=True,
                                  help="涨跌停次日可能买不进/卖不出，短线宜回避。")
        min_amount = st.slider("最小成交额（亿元）", 0.0, 20.0, 1.0, step=0.5,
                               help="成交额过低 3 个月内难进出，设流动性下限。")

    # —— 展示选项（折叠）——
    with st.expander("👁️ 展示选项", expanded=False):
        only_buy = st.checkbox("只看「值得买入」及以上", value=True)
        topn = st.number_input("展示数量", 10, 500, 50, step=10)

if go:
    progress_bar = st.progress(0, text="⏳ 连接行情接口…")
    try:
        def progress_cb(step, detail, pct):
            progress_bar.progress(min(int(pct * 100), 100), text=f"📡 {detail}")

        market = screener.fetch_market(progress_cb=progress_cb)
        ss.market = market
        ss.report_date = market.attrs.get("report_date")
        ss.enrich = {}          # 新数据 → 清空入围缓存
        ss.enrich_tried = set()
        ss.loaded = True
        progress_bar.empty()    # 完成即移除进度条，避免残留空框
        st.toast(f"✅ 全市场 {len(market)} 只已就绪，可即时筛选", icon="✅")
    except Exception as e:
        progress_bar.empty()
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

    # —— 指标仪表盘 ——
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📅 财报报告期", rd)
    c2.metric("📊 筛选后股票数", len(df))
    c3.metric("🎯 达到买入线", int((df["综合分"] >= 72).sum()))
    c4.metric("🕐 更新时间", datetime.datetime.now().strftime("%H:%M"))

    # —— 推荐等级分布条 ——
    buy_cnt = int((df["综合分"] >= 72).sum())
    watch_cnt = int(((df["综合分"] >= 63) & (df["综合分"] < 72)).sum())
    hold_cnt = int(((df["综合分"] >= 50) & (df["综合分"] < 63)).sum())
    avoid_cnt = int((df["综合分"] < 50).sum())

    st.markdown(f"""
    <div style="display:flex; gap:12px; margin:12px 0 16px 0; flex-wrap:wrap;">
        <div style="flex:1; min-width:120px; background:linear-gradient(135deg, #d4edda, #c3e6cb);
                    border-radius:12px; padding:12px 16px; text-align:center;
                    border:1px solid #b7dfb9; box-shadow:0 2px 8px rgba(0,0,0,0.04);">
            <div style="font-size:1.6rem; font-weight:700; color:#155724;">{buy_cnt}</div>
            <div style="font-size:0.78rem; color:#2d6a3f; font-weight:500;">🟢 值得买入</div>
        </div>
        <div style="flex:1; min-width:120px; background:linear-gradient(135deg, #fff3cd, #ffeeba);
                    border-radius:12px; padding:12px 16px; text-align:center;
                    border:1px solid #ffe082; box-shadow:0 2px 8px rgba(0,0,0,0.04);">
            <div style="font-size:1.6rem; font-weight:700; color:#856404;">{watch_cnt}</div>
            <div style="font-size:0.78rem; color:#b68b00; font-weight:500;">🟡 可关注</div>
        </div>
        <div style="flex:1; min-width:120px; background:linear-gradient(135deg, #ffe5cc, #ffd6a5);
                    border-radius:12px; padding:12px 16px; text-align:center;
                    border:1px solid #ffc078; box-shadow:0 2px 8px rgba(0,0,0,0.04);">
            <div style="font-size:1.6rem; font-weight:700; color:#c6640a;">{hold_cnt}</div>
            <div style="font-size:0.78rem; color:#d9780f; font-weight:500;">🟠 观望</div>
        </div>
        <div style="flex:1; min-width:120px; background:linear-gradient(135deg, #f8d7da, #f1c0c4);
                    border-radius:12px; padding:12px 16px; text-align:center;
                    border:1px solid #f1aeb5; box-shadow:0 2px 8px rgba(0,0,0,0.04);">
            <div style="font-size:1.6rem; font-weight:700; color:#721c24;">{avoid_cnt}</div>
            <div style="font-size:0.78rem; color:#a1424a; font-weight:500;">🔴 暂不推荐</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.subheader("📋 推荐清单")
    hc1, hc2 = st.columns([3, 1])
    with hc1:
        st.caption(f"风格：{profile_label}　|　入围精算：{len(ss.enrich)} 只　|　"
                   f"综合分 ≥72：{int((df['综合分'] >= 72).sum())} 只")
    with hc2:
        show_all = st.toggle("显示全部字段", value=False,
                             help="默认只看核心列（不横向滚动）；打开后展开 PE/PB/动量/RSI 等明细。")

    # —— 展示列定义：默认核心列（综合分/建议在最右、无需横向滚动）——
    core_cols = ["代码", "名称", "行业", "最新价",
                 "价值分", "技术分", "估值分位", "综合分", "建议"]
    full_cols = ["代码", "名称", "行业", "最新价", "涨跌幅", "PE", "PB", "ROE",
                 "净利润同比", "毛利率", "换手率",
                 "价值分", "技术分", "动量60", "RSI", "均线", "估值分位", "综合分", "建议"]
    show_cols = full_cols if show_all else core_cols
    available = [c for c in show_cols if c in view.columns]
    show = view[available].reset_index(drop=True).copy()

    # ProgressColumn 的 min/max 已是 0-100，直接用原值（勿再 /100，否则条几乎不填充、数值显示成 0.x）
    show["_score_pct"] = show["综合分"].clip(0, 100)

    # 建议列加 emoji
    def _advice_emoji(val):
        s = str(val)
        if "强烈" in s:   return "🟢 " + s
        if "值得买入" in s: return "🟢 " + s
        if "可关注" in s:   return "🟡 " + s
        if "观望" in s:    return "🟠 " + s
        if "暂不" in s:    return "🔴 " + s
        return "⚪ " + s
    show["建议"] = show["建议"].apply(_advice_emoji)

    col_cfg = {
        "代码": st.column_config.TextColumn("代码", width="small"),
        "名称": st.column_config.TextColumn("名称", width="small"),
        "行业": st.column_config.TextColumn("行业", width="small"),
        "最新价": st.column_config.NumberColumn("最新价", format="%.2f", width="small"),
        "涨跌幅": st.column_config.NumberColumn("涨跌幅", format="%.2f%%", width="small"),
        "PE": st.column_config.NumberColumn("PE", format="%.1f", width="small"),
        "PB": st.column_config.NumberColumn("PB", format="%.2f", width="small"),
        "ROE": st.column_config.NumberColumn("ROE", format="%.1f%%", width="small"),
        "净利润同比": st.column_config.NumberColumn("净利同比", format="%.1f%%", width="small"),
        "毛利率": st.column_config.NumberColumn("毛利率", format="%.1f%%", width="small"),
        "换手率": st.column_config.NumberColumn("换手率", format="%.2f%%", width="small"),
        "价值分": st.column_config.NumberColumn("价值分", format="%.1f", width="small"),
        "技术分": st.column_config.NumberColumn("技术分", format="%.1f", width="small"),
        "动量60": st.column_config.NumberColumn("60日动量", format="%.1f%%", width="small"),
        "RSI": st.column_config.NumberColumn("RSI", format="%.0f", width="small"),
        "均线": st.column_config.TextColumn("均线", width="small"),
        "估值分位": st.column_config.NumberColumn("估值分位", format="%.0f%%", width="small"),
        "_score_pct": st.column_config.ProgressColumn("综合分", format="%.1f", min_value=0, max_value=100, width="medium"),
        "建议": st.column_config.TextColumn("建议", width="medium"),
    }

    # 展示顺序保持自然，把 raw 综合分 替换成进度条列；综合分→建议 自然落在最右
    disp_cols = ["_score_pct" if c == "综合分" else c for c in available]

    st.dataframe(
        show[disp_cols],
        column_config={k: v for k, v in col_cfg.items() if k in disp_cols},
        use_container_width=True,
        height=520,
        hide_index=True,
    )

    st.download_button("⬇️ 下载完整结果 CSV",
                       df.to_csv(index=False).encode("utf-8-sig"),
                       file_name="选股结果.csv", mime="text/csv")

    # —— 分割线 ——
    st.markdown('<div style="margin: 1.5rem 0; border-top: 2px solid rgba(102,126,234,0.12);"></div>', unsafe_allow_html=True)

    # 个股技术详情
    st.subheader("🔍 个股技术详情")
    options = (view["代码"] + " " + view["名称"]).tolist()
    if options:
        col_pick, col_empty = st.columns([1, 2])
        with col_pick:
            pick = st.selectbox("选择一只股票查看走势 / 均线 / RSI", options, label_visibility="collapsed",
                                placeholder="🔍 点击选择股票…")
        code = pick.split()[0]
        with st.spinner("📡 拉取个股历史…"):
            h = load_stock(code)
        if h is None or len(h) == 0:
            st.warning("⚠️ 未取到该股历史数据")
        else:
            # 技术面概览卡片
            trend = screener.trend_note(h)
            st.markdown(f"""
            <div style="background:linear-gradient(135deg, #f8fafc, #f1f5f9); border-radius:14px;
                        padding:14px 20px; margin:10px 0; border-left:4px solid #667eea;
                        box-shadow:0 2px 10px rgba(0,0,0,0.04);">
                <span style="font-weight:600; color:#1a1a2e;">📊 技术面分析：</span>
                <span style="color:#475569;">{trend}</span>
            </div>
            """, unsafe_allow_html=True)

            hi = h.set_index("date")
            col_left, col_right = st.columns(2)
            with col_left:
                st.caption("📈 价格走势 & 均线")
                st.line_chart(hi[["close", "MA20", "MA60"]], height=300)
            with col_right:
                st.caption("📉 RSI 指标")
                st.line_chart(hi[["RSI"]], height=300)
else:
    # —— 空状态欢迎页 ——
    st.markdown("""
    <div style="display:flex; flex-direction:column; align-items:center; justify-content:center;
                padding: 60px 20px; text-align: center;">
        <div style="font-size: 4rem; margin-bottom: 20px;">📊</div>
        <h2 style="color: #1a1a2e; margin-bottom: 12px; font-weight: 700;">欢迎使用 A股选股助手</h2>
        <p style="color: #64748b; font-size: 1.05rem; max-width: 480px; line-height: 1.7;">
            点击侧边栏 <strong style="color: #667eea;">🚀 拉取 / 刷新全市场数据</strong> 开始选股<br/>
            <small>首次需联网，约 40~90 秒。拉取后可即时筛选，无需重新联网。</small>
        </p>
        <div style="display:flex; gap: 24px; margin-top: 28px;">
            <div style="text-align:center;">
                <div style="font-size:1.8rem;">⚡</div>
                <div style="font-size:0.8rem; color:#64748b;">实时行情</div>
            </div>
            <div style="text-align:center;">
                <div style="font-size:1.8rem;">📈</div>
                <div style="font-size:0.8rem; color:#64748b;">技术分析</div>
            </div>
            <div style="text-align:center;">
                <div style="font-size:1.8rem;">💎</div>
                <div style="font-size:0.8rem; color:#64748b;">价值评估</div>
            </div>
            <div style="text-align:center;">
                <div style="font-size:1.8rem;">🛡️</div>
                <div style="font-size:0.8rem; color:#64748b;">风险控制</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

# —— 底部信息栏 ——
st.markdown("""
<div style="text-align:center; padding:20px 0 10px 0; margin-top:30px;
            border-top:1px solid rgba(102,126,234,0.1);">
    <span style="color:#94a3b8; font-size:0.78rem;">
        📡 数据来源：东方财富 / 腾讯 / baostock &nbsp;|&nbsp;
        仅供研究参考，不构成投资建议 &nbsp;|&nbsp;
        ⚠️ 投资有风险，入市需谨慎
    </span>
</div>
""", unsafe_allow_html=True)
