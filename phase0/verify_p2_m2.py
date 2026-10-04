#!/usr/bin/env python3
"""Gate M2 真机验证（Task 2.4 / P2-03/04/05 集成载体）。

设计 §16 Gate M2 的判据机械映射（本脚本 = 真机那一半；FakeDriver 那一半在
`tests/fault_injection/test_p2_matrix_m2.py`）：

  G1. P2 矩阵 #1/#2/#3/#4/#15 自动化全绿（pytest 实跑，12 项）；
  G2. F5 漂移 **第一次** run：LLM 救回 → `RECOVERED_LLM` / exit 5
      + 建 PENDING review（9.5 学习入口）；
  G3. 人工 ACCEPT → Experience Candidate（E5 种子三件套齐）；
  G4. 同漂移 **第二次** run：`RECOVERED_EXPERIENCE` / exit 5
      **且 LLM calls = 0**（设计 3.1「Experience 命中时根本不消耗 Budget」
      ——这是「知识积累」唯一可观测的形状）；
  G5. 4.7 记账：experience_runs 落一行 SUCCESS、step_id 是真 steps.id、
      `validated_builds` 追加 app_build（E7）；recoveries.kind = EXPERIENCE；
  G6. P1 行为保留：空 experience 库 + `--no-llm` 同场景仍 FAIL / exit 1。

漂移设计（沿用 verify_p1_m4 的方法）：源码 username_field → user_field，
真实改名重编译重装，用例仍引旧 id。

流程：
  1. make repo-generate（基线，身份与 HEAD 对齐）；
  2. 改名 → make p1-build → make p1-install → 重扫描到临时目录（不带
     --check：overrides 手工旧 id 悬空正是 12.6 应报的）；
  3. G2 真机跑（LLM 开）→ 查 trace 断言 RECOVERED_LLM；
  4. `mta review accept` → Candidate（--exp-db 指向临时库，绝不写仓库
     out/experience.db——测试纪律）；
  5. G4/G5 第二次真机跑（同 exp-db）→ 断言 Experience 命中且 LLM 零调用；
  6. G6 空库 + --no-llm → FAIL；
  7. git checkout 还原源码 + 重编译重装 + repo-generate（现场还原，可重跑）。

前置：booted 模拟器、Appium 4723、LLM 网关（LLM_BASE_URL，默认内网 15721）。
产出：out/p2_m2_gate/summary.json；exit 0 = Gate M2 真机判据全绿。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "out" / "p2_m2_gate"
BUNDLE = "com.phaset0.logindemo"
UDID = os.environ.get("MTA_SIM_UDID", "AEBDAE77-7C5B-468B-A5A7-01D41EDAAD9E")
APP_SOURCE = ROOT / "ios_demo/LoginDemo/LoginDemoApp.swift"
OLD_ID, NEW_ID = "username_field", "user_field"
PY = str(ROOT / ".venv/bin/python")

results: dict[str, dict] = {}
_T0 = time.time()


def sh(*args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True,
                          env=_env(), cwd=ROOT, timeout=timeout)


def _env() -> dict:
    e = dict(os.environ)
    e.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    e.setdefault("LLM_BASE_URL", "http://127.0.0.1:15721/v1")
    e.setdefault("LLM_MODEL", "glm-5-3-flash")
    e.setdefault("LLM_API_KEY", "sk-test")
    e.setdefault("TEST_USERNAME", "qa_agent")
    e.setdefault("TEST_PASSWORD", "qa_pass")
    # 本机 4723/15721 都是环回服务，而开发机常带 HTTP_PROXY——代理对环回
    # 端口的转发行为不稳定（实测 4723 被拒、15721 放行），会让 preflight
    # 出现「服务明明在跑却探测失败」的假红。显式豁免环回。
    e["no_proxy"] = e["NO_PROXY"] = "127.0.0.1,localhost,::1"
    e["MTA_SIM_UDID"] = UDID
    return e


def check(name: str, ok: bool, detail: str) -> bool:
    results[name] = {"pass": bool(ok), "detail": detail}
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
    return bool(ok)


def _probe(url: str, timeout: float) -> bool:
    """环回探测**绕过代理**（_env 的 no_proxy 只管子进程，本进程自己的
    urllib 也要显式装无代理 opener——代理会把环回请求转上游后拒绝）。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


_MACRO_PROBE = """\
import SwiftUI
struct Probe: View {
    @State var n = 0
    var body: some View { Text("\\(n)") }
}
"""

# 环境级阻塞的指纹：Swift 编译器在 `sandbox-exec` 里拉起 swift-plugin-server
# 展开 SwiftUI 宏；宿主禁止 sandbox_apply 时插件进程起不来，宏展开失败。
# 这不是工程缺陷，重跑无用——要么换机器，要么在允许 sandbox_apply 的环境跑。
_ENV_BLOCK_MARKERS = ("sandbox_apply", "malformed response")


def _swift_macro_probe() -> tuple[bool, str]:
    """SwiftUI 宏工具链可用性（G0 的**先决条件**，独立成项）。

    为什么要单独探：宏工具链坏掉时 `make p1-build` 只报一片
    「SwiftCompile failed」，看不出是代码问题还是环境问题——Task 2.4 实测
    就撞上过（`swift-plugin-server` 被 sandbox_apply 拒绝），白花一轮排查。
    """
    probe_dir = OUT_DIR / "swift_probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    src = probe_dir / "probe.swift"
    src.write_text(_MACRO_PROBE, encoding="utf-8")
    r = sh("xcrun", "swiftc", "-sdk",
           sh("xcrun", "--sdk", "iphonesimulator",
              "--show-sdk-path").stdout.strip(),
           "-target", "arm64-apple-ios18.5-simulator",
           "-typecheck", str(src), timeout=180)
    if r.returncode == 0:
        return True, "SwiftUI 宏展开可用（@State typecheck 通过）"
    blob = (r.stdout + r.stderr)[:400]
    env_blocked = any(m in blob for m in _ENV_BLOCK_MARKERS)
    kind = "环境级（宿主禁止 sandbox_apply）" if env_blocked else "工具链级"
    return False, f"{kind}：{blob.strip().splitlines()[-1][:200]}"


def _llm_latency_probe(timeout: float = 25.0) -> tuple[bool, str]:
    """真实 completion 的延迟探活（**不是** `/models` 的 200）。

    `/models` 秒回 200 只说明网关在，不代表 completion 能在 budget 的超时内
    返回——网关后面挂的是**推理模型**（输出先走 `reasoning_content`，长推理
    链），实测撞过「探活四项全绿、G2 连续两次
    `LLMError: LLM_PROVIDER_ERROR: timed out`」的假绿。

    ⚠️ **这是下界，不是保证**（review_p2_task24_final P3-6）：探活用
    `max_tokens=8` 的最小 prompt，而 G2 跑的是完整恢复 prompt（几千 token
    的 UI 树）——探活绿仍可能 G2 红。G2 侧靠 provider 级重试兜。

    阈值取自 `BudgetConfig.timeout_seconds`（**单一真值源**：budget 默认值一
    变，这里跟着变，不会悄悄漂）。HTTP 4xx（模型名不存在等）与超时**分开
    报**：前者是「环境未配」，后者是「环境太慢」，排障含义不同。
    """
    from llm.budget import BudgetConfig

    budget_timeout = float(BudgetConfig().timeout_seconds)
    model = _env().get("LLM_MODEL", "glm-5-3-flash")
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": "OK"}],
                       "max_tokens": 8}).encode("utf-8")
    req = urllib.request.Request(
        _env()["LLM_BASE_URL"].rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {_env().get('LLM_API_KEY', '')}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.monotonic()
    try:
        with opener.open(req, timeout=timeout) as resp:
            resp.read()
    except urllib.error.HTTPError as e:  # 4xx/5xx：模型名/凭据等配置问题
        return False, (f"HTTP {e.code}（model={model!r} 不可用或凭据有误）"
                       f"（{time.monotonic() - t0:.1f}s）")
    except Exception as e:  # noqa: BLE001 — 超时/拒连
        return False, (f"{type(e).__name__}: {e}"
                       f"（{time.monotonic() - t0:.1f}s）")
    took = time.monotonic() - t0
    return took < budget_timeout, (
        f"completion {took:.1f}s < budget timeout {budget_timeout:.0f}s"
        f"（最小 prompt 的下界，非 G2 完整 prompt 的保证）")


def preflight() -> bool:
    ok = True
    r = sh("xcrun", "simctl", "list", "devices", "booted")
    ok &= check("PF_simulator_booted", UDID in r.stdout and "Booted" in r.stdout,
                f"UDID={UDID}")
    ok &= check("PF_appium_4723",
                _probe("http://127.0.0.1:4723/status", 4),
                "Appium status 200")
    base = _env()["LLM_BASE_URL"].rstrip("/")
    ok &= check("PF_llm_gateway", _probe(f"{base}/models", 5),
                f"LLM_BASE_URL={_env()['LLM_BASE_URL']}")
    ok &= check("PF_llm_completion_latency", *_llm_latency_probe())
    macro_ok, macro_detail = _swift_macro_probe()
    ok &= check("PF_swift_macro_toolchain", macro_ok, macro_detail)
    return ok


def g1_matrix_pytest() -> bool:
    """G1：P2 矩阵 pytest。

    `--basetemp` 必须指到**工作区内**：tmp_path fixture 要写系统临时目录，
    而受限执行环境会拦掉那里（实测 200+ 个
    `PermissionError: EEXIST ... pytest-of-unknown` 假失败）——这是本项目
    跑 pytest 的既定纪律，gate 脚本同样适用。

    残留目录**交给 pytest 自己清**（`--basetemp` 会在会话开始时重建它）：
    脚本侧 `rmtree` 一次要删几百个文件，会撞上执行环境的批量删除保护而
    静默失败——清不掉还多一条噪声。
    """
    base = OUT_DIR / "_pytest_tmp"
    proc = sh(PY, "-m", "pytest",
              "tests/fault_injection/test_p2_matrix_m2.py",
              "-p", "no:cacheprovider", "--basetemp", str(base), timeout=600)
    tail = (proc.stdout.strip().splitlines() or ["<no stdout>"])[-1]
    return check("G1_p2_matrix_12of12", proc.returncode == 0,
                 f"exit={proc.returncode} {tail[:120]}")


def make(target: str) -> bool:
    r = sh("make", target)
    if r.returncode != 0:
        print(f"make {target} failed:\n{r.stdout[-1200:]}\n{r.stderr[-600:]}")
    return r.returncode == 0


def ensure_original_source() -> None:
    if OLD_ID in APP_SOURCE.read_text(encoding="utf-8"):
        return
    print("ensure_original_source: 检出改名残留 → 还原")
    sh("git", "checkout", "--", str(APP_SOURCE))
    make("p1-build")
    make("p1-install")


_SOURCE_TOUCHED = False


def rename_and_build(gen_root: Path) -> bool:
    """真实改名 → 注入构建 → 安装 → 重扫描登记新 id。"""
    global _SOURCE_TOUCHED
    src = APP_SOURCE.read_text(encoding="utf-8")
    if NEW_ID in src and OLD_ID not in src:
        pass  # 上次中断残留的改名形态，直接续跑
    elif OLD_ID not in src:
        print(f"PREFLIGHT ERROR: {OLD_ID} 不在源码中")
        return False
    else:
        APP_SOURCE.write_text(src.replace(f'"{OLD_ID}"', f'"{NEW_ID}"'),
                              encoding="utf-8")
        _SOURCE_TOUCHED = True
    if not (make("p1-build") and make("p1-install")):
        return False
    r = sh(PY, "-m", "cli.main", "repo", "generate", "ios_demo/LoginDemo",
           "--out", str(gen_root))
    if r.returncode != 0:
        print(f"repo generate failed: {r.stdout[-600:]}{r.stderr[-300:]}")
        return False
    meta = json.loads(
        (gen_root / "local" / "source_metadata.json").read_text())
    ids = {e["accessibility_id"]
           for s in meta["screen_elements"] for e in s["elements"]
           if e.get("accessibility_id")}
    if NEW_ID not in ids or OLD_ID in ids:
        print(f"PREFLIGHT ERROR: 重扫描产物不含 {NEW_ID}（ids 样本："
              f"{sorted(ids)[:8]}）")
        return False
    return True


def restore() -> None:
    """现场还原（源码 + 还原版重装 + 重扫描）。

    只在**本脚本真的改过源码**时才动：没改名就没东西可还原，无条件重编译
    会白等几分钟（设备半边因前置不满足被跳过时尤其刺眼），也把「脚本没跑
    过设备」伪装成「跑过并还原了」。
    """
    if not _SOURCE_TOUCHED:
        print("restore: 本次未改源码，无需还原")
        return
    if OLD_ID in APP_SOURCE.read_text(encoding="utf-8"):
        print("restore: 源码已是原样，跳过 checkout")
    else:
        r = sh("git", "checkout", "--", str(APP_SOURCE))
        if r.returncode != 0:
            print(f"restore WARN: git checkout 失败: {r.stderr[:200]}")
            return
    if make("p1-build") and make("p1-install"):
        make("repo-generate")
    else:
        print("restore WARN: 还原版重编译/安装失败（源码已还原，App 未还原）")


DRIFT_CASE = """\
schema_version: "0.2"
id: drift_p2
name: Gate M2 经验积累（username_field 真实改名）
suite: driftgate
tags: [drift]
precondition:
  reset: RELAUNCH
steps:
  - action: launch_app
  - wait_for:
      target: LoginView.password_field
      condition: visible
      timeout: 10
  - action: input
    target: LoginView.username_field
    value: ${TEST_USERNAME}
  - action: input
    target: LoginView.password_field
    value: ${TEST_PASSWORD}
    sensitive: true
  - action: tap
    target: LoginView.login_button
    idempotency: IDEMPOTENT
  - wait_for:
      target: screen:HomeView
      condition: active
      timeout: 10
  - action: tap
    target: HomeView.go_profile
  - wait_for:
      target: screen:ProfileView
      condition: active
      timeout: 10
  - assertion:
      target: ProfileView.logout_button
      condition: exists
      timeout: 5
"""


DRIFT_ALIAS_DOC = """
---
# Gate M2：模拟 9.5 review-accept 产物——LLM 候选 user_field 经人工确认后
# 落 overrides（含风险声明）。rename 漂移的恢复闭环 = 人的确认（risk 声明）
# + LLM 桥接运行时命名；无此声明则源扫描元素 risk=None → LLM_RISK_BLOCKED
# （experience.runtime_guard 共享链的 fail-closed 纪律，设计 9.3 第 4 行）。
schema_version: "1.0"
kind: element
id: user_field
screen: LoginView
type: textfield
strategies:
  - {type: accessibility_id, value: user_field, origin: manual,
     source_file: LoginDemoApp.swift, source_line: 66}
metadata:
  risk: LOW
  idempotency: IDEMPOTENT
  data_class: INTERNAL
"""


def _scaffold() -> Path:
    tmp = OUT_DIR / "drift_env"
    (tmp / "suites").mkdir(parents=True, exist_ok=True)
    (tmp / "suites" / "drift_p2.yaml").write_text(DRIFT_CASE, encoding="utf-8")
    return tmp


def prepare_overrides(tmp: Path) -> Path:
    """overrides 副本 + user_field 别名（H15：绝不写 repository/overrides）。"""
    ov = tmp / "overrides"
    if ov.exists():
        shutil.rmtree(ov)
    shutil.copytree(ROOT / "repository" / "overrides", ov)
    lv = ov / "elements" / "LoginView.yaml"
    text = lv.read_text(encoding="utf-8")
    if "id: user_field" not in text:
        lv.write_text(text + DRIFT_ALIAS_DOC, encoding="utf-8")
    return ov


def mta_run(db: Path, suites: Path, gen_root: Path, overrides: Path,
            exp_db: Path, *extra: str) -> subprocess.CompletedProcess:
    return sh(PY, "-m", "cli.main", "run", "--case", "drift_p2",
              "--suites-root", str(suites), "--db", str(db),
              "--udid", UDID, "--bundle-id", BUNDLE,
              "--metadata", str(gen_root / "local" / "source_metadata.json"),
              "--generated", str(gen_root / "local"),
              "--overrides", str(overrides),
              "--exp-db", str(exp_db),
              *extra, timeout=900)


def _db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def trace_facts(db: Path) -> dict:
    conn = _db(db)
    try:
        run = conn.execute(
            "SELECT run_id, exit_code, status, llm_enabled, llm_calls FROM runs"
            " ORDER BY rowid DESC LIMIT 1").fetchone()
        tc = conn.execute(
            "SELECT status, detail_json FROM testcase_runs"
            " ORDER BY rowid DESC LIMIT 1").fetchone()
        recoveries = [dict(r) for r in conn.execute(
            "SELECT kind, expected_target, candidate_target, candidate_type,"
            " screen FROM recoveries ORDER BY id").fetchall()]
        recovered_steps = [dict(r) for r in conn.execute(
            "SELECT id, step_index, step_type, target_id FROM steps"
            " WHERE status='RECOVERED' ORDER BY id").fetchall()]
        reviews = [dict(r) for r in conn.execute(
            "SELECT id, recovery_id, review_status FROM recovery_reviews"
            " ORDER BY id").fetchall()]
        detail = {}
        if tc and tc["detail_json"]:
            try:
                detail = json.loads(tc["detail_json"])
            except Exception:
                detail = {}
        return {"run_id": run["run_id"],
                "exit_code": run["exit_code"], "run_status": run["status"],
                "llm_enabled": run["llm_enabled"], "llm_calls": run["llm_calls"],
                "tc_status": tc["status"] if tc else None,
                "tc_detail": detail,
                "recovered_kinds": detail.get("recovered_kinds"),
                "recovery_kinds": detail.get("recovery_kinds"),
                "recoveries": recoveries,
                "recovered_steps": recovered_steps,
                "reviews": reviews}
    finally:
        conn.close()


def exp_facts(exp_db: Path) -> dict:
    if not exp_db.exists():
        return {"experiences": [], "runs": []}
    conn = _db(exp_db)
    try:
        exps = [dict(r) for r in conn.execute(
            "SELECT experience_id, app_id, screen_id, target_id,"
            " strategy_json, status, sample_count, success_count,"
            " failure_count, validated_builds_json FROM experiences"
            " ORDER BY rowid").fetchall()]
        runs = [dict(r) for r in conn.execute(
            "SELECT experience_id, run_id, step_id, app_build, result,"
            " guard_reason, uniqueness_count, element_type_match"
            " FROM experience_runs ORDER BY id").fetchall()]
        return {"experiences": exps, "runs": runs}
    finally:
        conn.close()


def _llm_rows(db: Path) -> list:
    """recoveries 里 **kind='LLM'** 的行。

    必须显式过滤：全表返回会把 Experience 命中自己那行也算进来，于是
    「LLM 一次都没出手」这个**正确**行为会被判成失败（Task 2.4 实测踩过）。
    """
    conn = _db(db)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT kind, candidate_target, reject_reason, result"
            " FROM recoveries WHERE kind='LLM' ORDER BY id").fetchall()]
    finally:
        conn.close()


def _provider_errors(db: Path) -> int:
    conn = _db(db)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM steps WHERE failure_type='LLM_PROVIDER_ERROR'"
        ).fetchone()[0]
    finally:
        conn.close()


def g2_first_run(tmp: Path, suites: Path, gen_root: Path, overrides: Path,
                 exp_db: Path) -> tuple[bool, int | None]:
    """G2：第一次 run —— LLM 救回 + 建 PENDING review（学习入口）。"""
    db = tmp / "trace_run1.db"
    db.unlink(missing_ok=True)
    f: dict = {}
    r = None
    # 4 次：网关后面是推理模型，单次 completion 偶发越过 budget 的 20s
    # （LLM_PROVIDER_ERROR: timed out）。只对 provider 级错误重试——校验链
    # 拒绝是确定性语义，重试只会掩盖真问题。
    for attempt in range(1, 5):
        r = mta_run(db, suites, gen_root, overrides, exp_db)
        print(f"  G2 attempt {attempt} stdout tail:",
              (r.stdout.strip().splitlines() or ["<empty>"])[-3:])
        f = trace_facts(db)
        if _provider_errors(db) == 0:
            break
        print(f"  G2 attempt {attempt}: LLM_PROVIDER_ERROR（网关抖动）→ 重试")
        db.unlink(missing_ok=True)
    print(f"  G2 run: exit={r.returncode if r else None} facts={f}")
    ok = check(
        "G2_first_run_llm_recovers",
        bool(r) and r.returncode == 5 and f["exit_code"] == 5
        and f["tc_status"] == "RECOVERED"
        and f["recovered_kinds"] == ["RECOVERED_LLM"]
        and [x["kind"] for x in f["recoveries"]] == ["LLM"]
        and len(f["reviews"]) == 1
        and f["reviews"][0]["review_status"] == "PENDING",
        f"proc_exit={r.returncode if r else None} tc={f['tc_status']} "
        f"kinds={f['recovered_kinds']} recoveries="
        f"{[x['kind'] for x in f['recoveries']]} reviews={f['reviews']}")
    review_id = f["reviews"][0]["id"] if f["reviews"] else None
    return ok, review_id


def g3_accept(review_id: int, db: Path, exp_db: Path, tmp: Path) -> bool:
    """G3：人工 ACCEPT → Candidate（E5 种子三件套）。"""
    patch = tmp / "overrides_patch.yaml"
    r = sh(PY, "-m", "cli.main", "review", "accept", str(review_id),
           "--db", str(db), "--exp-db", str(exp_db),
           "--reviewer", "gate_m2", "--out", str(patch))
    print("  G3 stdout:", (r.stdout.strip().splitlines() or ["<empty>"])[-2:])
    if r.stderr.strip():
        print("  G3 stderr tail:", r.stderr.strip().splitlines()[-3:])
    facts = exp_facts(exp_db)
    exps = facts["experiences"]
    seen = [(e["status"], e["app_id"], e["screen_id"], e["target_id"])
            for e in exps]
    ok = check(
        "G3_accept_creates_candidate",
        r.returncode == 0 and len(exps) == 1
        and exps[0]["status"] == "CANDIDATE"
        and exps[0]["app_id"] == BUNDLE
        and exps[0]["screen_id"] == "LoginView"
        and exps[0]["target_id"] == OLD_ID
        and NEW_ID in exps[0]["strategy_json"],
        f"exit={r.returncode} candidates={seen}")
    return ok


def g4_g5_second_run(tmp: Path, suites: Path, gen_root: Path,
                     overrides: Path, exp_db: Path) -> bool:
    """G4/G5：第二次 run —— Experience 命中、LLM 零调用、4.7 记账。"""
    db = tmp / "trace_run2.db"
    db.unlink(missing_ok=True)
    r = mta_run(db, suites, gen_root, overrides, exp_db)
    print("  G4 stdout tail:",
          (r.stdout.strip().splitlines() or ["<empty>"])[-3:])
    f = trace_facts(db)
    print(f"  G4 run: exit={r.returncode} facts={f}")
    ok4 = check(
        "G4_second_run_experience_zero_llm",
        r.returncode == 5 and f["exit_code"] == 5
        and f["tc_status"] == "RECOVERED"
        and f["recovered_kinds"] == ["RECOVERED_EXPERIENCE"]
        and f["recovery_kinds"] == ["experience"]
        and f["llm_calls"] == 0 and _llm_rows(db) == [],
        f"proc_exit={r.returncode} tc={f['tc_status']} "
        f"kinds={f['recovered_kinds']} llm_calls={f['llm_calls']} "
        f"llm_rows={_llm_rows(db)}")
    if not ok4:
        return False

    exps = exp_facts(exp_db)["experiences"]
    runs = exp_facts(exp_db)["runs"]
    step_ids = {s["id"] for s in f["recovered_steps"]}
    exp = exps[0] if exps else {}
    builds = json.loads(exp.get("validated_builds_json") or "[]")
    ok5 = check(
        "G5_sample_recorded_and_build_validated",
        [x["kind"] for x in f["recoveries"]] == ["EXPERIENCE"]
        and f["recoveries"][0]["candidate_target"] == NEW_ID
        and len(runs) == 1
        and runs[0]["result"] == "SUCCESS"
        and runs[0]["step_id"] in step_ids
        and runs[0]["run_id"] == f["run_id"]
        and exp.get("sample_count") == 1 and exp.get("success_count") == 1
        and exp.get("validated_builds_json") not in (None, "[]"),
        f"recoveries={[(x['kind'], x['candidate_target']) for x in f['recoveries']]}"
        f" runs={runs} step_ids={sorted(step_ids)} run_id={f['run_id']} "
        f"samples={exp.get('sample_count')}/{exp.get('success_count')} "
        f"validated_builds={builds}")
    return ok5


def g6_empty_store_keeps_p1(tmp: Path, suites: Path, gen_root: Path,
                            overrides: Path) -> bool:
    """G6：P1 行为保留——空库 + --no-llm 同场景仍 FAIL / exit 1。"""
    db = tmp / "trace_nollm.db"
    empty = tmp / "empty_experience.db"
    db.unlink(missing_ok=True)
    empty.unlink(missing_ok=True)
    r = mta_run(db, suites, gen_root, overrides, empty, "--no-llm")
    f = trace_facts(db)
    print(f"  G6 run: exit={r.returncode} facts={f}")
    return check(
        "G6_empty_store_no_llm_keeps_p1",
        r.returncode == 1 and f["exit_code"] == 1
        and f["tc_status"] == "FAIL" and not f["llm_enabled"]
        and f["llm_calls"] == 0,
        f"proc_exit={r.returncode} run_exit={f['exit_code']} "
        f"tc={f['tc_status']} llm_enabled={f['llm_enabled']} "
        f"llm_calls={f['llm_calls']}")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pre_ok = preflight()
    # G1 是纯 pytest（不需要设备/编译器），**独立于前置**——前置挂了它照样
    # 给出结论，这样「Gate 的哪一半绿了」永远可分辨。
    g1_ok = g1_matrix_pytest()
    tmp = _scaffold()
    gen_root = tmp / "generated"
    # 绝不写仓库 out/experience.db（测试纪律）：全程用临时库
    exp_db = tmp / "experience.db"
    for stale in tmp.glob("*.db"):
        stale.unlink()
    device_ok = pre_ok
    if device_ok:
        ensure_original_source()
        device_ok = check("G0_baseline_and_drift_build",
                          make("repo-generate") and rename_and_build(gen_root),
                          "基线重扫描（G8 对齐 HEAD）→ 真实改名重编译安装 → "
                          "重扫描登记新 id")
    try:
        if device_ok:
            device_ok, review_id = g2_first_run(tmp, tmp / "suites", gen_root,
                                                prepare_overrides(tmp), exp_db)
            if device_ok and review_id is not None:
                device_ok = g3_accept(review_id, tmp / "trace_run1.db", exp_db,
                                      tmp)
            if device_ok:
                device_ok = g4_g5_second_run(tmp, tmp / "suites", gen_root,
                                             tmp / "overrides", exp_db)
            if device_ok:
                device_ok = g6_empty_store_keeps_p1(tmp, tmp / "suites",
                                                    gen_root,
                                                    tmp / "overrides")
    finally:
        restore()
    ok = pre_ok and g1_ok and device_ok
    return _finish(0 if ok else 1, pre_ok=pre_ok, g1_ok=g1_ok,
                   device_ok=device_ok)


def _finish(code: int, *, pre_ok: bool, g1_ok: bool, device_ok: bool) -> int:
    """落 summary.json。

    `device_half` 三态而不是布尔：`SKIPPED_ENV`（前置环境不满足，本脚本没
    有机会跑）与 `FAIL`（跑了但判据没过）是**完全不同**的结论——混成一个
    FAIL 会让「环境没准备好」被读成「实现有问题」（Task 2.3 的 R13-3 就是
    这么被误读的：只留了文字、没有产物，事后无法复核）。
    """
    if device_ok:
        device_half = "PASS"
    elif not pre_ok:
        device_half = "SKIPPED_ENV"
    else:
        device_half = "FAIL"
    blocked = [k for k, v in results.items()
               if k.startswith("PF_") and not v["pass"]]
    summary = {"verdict": "PASS" if code == 0 else "FAIL",
               "gate": "M2", "udid": UDID,
               "g1_matrix_pytest": "PASS" if g1_ok else "FAIL",
               "device_half": device_half,
               "blocked_by": blocked,
               "elapsed_s": round(time.time() - _T0, 1),
               "results": results}
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"summary: {OUT_DIR / 'summary.json'}")
    print(f"verify_p2_m2: exit {code} (device_half={device_half}, "
          f"blocked_by={blocked})")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
