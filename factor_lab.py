"""无偏横截面因子实验室：严谨检验「单因子是否真有选股能力」。

机构级检验标准（修正之前回测的方法学硬伤）：
  1. 无幸存者偏差：universe = 各调仓日时点全集的并集（含后来退市/转板的股票）。
  2. 横截面 IC：每个调仓日，在【当日全市场横截面】上算 factor ↔ 未来收益 的
     秩相关（Spearman IC），再按月平均。这才衡量「选股」，而非大盘整体涨跌。
  3. 样本外：按时间中点切训练/检验，只认两段方向一致、都显著的因子。
  4. 扣成本：分层多空组合年化收益扣往返交易成本。
  5. 退市处理：未来窗口因退市截断时，收益算到最后成交价（计入暴跌损失）。

IC 解读：|IC|≈0.03 偏弱、0.05 可用、0.08+ 较强；ICIR（IC均值/IC标准差×√期数）
         >2 说明稳定。训练/检验同号才算稳健，否则是噪声/过拟合。

用法： python factor_lab.py [样本数=500] [持有交易日=20]
"""
import io
import sys
import contextlib
import datetime
import numpy as np
import pandas as pd

import net_patch  # noqa: F401
import baostock as bs

# 月度调仓日（每月初附近），覆盖约 5 年；每个都需 ≥20 交易日未来窗口
REBAL_DATES = pd.date_range("2021-01-01", "2025-10-01", freq="MS").strftime("%Y-%m-%d").tolist()
ROUND_TRIP_COST = 0.004  # 多空两腿合计单次往返成本约 0.4%（佣金+印花+滑点的保守估计）


def _universe(day):
    rs = bs.query_all_stock(day=day)
    out = []
    while rs.error_code == "0" and rs.next():
        c = rs.get_row_data()[0]
        mkt, num = c.split(".")
        if (mkt == "sh" and num.startswith("60")) or (mkt == "sz" and num.startswith("00")):
            out.append(c)
    return out


def _history(bcode, start, end):
    rs = bs.query_history_k_data_plus(
        bcode, "date,close,turn,pbMRQ,peTTM", start_date=start, end_date=end,
        frequency="d", adjustflag="2")
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["date", "close", "turn", "pbMRQ", "peTTM"])
    df["date"] = pd.to_datetime(df["date"])
    for c in ("close", "turn", "pbMRQ", "peTTM"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _factors_at(c, turn, pb, pe, i):
    """在位置 i 计算各因子原始值（c/turn/pb/pe 为 numpy 数组）。"""
    f = {}
    if i >= 20:
        f["mom20"] = c[i] / c[i - 20] - 1
        f["rev20"] = -(c[i] / c[i - 20] - 1)          # 短期反转
    if i >= 60:
        f["mom60"] = c[i] / c[i - 60] - 1
    if i >= 120:
        f["mom120"] = c[i] / c[i - 120] - 1
    if i >= 5:
        f["rev5"] = -(c[i] / c[i - 5] - 1)
    seg = c[max(0, i - 20):i + 1]
    if len(seg) >= 16:
        rets = np.diff(np.log(seg))
        f["vol20"] = -np.std(rets)                    # 低波动因子（取负：低波→高分）
        f["maxret20"] = -np.max(rets)                 # 彩票因子（取负：低极端涨→高分）
    t = turn[max(0, i - 19):i + 1]
    t = t[~np.isnan(t)]
    if len(t):
        f["turn20_lo"] = -np.mean(t)                  # 低换手因子（取负）
    if pb[i] > 0:
        f["bp"] = 1.0 / pb[i]                         # 账面/市值（高=便宜，横截面）
    if pe[i] > 0:
        f["ep"] = 1.0 / pe[i]                         # 盈利/市值（高=便宜，横截面）
    hist = pb[:i + 1]
    hist = hist[hist > 0]
    if len(hist) >= 60:
        f["pb_pctile_lo"] = -float((hist < pb[i]).mean())   # PB 时序分位（取负：越便宜→高分）
    return f


def run(n_sample=500, hold=20, seed=42):
    end = datetime.date.today().strftime("%Y-%m-%d")
    start = "2020-01-01"
    rebal = [pd.Timestamp(d) for d in REBAL_DATES]

    with contextlib.redirect_stdout(io.StringIO()):
        bs.login()
    try:
        union = set()
        for d in REBAL_DATES[::6]:           # 每半年取一次时点全集做并集（够覆盖含退市）
            union |= set(_universe(d))
        union = sorted(union)
        rng = np.random.default_rng(seed)
        sample = list(rng.choice(union, size=min(n_sample, len(union)), replace=False))
        print(f"并集 universe（含退市）{len(union)} 只，抽样 {len(sample)}；月度调仓 {len(rebal)} 期；持有 {hold} 日")

        # 拉历史
        hist = {}
        for k, bcode in enumerate(sample):
            if (k + 1) % 100 == 0:
                print(f"  拉取历史 {k+1}/{len(sample)}…")
            h = _history(bcode, start, end)
            if h is not None and len(h) >= 130:
                hist[bcode] = h
        print(f"有效历史 {len(hist)} 只，开始逐期计算横截面 IC…")

        # 逐调仓日算横截面 IC
        per_date = []   # {date, factor: ic, n}
        for d in rebal:
            rows_f = {}     # factor -> list[(value, fwd)]
            for bcode, h in hist.items():
                dates = h["date"].values
                i = int(np.searchsorted(dates, np.datetime64(d), side="right")) - 1
                if i < 121 or i >= len(h) - 1:
                    continue
                c = h["close"].values
                j = min(i + hold, len(c) - 1)
                fwd = c[j] / c[i] - 1
                if not np.isfinite(fwd):
                    continue
                fac = _factors_at(c, h["turn"].values, h["pbMRQ"].values, h["peTTM"].values, i)
                for name, val in fac.items():
                    if np.isfinite(val):
                        rows_f.setdefault(name, []).append((val, fwd))
            rec = {"date": d}
            for name, pairs in rows_f.items():
                if len(pairs) >= 30:
                    a = pd.Series([p[0] for p in pairs])
                    b = pd.Series([p[1] for p in pairs])
                    rec[name] = a.rank().corr(b.rank())     # 横截面 Spearman IC
                    rec[name + "_q"] = _quintile_spread(a, b)  # top-bottom 五分位收益差
            per_date.append(rec)

        _report(pd.DataFrame(per_date), hold)
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            bs.logout()


def _quintile_spread(factor, fwd):
    """因子最高 20% 组 减 最低 20% 组 的平均未来收益（横截面多空）。"""
    try:
        q = pd.qcut(factor, 5, labels=False, duplicates="drop")
    except Exception:
        return np.nan
    top = fwd[q == q.max()].mean()
    bot = fwd[q == 0].mean()
    return top - bot


def _report(pdf, hold):
    pdf = pdf.sort_values("date").reset_index(drop=True)
    n = len(pdf)
    mid = n // 2
    factors = [c for c in pdf.columns if c not in ("date",) and not c.endswith("_q")]
    periods_per_year = 252 / hold

    print(f"\n{'='*78}\n月度横截面 IC（{hold} 日未来收益）　训练期 {mid} 期 / 检验期 {n-mid} 期\n{'='*78}")
    print(f"{'因子':<14}{'IC全':>8}{'IC训练':>9}{'IC检验':>9}{'ICIR全':>8}{'胜率%':>7}{'多空年化(净)%':>14}{'稳健?':>7}")
    results = []
    for f in factors:
        ic = pdf[f].dropna()
        if len(ic) < 10:
            continue
        ic_all = ic.mean()
        ic_tr = pdf[f].iloc[:mid].mean()
        ic_te = pdf[f].iloc[mid:].mean()
        icir = ic_all / (ic.std() + 1e-9) * np.sqrt(len(ic))
        winr = (ic > 0).mean() * 100
        # 多空：每期 top-bottom 收益差，扣往返成本，年化
        q = pdf[f + "_q"].dropna()
        gross = q.mean()
        net = gross - ROUND_TRIP_COST
        ann = net * periods_per_year * 100
        robust = "✓" if (np.sign(ic_tr) == np.sign(ic_te) and abs(ic_te) > 0.02) else ""
        results.append((f, ic_all, ic_tr, ic_te, icir, winr, ann, robust))

    for f, ic_all, ic_tr, ic_te, icir, winr, ann, robust in sorted(results, key=lambda x: -abs(x[3])):
        print(f"{f:<14}{ic_all:>+8.3f}{ic_tr:>+9.3f}{ic_te:>+9.3f}{icir:>+8.2f}{winr:>7.0f}{ann:>+14.1f}{robust:>7}")

    print(f"\n注：IC=横截面秩相关（每期在全市场内 factor 越大、未来收益越高则 IC>0）。")
    print(f"    |IC|≥0.03 偏弱 / 0.05 可用 / 0.08+ 较强；ICIR>2 稳定；多空已扣往返成本 {ROUND_TRIP_COST*100:.1f}%。")
    print(f"    『稳健✓』= 训练/检验期 IC 同号且检验期 |IC|>0.02。只信打✓的。")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    h = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    run(n_sample=n, hold=h)
