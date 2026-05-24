"""网络补丁：必须在 import akshare 之前 import 本模块。

解决两件事（本机环境实测的坑）：
1. macOS 系统级代理(127.0.0.1:7890)会让 requests 走代理访问东方财富等国内站，
   经常握手失败。设置 NO_PROXY="*" 让所有请求绕过系统代理、直连。
2. 东方财富分页接口（A股 5000+ 只要翻几十页）偶尔会单页断连，导致整次拉取失败。
   给 requests 的每个 Session 自动挂上 urllib3 重试适配器，按页自动重试。
"""
import os

# 绕过系统/环境代理，直连国内数据站
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
           "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_orig_init = requests.sessions.Session.__init__


def _patched_init(self, *args, **kwargs):
    _orig_init(self, *args, **kwargs)
    # 重试用于兜底"偶尔单页断连"，不宜过多：原先 total=15+backoff_max=8 在
    # 接口完全不通时会让单个请求空转 ~2 分钟，叠加分页/多镜像直接卡死十几分钟。
    # 降到 total=3，配合 _em_clist 的多镜像轮换与总超时，失败可在数秒内暴露。
    #
    # 兼容 urllib3 v1/v2：v2 支持 allowed_methods/backoff_max，v1 用 method_whitelist 且无 backoff_max
    import urllib3
    _is_v2 = tuple(int(x) for x in urllib3.__version__.split(".")[:2]) >= (2, 0)
    retry_kwargs = dict(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    if _is_v2:
        retry_kwargs["backoff_max"] = 4
        retry_kwargs["allowed_methods"] = None  # 对所有方法(含GET)重试
    else:
        retry_kwargs["method_whitelist"] = None  # v1 用 method_whitelist
    retry = Retry(**retry_kwargs)
    adapter = HTTPAdapter(max_retries=retry)
    self.mount("http://", adapter)
    self.mount("https://", adapter)
    # 不再信任系统代理设置
    self.trust_env = False


requests.sessions.Session.__init__ = _patched_init
