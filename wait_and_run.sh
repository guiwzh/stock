#!/bin/zsh
# 轮询 baostock 是否恢复，恢复后自动跑纯多头组合验证。
cd "$(dirname "$0")"

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

for attempt in $(seq 1 40); do   # 40 × 3min ≈ 2 小时上限
    res=$(probe)
    if [[ "$res" == *ALIVE* ]]; then
        echo "✅ [$attempt] baostock 已恢复，开始跑纯多头验证 $(date +%H:%M)"
        rm -f _strat_hist.pkl
        venv/bin/python -u strategy.py 500 20
        echo "=== 策略验证结束 $(date +%H:%M) ==="
        exit 0
    fi
    echo "⏳ [$attempt] baostock 仍不可用，3 分钟后重试 $(date +%H:%M)"
    sleep 180
done
echo "❌ 2 小时内 baostock 未恢复，已放弃；稍后可手动跑 venv/bin/python strategy.py 500 20"
