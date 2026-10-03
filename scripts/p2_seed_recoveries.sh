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
#                                 [--yes]   # 冒烟/CI 免交互（仍走 review 流程）
#
# 前置（preflight 逐项检查，缺一退出）：
#   - 模拟器 booted（P1 惯例 iPhone 14 / iOS 18.5）
#   - Appium 4723 已起
#   - LLM_API_KEY / LLM_BASE_URL 已配置（真 LLM 恢复的前提）
#   - TEST_USERNAME / TEST_PASSWORD 已配置（${VAR} 解析）
#   - $SWIFT 无未提交改动（trap 还原用 git checkout，不能误伤）
#
# 漂移前提链（缺一即假漂移，F5/M4 教训自文档——**四步**，review P1-1a 补第 4 步）：
#   1. 漂移注入前 `make repo-generate` 在当前 HEAD 生成 metadata（元素集
#      含旧名）；2. 漂移只改源码不 commit → HEAD 不变 → App 注入的
#      MTA_GIT_COMMIT 与 metadata 一致（12.5 通过，不会被 fail-closed 拦）；
#      3. generated 元素集是旧的 → runtime find 必然 ELEMENT_NOT_FOUND；
#   4. **候选必须过 9.3 全链**：LLM 给出的新名要在 Repository 登记得到
#      且 risk==LOW，否则 LLM_TARGET_SCREEN_MISMATCH / LLM_RISK_BLOCKED，
#      RECOVERED 永远出不来。M4 Gate 用 5 个真机 round 换来的教训：漂移
#      候选必须**预登记别名**（本脚本 --register 走 overrides 补丁目录，
#      origin: manual + review 审计；只登记 override 里 risk=LOW 的目标）。
#      绝不在漂移态调 repo-generate（会扫到新名，前提破坏）。
#
# 场景表只收 overrides 里 risk=LOW 的目标（review P2-1：MEDIUM 目标
# （login_button/password_field/logout_button）的漂移候选过不了 9.3-4
# fail-closed——那是正确行为；用「别名声明 LOW」绕过是风险作弊，禁止）。
# MEDIUM 目标的漂移作为预期拒绝负例，将来单独开 --negative 轮。
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
# M5 记账教训：本机 `python` 解析到无关 venv（无 appium 依赖）——
# 默认项目自己的解释器（review P1-1d）。
PY="${P2_PY:-$(pwd)/.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"
AUTO_YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rounds) ROUNDS="$2"; shift 2 ;;
    --db) DB="$2"; shift 2 ;;
    --case) CASE_ID="$2"; shift 2 ;;
    --yes) AUTO_YES=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# 场景轮换表 "case_id:screen:old_target:new_name"——**只收 overrides 里
# risk=LOW 的目标**（review P2-1）。每行自带 case_id：LOW 目标分散在
# login_001 之外的多条用例里（username_field→login_001、go_search→
# search_001、go_profile→profile_001、search_field→search_002、
# profile_title→profile_view_001）。候选别名统一后缀 _v2。
SCENARIOS=(
  'login_001:LoginView:username_field:username_field_v2'
  'login_001:LoginView:username_field:user_login_field'
  'search_001:HomeView:go_search:go_search_v2'
  'search_001:HomeView:go_search:search_entry_button'
  'profile_001:HomeView:go_profile:go_profile_v2'
  'profile_001:HomeView:go_profile:profile_entry_button'
  'search_002:SearchView:search_field:search_field_v2'
  'profile_view_001:DetailView:profile_title:profile_title_v2'
)
N=${#SCENARIOS[@]}

say()  { printf '\n[p2-seed] %s\n' "$*"; }
die()  { printf '[p2-seed] FATAL: %s\n' "$*" >&2; exit 2; }

# --- preflight ---
# M1/M5 老坑：xcrun 依赖 DEVELOPER_DIR，环境不齐时 simctl 静默失败
# → 误报「无 booted」（review P3-3）。显式兜底。
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
command -v xcrun >/dev/null || die "xcrun 不存在（需 Xcode）"
xcrun simctl list devices | grep -q Booted || die "无 booted 模拟器（先 open -a Simulator）"
curl -sf -m 3 http://127.0.0.1:4723/status >/dev/null || die "Appium 4723 未响应（先起 appium）"
[[ -n "${LLM_API_KEY:-}" ]] || die "LLM_API_KEY 未配置（真 LLM 恢复的前提，E5）"
[[ -n "${TEST_USERNAME:-}" && -n "${TEST_PASSWORD:-}" ]] || \
  die "TEST_USERNAME / TEST_PASSWORD 未配置（\${VAR} 解析）"
git diff --quiet -- "$SWIFT" || die "$SWIFT 有未提交改动，拒绝运行（防 trap 误伤）"
[[ -f "$SWIFT" ]] || die "$SWIFT 不存在"

say "preflight OK：rounds=$ROUNDS db=$DB case=$CASE_ID scenarios=$N py=$PY"

# 漂移前的 metadata 同步（当前 HEAD + 原始源码 → 12.5 commit 一致 + 旧元素集）
say "刷新 metadata（原始源码态，make repo-generate）"
make repo-generate || die "repo-generate 失败（一致性 Gate 不过，先解决）"

# trap：任何退出路径都还原源码（漂移永不落 commit）
restore_src() { git checkout -- "$SWIFT" 2>/dev/null || true; }
trap restore_src EXIT

mkdir -p out
[[ -f "$CSV" ]] || echo "round,scenario,run_id,review_id,decision,run_exit_code" > "$CSV"

# P1-1a：漂移候选预登记（M4 Gate 实证解法，verify_p1_m4.prepare_overrides
# 的 overrides 副本模式）——把新名以别名元素写进 overrides 补丁目录，
# run 时 --generated 双源合并 + --overrides 指过去（原 overrides 复制 +
# 种子专用文件追加；原目录绝不改写，H15 同纪律）。
# 文件按 9.3-3 的 Screen 归属分片；risk 显式 LOW 且**与原目标同 risk 档**
# （只在 LOW 目标上开种子轮——P2-1：MEDIUM 目标的别名降级是风险作弊）。
register_candidate() { # $1=screen $2=new_name $3=round
  local screen="$1" new="$2" round="$3"
  local seed_dir="out/p2_seed_overrides"
  mkdir -p "$seed_dir/elements" "$seed_dir/screens"
  rm -rf "$seed_dir/elements" "$seed_dir/screens"
  cp -R repository/overrides/elements "$seed_dir/elements"
  cp -R repository/overrides/screens "$seed_dir/screens"
  local f="$seed_dir/elements/${screen}.yaml"
  cat >> "$f" <<YAML
---
schema_version: '1.0'
kind: element
id: ${new}
screen: ${screen}
type: button
strategies:
  - {type: accessibility_id, value: ${new}, origin: manual}
metadata:
  origin: manual
  review_id: p2-seed-round-${round}
  risk: LOW
YAML
  echo "$seed_dir"
}

accepted=0; skipped=0; failed=0
for ((round=1; round<=ROUNDS; round++)); do
  sc="${SCENARIOS[$(( (round-1) % N ))]}"
  IFS=':' read -r case_id screen old new <<<"$sc"

  say "── Round $round/$ROUNDS: $case_id $screen.$old → $new ──"

  # 1. 注入漂移（identifier 必须唯一命中一次，否则 sed 会误伤）
  hits=$(grep -c "accessibilityIdentifier(\"$old\")" "$SWIFT" || true)
  [[ "$hits" == "1" ]] || { say "FATAL round $round ：identifier '$old' 命中 $hits 次（需恰 1），跳过本轮"; failed=$((failed+1)); continue; }
  sed -i '' "s/accessibilityIdentifier(\"$old\")/accessibilityIdentifier(\"$new\")/" "$SWIFT"

  # 2. 重编重装（Makefile 内部处理 DEVELOPER_DIR / PlistBuddy 注入）。
  #    P1-1b：**每轮注入前源码都在原始态**——上一轮的还原在下方各分支
  #    显式执行（ACCEPTED 分支也还原），轮换表第 2 轮起才可能走通。
  seed_dir=$(register_candidate "$screen" "$new" "$round")
  make p1-build p1-install || { say "FATAL round $round ：构建/安装失败"; git checkout -- "$SWIFT"; failed=$((failed+1)); continue; }

  # 3. 真跑（RECOVERED → exit 5 是本轮的有效结果；set -e 下须捕获）。
  #    --overrides 指向含候选别名的副本 + --generated 双源合并——
  #    否则候选 resolve 不到 → 9.3-3 fail-closed（P1-1a 第 4 步）。
  rc=0
  "$PY" -m cli.main run --case "$case_id" --bundle-id "$BUNDLE_ID" --db "$DB" \
    --udid "${MTA_SIM_UDID:-}" \
    --overrides "$seed_dir" \
    --generated repository/generated/local \
    || rc=$?
  run_id=$("$PY" -c "import sqlite3;c=sqlite3.connect('$DB');print(c.execute(\"select run_id from runs order by start_time desc, rowid desc limit 1\").fetchone()[0])")

  # 每轮结束都还原源码（P1-1b：含 ACCEPTED 分支——否则轮换表全灭）
  restore_src

  if [[ "$rc" == "0" ]]; then
    say "WARN round $round ：用例直接 PASS（run_id=$run_id ）——漂移未生效或未被恢复路径消费，不给 accept"
    echo "$round,$sc,$run_id,,WARN_PASS,$rc" >> "$CSV"
    skipped=$((skipped+1)); continue
  elif [[ "$rc" != "5" ]]; then
    say "FATAL round $round ：mta run 退出码 $rc （非 RECOVERED），run_id=$run_id"
    echo "$round,$sc,$run_id,,FAILED,$rc" >> "$CSV"
    failed=$((failed+1)); continue
  fi

  # 4. 找本轮产生的 PENDING review，人工确认（E5：这一步不许自动化掉）
  row=$("$PY" -c "
import sqlite3
c = sqlite3.connect('$DB'); c.row_factory = sqlite3.Row
r = c.execute('select rr.id, rr.recovery_id, rec.kind, rec.expected_target, rec.candidate_target, rec.screen'
              ' from recovery_reviews rr join recoveries rec on rec.id = rr.recovery_id'
              \" where rr.review_status='PENDING' order by rr.id desc limit 1\").fetchone()
print(f\"{r['id']}|{r['recovery_id']}|{r['kind']}|{r['expected_target']}|{r['candidate_target']}|{r['screen']}\" if r else 'NONE|0||||')")
  rev_id=$(cut -d'|' -f1 <<<"$row")
  if [[ "$rev_id" == "NONE" ]]; then
    say "FATAL round $round ：RECOVERED 但无 PENDING review（run_id=$run_id ）——管线未走新路径，检查 cli/pipeline.py"
    echo "$round,$sc,$run_id,,NO_REVIEW,$rc" >> "$CSV"
    failed=$((failed+1)); continue
  fi
  say "review #$rev_id ：$(cut -d'|' -f4- <<<"$row")（run_id=$run_id ）"
  ans="n"
  if [[ "$AUTO_YES" == "1" ]]; then
    ans="y"
    say "--yes：自动 accept（冒烟模式；正式轮请人工复核）"
  else
    read -r -p "[p2-seed] 人工确认：accept review #$rev_id? [y/N] " ans
  fi
  if [[ "${ans:-n}" == "y" || "${ans:-n}" == "Y" ]]; then
    "$PY" -m cli.main review accept "$rev_id" --db "$DB" \
      --note "p2-seed round=$round scenario=$old->$new run_id=$run_id"
    echo "$round,$sc,$run_id,$rev_id,ACCEPTED,$rc" >> "$CSV"
    accepted=$((accepted+1))
    say "round $round ：ACCEPTED（review #$rev_id ）"
  else
    echo "$round,$sc,$run_id,$rev_id,SKIPPED,$rc" >> "$CSV"
    skipped=$((skipped+1))
    say "round $round ：跳过（review 保持 PENDING，可稍后 mta review accept）"
  fi
done

# 收尾：还原源码并重编原始版，让环境回到可跑常规套件的状态
restore_src
say "还原源码，重编原始版 App…"
make p1-build p1-install || say "WARN：原始版重编失败（后续常规跑需手动 make p1-build p1-install）"

say "完成：accepted=$accepted skipped=$skipped failed=$failed （明细：$CSV ）"
say "复核：$PY scripts/p2_audit_recoveries.py --db $DB --out docs/p2_data_audit.md"
[[ $failed -eq 0 ]] || exit 1
