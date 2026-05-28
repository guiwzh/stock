#!/bin/zsh
# 等 1 小时后重试纯多头验证；到点再探测 baostock（最多探 4 次/每 5 分钟），不提前轮询。
cd "$(dirname "$0")"
echo "⏰ 计划 1 小时后重试，开始等待 $(date +%H:%M)"
sleep 3600

probe() {
venv/bin/python -u - <<'PY'
import net_patch, io, contextlib, threading
import baostock as bs
ok = {"v": False}
def p():
    with contextlib.redirect_stdout(io.StringIO()):
        lg = bs.login()
    if lg.error_code != "0":
        return
    rs = bs.query_history_k_data_plus("sh.600000", "date,close",
        start_date="2024-01-01", end_date="2024-02-01", frequency="d", adjustflag="2")
    n = 0
    while rs.error_code == "0" and rs.next():
        n += 1
    ok["v"] = n > 0
    with contextlib.redirect_stdout(io.StringIO()):
        bs.logout()
t = threading.Thread(target=p, daemon=True); t.start(); t.join(20)
print("ALIVE" if ok["v"] else "DOWN")
import os; os._exit(0)
PY
}

for attempt in $(seq 1 4); do
    if [[ "$(probe)" == *ALIVE* ]]; then
        echo "✅ baostock 已恢复，开始跑纯多头验证 $(date +%H:%M)"
        rm -f _strat_hist.pkl
        venv/bin/python -u strategy.py 500 20
        echo "=== 策略验证结束 $(date +%H:%M) ==="
        exit 0
    fi
    echo "⏳ [$attempt/4] 仍不可用，5 分钟后再探 $(date +%H:%M)"
    sleep 300
done
echo "❌ 1 小时后 baostock 仍未恢复；可手动跑 venv/bin/python strategy.py 500 20"
