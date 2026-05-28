"""消除幸存者偏差的回测（survivorship-free）。

与 backtest.py 的关键区别：
  • universe 取【各调仓日的历史时点全集的并集】，包含后来退市/转板的股票
    （baostock query_all_stock(day=过去某日) 会返回当时在市的股票，含已退市的）；
  • 退市股的未来收益算到其【最后成交价】，从而把"接飞刀"那段暴跌损失计入——
    这正是原回测（只用今天仍在市的成分股）所隐藏的部分。

结论用于校准：现在这套"估值低位 + 反转技术"在真实样本下到底还有多少 edge。

用法： python backtest_sf.py [样本数=300] [持有交易日=60]
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

# 半年一个调仓日；每个都留 ≥60 交易日的未来窗口
REBAL_DATES = ["2021-06-01", "2021-12-01", "2022-06-01", "2022-12-01",
               "2023-06-01", "2023-12-01", "2024-06-03", "2024-12-02", "2025-06-03"]


def _universe(day):
    rs = bs.query_all_stock(day=day)
    codes = []
    while rs.error_code == "0" and rs.next():
        c = rs.get_row_data()[0]
        mkt, num = c.split(".")
        if (mkt == "sh" and num.startswith("60")) or (mkt == "sz" and num.startswith("00")):
            codes.append(c)
    return codes


def _history(bcode, start, end):
    rs = bs.query_history_k_data_plus(
        bcode, "date,close,turn,pbMRQ", start_date=start, end_date=end,
        frequency="d", adjustflag="2")
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["date", "close", "turn", "pbMRQ"])
    df["date"] = pd.to_datetime(df["date"])
    for c in ("close", "turn", "pbMRQ"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _spearman(a, b):
    return a.rank().corr(b.rank())


def run(n_sample=300, hold=60, seed=42):
    end = datetime.date.today().strftime("%Y-%m-%d")
    start = "2020-06-01"  # 早于首个调仓日，留足 65 日历史
    rebal = [pd.Timestamp(d) for d in REBAL_DATES]

    with contextlib.redirect_stdout(io.StringIO()):
        bs.login()
    try:
        # 并集 universe（含后来退市的）
        union = set()
        for d in REBAL_DATES:
            union |= set(_universe(d))
        union = sorted(union)
        rng = np.random.default_rng(seed)
        sample = list(rng.choice(union, size=min(n_sample, len(union)), replace=False))
        print(f"并集 universe（含退市）: {len(union)} 只；抽样 {len(sample)} 只；持有 {hold} 交易日")

        rows = []
        delisted_hits = 0
        for k, bcode in enumerate(sample):
            if (k + 1) % 50 == 0:
                print(f"  ...已处理 {k+1}/{len(sample)}，样本 {len(rows)}，含退市窗口 {delisted_hits}")
            h = _history(bcode, start, end)
            if h is None or len(h) < 66:
                continue
            dates = h["date"].values
            closes = h["close"].tolist()
            turns = h["turn"].tolist()
            pbs = h["pbMRQ"]
            last_date = h["date"].iloc[-1]
            L = len(closes)
            for d in rebal:
                # 该股最后成交日早于调仓日 → 调仓时已退市，跳过
                if last_date < d:
                    continue
                i = int(np.searchsorted(dates, np.datetime64(d), side="right")) - 1
                if i < 65 or i >= L - 1:        # 需要 65 日历史，且 i 后面至少有 1 天
                    continue
                metrics = sc._tech_metrics(closes[:i + 1])
                if metrics is None:
                    continue
                pb_hist = pbs.iloc[:i + 1].dropna()
                pb_hist = pb_hist[pb_hist > 0]
                if len(pb_hist) < 60:
                    continue
                pct = float((pb_hist < pb_hist.iloc[-1]).mean() * 100)
                tech = sc._real_tech_score(metrics, turns[i], "短线波段")
                # 未来收益：满 60 日则用 i+hold；否则（退市/数据到头）用最后一笔
                j = i + hold
                if j >= L:
                    j = L - 1
                    delisted_hits += 1
                fwd = (closes[j] / closes[i] - 1) * 100
                if not np.isfinite(fwd):
                    continue
                rows.append({"技术分": tech, "估值分位": pct,
                             "估值分": sc._valuation_score(pct), "未来收益": fwd})

        df = pd.DataFrame(rows)
        _report(df, hold, delisted_hits)
        df.to_csv("回测样本_sf.csv", index=False, encoding="utf-8-sig")
        print("\n明细已存 回测样本_sf.csv")
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            bs.logout()


def _quintile(df, by):
    q = pd.qcut(df[by], 5, labels=["Q1(低)", "Q2", "Q3", "Q4", "Q5(高)"], duplicates="drop")
    g = df.groupby(q, observed=True)["未来收益"]
    return pd.DataFrame({"样本": g.size(), "平均收益%": g.mean().round(2),
                         "中位%": g.median().round(2),
                         "胜率%": g.apply(lambda s: (s > 0).mean() * 100).round(1)})


def _report(df, hold, delisted_hits):
    if len(df) < 50:
        print(f"⚠️ 样本仅 {len(df)}，不足。")
        return
    base = df["未来收益"].mean()
    print(f"\n{'='*64}\n样本 {len(df)}（其中 {delisted_hits} 个未来窗口因退市截断）"
          f"\n全样本平均 {hold} 日收益 = {base:.2f}%（基准，含退市损失）\n{'='*64}")
    print(f"\nSpearman：技术分 {_spearman(df['技术分'], df['未来收益']):+.3f}　"
          f"估值分位 {_spearman(df['估值分位'], df['未来收益']):+.3f}")
    print(f"\n按【技术分(反转)】分档（Q5=最超跌→最高分）：")
    print(_quintile(df, "技术分").to_string())
    print(f"\n按【估值分位】分档（Q1=最便宜）：")
    print(_quintile(df, "估值分位").to_string())

    print(f"\n短线综合(w×技术 + (1-w)×估值分) Top20% 组表现：")
    print(f"{'w技术':>6}{'Spearman':>10}{'Top20%收益':>12}{'超额':>9}")
    for w in (1.0, 0.7, 0.5, 0.3, 0.0):
        s = w * df["技术分"] + (1 - w) * df["估值分"]
        top = df[s >= s.quantile(0.8)]["未来收益"].mean()
        print(f"{w:>6.1f}{_spearman(s, df['未来收益']):>+10.3f}{top:>11.2f}%{top-base:>+8.2f}%")
    print(f"\n（基准 {base:.2f}%。与 backtest.py 的纯幸存者样本对比，看 edge 缩水多少。）")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    h = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    run(n_sample=n, hold=h)
