"""验证「长期趋势过滤」是否能避开价值陷阱（如白酒式长期下跌）。
复用 strategy.py 缓存。每月取量化合成分 top 档（策略会买的票），比较其中
「站上年线MA250(企稳/上升)」vs「跌破年线(确认下跌)」的未来收益差异。
若下跌组明显跑输，则趋势过滤有效。
"""
import os
import pickle
import numpy as np
import pandas as pd
import net_patch  # noqa: F401

CACHE = "_strat_hist.pkl"
REBAL = pd.date_range("2021-06-01", "2025-10-01", freq="MS")
H = 20  # 持有约一个月


def _zs(x):
    x = pd.Series(x, dtype=float)
    sd = x.std()
    return (x - x.mean()) / sd if sd > 1e-9 else x * 0


def run():
    hist = pickle.load(open(CACHE, "rb"))["hist"]
    arrs = {bc: (h["date"].values, h["close"].values, h["turn"].values, h["pbMRQ"].values)
            for bc, h in hist.items()}
    print(f"复用 {len(arrs)} 只历史；持有 {H} 日\n")

    rows = []  # 每期: top档的 (fwd, 是否站上年线)
    for d in REBAL:
        dnp = np.datetime64(d)
        recs = []
        for bc, (dates, c, turn, pb) in arrs.items():
            i = int(np.searchsorted(dates, dnp, side="right")) - 1
            if i < 250 or i >= len(c) - 1:
                continue
            seg = c[i - 20:i + 1]
            r = np.diff(np.log(seg))
            vol = -np.std(r)
            mx = -np.max(r)
            rev60 = -(c[i] / c[i - 60] - 1)
            bp = (1.0 / pb[i]) if pb[i] > 0 else np.nan
            ma250 = c[i - 250:i + 1].mean()
            up = c[i] >= ma250            # 站上年线 = 企稳/上升
            mom12 = c[i] / c[i - 250] - 1
            j = min(i + H, len(c) - 1)
            fwd = c[j] / c[i] - 1
            if not np.isfinite(fwd) or not np.isfinite(bp):
                continue
            recs.append((bc, vol, mx, rev60, bp, up, mom12, fwd))
        if len(recs) < 40:
            continue
        dfd = pd.DataFrame(recs, columns=["bc", "vol", "mx", "rev", "bp", "up", "mom12", "fwd"])
        comp = (_zs(dfd["vol"]) + _zs(dfd["mx"]) + _zs(dfd["rev"]) + _zs(dfd["bp"])) / 4
        dfd["comp"] = comp.values
        base = dfd["fwd"].mean()
        cut = dfd["comp"].quantile(0.8)
        top = dfd[dfd["comp"] >= cut]
        rows.append({"date": d, "base": base,
                     "top_all": top["fwd"].mean(),
                     "top_up": top[top["up"]]["fwd"].mean(),
                     "top_down": top[~top["up"]]["fwd"].mean(),
                     "n_up": int(top["up"].sum()), "n_down": int((~top["up"]).sum())})
    _report(pd.DataFrame(rows))


def _ann(x):
    return x * (252 / H) * 100


def _seg(df, label):
    base = df["base"].mean()
    print(f"\n【{label}】{len(df)} 期  (基准=全样本均值)")
    print(f"  策略 top档(全部)        年化 {_ann(df['top_all'].mean()):+.1f}%  超额 {_ann(df['top_all'].mean()-base):+.1f}%")
    print(f"  ├ 站上年线(企稳/上升)    年化 {_ann(df['top_up'].mean()):+.1f}%  超额 {_ann(df['top_up'].mean()-base):+.1f}%  (均{df['n_up'].mean():.0f}只/期)")
    print(f"  └ 跌破年线(确认下跌)    年化 {_ann(df['top_down'].mean()):+.1f}%  超额 {_ann(df['top_down'].mean()-base):+.1f}%  (均{df['n_down'].mean():.0f}只/期)")
    print(f"  → 趋势过滤增益(上升组−下跌组): {_ann(df['top_up'].mean()-df['top_down'].mean()):+.1f}%/年")


def _report(df):
    df = df.dropna(subset=["top_up", "top_down"]).sort_values("date").reset_index(drop=True)
    if len(df) < 10:
        print("样本不足"); return
    mid = len(df) // 2
    _seg(df, "全期")
    _seg(df.iloc[:mid], "训练期")
    _seg(df.iloc[mid:], "检验期")
    print("\n判读：若『站上年线』组持续显著强于『跌破年线』组，则趋势过滤能避开价值陷阱(白酒式长跌)。")


if __name__ == "__main__":
    if not os.path.exists(CACHE):
        print(f"缺 {CACHE}，先跑 strategy.py")
    else:
        run()
