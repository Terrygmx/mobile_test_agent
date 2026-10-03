#!/usr/bin/env bash
# P2-01 Task 1.1 —— 真实 Recovery 事件补齐（设计 1.2 / E5）。
#
# 每一轮：注入真实漂移（改名 identifier → 重编重装）→ `mta run`（真 LLM
# Recovery，走 cli/pipeline.py 新管线，产生 PENDING review）→ **人工确认
# 后** `mta review accept`（E5：不许手工插库、不许跳过人工）。ACCEPT 记录
# 就是 M2 Task 2.3 的 Candidate 种子来源（接线后自动 create_candidate）。
#
# 用法：
#   scripts/p2_seed_recoveries.sh [--rounds N] [--db PATH] [--case CASE_ID]
#
# 前置（preflight 逐项检查，缺一退出）：
#   - 模拟器 booted（P1 惯例 iPhone 14 / iOS 18.5）
#   - Appium 4723 已起
#   - LLM_API_KEY / LLM_BASE_URL 已配置（真 LLM 恢复的前提）
#   - TEST_USERNAME / TEST_PASSWORD 已配置（login_001 的 ${VAR} 解析）
#   - $SWIFT 无未提交改动（trap 还原用 git checkout，不能误伤）
#
# 漂移前提链（缺一即假漂移，脚本会在第 1 轮前自检）：
#   1. 漂移注入前 `make repo-generate` 在当前 HEAD 生成 metadata（元素集
#      含旧名）；2. 漂移只改源码不 commit → HEAD 不变 → App 注入的
#      MTA_GIT_COMMIT 与 metadata 一致（12.5 通过，不会被 fail-closed 拦）；
#      3. generated 元素集是旧的 → runtime find 必然 ELEMENT_NOT_FOUND →
#      真漂移。绝不在漂移态调 repo-generate（会扫到新名，前提破坏，F5 教训）。
#
# 退出码语义（P1 8.4）：run 返回 5 = RECOVERED（本轮有效，待人工 ACCEPT）；
# 0 = 用例直接 PASS（漂移未生效，记 WARN 不给 accept）；其余 = 该轮失败。
set -euo pipefail

cd "$(dirname "$0")/.."

ROUNDS=1
DB="out/trace.db"
CASE_ID="login_001"
BUNDLE_ID="com.phaset0.logindemo"
SWIFT="ios_demo/LoginDemo/LoginDemoApp.swift"
CSV="out/p2_seeds.csv"
PY="python"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rounds) ROUNDS="$2"; shift 2 ;;
    --db) DB="$2"; shift 2 ;;
    --case) CASE_ID="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# 场景轮换表 "old_target:new_name"（login_001 的三个动作步目标 × 多漂移名，
# 保证 50 轮积累出 (target, candidate) 多样性，供 M3 distinct_runs 使用）。
SCENARIOS=(
  'login_button:signin_button'
  'login_button:submit_login_btn'
  'login_button:primary_action_button'
  'username_field:user_email_field'
  'username_field:account_name_field'
  'password_field:passcode_field'
  'password_field:user_secret_field'
)
N=${#SCENARIOS[@]}

say()  { printf '\n[p2-seed] %s\n' "$*"; }
die()  { printf '[p2-seed] FATAL: %s\n' "$*" >&2; exit 2; }

# --- preflight ---
command -v xcrun >/dev/null || die "xcrun 不存在（需 Xcode）"
xcrun simctl list devices | grep -q Booted || die "无 booted 模拟器（先 open -a Simulator）"
curl -sf -m 3 http://127.0.0.1:4723/status >/dev/null || die "Appium 4723 未响应（先起 appium）"
[[ -n "${LLM_API_KEY:-}" ]] || die "LLM_API_KEY 未配置（真 LLM 恢复的前提，E5）"
[[ -n "${TEST_USERNAME:-}" && -n "${TEST_PASSWORD:-}" ]] || \
  die "TEST_USERNAME / TEST_PASSWORD 未配置（login_001 的 \${VAR} 解析）"
git diff --quiet -- "$SWIFT" || die "$SWIFT 有未提交改动，拒绝运行（防 trap 误伤）"
[[ -f "$SWIFT" ]] || die "$SWIFT 不存在"

say "preflight OK：rounds=$ROUNDS db=$DB case=$CASE_ID scenarios=$N"

# 漂移前的 metadata 同步（当前 HEAD + 原始源码 → 12.5 commit 一致 + 旧元素集）
say "刷新 metadata（原始源码态，make repo-generate）"
make repo-generate || die "repo-generate 失败（一致性 Gate 不过，先解决）"

# trap：任何退出路径都还原源码（漂移永不落 commit）
restore_src() { git checkout -- "$SWIFT" 2>/dev/null || true; }
trap restore_src EXIT

mkdir -p out
[[ -f "$CSV" ]] || echo "round,scenario,run_id,review_id,decision,run_exit_code" > "$CSV"

accepted=0; skipped=0; failed=0
for ((round=1; round<=ROUNDS; round++)); do
  sc="${SCENARIOS[$(( (round-1) % N ))]}"
  old="${sc%%:*}"; new="${sc##*:}"

  say "── Round $round/$ROUNDS：$old → $new ──"

  # 1. 注入漂移（identifier 必须唯一命中一次，否则 sed 会误伤）
  hits=$(grep -c "accessibilityIdentifier(\"$old\")" "$SWIFT" || true)
  [[ "$hits" == "1" ]] || { say "FATAL round $round：identifier '$old' 命中 $hits 次（需恰 1），跳过本轮"; failed=$((failed+1)); continue; }
  sed -i '' "s/accessibilityIdentifier(\"$old\")/accessibilityIdentifier(\"$new\")/" "$SWIFT"

  # 2. 重编重装（Makefile 内部处理 DEVELOPER_DIR / PlistBuddy 注入）
  make p1-build p1-install || { say "FATAL round $round：构建/安装失败"; git checkout -- "$SWIFT"; failed=$((failed+1)); continue; }

  # 3. 真跑（RECOVERED → exit 5 是本轮的有效结果；set -e 下须捕获）
  rc=0
  $PY -m cli.main run --case "$CASE_ID" --bundle-id "$BUNDLE_ID" --db "$DB" || rc=$?
  run_id=$($PY -c "import sqlite3;c=sqlite3.connect('$DB');print(c.execute(\"select run_id from runs order by start_time desc, rowid desc limit 1\").fetchone()[0])")

  if [[ "$rc" == "0" ]]; then
    say "WARN round $round：用例直接 PASS（run_id=$run_id）——漂移未生效或未被恢复路径消费，不给 accept"
    echo "$round,$sc,$run_id,,$WARN_PASS,$rc" >> "$CSV"
    git checkout -- "$SWIFT"; skipped=$((skipped+1)); continue
  elif [[ "$rc" != "5" ]]; then
    say "FATAL round $round：mta run 退出码 $rc（非 RECOVERED），run_id=$run_id"
    echo "$round,$sc,$run_id,,$FAILED,$rc" >> "$CSV"
    git checkout -- "$SWIFT"; failed=$((failed+1)); continue
  fi

  # 4. 找本轮产生的 PENDING review，人工确认（E5：这一步不许自动化掉）
  row=$($PY -c "
import sqlite3
c = sqlite3.connect('$DB'); c.row_factory = sqlite3.Row
r = c.execute('select rr.id, rr.recovery_id, rec.kind, rec.expected_target, rec.candidate_target, rec.screen'
              ' from recovery_reviews rr join recoveries rec on rec.id = rr.recovery_id'
              \" where rr.review_status='PENDING' order by rr.id desc limit 1\").fetchone()
print(f\"{r['id']}|{r['recovery_id']}|{r['kind']}|{r['expected_target']}|{r['candidate_target']}|{r['screen']}\" if r else 'NONE|0||||')")
  rev_id=$(cut -d'|' -f1 <<<"$row")
  if [[ "$rev_id" == "NONE" ]]; then
    say "FATAL round $round：RECOVERED 但无 PENDING review（run_id=$run_id）——管线未走新路径，检查 cli/pipeline.py"
    echo "$round,$sc,$run_id,,$NO_REVIEW,$rc" >> "$CSV"
    git checkout -- "$SWIFT"; failed=$((failed+1)); continue
  fi
  say "review #$rev_id：$(cut -d'|' -f4- <<<"$row")（run_id=$run_id）"
  read -r -p "[p2-seed] 人工确认：accept review #$rev_id? [y/N] " ans
  if [[ "${ans:-n}" == "y" || "${ans:-n}" == "Y" ]]; then
    $PY -m cli.main review accept "$rev_id" --db "$DB" \
      --note "p2-seed round=$round scenario=$old->$new run_id=$run_id"
    echo "$round,$sc,$run_id,$rev_id,ACCEPTED,$rc" >> "$CSV"
    accepted=$((accepted+1))
    say "round $round：ACCEPTED（review #$rev_id）"
  else
    echo "$round,$sc,$run_id,$rev_id,SKIPPED,$rc" >> "$CSV"
    git checkout -- "$SWIFT"; skipped=$((skipped+1))
    say "round $round：跳过（review 保持 PENDING，可稍后 mta review accept）"
  fi
done

# 收尾：还原源码并重编原始版，让环境回到可跑常规套件的状态
restore_src
say "还原源码，重编原始版 App…"
make p1-build p1-install || say "WARN：原始版重编失败（后续常规跑需手动 make p1-build p1-install）"

say "完成：accepted=$accepted skipped=$skipped failed=$failed（明细：$CSV）"
say "复核：python scripts/p2_audit_recoveries.py --db $DB --out docs/p2_data_audit.md"
[[ $failed -eq 0 ]] || exit 1
