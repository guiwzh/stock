"""轻量回测：验证「真实技术分」与「估值分位」对未来 ~3 个月收益是否有预测力。

方法（point-in-time 时点回测，复用 screener 的打分函数）：
  • 从沪深主板抽样 N 只，每只用 baostock 取一次完整历史（close/换手率/PB）。
  • 在多个【非重叠】时点 T 上，用 T 之前的数据算「当时的技术分 / 估值分位」，
    再算 T 之后 H≈60 个交易日（约 3 个月）的实际收益。
  • 汇总所有 (股票, T) 样本：算 Spearman 相关、分组（五档）平均收益、胜率。

样本外验证：
  • 按回测区间中点（或指定日期）切分为「训练期」与「检验期」。
  • 同一套打分规则在两段分别评估，对比方向/单调性是否一致。
  • 若训练期有效的信号在检验期同样成立，说明非过拟合某段行情。

诚实说明：
  - 仅验证技术分 + 估值分位（短线最相关的两项）。价值面（ROE/成长）做历史时点
    对齐成本高，未纳入——所以这不是对「完整综合分」的检验。
  - 用前复权日线；样本时点不足/停牌段会跳过。非重叠窗口已尽量降低自相关，但
    A 股不同股票同期收益高度相关（系统性），故结论是方向性参考，非严格统计显著。

用法：  python backtest.py [样本数=200] [持有交易日=60] [--split YYYY-MM-DD]
"""
import io
import sys
import contextlib
import datetime
import numpy as np
import pandas as pd

import net_patch  # noqa: F401
import baostock as bs
import screener as sc


def _all_main_board():
    """取最近交易日的沪深主板代码（带 baostock 前缀，如 sh.600000）。"""
    syms = []
    for back in range(8):
        d = (datetime.date.today() - datetime.timedelta(days=back)).strftime("%Y-%m-%d")
        rs = bs.query_all_stock(day=d)
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            continue
        for code, _st, _name in rows:
            mkt, num = code.split(".")
            if (mkt == "sh" and num.startswith("60")) or (mkt == "sz" and num.startswith("00")):
                syms.append(code)
        break
    return syms


def _history(bcode, start, end):
    """取单只前复权日线：date/close/turn/pbMRQ。"""
    rs = bs.query_history_k_data_plus(
        bcode, "date,close,turn,pbMRQ", start_date=start, end_date=end,
        frequency="d", adjustflag="2")
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["date", "close", "turn", "pbMRQ"])
    for c in ("close", "turn", "pbMRQ"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def run(n_sample=200, hold=60, years=6, seed=42, split_date=None):
    """回测主函数。

    split_date: 若指定 (如 '2023-01-01')，则按该日切分训练/检验期做样本外验证；
                默认按区间中点自动切分。设为 None 则不做切分（全区间报告）。
    """
    end = datetime.date.today().strftime("%Y-%m-%d")
    start = (datetime.date.today() - datetime.timedelta(days=int(365.25 * years))).strftime("%Y-%m-%d")

    # 自动切分点：区间中点（若未显式指定）
    if split_date is None:
        mid = datetime.date.today() - datetime.timedelta(days=int(365.25 * years / 2))
        split_date = mid.strftime("%Y-%m-%d")

    with contextlib.redirect_stdout(io.StringIO()):
        bs.login()
    try:
        universe = _all_main_board()
        rng = np.random.default_rng(seed)
        sample = list(rng.choice(universe, size=min(n_sample, len(universe)), replace=False))
        print(f"主板共 {len(universe)} 只，抽样 {len(sample)} 只；持有期 {hold} 交易日(≈{hold/20:.0f}个月)")
        print(f"样本外切分点: {split_date}")

        samples = []
        done = 0
        for bcode in sample:
            done += 1
            if done % 25 == 0:
                print(f"  ...已处理 {done}/{len(sample)}，累计样本 {len(samples)}")
            h = _history(bcode, start, end)
            if h is None or len(h) < 65 + hold + 5:
                continue
            closes = h["close"].tolist()
            turns = h["turn"].tolist()
            pbs = h["pbMRQ"]
            dates = h["date"].tolist()
            L = len(closes)
            for t in range(65, L - hold, hold):
                metrics = sc._tech_metrics(closes[:t + 1])
                if metrics is None:
                    continue
                tech = sc._real_tech_score(metrics, turns[t], "短线波段")
                pb_hist = pbs.iloc[:t + 1].dropna()
                pb_hist = pb_hist[pb_hist > 0]
                if len(pb_hist) < 60:
                    continue
                pct = float((pb_hist < pb_hist.iloc[-1]).mean() * 100)
                val = sc._valuation_score(pct)
                fwd = (closes[t + hold] / closes[t] - 1) * 100
                if not np.isfinite(fwd):
                    continue
                samples.append({
                    "日期": dates[t],
                    "动量20": metrics["动量20"], "动量60": metrics["动量60"],
                    "RSI": metrics["RSI"], "乖离": metrics["乖离"],
                    "波动率": metrics["波动率"], "多头": 1 if metrics["均线"] == "多头" else 0,
                    "技术分": tech, "估值分位": pct, "估值分": val, "未来收益": fwd,
                })

        df = pd.DataFrame(samples)
        if "日期" in df.columns:
            df["日期"] = pd.to_datetime(df["日期"])

        # 切分训练/检验
        split_dt = pd.Timestamp(split_date)
        df_train = df[df["日期"] < split_dt].copy()
        df_test = df[df["日期"] >= split_dt].copy()

        print(f"\n总样本 {len(df)}：训练期 {len(df_train)}（<{split_date}） + 检验期 {len(df_test)}（≥{split_date}）")

        # 全区间报告
        _report(df, hold, label="全区间")
        # 训练期报告
        _report(df_train, hold, label=f"训练期 (<{split_date})")
        # 检验期报告
        _report(df_test, hold, label=f"检验期 (≥{split_date})")
        # 对比摘要
        _oos_summary(df_train, df_test, hold, split_date)

        df.to_csv("回测样本.csv", index=False, encoding="utf-8-sig")
        print("\n明细已存 回测样本.csv")
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            bs.logout()


def _spearman(a, b):
    """秩相关 = 对排名做 Pearson，避免依赖 scipy。"""
    return a.rank().corr(b.rank())


def _quintile_table(df, by, ascending=True):
    """按某列分 5 档，看各档未来收益。"""
    q = pd.qcut(df[by], 5, labels=["Q1(低)", "Q2", "Q3", "Q4", "Q5(高)"], duplicates="drop")
    g = df.groupby(q, observed=True)["未来收益"]
    return pd.DataFrame({"样本数": g.size(), "平均收益%": g.mean().round(2),
                         "中位收益%": g.median().round(2), "胜率%": (g.apply(lambda s: (s > 0).mean() * 100)).round(1)})


def _report(df, hold, label="全区间"):
    if len(df) < 20:
        print(f"\n{'─'*60}\n【{label}】⚠️ 有效样本仅 {len(df)} 个，跳过。\n{'─'*60}")
        return
    base = df["未来收益"].mean()
    print(f"\n{'='*60}\n【{label}】样本数 {len(df)}　|　平均 {hold} 日收益 = {base:.2f}%（基准）\n{'='*60}")

    print(f"\n  各原始因子 Spearman 相关（与未来 {hold} 日收益）：")
    print(f"  {'因子':<12}{'相关':>9}   方向解读")
    notes = {
        "动量20": "正=追涨有效 / 负=均值回归",
        "动量60": "正=追涨有效 / 负=均值回归",
        "RSI": "负=超买回落、超卖反弹",
        "乖离": "负=偏离均线越远越回归",
        "波动率": "高波动后续走势",
        "多头": "多头排列(1)是否更优",
        "技术分": "反转式（奖励超跌，经样本外验证）",
        "估值分位": "负=越便宜后续越涨",
    }
    for f, note in notes.items():
        if f in df.columns:
            print(f"  {f:<8}{_spearman(df[f], df['未来收益']):>+9.3f}   {note}")

    # 技术分档
    if "技术分" in df.columns:
        print(f"\n  按【技术分】分档（Q5=最超跌→最高分）：")
        print("  " + _quintile_table(df, "技术分").to_string().replace("\n", "\n  "))

    print(f"\n  按【RSI】分档（Q1=最超卖）：")
    print("  " + _quintile_table(df, "RSI").to_string().replace("\n", "\n  "))
    print(f"\n  按【估值分位】分档（Q1=最便宜）：")
    print("  " + _quintile_table(df, "估值分位").to_string().replace("\n", "\n  "))

    # 综合分权重扫描
    d2 = df.copy()
    print(f"\n  综合分 = w×技术(反转) + (1-w)×估值分　各权重表现（最高20%组）：")
    print(f"  {'w技术':>6}{'Spearman':>9}{'Top20%收益':>11}{'超额':>9}")
    for w in (1.0, 0.7, 0.5, 0.3, 0.0):
        s = w * d2["技术分"] + (1 - w) * d2["估值分"]
        sp = _spearman(s, d2["未来收益"])
        top = d2[s >= s.quantile(0.8)]["未来收益"].mean()
        print(f"  {w:>6.1f}{sp:>+9.3f}{top:>10.2f}%{top-base:>+8.2f}%")

    print(f"  （基准={base:.2f}%。正相关 + Top组超额为正 = 信号有效。）")
    print(f"\n（基准 = {base:.2f}%。正相关；Top 组跑赢基准越多越好；A 股噪声大，|相关| 0.05~0.10 已算可用。）"
           "\n（关键是看分档是否单调、Top 组能否稳定跑赢。）\n")


def _oos_summary(df_train, df_test, hold, split_date):
    """样本外验证摘要：对比训练期 vs 检验期的信号稳定性。"""
    if len(df_train) < 20 or len(df_test) < 20:
        print("\n⚠️ 训练/检验期样本不足，无法做样本外对比。")
        return

    print(f"\n{'#'*60}")
    print(f"#  样本外验证摘要（切分点 {split_date}）")
    print(f"{'#'*60}")

    factors = ["动量20", "动量60", "RSI", "乖离", "波动率", "多头", "技术分", "估值分位"]
    rows = []
    for f in factors:
        if f not in df_train.columns:
            continue
        sp_tr = _spearman(df_train[f], df_train["未来收益"])
        sp_te = _spearman(df_test[f], df_test["未来收益"])
        sign_agree = "✅" if sp_tr * sp_te > 0 else "❌ 方向反转!"
        rows.append({
            "因子": f, "训练期相关": f"{sp_tr:+.3f}", "检验期相关": f"{sp_te:+.3f}",
            "方向一致": sign_agree,
        })

    print("\n  Spearman 秩相关对比（方向一致 = 信号稳定，非过拟合某段）：")
    print(f"  {'因子':<8}{'训练期':>9}{'检验期':>9}  方向一致")
    for r in rows:
        print(f"  {r['因子']:<8}{r['训练期相关']:>9}{r['检验期相关']:>9}  {r['方向一致']}")

    # 估值分位分档对比
    for factor, ascending in [("估值分位", True), ("技术分", True)]:
        if factor not in df_train.columns:
            continue
        q_tr = pd.qcut(df_train[factor], 5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"], duplicates="drop")
        q_te = pd.qcut(df_test[factor], 5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"], duplicates="drop")
        ret_tr = df_train.groupby(q_tr, observed=True)["未来收益"].mean().round(2)
        ret_te = df_test.groupby(q_te, observed=True)["未来收益"].mean().round(2)
        mono_tr = "单调" if ret_tr.is_monotonic_increasing or ret_tr.is_monotonic_decreasing else "不单调"
        mono_te = "单调" if ret_te.is_monotonic_increasing or ret_te.is_monotonic_decreasing else "不单调"

        print(f"\n  {factor} 分档收益对比：")
        print(f"  {'分档':<6}{'训练期':>8}{'检验期':>8}")
        for q in ret_tr.index.intersection(ret_te.index):
            print(f"  {q:<6}{ret_tr[q]:>7.2f}%{ret_te[q]:>7.2f}%")
        print(f"  单调性: 训练期 {mono_tr}  |  检验期 {mono_te}")

    print(f"\n{'─'*60}")
    print("  解读：若训练期有效的方向在检验期保持一致（同号、同单调），")
    print("  说明反转策略有跨时段稳定性，不是过度拟合某一段行情的巧合。")
    print(f"{'─'*60}\n")


if __name__ == "__main__":
    args = sys.argv[1:]
    n = 200
    h = 60
    split = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--split" and i + 1 < len(args):
            split = args[i + 1]
            i += 2
        elif a.startswith("--split="):
            split = a.split("=", 1)[1]
            i += 1
        elif i == 0:
            n = int(a)
            i += 1
        elif i == 1:
            h = int(a)
            i += 1
        else:
            i += 1
    run(n_sample=n, hold=h, split_date=split)
