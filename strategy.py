"""纯多头组合多因子策略 —— 验证「能否真正跑赢市场」。

基于 factor_lab 的发现，用样本外仍稳健的因子构建组合：
  • 低波动 (vol20)        —— 低风险异象，最稳
  • 反彩票 (maxret20)     —— 避开月内极端暴涨的妖股，最强
  • 中期反转 (-mom60)     —— A 股 2-6 月动量是反的
  • 价值 (BP=1/PB)        —— 便宜，小权重补充

方法（继续保持无偏严谨）：
  • universe = 各调仓日时点全集并集（含退市股），退市收益算到最后成交价；
  • 每月在横截面上对各因子算 z-score（去极值 ±3），等权合成；
  • 纯多头：选合成分最高一档（及 Top30 只），等权，对标【等权全市场】算超额；
  • 训练/检验分段；按【实际换手】扣往返成本；散户不能做空，故只看多头超额。

用法： python strategy.py [样本数=600] [持有交易日=20] [--refetch]
"""
import io
import os
import sys
import pickle
import threading
import contextlib
import datetime
import numpy as np
import pandas as pd

import net_patch  # noqa: F401
import baostock as bs

REBAL_DATES = pd.date_range("2021-01-01", "2025-10-01", freq="MS").strftime("%Y-%m-%d").tolist()
ONE_WAY_COST = 0.0015          # 单边成本（佣金+印花/2+滑点）约 0.15%
CACHE = "_strat_hist.pkl"


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


def _with_timeout(fn, timeout):
    """在 daemon 线程里跑 fn，超时返回 None（baostock 偶发挂死时不拖死整轮）。"""
    box = {}
    t = threading.Thread(target=lambda: box.update(r=fn()), daemon=True)
    t.start()
    t.join(timeout)
    return None if t.is_alive() else box.get("r")


def _load_histories(n_sample, seed, refetch, per_timeout=15):
    if os.path.exists(CACHE) and not refetch:
        with open(CACHE, "rb") as f:
            d = pickle.load(f)
        if d.get("n") == n_sample and d.get("seed") == seed and len(d.get("hist", {})) > 0:
            print(f"复用缓存历史 {len(d['hist'])} 只（{CACHE}）")
            return d["hist"]
    union = set()
    for day in REBAL_DATES[::6]:
        u = _with_timeout(lambda: _universe(day), 20)
        if u:
            union |= set(u)
    union = sorted(union)
    rng = np.random.default_rng(seed)
    sample = list(rng.choice(union, size=min(n_sample, len(union)), replace=False))
    print(f"并集 universe（含退市）{len(union)} 只，抽样 {len(sample)}，拉取历史…", flush=True)
    today = datetime.date.today().strftime("%Y-%m-%d")
    hist, skipped = {}, 0
    for k, bcode in enumerate(sample):
        h = _with_timeout(lambda: _history(bcode, "2020-01-01", today), per_timeout)
        if h is not None and len(h) >= 130:
            hist[bcode] = h
        else:
            skipped += 1
        if (k + 1) % 50 == 0:
            print(f"  {k+1}/{len(sample)}…（有效 {len(hist)}，跳过 {skipped}）", flush=True)
            with open(CACHE, "wb") as f:           # 增量存盘，防中途挂死丢进度
                pickle.dump({"n": n_sample, "seed": seed, "hist": hist}, f)
    with open(CACHE, "wb") as f:
        pickle.dump({"n": n_sample, "seed": seed, "hist": hist}, f)
    print(f"有效历史 {len(hist)} 只（跳过 {skipped}），已缓存", flush=True)
    return hist


INDICES = {"沪深300": "sh.000300", "中证500": "sh.000905", "中证1000": "sh.000852"}


def _load_indices():
    """一次登录取多个宽基指数日线，用于区分『真 alpha』与『小盘风格红利』。"""
    def fetch():
        with contextlib.redirect_stdout(io.StringIO()):
            bs.login()
        out = {}
        try:
            for name, code in INDICES.items():
                rs = bs.query_history_k_data_plus(
                    code, "date,close", start_date="2020-01-01",
                    end_date=datetime.date.today().strftime("%Y-%m-%d"),
                    frequency="d", adjustflag="3")
                rows = []
                while rs.error_code == "0" and rs.next():
                    rows.append(rs.get_row_data())
                if rows:
                    df = pd.DataFrame(rows, columns=["date", "close"])
                    out[name] = (pd.to_datetime(df["date"]).values,
                                 pd.to_numeric(df["close"], errors="coerce").values)
        finally:
            with contextlib.redirect_stdout(io.StringIO()):
                bs.logout()
        return out
    return _with_timeout(fetch, 50) or {}


def _idx_fwd(arr, d, hold):
    dates, close = arr
    ii = int(np.searchsorted(dates, np.datetime64(d), side="right")) - 1
    if 0 <= ii < len(close) - 1:
        return close[min(ii + hold, len(close) - 1)] / close[ii] - 1
    return float("nan")


def _raw_factors(c, turn, pb, i):
    """位置 i 的原始因子（已统一为"越大越好"方向）。"""
    seg = c[max(0, i - 20):i + 1]
    if len(seg) < 16 or i < 61:
        return None
    rets = np.diff(np.log(seg))
    f = {
        "低波动": -np.std(rets),
        "反彩票": -np.max(rets),
        "中期反转": -(c[i] / c[i - 60] - 1),
    }
    f["价值BP"] = (1.0 / pb[i]) if pb[i] > 0 else np.nan
    return f


def _zscore(s):
    s = s.astype(float)
    mu, sd = s.mean(), s.std()
    if sd < 1e-9:
        return pd.Series(0.0, index=s.index)
    return ((s - mu) / sd).clip(-3, 3)


def run(n_sample=600, hold=20, seed=42, refetch=False):
    with contextlib.redirect_stdout(io.StringIO()):
        bs.login()
    try:
        hist = _load_histories(n_sample, seed, refetch)
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            bs.logout()

    idxs = _load_indices()           # {名称: (dates, close)}
    if not idxs:
        print("⚠️ 指数未取到，将只用等权全市场基准。", flush=True)

    rebal = [pd.Timestamp(d) for d in REBAL_DATES]
    recs = []
    prev_top, prev_t30 = set(), set()
    for d in rebal:
        codes, vals, fwds = [], [], []
        for bcode, h in hist.items():
            dates = h["date"].values
            i = int(np.searchsorted(dates, np.datetime64(d), side="right")) - 1
            if i < 121 or i >= len(h) - 1:
                continue
            c = h["close"].values
            fac = _raw_factors(c, h["turn"].values, h["pbMRQ"].values, i)
            if fac is None:
                continue
            j = min(i + hold, len(c) - 1)
            fwd = c[j] / c[i] - 1
            if not np.isfinite(fwd):
                continue
            codes.append(bcode)
            vals.append(fac)
            fwds.append(fwd)
        if len(codes) < 50:
            continue
        fdf = pd.DataFrame(vals, index=codes)
        # 横截面 z-score，逐因子标准化后等权合成（缺失因子按 0 中性）
        z = pd.DataFrame({col: _zscore(fdf[col]) for col in fdf.columns}).fillna(0.0)
        composite = z.mean(axis=1)
        fwd = pd.Series(fwds, index=codes)

        bench = fwd.mean()
        order = composite.sort_values(ascending=False)
        qn = max(len(order) // 5, 1)
        top = set(order.index[:qn]); bot = set(order.index[-qn:])
        t30 = set(order.index[:30])
        top_turn = 1 - len(top & prev_top) / len(top) if prev_top else 1.0
        t30_turn = 1 - len(t30 & prev_t30) / len(t30) if prev_t30 else 1.0
        prev_top, prev_t30 = top, t30
        rec = {
            "date": d, "bench": bench,
            "port": fwd[list(top)].mean() - top_turn * 2 * ONE_WAY_COST,
            "port_gross": fwd[list(top)].mean(),
            "bot": fwd[list(bot)].mean(),
            "top30": fwd[list(t30)].mean() - t30_turn * 2 * ONE_WAY_COST,
            "n": len(codes),
        }
        for name, arr in idxs.items():
            rec[name] = _idx_fwd(arr, d, hold)
        recs.append(rec)
    _report(pd.DataFrame(recs), hold)


def _ann(monthly_mean, hold):
    return monthly_mean * (252 / hold) * 100


def _maxdd(curve):
    peak = curve.cummax()
    return ((curve - peak) / peak).min() * 100


def _seg(df, hold, label):
    port = df["port"].mean()
    print(f"\n【{label}】{len(df)} 期   组合(最优档)年化 {_ann(port, hold):+.1f}%")
    print(f"  vs 等权全市场 ({_ann(df['bench'].mean(), hold):+.1f}%) : 净超额 {_ann(port-df['bench'].mean(), hold):+.1f}%/年  （同口径，剔除 size：>0 才是真选股 alpha）")
    for name in ("沪深300", "中证500", "中证1000"):
        if name in df.columns and df[name].notna().any():
            b = df[name].mean(); win = (df["port"] > df[name]).mean() * 100
            print(f"  vs {name} ({_ann(b, hold):+.1f}%) : 净超额 {_ann(port-b, hold):+.1f}%/年  胜率 {win:.0f}%")
    print(f"  参考最差档 {_ann(df['bot'].mean(), hold):+.1f}%（多空毛 {_ann(df['port_gross'].mean()-df['bot'].mean(), hold):+.1f}%/年）")


def _report(df, hold):
    if len(df) == 0:
        print("\n⚠️ 没有任何可用样本——baostock 未返回历史数据（多半是 IP 被限流/封锁）。\n"
              "   请在未被限流的网络（如你本机）重试：venv/bin/python strategy.py 500 20")
        return
    df = df.sort_values("date").reset_index(drop=True)
    n = len(df); mid = n // 2
    print(f"\n{'='*70}\n纯多头组合多因子（低波+反彩票+中期反转+价值），持有 {hold} 日，月度调仓"
          f"\n样本均值股票数/期 ≈ {df['n'].mean():.0f}，扣单边成本 {ONE_WAY_COST*100:.2f}%\n{'='*70}")
    _seg(df, hold, "全期")
    _seg(df.iloc[:mid], hold, "训练期(前半)")
    test = df.iloc[mid:]
    _seg(test, hold, "检验期(后半)")

    # —— 自动结论判定：区分『真选股 alpha』(对同口径/同体量为正) 与『小盘风格红利』(只赢沪深300) ——
    def e(col):
        return _ann(test["port"].mean() - test[col].mean(), hold) if col in test.columns else float("nan")
    e_ew, e300, e500, e1000 = e("bench"), e("沪深300"), e("中证500"), e("中证1000")

    print(f"\n{'='*70}\n📌 结论判定（检验期净超额，年化）\n{'='*70}")
    print(f"  vs 等权全市场(同口径,剔size) {e_ew:+.1f}%　vs沪深300 {e300:+.1f}%　"
          f"vs中证500 {e500:+.1f}%　vs中证1000 {e1000:+.1f}%")

    # 真选股 alpha 的硬标准：对【同口径等权全市场】为正（剔除了 size 与等权/市值加权差异）
    if e_ew > 0:
        verdict = ("✅ 真选股 alpha：对同口径等权全市场也有正超额，选股力扎实。可作为选股主依据。")
    elif (e300 > 0 and e500 > 0 and e1000 > 0):
        verdict = ("🟢 实用『风格edge』(非选股alpha)：跑赢可买的宽基ETF(沪深300/中证500/1000)，"
                   "但对【同口径等权全市场】无超额——超额主要来自『等权+中小盘+防御』风格，"
                   "而非选股本身。\n"
                   "       → 实用价值：若你本来就买宽基ETF，用它替代能多赚几个点；\n"
                   "       → 风险：这是风格暴露，大盘股/高beta领涨时会失效甚至跑输。")
    elif e300 > 0:
        verdict = "🟡 仅能跑赢沪深300，对中证500/1000/等权全市场无稳定超额，结论高度依赖基准选择。"
    else:
        verdict = "❌ 不建议采用：对各基准均无稳定正超额。"
    print(f"\n  {verdict}")
    print(f"\n  ⚠️ 注：单一样本/抽样、未含冲击成本与停牌不可交易等摩擦；仅供研究参考，不构成投资建议。")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    refetch = "--refetch" in sys.argv
    n = int(args[0]) if len(args) > 0 else 600
    h = int(args[1]) if len(args) > 1 else 20
    run(n_sample=n, hold=h, refetch=refetch)
