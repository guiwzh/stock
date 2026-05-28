"""行业动量回测：验证「买近期领涨的赛道」在 A 股是否真能赚钱（无幸存者偏差）。

方法：
  • 复用 strategy.py 缓存的历史（含退市股的 union 抽样）；用 baostock 行业分类把个股归行业；
  • 每月在【横截面行业】上：算各行业近 L 日等权收益（动量）→ 看其后 H 日等权收益；
  • 行业动量 IC = 横截面 rank corr(过去收益, 未来收益)，再按月平均；
  • 比较「领涨档（top 行业）」未来收益 vs 全行业均值（=买领涨赛道的超额）；训练/检验分段。

结论用于判断：买领涨赛道是趋势延续(IC>0)还是反转(IC<0)，超额是否扛得住成本。

用法： python backtest_sector.py [持有交易日=20]
"""
import io
import os
import sys
import pickle
import contextlib
import numpy as np
import pandas as pd

import net_patch  # noqa: F401
import baostock as bs

CACHE = "_strat_hist.pkl"
REBAL = pd.date_range("2021-01-01", "2025-10-01", freq="MS")


def _industry_map():
    """baostock 行业分类：{ 'sh.600000': '银行', ... }（当前口径，近似时点）。"""
    with contextlib.redirect_stdout(io.StringIO()):
        bs.login()
    try:
        rs = bs.query_stock_industry()
        out = {}
        while rs.error_code == "0" and rs.next():
            row = rs.get_row_data()   # [updateDate, code, code_name, industry, classification]
            if len(row) >= 4 and row[3]:
                out[row[1]] = row[3]
        return out
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            bs.logout()


def _spearman(a, b):
    return pd.Series(a).rank().corr(pd.Series(b).rank())


def run(hold=20, min_members=5):
    if not os.path.exists(CACHE):
        print(f"缺少 {CACHE}，请先跑 strategy.py 生成缓存"); return
    with open(CACHE, "rb") as f:
        hist = pickle.load(f)["hist"]
    print(f"复用缓存 {len(hist)} 只历史")
    indmap = _industry_map()
    # 个股按行业归组
    groups = {}
    for bcode, h in hist.items():
        ind = indmap.get(bcode)
        if not ind:
            continue
        groups.setdefault(ind, []).append((bcode, h))
    groups = {k: v for k, v in groups.items() if len(v) >= min_members}
    print(f"有效行业 {len(groups)} 个（每行业≥{min_members}只）")

    for lookback in (10, 20, 60):
        rows = []
        for d in REBAL:
            dnp = np.datetime64(d)
            tr, fw = {}, {}
            for ind, members in groups.items():
                trs, fws = [], []
                for _bc, h in members:
                    dates = h["date"].values
                    c = h["close"].values
                    i = int(np.searchsorted(dates, dnp, side="right")) - 1
                    if i < lookback or i >= len(c) - 1:
                        continue
                    trs.append(c[i] / c[i - lookback] - 1)
                    j = min(i + hold, len(c) - 1)
                    fws.append(c[j] / c[i] - 1)
                if len(trs) >= 3:
                    tr[ind] = np.mean(trs)
                    fw[ind] = np.mean(fws)
            if len(tr) >= 8:
                inds = list(tr)
                rows.append({"date": d,
                             "ic": _spearman([tr[k] for k in inds], [fw[k] for k in inds]),
                             "tr": {k: tr[k] for k in inds},
                             "fw": {k: fw[k] for k in inds}})
        _report(rows, hold, lookback)


def _quint_excess(rows, which="top"):
    """每期取动量 top/bottom 20% 行业，未来收益减全行业均值，按期平均。"""
    exc = []
    for r in rows:
        inds = list(r["tr"])
        s = pd.Series(r["tr"])
        base = np.mean([r["fw"][k] for k in inds])
        cut = s.quantile(0.8 if which == "top" else 0.2)
        sel = s[s >= cut].index if which == "top" else s[s <= cut].index
        if len(sel):
            exc.append(np.mean([r["fw"][k] for k in sel]) - base)
    return np.mean(exc) if exc else float("nan")


def _report(rows, hold, lookback):
    if len(rows) < 10:
        print(f"\n[回看{lookback}日] 样本期不足"); return
    ics = pd.Series([r["ic"] for r in rows]).dropna()
    n = len(rows); mid = n // 2
    ic_all, ic_tr, ic_te = ics.mean(), pd.Series([r["ic"] for r in rows[:mid]]).mean(), pd.Series([r["ic"] for r in rows[mid:]]).mean()
    icir = ic_all / (ics.std() + 1e-9) * np.sqrt(len(ics))
    ann = 252 / hold
    top_exc = _quint_excess(rows, "top") * ann * 100
    top_tr = _quint_excess(rows[:mid], "top") * ann * 100
    top_te = _quint_excess(rows[mid:], "top") * ann * 100
    print(f"\n{'='*64}\n行业动量｜回看 {lookback} 日，持有 {hold} 日，{n} 期\n{'='*64}")
    print(f"  行业IC(过去收益→未来收益): 全 {ic_all:+.3f}  训练 {ic_tr:+.3f}  检验 {ic_te:+.3f}  ICIR {icir:+.2f}")
    print(f"  {'解读: IC>0=领涨赛道继续领涨(动量); IC<0=领涨后回落(反转)'}")
    print(f"  买领涨档(top20%行业) 超额(年化,对全行业均值): 全 {top_exc:+.1f}%  训练 {top_tr:+.1f}%  检验 {top_te:+.1f}%")


if __name__ == "__main__":
    h = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    run(hold=h)
