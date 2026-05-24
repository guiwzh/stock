#!/bin/bash
# 一键启动 A股选股网页
cd "$(dirname "$0")"

# —— 关闭代理（仅对本脚本生效，不影响其他终端/应用）——
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy
export NO_PROXY="*"
echo "已为本次运行清除代理变量；当前 proxy 设置："
env | grep -i proxy || echo "  (无代理，正常)"
echo "----------------------------------------------"

source venv/bin/activate
streamlit run app.py
