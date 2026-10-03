#!/usr/bin/env bash
# p1_stability_run.sh — M5 稳定性基线跑轮（设计 17 M5 / 1.2，Task 5.1 / P1-14）。
#
# 一轮 = 按固定顺序跑完全部套件（同 build 同套件集，20 条用例）；逐次
# `mta run --no-llm` 调用落一行 CSV（时间窗 + 退出码），供
# p1_stability_report.py 按窗归因聚合。
#
# 断点续跑（plan Task 5.1 第 1 步）：CSV 里已有的最大轮号之后继续——中断
# 后重跑本脚本自动接续，不重跑已完成轮。
#
# 用法：
#   scripts/p1_stability_run.sh              # 正式 50 轮
#   ROUNDS=3 scripts/p1_stability_run.sh     # 冒烟 3 轮（plan 要求先冒烟）
#
# 前置（不满足 fail-loud，绝不带病刷轮）：
#   - Appium 4723 在线、模拟器 booted；
#   - 被测 App 已按当前 commit 构建/安装（make p1-build / xcodebuild +
#     simctl install，见 verify_p1_m4 流程），metadata 同源；
#   - TEST_USERNAME/TEST_PASSWORD 已入 env（mta lint 的 secret 前置门）。
#
# 每轮卫生检查（Gate M5）：RUNNING 残留（TraceStore 打开即自愈，但仍
# 核对当轮窗口）、UNTRIAGED×FAIL 计数、Appium 存活；连续 3 次调用
# exit 3（前置错误）→ 终止脚本（环境坏了继续刷只会产出垃圾基线）。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# M1 老坑：xcrun 依赖 DEVELOPER_DIR，环境不齐时 simctl 静默失败 →
# preflight 误报「无 booted 模拟器」。显式兜底（review_m5_task51 P3-5）。
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
cd "$ROOT"

ROUNDS="${ROUNDS:-50}"
SUITES="${SUITES:-smoke search account regression}"
DB="${DB:-out/stability/trace.db}"
CSV="${CSV:-out/stability/rounds.csv}"
PY="${PY:-$ROOT/.venv/bin/python}"
CONSEC_PREFLIGHT_FAILS=0

mkdir -p "$(dirname "$DB")" "$(dirname "$CSV")"

now_iso() { "$PY" -c 'import time; print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))'; }

preflight() {
  if ! curl -s -m 3 http://127.0.0.1:4723/status >/dev/null 2>&1; then
    echo "PREFLIGHT ERROR: Appium 4723 不在线（先启动 Appium + booted 模拟器）" >&2
    return 1
  fi
  if ! xcrun simctl list devices booted 2>/dev/null | grep -q Booted; then
    echo "PREFLIGHT ERROR: 无 booted 模拟器" >&2
    return 1
  fi
  if [ -z "${TEST_USERNAME:-}" ] || [ -z "${TEST_PASSWORD:-}" ]; then
    echo "PREFLIGHT ERROR: TEST_USERNAME/TEST_PASSWORD 未入 env（lint 前置门会拒）" >&2
    return 1
  fi
  return 0
}

hygiene() {
  # 当轮窗口的 RUNNING 残留 + UNTRIAGED FAIL（Gate M5 三查之二；第三查
  # WDA 泄漏需 simctl/Appium 会话计数，环境在线时顺带做）
  "$PY" - "$DB" "$2" "$3" <<'PYEOF'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
s, e = sys.argv[2], sys.argv[3]
running = conn.execute(
    "SELECT COUNT(*) FROM runs WHERE status='RUNNING' AND start_time >= ? AND start_time <= ?",
    (s, e)).fetchone()[0]
untriaged = conn.execute(
    "SELECT COUNT(*) FROM testcase_runs t JOIN runs r ON t.run_id=r.run_id"
    " WHERE r.start_time >= ? AND r.start_time <= ? AND t.status='FAIL'"
    " AND COALESCE(t.failure_attribution,'UNTRIAGED')='UNTRIAGED'", (s, e)).fetchone()[0]
print(f"{running},{untriaged}")
PYEOF
}

if ! preflight; then
  exit 3
fi

# 断点续跑：已有最大轮号之后继续
START_ROUND=1
if [ -f "$CSV" ]; then
  LAST=$(tail -n +2 "$CSV" | cut -d, -f1 | sort -n | tail -1)
  [ -n "$LAST" ] && START_ROUND=$((LAST + 1))
  echo "resume: 已有 ${LAST} 轮记录，从第 ${START_ROUND} 轮继续"
fi

# 表头只在文件新建时写一次（重复 append 会让 report 多读一行哑窗）
if [ ! -s "$CSV" ]; then
  echo "round,suite,seq,start,end,exit_code,duration_s,running_left,untriaged_fails,wda_procs" >> "$CSV"
fi

for ((r = START_ROUND; r <= ROUNDS; r++)); do
  echo "=== round ${r}/${ROUNDS} $(now_iso) ==="
  seq=0
  for suite in $SUITES; do
    seq=$((seq + 1))
    t0=$(date +%s)
    start=$(now_iso)
    "$PY" -m cli.main run --suite "$suite" --no-llm \
      --db "$DB" \
      --udid "${MTA_SIM_UDID:-}" --bundle-id "${MTA_BUNDLE_ID:-com.phaset0.logindemo}" \
      --junit "out/stability/junit_r${r}_${suite}.xml" \
      > "out/stability/r${r}_${suite}.log" 2>&1
    code=$?
    end=$(now_iso)
    dur=$(( $(date +%s) - t0 ))
    read -r run_left untriaged < <(hygiene "$DB" "$start" "$end" | tr ',' ' ')
    wda_procs=$(pgrep -f WebDriverAgentRunner-Runner 2>/dev/null | wc -l | tr -d ' ')
    echo "${r},${suite},${seq},${start},${end},${code},${dur},${run_left},${untriaged},${wda_procs}" >> "$CSV"
    echo "  ${suite}: exit=${code} dur=${dur}s running_left=${run_left} untriaged_fails=${untriaged} wda_procs=${wda_procs}"

    if [ "$code" -eq 3 ]; then
      CONSEC_PREFLIGHT_FAILS=$((CONSEC_PREFLIGHT_FAILS + 1))
      if [ "$CONSEC_PREFLIGHT_FAILS" -ge 3 ]; then
        echo "ABORT: 连续 3 次前置错误（exit 3）——环境已坏，终止刷轮" >&2
        exit 2
      fi
    else
      CONSEC_PREFLIGHT_FAILS=0
    fi
  done
  # WDA 泄漏检查（尽力而为：环境在线才做，缺工具不阻塞）
  if command -v xcrun >/dev/null 2>&1; then
    xcrun simctl listapps booted >/dev/null 2>&1 \
      || echo "  warn: simctl listapps 失败（WDA 泄漏检查跳过）"
  fi
done

echo "done: ${ROUNDS} 轮完成，CSV=${CSV}，聚合：$PY scripts/p1_stability_report.py --db $DB --csv $CSV"
