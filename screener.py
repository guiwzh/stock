"""A股选股引擎：价值面 + 技术面综合打分。

数据源：东方财富（多级降级到腾讯/新浪）。
- 全市场实时快照：自实现 clist 分页抓取(_em_clist)，多镜像轮换+停顿，规避反爬，
  取 价格/PE/PB/市值/动量/换手率/量比
- 业绩报表 ak.stock_yjbb_em：ROE/净利润同比/营收同比/毛利率/EPS（一次拿全市场）
- 个股历史 ak.stock_zh_a_hist：用于个股详情的均线/RSI/趋势

打分思路（阈值法，结果可解释、跨期稳定）：
  价值分(0-100)：ROE、PE、PB、净利润增长、营收增长、毛利率 加权 × 现金流质量乘数
  技术分(0-100)：快照层(动量+换手率) → 入围后 baostock 前复权真实层(反转式)
  估值分(0-100)：PB 历史百分位（仅入围股，其余中性 50）
  综合分 = w₁×价值 + w₂×技术 + w₃×估值（权重按投资风格切换，经回测样本外验证）
"""
import net_patch  # noqa: F401  必须最先导入，打好代理/重试补丁
import time
import datetime
import pandas as pd
import akshare as ak

# ---------------------------------------------------------------- 数据获取


def _retry_call(fn, tries=4, gap=2.0, label=""):
    """整次接口调用的外层重试：东方财富分页偶尔整段断连时兜底。"""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            if i < tries - 1:
                time.sleep(gap * (i + 1))
    raise RuntimeError(f"{label} 多次重试仍失败：{last}")


def _find_col(df, *keys):
    """按子串匹配列名，容忍 akshare 列名小幅变动。"""
    for c in df.columns:
        for k in keys:
            if k in str(c):
                return c
    return None


# 东方财富行情接口有多个等价镜像域名，轮换可绕过单域名的反爬/限流
_EM_HOSTS = [
    "https://push2.eastmoney.com",
    "https://1.push2.eastmoney.com",
    "https://7.push2.eastmoney.com",
    "https://23.push2.eastmoney.com",
    "https://82.push2.eastmoney.com",
]
_EM_FIELDS = {  # f代码 -> 含义
    "f2": "最新价", "f3": "涨跌幅", "f6": "成交额", "f8": "换手率", "f9": "PE",
    "f10": "量比", "f12": "代码", "f14": "名称", "f20": "总市值",
    "f23": "PB", "f24": "动量60", "f25": "年初至今",
}


_PROXY_HINT = (
    "无法连接东方财富行情接口(push2.eastmoney.com)。\n"
    "最常见原因：代理/VPN(如 Clash)的 TUN/增强模式劫持了流量——\n"
    "  注意：仅关闭「系统代理」开关在 TUN 模式下无效，需【彻底退出代理软件】。\n"
    "确认方法：\n"
    "  • 终端执行  env | grep -i proxy   应为空\n"
    "  • 终端执行  ifconfig | grep utun  应无虚拟网卡\n"
    "处理后重试即可（同公司的 datacenter 接口能通、唯独 push2 不通，"
    "基本就是分流规则把行情域名路由坏了）。"
)


def preflight_check(timeout=6):
    """启动自检：快速探测行情接口是否可达。

    优先检测东方财富 push2，不通则尝试新浪，都不通则抛出明确提示。
    专门绕开 net_patch 的重试适配器(max_retries=0)，确保几秒内就能失败。
    """
    import requests
    from requests.adapters import HTTPAdapter

    sess = requests.Session()
    sess.trust_env = False
    sess.mount("https://", HTTPAdapter(max_retries=0))
    sess.mount("http://", HTTPAdapter(max_retries=0))
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
    params = {
        "pn": 1, "pz": 1, "po": 1, "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2, "invt": 2, "fid": "f3", "fs": "m:0 t:6", "fields": "f12",
    }

    # 1) 试东方财富
    for host in _EM_HOSTS:
        try:
            r = sess.get(f"{host}/api/qt/clist/get", params=params,
                         headers=headers, timeout=timeout)
            data = r.json()
            if isinstance(data, dict) and "data" in data:
                return  # 东方财富可用
        except Exception:
            continue

    # 2) 东方财富不可用，试新浪
    try:
        import random
        r = sess.get(
            "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
            "/Market_Center.getHQNodeData",
            params={"page": 1, "num": 5, "sort": "symbol", "asc": "1",
                    "node": "hs_a", "symbol": "", "_s_r_a": str(random.random())},
            timeout=timeout)
        if len(r.text) > 50:  # Sina 可能返回非200
            import warnings
            warnings.warn("⚠️ 东方财富行情不通，将降级使用备用源"
                          "（baostock 清单 + 腾讯行情，缺少 60日动量/年初至今 字段）。")
            return  # 备用源可用
    except Exception:
        pass

    raise RuntimeError(f"{_PROXY_HINT}\n原始错误：东方财富和新浪行情接口均不可达")


def _em_clist(fs, pause=0.25, page_timeout=12, max_seconds=180):
    """自控分页抓取东方财富 clist 行情，规避反爬：
    - 每页之间停顿，降低请求频率
    - 多镜像域名轮换；单页失败换域名重试
    - 大 pz 减少总请求数
    - max_seconds 总超时：网络异常时不再无限重试，超时即明确报错
    """
    import requests

    sess = requests.Session()  # 已被 net_patch 挂上重试适配器
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
    pz = 200
    fields = ",".join(_EM_FIELDS.keys())
    rows, pn, total = [], 1, None
    start = time.time()
    while True:
        if time.time() - start > max_seconds:
            raise RuntimeError(
                f"实时快照拉取超过 {max_seconds}s 仍未完成，已中止。\n{_PROXY_HINT}")
        params = {
            "pn": pn, "pz": pz, "po": 1, "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2, "invt": 2, "fid": "f3", "fs": fs, "fields": fields,
        }
        data = None
        for k in range(len(_EM_HOSTS) * 2):
            if time.time() - start > max_seconds:
                break
            host = _EM_HOSTS[(pn + k) % len(_EM_HOSTS)]
            try:
                r = sess.get(f"{host}/api/qt/clist/get", params=params,
                             headers=headers, timeout=page_timeout)
                data = r.json()
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5 * (k + 1))
        if data is None:
            raise RuntimeError(f"实时快照第 {pn} 页多镜像均失败。\n{_PROXY_HINT}")
        block = (data or {}).get("data") or {}
        diff = block.get("diff") or []
        if isinstance(diff, dict):  # 个别版本返回 dict
            diff = list(diff.values())
        if not diff:
            break
        rows.extend(diff)
        total = block.get("total", total)
        if total and pn * pz >= total:
            break
        pn += 1
        time.sleep(pause)
    return pd.DataFrame(rows)


def _tf(parts, i):
    """按下标安全取 float，越界/非数 -> NaN。"""
    try:
        return float(parts[i])
    except (ValueError, IndexError, TypeError):
        return float("nan")


def _all_main_board_symbols():
    """用 baostock 取全市场清单，过滤出沪深主板（沪 60 / 深 00），
    返回腾讯行情用的带前缀代码（如 sh601899 / sz000001）。

    baostock 免 token、走自有协议、连接稳定，不依赖东方财富，
    专门给「东财 push2 不通」时的腾讯备援提供代码全集。
    """
    import io
    import contextlib
    import baostock as bs

    with contextlib.redirect_stdout(io.StringIO()):  # 屏蔽 baostock 登录噪声
        lg = bs.login()
    try:
        if lg.error_code != "0":
            raise RuntimeError(f"baostock 登录失败：{lg.error_msg}")
        syms = []
        for back in range(8):  # 回溯最近交易日（跳过周末/节假日）
            d = (datetime.date.today() - datetime.timedelta(days=back)).strftime("%Y-%m-%d")
            rs = bs.query_all_stock(day=d)
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                continue
            for row in rows:
                code = row[0]  # 形如 'sh.601899'
                if "." not in code:
                    continue
                mkt, num = code.split(".", 1)
                # 用交易所前缀区分：沪 000xxx 是指数，深 000xxx 才是个股
                if mkt == "sh" and num.startswith("60"):
                    syms.append("sh" + num)
                elif mkt == "sz" and num.startswith("00"):
                    syms.append("sz" + num)
            break
        return syms
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            bs.logout()


def _fetch_spot_tencent(symbols, progress_cb=None, batch=60, pause=0.1, max_seconds=120):
    """腾讯行情(qt.gtimg.cn)按代码批量取快照，东财 push2 不通时的快速备援。

    symbols：带交易所前缀的代码列表，如 ['sh601899', 'sz000001']。
    腾讯返回 GBK 编码、'~' 分隔串，字段位置经实测核对：
      [1]名称 [2]代码 [3]最新价 [32]涨跌幅 [38]换手率 [39]PE
      [44]流通市值(亿) [45]总市值(亿) [46]PB [49]量比
    腾讯不提供 60日动量/年初至今，置 NaN（打分时按中性 50 处理）。
    """
    import requests

    sess = requests.Session()
    sess.trust_env = False
    sess.headers.update({"User-Agent": "Mozilla/5.0"})
    rows = []
    n = len(symbols)
    start = time.time()
    for bi in range(0, n, batch):
        if time.time() - start > max_seconds:
            raise RuntimeError(f"腾讯行情拉取超过 {max_seconds}s 仍未完成，已中止。")
        chunk = symbols[bi:bi + batch]
        done = min(bi + batch, n)
        if progress_cb:
            progress_cb(f"腾讯行情 {done}/{n}", done / max(n, 1))
        try:
            r = sess.get("http://qt.gtimg.cn/q=" + ",".join(chunk), timeout=10)
            r.encoding = "gbk"
            text = r.text
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
            continue
        for line in text.split(";"):
            line = line.strip()
            if not line.startswith("v_") or '="' not in line:
                continue
            body = line.split('="', 1)[1].rstrip('"')
            p = body.split("~")
            if len(p) < 50:  # 停牌/退市可能返回空串
                continue
            rows.append({
                "代码": p[2], "名称": p[1],
                "最新价": _tf(p, 3), "涨跌幅": _tf(p, 32),
                "成交额": _tf(p, 37) * 1e4,  # 腾讯单位「万元」→ 元
                "PE": _tf(p, 39), "PB": _tf(p, 46),
                "总市值": _tf(p, 45) * 1e8,  # 腾讯单位「亿元」→ 元
                "换手率": _tf(p, 38), "量比": _tf(p, 49),
            })
        time.sleep(pause)
    out = pd.DataFrame(rows)
    if len(out) == 0:
        raise RuntimeError("腾讯行情未取到任何数据")
    out["代码"] = out["代码"].astype(str).str.zfill(6)
    out["动量60"] = float("nan")
    out["年初至今"] = float("nan")
    return out


def fetch_spot(progress_cb=None):
    """全市场实时快照（沪深京A股）。优先东方财富，失败降级新浪。

    返回与之前兼容的标准化列：代码/名称/最新价/涨跌幅/PE/PB/总市值/换手率/量比/动量60/年初至今
    progress_cb(detail, percent) 可选进度回调，percent 0~1。
    """
    # 先尝试东方财富（数据最全）
    try:
        import requests as _r
        sess = _r.Session()
        sess.trust_env = False
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
        params = {"pn": 1, "pz": 1, "po": 1, "np": 1, "ut": "bd1d9ddb04089700cf9c27f6f7426281", "fltt": 2, "invt": 2, "fid": "f3", "fs": "m:0 t:6", "fields": "f12"}
        for host in _EM_HOSTS:
            try:
                r = sess.get(f"{host}/api/qt/clist/get", params=params, headers=headers, timeout=8)
                if isinstance(r.json().get("data"), dict):
                    break
            except Exception:
                continue
        else:
            raise RuntimeError("EM unavailable")
        # 东方财富可用：走原有分页逻辑
        fs = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048"
        raw = _em_clist(fs)
        raw = raw.rename(columns=_EM_FIELDS)
        out = pd.DataFrame()
        out["代码"] = raw["代码"].astype(str).str.zfill(6)
        out["名称"] = raw["名称"]
        for c in ["最新价", "涨跌幅", "成交额", "PE", "PB", "总市值", "换手率",
                  "量比", "动量60", "年初至今"]:
            out[c] = pd.to_numeric(raw.get(c), errors="coerce")
        return out
    except Exception:
        pass  # 东财不通，降级

    # === 腾讯备援：baostock 取清单 + 腾讯批量行情（比新浪快很多）===
    try:
        if progress_cb:
            progress_cb("东财行情不通，改用 baostock 清单 + 腾讯行情…", 0.0)
        syms = _all_main_board_symbols()
        if syms:
            return _fetch_spot_tencent(syms, progress_cb=progress_cb)
    except Exception as e:  # noqa: BLE001
        import warnings
        warnings.warn(f"腾讯备援失败，转新浪兜底：{e}")

    # === 新浪财经降级方案（最后兜底）===
    import requests as _r
    import json as _json
    import random as _random

    sina_url = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    sina_payload = {"page": 1, "num": 80, "sort": "symbol", "asc": "1",
                    "node": "hs_a", "symbol": "", "_s_r_a": ""}

    rows = []
    page = 1
    max_pages = 100
    start = time.time()
    # 绕过 net_patch 的重试适配器：Sina HTTP 不需要重试，单页超时即跳过
    sess = _r.Session()
    sess.trust_env = False
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://vip.stock.finance.sina.com.cn/mkt/",
    })
    from requests.adapters import HTTPAdapter as _HA
    sess.mount("http://", _HA(max_retries=0))
    sess.mount("https://", _HA(max_retries=0))

    while page <= max_pages:
        if time.time() - start > 180:
            raise RuntimeError("新浪行情拉取超时")
        if progress_cb:
            progress_cb(f"新浪分页 {page}/{max_pages}，已收集 {len(rows)} 只", page / max_pages)
        sina_payload["page"] = page
        sina_payload["_s_r_a"] = str(_random.random())
        try:
            r = sess.get(sina_url, params=sina_payload, timeout=30)
            data = _json.loads(r.text)
        except Exception:
            time.sleep(1.5)
            page += 1  # 跳过错页，避免死循环
            continue
        if not isinstance(data, list) or len(data) == 0:
            break
        for item in data:
            # Sina API: symbol="bj920000", code="920000"
            code = str(item.get("code", item.get("symbol", ""))).strip()
            rows.append({
                "代码": code,
                "名称": str(item.get("name", "")).strip(),
                "最新价": _to_f(item, "trade"),
                "涨跌幅": _to_f(item, "changepercent"),
                "PE": _to_f(item, "per"),
                "PB": _to_f(item, "pb"),
                "总市值": _to_f(item, "mktcap") * 1e4,  # Sina 万元→元
                "成交额": _to_f(item, "amount"),  # Sina amount 单位元
                "换手率": _to_f(item, "turnoverratio"),
            })
        page += 1
        time.sleep(0.35)  # 避免被封

    out = pd.DataFrame(rows)
    out["代码"] = out["代码"].astype(str).str.zfill(6)
    out["量比"] = float("nan")
    out["动量60"] = float("nan")
    out["年初至今"] = float("nan")
    return out


def _to_f(d, key):
    """安全转float，NaN -> NaN"""
    v = d.get(key, float("nan"))
    try:
        return float(v)
    except (ValueError, TypeError):
        return float("nan")


def fetch_fundamentals(progress_cb=None):
    """业绩报表：自动回退到最近一个可用报告期，同时拉取同比 ROE（一年前同季度）。
    返回 (DataFrame, report_date, year_ago_roe_map)。
    year_ago_roe_map: {code: 年化ROE}，用于检测 ROE 趋势（价值陷阱过滤）。
    progress_cb(detail, percent) 可选进度回调。"""
    today = datetime.date.today()
    cands = []
    for y in (today.year, today.year - 1):
        for md in ("0331", "0630", "0930", "1231"):
            cands.append(f"{y}{md}")
    cands = [d for d in cands if d <= today.strftime("%Y%m%d")]
    cands.sort(reverse=True)

    for i, d in enumerate(cands):
        if progress_cb:
            progress_cb(f"尝试报告期 {d}…", (i + 1) / len(cands))
        try:
            df = _retry_call(lambda: ak.stock_yjbb_em(date=d), tries=2,
                             label=f"业绩报表{d}")
        except Exception:
            continue
        if df is None or len(df) < 100:
            continue
        out = pd.DataFrame()
        out["代码"] = df[_find_col(df, "股票代码", "代码")].astype(str).str.zfill(6)
        out["EPS"] = pd.to_numeric(df[_find_col(df, "每股收益")], errors="coerce")
        out["ROE"] = pd.to_numeric(df[_find_col(df, "净资产收益率")], errors="coerce")
        out["净利润同比"] = pd.to_numeric(
            df[_find_col(df, "净利润-同比", "净利润同比")], errors="coerce")
        out["营收同比"] = pd.to_numeric(
            df[_find_col(df, "营业总收入-同比", "营业收入-同比", "营收同比",
                         "总收入-同比")], errors="coerce")
        out["毛利率"] = pd.to_numeric(df[_find_col(df, "毛利率")], errors="coerce")
        out["每股经营现金流"] = pd.to_numeric(
            df[_find_col(df, "每股经营现金流量")], errors="coerce")
        out["每股净资产"] = pd.to_numeric(
            df[_find_col(df, "每股净资产")], errors="coerce")
        ind = _find_col(df, "所处行业", "行业")
        out["行业"] = df[ind] if ind else ""
        out["报告期"] = d

        # ROE 年化
        _ann = {"03": 4.0, "06": 2.0, "09": 4.0 / 3.0, "12": 1.0}.get(d[4:6], 1.0)
        if _ann != 1.0:
            out["ROE"] = out["ROE"] * _ann

        # —— 拉取一年前同季度 ROE（用于趋势检测）——
        yago_roe = {}
        yago_date = str(int(d[:4]) - 1) + d[4:]
        if yago_date <= today.strftime("%Y%m%d"):
            try:
                if progress_cb:
                    progress_cb(f"同比 ROE {yago_date}…", 0.95)
                df_yago = _retry_call(lambda: ak.stock_yjbb_em(date=yago_date),
                                      tries=2, label=f"同比ROE{yago_date}")
                if df_yago is not None and len(df_yago) >= 100:
                    code_col = _find_col(df_yago, "股票代码", "代码")
                    roe_col = _find_col(df_yago, "净资产收益率")
                    for _, row in df_yago.iterrows():
                        code = str(row[code_col]).zfill(6)
                        try:
                            roe = float(row[roe_col])
                            if not pd.isna(roe) and _ann != 1.0:
                                roe *= _ann
                            yago_roe[code] = roe if not pd.isna(roe) else None
                        except (ValueError, TypeError):
                            pass
            except Exception:
                pass

        return out, d, yago_roe
    raise RuntimeError("未能获取任何业绩报表数据")


def enrich_shortlist(rows, years=5, progress_cb=None, max_seconds=240):
    """对入围候选用 baostock 拉近 N 年日线（close + pbMRQ），一次查询同时算出：
      • 估值分位：当前 PB 处于自身历史的百分位（越低越便宜）；
      • 真实技术指标：动量/均线/RSI/乖离/波动率。

    rows：DataFrame，需含「代码」列。返回 {code: {"估值分位":, "metrics":{...}}}。

    优化：`.baostock_cache/` 目录本地缓存（24h TTL），第二次刷新瞬间完成。
    注意：baostock 不支持并发连接，故用串行查询。
    """
    import io
    import contextlib
    import pickle
    import os as _os
    import baostock as bs

    end = datetime.date.today().strftime("%Y-%m-%d")
    start = (datetime.date.today()
             - datetime.timedelta(days=int(365.25 * years))).strftime("%Y-%m-%d")
    codes = rows["代码"].tolist()
    if not codes:
        return {}

    # —— 本地缓存 ——
    cache_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".baostock_cache")
    _os.makedirs(cache_dir, exist_ok=True)
    cache_ttl = datetime.timedelta(hours=24)
    out = {}
    need_query = []

    for c in codes:
        cache_file = _os.path.join(cache_dir, f"{c}.pkl")
        try:
            if _os.path.exists(cache_file):
                with open(cache_file, "rb") as f:
                    cached = pickle.load(f)
                cache_time = cached.get("_ts")
                if cache_time and (datetime.datetime.now() - cache_time) < cache_ttl:
                    info = cached.get("info")
                    if info:
                        out[c] = info
                        continue
        except Exception:
            pass
        need_query.append(c)

    if not need_query:
        return out

    if progress_cb:
        hit = len(codes) - len(need_query)
        progress_cb(f"缓存命中 {hit}/{len(codes)}，需查询 {len(need_query)} 只（约{len(need_query)*1.5:.0f}秒）", 0.0)

    # —— 串行查询（baostock 不支持并发，多线程会冲突卡死）——
    with contextlib.redirect_stdout(io.StringIO()):
        lg = bs.login()
    try:
        if lg.error_code != "0":
            return out
        n = len(need_query)
        t0 = time.time()
        for i, c in enumerate(need_query):
            if time.time() - t0 > max_seconds:
                break
            if progress_cb and i % 5 == 0:
                elapsed = time.time() - t0
                eta = elapsed / max(i, 1) * (n - i)
                progress_cb(f"入围增强 {i}/{n}（缓存:{len(codes)-len(need_query)} 已用{elapsed:.0f}s 剩余~{eta:.0f}s）",
                            i / max(n, 1))
            bcode = ("sh." if c.startswith("6") else "sz.") + c
            try:
                rs = bs.query_history_k_data_plus(
                    bcode, "date,close,pbMRQ", start_date=start, end_date=end,
                    frequency="d", adjustflag="2")
                closes, pbs = [], []
                while rs.error_code == "0" and rs.next():
                    row = rs.get_row_data()
                    closes.append(row[1])
                    pbs.append(row[2])
                info = {}
                pb = pd.to_numeric(pd.Series(pbs), errors="coerce").dropna()
                pb = pb[pb > 0]
                if len(pb) >= 1:
                    info["pb_eod"] = round(float(pb.iloc[-1]), 3)  # 收盘 PB，盘中不变
                if len(pb) >= 60:
                    cur = pb.iloc[-1]
                    info["估值分位"] = round(float((pb < cur).mean() * 100), 1)
                m = _tech_metrics(closes)
                if m is not None:
                    info["metrics"] = m
                if info:
                    out[c] = info
                    # 写入缓存
                    try:
                        cache_file = _os.path.join(cache_dir, f"{c}.pkl")
                        with open(cache_file, "wb") as f:
                            pickle.dump({"_ts": datetime.datetime.now(), "info": info}, f)
                    except Exception:
                        pass
            except Exception:
                continue
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            bs.logout()

    return out


# ---------------------------------------------------------------- 打分


def _piecewise(x, points):
    """按 (阈值, 分值) 升序锚点做线性插值打分。x 为 NaN 给中性 50。"""
    if pd.isna(x):
        return 50.0
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            t = (x - xs[i]) / (xs[i + 1] - xs[i])
            return ys[i] + t * (ys[i + 1] - ys[i])
    return 50.0


def _roe_score(x):
    return _piecewise(x, [(0, 10), (5, 45), (10, 65), (15, 82), (20, 95), (30, 100)])


def _pe_score(x):
    if pd.isna(x):
        return 45.0
    if x <= 0:  # 亏损
        return 5.0
    return _piecewise(x, [(8, 100), (15, 92), (25, 78), (40, 58), (60, 38), (100, 18)])


def _pb_score(x):
    if pd.isna(x):
        return 45.0
    if x <= 0:
        return 20.0
    return _piecewise(x, [(0.8, 95), (2, 88), (4, 70), (7, 48), (12, 25)])


def _growth_score(x):
    return _piecewise(x, [(-30, 8), (-15, 30), (0, 55), (15, 78), (30, 92), (60, 100)])


def _margin_score(x):
    return _piecewise(x, [(5, 35), (15, 58), (25, 75), (40, 92), (60, 100)])


def _mom_score(x):
    """动量：温和上涨最佳，下跌差，过热回落。"""
    return _piecewise(x, [(-40, 12), (-20, 32), (-5, 50), (5, 72),
                          (25, 95), (50, 100), (80, 78), (120, 55)])


def _liquidity_score(turnover):
    """换手率：太低没人气、太高过度投机，温和最好。"""
    return _piecewise(turnover, [(0.3, 40), (1, 70), (3, 90), (8, 80), (15, 55), (30, 30)])


VALUE_WEIGHTS = {
    "ROE": 0.28, "PE": 0.20, "PB": 0.14,
    "净利润同比": 0.16, "营收同比": 0.10, "毛利率": 0.12,
}
TECH_WEIGHTS = {"动量60": 0.45, "年初至今": 0.30, "换手率": 0.25}

# 综合分权重按「投资风格」切换（价值, 技术, 估值分位）：
#  长线价值——基本面主导，适合长期持有；
#  短线波段——技术/择时主导，适合 ≤3 个月的波段（年报基本面短期几乎不变，意义有限）。
COMPOSITE_WEIGHTS = {
    # (价值, 技术, 估值分位)
    "长线价值": (0.57, 0.31, 0.12),
    # 短线权重由回测定：估值分位是最强有效信号、反转技术为辅，价值面(无法时点回测)
    # 仅留小权重当质量底。验证：该组合在 ~3 月维度 Top20% 组超额 +4.6%。
    "短线波段": (0.15, 0.30, 0.55),
}


def _composite(value_s, tech_s, val_s, profile="长线价值"):
    wv, wt, wq = COMPOSITE_WEIGHTS.get(profile, COMPOSITE_WEIGHTS["长线价值"])
    s = value_s * wv + tech_s * wt + val_s * wq
    # .round() 对 Series 生效；标量 float 无此方法，用 builtin round
    if hasattr(s, "round"):
        return s.round(1)
    return round(float(s), 1)


def _valuation_score(pct):
    """PB 历史百分位 -> 分(0-100)：分位越低(当前越便宜)分越高。NaN 给中性 50。"""
    if pd.isna(pct):
        return 50.0
    return _piecewise(pct, [(0, 100), (20, 85), (40, 68), (60, 50), (80, 32), (100, 12)])


# ---- 量化多因子（经无偏样本外回测验证的因子）：低波+反彩票+中期反转+价值 ----
def _zscore(s):
    s = pd.to_numeric(s, errors="coerce")
    sd = s.std()
    if not sd or pd.isna(sd) or sd < 1e-9:
        return pd.Series(0.0, index=s.index)
    return ((s - s.mean()) / sd).clip(-3, 3)


_NEUTRAL_MIN_MEMBERS = 5   # 行业内有效样本≥此数才做中性化；少于此用全样本 z（防"陪跑放大"）


def _zscore_neutral(values, industry):
    """行业中性化 z 分：行业内去均值消除"银行天然低PB/低波"的行业级偏向，再标准化。
    仅对样本≥_NEUTRAL_MIN_MEMBERS(=5) 的行业去均值；不足的行业**保留原值**走全样本 z——
    避免"行业内 2-3 只陪跑被放大成极端 z"（1 天名次剧跳的主因之一）。"""
    s = pd.to_numeric(values, errors="coerce")
    if industry is not None:
        g = s.groupby(industry)
        means = g.transform("mean")
        counts = g.transform("count")
        s = s.where(counts < _NEUTRAL_MIN_MEMBERS, s - means)
    return _zscore(s)


def quant_composite(df, sector_neutral=True):
    """低波(20日)+反彩票(月内最大日涨)+中期反转(-动量60)+价值(1/PB)，横截面 z 等权合成，
    综合分=入围(已增强)股内分位(0-100)。缺某因子用其余、不一票否决；未增强股留 NaN。
    sector_neutral=True 时按行业去均值，避免结果扎堆银行/证券。"""
    d = df.copy()
    d["量化分"] = float("nan")
    enriched = pd.Series(False, index=d.index)
    for col in ("波动率", "动量60"):
        if col in d.columns:
            enriched = enriched | d[col].notna()
    sub = d[enriched]
    if len(sub) < 5:          # 入围因子数据太少，无法做横截面分位
        return d
    ind = sub["行业"] if (sector_neutral and "行业" in sub.columns) else None

    def z(series):
        return _zscore_neutral(series, ind)

    # (展示列名, 因子序列) —— 方向已统一为"越大越好"
    specs = []
    if "波动率" in sub.columns:
        specs.append(("低波z", z(-pd.to_numeric(sub["波动率"], errors="coerce"))))
    if "最大日涨幅" in sub.columns:
        specs.append(("反彩票z", z(-pd.to_numeric(sub["最大日涨幅"], errors="coerce"))))
    if "动量60" in sub.columns:
        specs.append(("反转z", z(-pd.to_numeric(sub["动量60"], errors="coerce"))))
    # 价值因子优先用收盘 PB(PB_EOD)，盘中不变；缺失才退回实时 PB
    pb_col = "PB_EOD" if ("PB_EOD" in sub.columns and sub["PB_EOD"].notna().any()) else "PB"
    if pb_col in sub.columns:
        pb = pd.to_numeric(sub[pb_col], errors="coerce")
        if pb_col == "PB_EOD" and "PB" in sub.columns:   # PB_EOD 个别缺失时用实时补
            pb = pb.fillna(pd.to_numeric(sub["PB"], errors="coerce"))
        specs.append(("价值z", z((1.0 / pb).where(pb > 0))))
    if not specs:
        return d
    parts = [p for _, p in specs]
    comp = sum(p.fillna(0.0) for p in parts) / len(parts)   # 缺失因子按中性 0
    d.loc[sub.index, "量化分"] = (comp.rank(pct=True) * 100).round(1)
    for name, p in specs:                                   # 存各因子 z，供「为什么入选」拆解
        d.loc[sub.index, name] = p.round(2)
    return d


# ---- 真实短线技术打分（用 baostock 日线 close 计算，替代东财不通时的 NaN 动量）----


def _rsi_wilder(s, period=14):
    """Wilder 平滑 RSI，返回与 s 同长的 Series。"""
    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    ag = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    al = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = ag / al.replace(0, 1e-9)
    return 100 - 100 / (1 + rs)


def _tech_metrics(closes):
    """从 baostock 日线 close 计算短线技术原始指标，样本不足(<65 交易日)返回 None。"""
    s = pd.to_numeric(pd.Series(closes), errors="coerce").dropna()
    if len(s) < 65:
        return None
    last = s.iloc[-1]
    ma20 = s.tail(20).mean()
    ma60 = s.tail(60).mean()
    if last > ma20 > ma60:
        state = "多头"
    elif last < ma20 < ma60:
        state = "空头"
    else:
        state = "纠缠"
    ret20 = s.pct_change().tail(20)
    # 距年线(MA250)：>0 站上年线(企稳/上升)，<0 跌破年线(确认下跌→防白酒式长跌陷阱)
    juxianxian = float("nan")
    if len(s) >= 250:
        ma250 = s.tail(250).mean()
        if ma250:
            juxianxian = (last / ma250 - 1) * 100
    return {
        "动量20": (last / s.iloc[-21] - 1) * 100,
        "动量60": (last / s.iloc[-61] - 1) * 100,
        "RSI": float(_rsi_wilder(s).iloc[-1]),
        "均线": state,
        "乖离": (last / ma20 - 1) * 100 if ma20 else float("nan"),
        "波动率": ret20.std() * 100,
        "最大日涨幅": ret20.max() * 100,   # 反彩票因子（量化多因子用）
        "距年线": juxianxian,              # 长期趋势：>0 站上年线，<0 跌破年线
        "_last": last, "_ma20": ma20, "_ma60": ma60,
    }


# —— 反转式打分（短线场景）——
# 回测证据：A 股主板 ~3 个月维度强均值回归，动量/RSI/乖离均与未来收益负相关，
# 故短线奖励「超跌/超卖/低位」，并对极端暴跌轻微让分以防接飞刀。
def _mom_rev_score(x):
    return _piecewise(x, [(-50, 58), (-25, 82), (-10, 82), (0, 72),
                          (10, 55), (25, 40), (50, 26), (100, 12)])


def _rsi_rev_score(x):
    if pd.isna(x):
        return 50.0
    return _piecewise(x, [(10, 88), (30, 80), (45, 65), (55, 52),
                          (68, 36), (80, 22), (92, 12)])


def _bias_rev_score(x):
    if pd.isna(x):
        return 50.0
    return _piecewise(x, [(-25, 85), (-10, 80), (-2, 68), (4, 54),
                          (12, 38), (22, 22), (35, 12)])


def _ma_rev_score(state):
    return {"多头": 45.0, "纠缠": 60.0, "空头": 52.0}.get(state, 50.0)


def _real_tech_score(metrics, turnover, profile="长线价值"):
    """由技术原始指标算技术分(0-100)，统一用反转式打分：
      奖励超跌/超卖/低位——经回测样本外验证，反转式技术分在训练期(+0.110)和
      检验期(+0.246)均与未来3月收益正相关，方向一致；趋势式则全程负相关(-0.106/-0.228)。

      短线波段与长线价值的区别仅在于综合分的权重配比（价值/技术/估值），
      技术信号本身的方向不切换。
    """
    m = metrics
    # 权重由样本外验证校准：乖离率训练/检验期方向反转（不稳定的噪声），
    # 将其权重从 0.20 降至 0.06，差额分配给动量(0.45→0.52)和RSI(0.25→0.30)
    mom = 0.6 * _mom_rev_score(m["动量60"]) + 0.4 * _mom_rev_score(m["动量20"])
    return round(mom * 0.52 + _rsi_rev_score(m["RSI"]) * 0.30
                 + _bias_rev_score(m["乖离"]) * 0.06
                 + _ma_rev_score(m["均线"]) * 0.12, 1)


def _limit_flag(chg):
    """按当日涨跌幅标记涨跌停（主板 ±10%，留 0.3 缓冲；ST 已排除故不判 5%）。"""
    if pd.isna(chg):
        return ""
    if chg >= 9.7:
        return "涨停"
    if chg <= -9.7:
        return "跌停"
    return ""


def _cashflow_quality(cf_per_share, eps):
    """经营现金流质量乘数：现金流/净利润比。
    利润没现金支撑 = 盈利质量差，降权惩罚。"""
    if pd.isna(cf_per_share) or pd.isna(eps) or eps <= 0:
        return 1.0  # 无现金流数据或亏损时不惩罚（缺数据≠质量差，PE过滤已排除亏损）
    ratio = cf_per_share / eps
    if ratio < 0:
        return 0.55   # 经营现金流为负，大雷
    if ratio < 0.3:
        return 0.75   # 现金流严重不足
    if ratio < 0.7:
        return 0.90   # 偏弱
    if ratio <= 1.2:
        return 1.00   # 正常
    return 1.05        # 现金流超过利润，优秀


def _trend_quality(roe_now, roe_yago, profit_yoy, revenue_yoy):
    """基本面趋势质量乘数：ROE 恶化 + 营收净利背离 → 降权。
    - ROE 同比下滑超 30% → ×0.75（盈利能力在恶化，可能是价值陷阱）
    - ROE 同比下滑 15~30% → ×0.88
    - 营收增长(>5%)但净利润负增长(<-10%) → ×0.82（增收不增利，费用失控/减值）
    - 两者叠加取最低乘数。缺数据不惩罚(×1.0)。
    """
    penalty = 1.0
    # ROE 趋势
    if (roe_now is not None and roe_yago is not None
            and not pd.isna(roe_now) and not pd.isna(roe_yago)
            and roe_now > 0 and roe_yago > 0):
        change = (roe_now - roe_yago) / roe_yago
        if change < -0.30:
            penalty = min(penalty, 0.75)
        elif change < -0.15:
            penalty = min(penalty, 0.88)
    # 营收净利背离
    if (revenue_yoy is not None and profit_yoy is not None
            and not pd.isna(revenue_yoy) and not pd.isna(profit_yoy)):
        if revenue_yoy > 5 and profit_yoy < -10:
            penalty = min(penalty, 0.82)
    return penalty


def score(df, yago_roe=None):
    """对合并后的 DataFrame 计算各项分数，原地新增列并返回。
    yago_roe: 可选 {code: year_ago_ROE}，用于趋势检测。"""
    df = df.copy()
    df["分_ROE"] = df["ROE"].apply(_roe_score)
    df["分_PE"] = df["PE"].apply(_pe_score)
    df["分_PB"] = df["PB"].apply(_pb_score)
    df["分_净利增长"] = df["净利润同比"].apply(_growth_score)
    df["分_营收增长"] = df["营收同比"].apply(_growth_score)
    df["分_毛利率"] = df["毛利率"].apply(_margin_score)

    # 经营现金流质量乘数
    df["现金流乘数"] = df.apply(
        lambda r: _cashflow_quality(r.get("每股经营现金流"), r.get("EPS")), axis=1)

    # 趋势质量乘数（ROE恶化 / 营收净利背离 → 价值陷阱降权）
    yago = yago_roe or {}
    df["趋势乘数"] = df.apply(
        lambda r: _trend_quality(
            r.get("ROE"), yago.get(r["代码"]),
            r.get("净利润同比"), r.get("营收同比")), axis=1)

    df["价值分"] = (
        df["分_ROE"] * VALUE_WEIGHTS["ROE"]
        + df["分_PE"] * VALUE_WEIGHTS["PE"]
        + df["分_PB"] * VALUE_WEIGHTS["PB"]
        + df["分_净利增长"] * VALUE_WEIGHTS["净利润同比"]
        + df["分_营收增长"] * VALUE_WEIGHTS["营收同比"]
        + df["分_毛利率"] * VALUE_WEIGHTS["毛利率"]
    ) * df["现金流乘数"] * df["趋势乘数"]
    df["价值分"] = df["价值分"].clip(upper=100)  # 封顶

    df["分_动量"] = df["动量60"].apply(_mom_score)
    df["分_年内"] = df["年初至今"].apply(_mom_score)
    df["分_活跃"] = df["换手率"].apply(_liquidity_score)
    df["技术分"] = (
        df["分_动量"] * TECH_WEIGHTS["动量60"]
        + df["分_年内"] * TECH_WEIGHTS["年初至今"]
        + df["分_活跃"] * TECH_WEIGHTS["换手率"]
    )

    df["价值分"] = df["价值分"].round(1)
    df["技术分"] = df["技术分"].round(1)
    # 估值分位/真实技术指标由入围增强回填；其余股票留空、估值分按中性 50 计
    df["估值分位"] = float("nan")
    df["估值分"] = 50.0
    df["RSI"] = float("nan")
    df["均线"] = ""
    df["波动率"] = float("nan")
    df["最大日涨幅"] = float("nan")
    df["距年线"] = float("nan")
    df["综合分"] = _composite(df["价值分"], df["技术分"], df["估值分"])
    df["建议"] = df["综合分"].apply(_advice)
    return df


def _advice(s):
    if s >= 80:
        return "强烈关注"
    if s >= 72:
        return "值得买入"
    if s >= 63:
        return "可关注"
    if s >= 50:
        return "观望"
    return "暂不推荐"


# ---------------------------------------------------------------- 主流程


def fetch_market(progress_cb=None):
    """拉取全市场快照+业绩并打分（价值/快照技术），做结构性清洗（沪深主板/有效价），
    并标记 涨跌停 / 停牌。不含用户筛选与入围增强——供 app 缓存后反复筛选用。
    返回的 DataFrame 在 .attrs["report_date"] 带报告期。"""
    def _cb(label, detail, pct):
        if progress_cb:
            progress_cb(label, detail, pct)

    try:
        preflight_check()
    except RuntimeError as e:
        import warnings
        warnings.warn(str(e))

    _cb("行情数据", "正在拉取全市场实时行情…", 5)
    spot = fetch_spot(progress_cb=lambda d, p: _cb("行情数据", d, 5 + int(p * 0.60)))

    _cb("基本面", "正在拉取最新业绩报表…", 70)
    fund, report_date, yago_roe = fetch_fundamentals(
        progress_cb=lambda d, p: _cb("基本面", d, 70 + int(p * 0.25)))

    _cb("打分", "正在计算价值/技术得分…", 95)
    # 上游 push2/yjbb 偶有同代码重复行(分页/拼接遗留)；先各自去重再 merge，确保最终唯一
    spot = spot.drop_duplicates(subset=["代码"], keep="first")
    fund = fund.drop_duplicates(subset=["代码"], keep="first")
    df = spot.merge(fund, on="代码", how="inner")
    # 结构性清洗：有效价 + 仅沪深主板(排除科创板/创业板/北交所/B股)
    df = df[df["最新价"].notna() & (df["最新价"] > 0)]
    df = df[df["代码"].str.match(r"^(60|00)\d{4}$")]
    df = df.drop_duplicates(subset=["代码"], keep="first")    # 兜底再保险
    df = score(df, yago_roe)
    # 短线风控标记（成交额<=0 视为停牌/无成交；涨跌幅触及主板涨跌停）
    amt = df["成交额"] if "成交额" in df.columns else pd.Series(float("nan"), index=df.index)
    df["停牌"] = ~(pd.to_numeric(amt, errors="coerce") > 0)
    df["涨跌停"] = df["涨跌幅"].apply(_limit_flag)
    df.attrs["report_date"] = report_date
    return df


def apply_filters(df, exclude_st=True, exclude_loss=True, min_mktcap_yi=20,
                  min_roe=None, max_pe=None, exclude_halt=True,
                  avoid_limit=False, min_amount_yi=0):
    """对 fetch_market 的结果做用户级筛选（纯本地、秒级）。风控项在对应列存在时才生效。
    在 df.attrs['filter_stats'] 写各阶段统计（如 nan_roe_dropped），供 UI 透明展示。"""
    df = df.copy()
    stats = {}
    if exclude_st:
        df = df[~df["名称"].str.contains("ST|退", case=False, na=False)]
    if exclude_loss:
        df = df[(df["PE"] > 0) & (df["EPS"] > 0)]
    if min_mktcap_yi:
        df = df[df["总市值"] >= min_mktcap_yi * 1e8]
    if min_roe is not None:
        # 统计因 ROE NaN 被静默剔除的票数（NaN >= 数 → False，会被丢）
        roe = pd.to_numeric(df["ROE"], errors="coerce")
        stats["nan_roe_dropped"] = int(roe.isna().sum())
        df = df[roe >= min_roe]
    if max_pe is not None:
        df = df[df["PE"] <= max_pe]
    # —— 短线风控 ——
    if exclude_halt and "停牌" in df.columns:
        df = df[~df["停牌"].fillna(False)]
    if avoid_limit and "涨跌停" in df.columns:
        df = df[df["涨跌停"].fillna("") == ""]
    if min_amount_yi and "成交额" in df.columns:
        df = df[pd.to_numeric(df["成交额"], errors="coerce").fillna(0) >= min_amount_yi * 1e8]
    df.attrs["filter_stats"] = stats
    return df


def select_pool(df, n, profile="长线价值"):
    """选「入围精算池」（决定哪些股去 baostock 算因子）。
    - 量化多因子：按【成交额分行业分层】取——各行业最具流动性的先入池、逐档轮取，
      universe 行业均衡、有代表性，不被价值分预筛成"全是银行"；
    - 短线/长线：按价值分 top n（这两个模式本就价值主导）。
    返回子 DataFrame。"""
    if len(df) <= n:
        return df
    if profile == "量化多因子" and "行业" in df.columns:
        # 按成交额降序，每行业最多留 per_cap 只（保证行业内有深度做中性化，又不过度集中）
        d = df.copy()
        d["_amt"] = pd.to_numeric(d.get("成交额"), errors="coerce").fillna(0.0)
        ind = d["行业"].fillna("其他")
        d = d.sort_values("_amt", ascending=False)
        per_cap = max(3, int(n) // 12)
        d["_ic"] = d.groupby(ind, sort=False).cumcount()
        out = d[d["_ic"] < per_cap].head(int(n))
        return out.drop(columns=["_amt", "_ic"])
    return df.sort_values("价值分", ascending=False).head(int(n))


def rescore(df, profile="长线价值", enrich=None):
    """按风格权重重算综合分并排序。若给 enrich({code:{"估值分位","metrics"}})，
    则回填入围股的 估值分位、按风格现算的真实技术分、以及 动量60/RSI/均线 展示列。
    技术分按风格现算，故切换风格无需重新查询、纯本地、秒级。"""
    df = df.copy()
    if enrich:
        turn = dict(zip(df["代码"], df["换手率"]))
        pmap, tmap, mom, rsi, ma, vol, mx, pbe, jxx = {}, {}, {}, {}, {}, {}, {}, {}, {}
        for c, v in enrich.items():
            if "估值分位" in v:
                pmap[c] = v["估值分位"]
            if "pb_eod" in v:
                pbe[c] = v["pb_eod"]
            m = v.get("metrics")
            if m is not None:
                tmap[c] = _real_tech_score(m, turn.get(c), profile)
                mom[c] = round(m["动量60"], 1)
                rsi[c] = round(m["RSI"], 1)
                ma[c] = m["均线"]
                vol[c] = round(m["波动率"], 2)
                mx[c] = round(m.get("最大日涨幅", float("nan")), 2)
                jx = m.get("距年线", float("nan"))
                jxx[c] = round(jx, 1) if pd.notna(jx) else float("nan")
        df["估值分位"] = df["代码"].map(pmap)
        df["估值分"] = df["估值分位"].apply(_valuation_score)
        df["技术分"] = df["代码"].map(tmap).fillna(df["技术分"]).round(1)
        df["动量60"] = df["代码"].map(mom).fillna(df.get("动量60"))
        df["RSI"] = df["代码"].map(rsi)
        df["均线"] = df["代码"].map(ma)
        df["距年线"] = df["代码"].map(jxx)
        df["波动率"] = df["代码"].map(vol)
        df["最大日涨幅"] = df["代码"].map(mx)
        df["PB_EOD"] = df["代码"].map(pbe)   # 收盘 PB，量化价值因子用它（盘中稳定）

    if profile == "量化多因子":
        df = quant_composite(df)
        df["综合分"] = df["量化分"]
        df["建议"] = df["综合分"].apply(lambda s: _advice(s) if pd.notna(s) else "—")
        return df.sort_values("综合分", ascending=False, na_position="last").reset_index(drop=True)

    df["综合分"] = _composite(df["价值分"], df["技术分"], df["估值分"], profile)
    df["建议"] = df["综合分"].apply(_advice)
    return df.sort_values("综合分", ascending=False).reset_index(drop=True)


def run_screen(exclude_st=True, exclude_loss=True, min_mktcap_yi=20,
               min_roe=None, max_pe=None, top=None, val_top=100,
               profile="长线价值", exclude_halt=True, avoid_limit=False,
               min_amount_yi=0, progress_cb=None):
    """一站式全市场筛选（CLI/回测用）：拉取 → 筛选 → 入围增强 → 重排。
    app 端改用 fetch_market + apply_filters + enrich_shortlist + rescore 组合以支持秒级重算。"""
    def _cb(label, detail, pct):
        if progress_cb:
            progress_cb(label, detail, pct)

    df = fetch_market(progress_cb=progress_cb)
    report_date = df.attrs.get("report_date")
    df = apply_filters(df, exclude_st, exclude_loss, min_mktcap_yi, min_roe,
                       max_pe, exclude_halt, avoid_limit, min_amount_yi)

    enr = None
    if val_top and len(df):
        _cb("入围增强", "对入围股计算估值分位 + 真实短线技术（baostock）…", 96)
        sl = select_pool(df, int(val_top), profile)
        enr = enrich_shortlist(
            sl[["代码", "换手率"]],
            progress_cb=lambda d, p: _cb("入围增强", d, 96 + int(p * 3)))

    df = rescore(df, profile, enr)
    df.attrs["report_date"] = report_date
    df.attrs["profile"] = profile
    if top:
        df = df.head(top)
    return df


# ---------------------------------------------------------------- 个股详情（技术面）


def analyze_stock(code, days=250):
    """拉个股历史，计算均线/RSI/趋势，供详情页使用。"""
    code = str(code).zfill(6)
    end = datetime.date.today().strftime("%Y%m%d")
    start = (datetime.date.today() - datetime.timedelta(days=days * 2)).strftime("%Y%m%d")

    h = None
    # 1) 优先东方财富（数据完整）
    try:
        h = ak.stock_zh_a_hist(symbol=code, period="daily",
                               start_date=start, end_date=end, adjust="qfq")
    except Exception:
        pass

    # 2) 降级到新浪 daily
    if h is None or len(h) == 0:
        try:
            prefix = "sh" if code.startswith(("6", "68")) else "sz"
            h = ak.stock_zh_a_daily(symbol=f"{prefix}{code}", adjust="qfq")
        except Exception:
            pass

    if h is None or len(h) == 0:
        return None

    # 标准化列名
    h = h.rename(columns={
        "日期": "date", "收盘": "close", "最高": "high",
        "最低": "low", "开盘": "open", "成交量": "vol",
        "date": "date", "close": "close", "high": "high",
        "low": "low", "open": "open", "volume": "vol",
    })
    # 去重列（rename 可能产生重复）
    h = h.loc[:, ~h.columns.duplicated()].copy()
    h["date"] = pd.to_datetime(h["date"])
    h = h.sort_values("date").reset_index(drop=True)
    h["MA5"] = h["close"].rolling(5).mean()
    h["MA20"] = h["close"].rolling(20).mean()
    h["MA60"] = h["close"].rolling(60).mean()
    # RSI14（Wilder 平滑：用 alpha=1/14 的指数加权，符合标准 RSI 定义，
    # 与 trend_note 中 75/30 超买超卖阈值一致；旧版用 SMA 会有可见偏差）
    delta = h["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    h["RSI"] = 100 - 100 / (1 + rs)
    return h


def trend_note(h):
    """根据均线排列给一句技术面判断。"""
    last = h.iloc[-1]
    price, ma20, ma60 = last["close"], last["MA20"], last["MA60"]
    rsi = last["RSI"]
    notes = []
    if price > ma20 > ma60:
        notes.append("多头排列，趋势向上")
    elif price < ma20 < ma60:
        notes.append("空头排列，趋势向下")
    else:
        notes.append("均线缠绕，方向不明")
    if rsi >= 75:
        notes.append(f"RSI {rsi:.0f} 偏超买，注意回调")
    elif rsi <= 30:
        notes.append(f"RSI {rsi:.0f} 偏超卖，或有反弹")
    else:
        notes.append(f"RSI {rsi:.0f} 中性")
    return "；".join(notes)


# ---------------------------------------------------------------- CLI


if __name__ == "__main__":
    import sys
    profile = sys.argv[1] if len(sys.argv) > 1 else "短线波段"
    print(f"正在拉取全市场数据并打分（风格={profile}，约需 40~90 秒）...")
    res = run_screen(top=30, profile=profile)
    print(f"\n报告期: {res.attrs.get('report_date')}  风格: {res.attrs.get('profile')}"
          f"  共筛出 {len(res)} 只\n")
    cols = ["代码", "名称", "行业", "最新价", "PE", "PB", "ROE",
            "净利润同比", "价值分", "技术分", "动量60", "RSI", "均线",
            "估值分位", "综合分", "建议"]
    with pd.option_context("display.max_rows", None, "display.width", 200,
                           "display.unicode.east_asian_width", True):
        print(res[cols].to_string(index=False))
    res.to_csv("选股结果.csv", index=False, encoding="utf-8-sig")
    print("\n完整结果已保存到 选股结果.csv")
