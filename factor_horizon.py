"""不同持有期（5/10/20交易日）下各因子的横截面 IC 对比。
复用 strategy.py 缓存（含退市股 union 抽样），每月横截面算 IC，训练/检验分段。
回答："持有压缩到一周(~5日)会怎样"。
"""
import os
import pickle
import numpy as np
import pandas as pd
import net_patch  # noqa: F401

CACHE = "_strat_hist.pkl"
REBAL = pd.date_range("2021-01-01", "2025-11-01", freq="MS")


def _factors(c, turn, pb, i):
    f = {}
    if i >= 20:
        f["mom20(追涨)"] = c[i] / c[i - 20] - 1
    if i >= 60:
        f["mom60(追涨)"] = c[i] / c[i - 60] - 1
    if i >= 5:
        f["rev5(超跌反转)"] = -(c[i] / c[i - 5] - 1)
    seg = c[max(0, i - 20):i + 1]
    if len(seg) >= 16:
        r = np.diff(np.log(seg))
        f["vol20(低波)"] = -np.std(r)
        f["maxret(反彩票)"] = -np.max(r)
    t = turn[max(0, i - 19):i + 1]
    t = t[~np.isnan(t)]
    if len(t):
        f["turn(低换手)"] = -np.mean(t)
    if pb[i] > 0:
        f["bp(便宜)"] = 1.0 / pb[i]
    return f


def _sp(a, b):
    return pd.Series(a).rank().corr(pd.Series(b).rank())


def run():
    if not os.path.exists(CACHE):
        print(f"缺少 {CACHE}，先跑 strategy.py"); return
    hist = pickle.load(open(CACHE, "rb"))["hist"]
    arrs = {bc: (h["date"].values, h["close"].values, h["turn"].values, h["pbMRQ"].values)
            for bc, h in hist.items()}
    print(f"复用 {len(arrs)} 只历史\n")
    print(f"{'因子':<16}" + "".join(f"H={h:>2}日   " for h in (5, 10, 20)))
    print(f"{'':16}" + "  (IC全 / 检验)" * 3)

    factor_names = ["mom20(追涨)", "mom60(追涨)", "rev5(超跌反转)", "vol20(低波)",
                    "maxret(反彩票)", "turn(低换手)", "bp(便宜)"]
    table = {fn: {} for fn in factor_names}
    for H in (5, 10, 20):
        perdate = {fn: [] for fn in factor_names}
        for d in REBAL:
            dnp = np.datetime64(d)
            buf = {fn: [] for fn in factor_names}
            for bc, (dates, c, turn, pb) in arrs.items():
                i = int(np.searchsorted(dates, dnp, side="right")) - 1
                if i < 121 or i >= len(c) - 1:
                    continue
                j = min(i + H, len(c) - 1)
                fwd = c[j] / c[i] - 1
                if not np.isfinite(fwd):
                    continue
                for fn, v in _factors(c, turn, pb, i).items():
                    if fn in buf and np.isfinite(v):
                        buf[fn].append((v, fwd))
            for fn in factor_names:
                if len(buf[fn]) >= 30:
                    a = [x[0] for x in buf[fn]]; b = [x[1] for x in buf[fn]]
                    perdate[fn].append(_sp(a, b))
        for fn in factor_names:
            s = pd.Series(perdate[fn]).dropna()
            if len(s) >= 10:
                mid = len(s) // 2
                table[fn][H] = (s.mean(), s.iloc[mid:].mean())

    for fn in factor_names:
        cells = ""
        for H in (5, 10, 20):
            if H in table[fn]:
                a, te = table[fn][H]
                cells += f"{a:+.3f}/{te:+.3f}  "
            else:
                cells += "  --       "
        print(f"{fn:<16}{cells}")
    print("\n读法：IC=横截面秩相关(全样本/检验期)。正=该因子越大未来越涨。")
    print("关注 H=5(一周) 列与 H=20(一月) 列的变化。")


if __name__ == "__main__":
    run()
