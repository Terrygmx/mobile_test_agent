#!/usr/bin/env python3
"""M3 Gate 验收脚本（Task 3.3 / P1-10）：Repository 从「overrides 单源」
升为「generated + overrides 双源」，20 条 P1 用例集行为不变 + 一致性 Gate
绿灯。

检查项：
  G1. 12.1/12.2 源码扫描：`mta repo generate` 对 20 用例实际触碰的 App
      产出元素 metadata（27 元素 / 10 screen）；scanner 对插值 dynamic
      不猜值（12.2：accessibility_id 必须 nil，仅留静态前缀 id）；
  G2. 一致性 Gate（12.6）：generated vs overrides 全对齐（matched=25
      added=0 removed=0；插值前缀不计 REMOVED——人工登记是预期流程）；
  G3. 双源加载：GeneratedRepository + overrides 合并后，20 条用例里
      lint/execute 触碰的元素 resolve 全部命中（P1-08 的 20 用例是基线，
      M0-M2 已验证全绿；M3 只验证双源下**解析不回归**）；
  G4. 兼容性：只用 overrides 的既有行为不受影响（单源模式仍可用）；
  G5. build 维度：12.4 generated/<build>/ 布局，resolve(build=...) 不炸；
  G6. Source Coverage（12.7）：报告可产出、五桶加总 == 分母、unknown/missing
      为空、dynamic 可见（12.2 预期形态，不判红）。

产出：out/m3_gate/gate_summary.json；exit 0 = 全绿。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from repository.resolver import Repository          # noqa: E402
from source.consistency import check                # noqa: E402
from environment.secrets import EnvSecretProvider   # noqa: E402
from testcase.lint import Severity, lint, max_severity  # noqa: E402
from testcase.loader import load_testcase           # noqa: E402

OUT_DIR = ROOT / "out" / "m3_gate"
GENERATED = ROOT / "repository" / "generated" / "local"
OVERRIDES = ROOT / "repository" / "overrides"
SUITES = ROOT / "suites"
PARSER_VERSION = "1.0"


def _regenerate() -> dict:
    """跑正式入口（不是直接调 python api）——CLI 路径本身是交付物。"""
    proc = subprocess.run(
        [sys.executable, "-m", "cli.main", "repo", "generate",
         "ios_demo/LoginDemo", "--check"],
        capture_output=True, text=True, cwd=ROOT)
    if proc.returncode != 0:
        raise SystemExit(f"G1 FAILED: repo generate exited "
                         f"{proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    meta = json.loads((GENERATED / "source_metadata.json").read_text())
    return meta


def main() -> int:
    t0 = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}

    # ---- G1: 扫描产物 ----
    meta = _regenerate()
    n_elements = sum(len(s["elements"]) for s in meta["screen_elements"])
    n_screens = len(meta["screen_elements"])
    interp = [e for s in meta["screen_elements"] for e in s["elements"]
              if e["resolution_type"] == "dynamic"]
    results["G1_scan"] = {
        "pass": n_elements == 27 and n_screens == 10,
        "detail": f"elements={n_elements} screens={n_screens} "
                  f"dynamic={len(interp)}",
        "meta_top": {k: meta.get(k) for k in
                     ("app_version", "build", "parser_version",
                      "git_commit", "generated_at")},
    }
    # 12.2 硬契约：插值 dynamic 不得有 accessibility_id
    bad = [e for e in interp if e.get("accessibility_id")]
    results["G1_interp_no_a11y"] = {
        "pass": not bad,
        "detail": f"{len(interp)} dynamic, {len(bad)} with a11y_id"}

    # ---- G2: 一致性 Gate ----
    report = check(meta, OVERRIDES)
    results["G2_consistency"] = {
        "pass": report.ok and len(report.matched) >= 25
        and not report.added and not report.removed,
        "detail": f"matched={len(report.matched)} added={len(report.added)} "
                  f"removed={len(report.removed)} "
                  f"unresolvable={len(report.unresolvable)}",
        "unresolvable": [f"{s}.{i}" for s, i in report.unresolvable],
    }

    # ---- G3: 双源加载 ----
    # 凭据自给：Gate 脚本不能假设调用方 shell 带了 TEST_USERNAME/TEST_PASSWORD
    # （verify_p1_m2 依赖 shell env）。H9 unknown_secret 是**环境问题**，不是
    # M3 的缺口——注入 demo 凭据并把值 [REDACTED] 化（不落 summary）。
    # 注意：EnvSecretProvider 从进程 env 读，setdefault 不影响调用方已设的值。
    os.environ.setdefault("TEST_USERNAME", "demo")
    os.environ.setdefault("TEST_PASSWORD", "demo")
    repo = Repository.from_dirs(generated_root=str(GENERATED),
                                overrides_root=str(OVERRIDES))
    secrets = EnvSecretProvider()
    case_paths = sorted(SUITES.rglob("*.yaml"))
    cases = [load_testcase(p) for p in case_paths]
    unresolved: list[str] = []
    issues = lint(cases, repo, secrets)
    lint_errors = sum(1 for i in issues if i.severity is Severity.ERROR)
    # 用例里所有 target 能 resolve（P1-08 的 20 用例基线行为）
    for tc in cases:
        for step in tc.steps:
            refs = []
            if getattr(step, "target", None):
                refs.append(step.target)
            for pre in getattr(step, "preconditions", []) or []:
                if getattr(pre, "target", None):
                    refs.append(pre.target)
            for post in getattr(step, "postconditions", []) or []:
                if getattr(post, "target", None):
                    refs.append(post.target)
            for ref in refs:
                try:
                    repo.resolve(ref, build="local")
                except Exception as e:      # UnknownReferenceError 等
                    unresolved.append(f"{tc.id}: {ref} ({type(e).__name__})")
    n_cases = len(cases)
    results["G3_dual_source"] = {
        "pass": not unresolved and lint_errors == 0 and n_cases == 20,
        "detail": f"cases={n_cases} lint_error_files={lint_errors} "
                  f"unresolved={len(unresolved)}",
        "unresolved": unresolved[:10],
    }

    # ---- G4: overrides 单源兼容 ----
    repo_ov = Repository.from_dirs(overrides_root=str(OVERRIDES))
    n_ov = len(repo_ov.elements_of("HomeView"))
    results["G4_overrides_only"] = {
        "pass": n_ov >= 6,
        "detail": f"HomeView elements via overrides-only = {n_ov}"}

    # ---- G5: build 隔离 ----
    try:
        repo.resolve("HomeView.home_list", build="local")
        build_ok, build_err = True, "resolve(build=local) ok"
    except Exception as e:
        build_ok, build_err = False, f"{type(e).__name__}: {e}"
    results["G5_build_dim"] = {"pass": build_ok, "detail": build_err}

    # ---- G6: Source Coverage 报告（12.7；Gate M3 明文要求「Coverage 与
    #      unknown/dynamic 占比有报告」）----
    # 判据不是「覆盖率高」——P1 阶段 dynamic 是**预期形态**（12.2 插值不猜
    # 值，人工登记实例），把 dynamic 判红会逼人去猜值，那是设计禁止的。真正
    # 要卡的是：①报告能产出（纯函数不炸、CLI 路径通）；②分桶加总 == 分母
    # （口径自洽，指标没漏桶）；③unknown/missing 为空（这两种是真缺口，
    # dynamic 豁免但必须可见）。
    from cli.pipeline import SessionPipeline
    from source.coverage import compute_coverage
    cov = compute_coverage(meta, SessionPipeline(suites_root=SUITES).discover())
    bucket_sum = (cov.resolved + cov.dynamic + cov.unknown
                  + cov.ambiguous + cov.missing)
    cov_ok = (bucket_sum == cov.total and cov.total > 0
              and not cov.unknown and not cov.missing
              and cov.dynamic == len(cov.dynamic_refs) > 0)
    results["G6_source_coverage"] = {
        "pass": cov_ok,
        "detail": f"coverage={cov.coverage:.3f} "
                  f"({cov.resolved}/{cov.total}) "
                  f"dynamic={cov.dynamic} unknown={cov.unknown} "
                  f"ambiguous={cov.ambiguous} missing={cov.missing} "
                  f"screen_coverage={cov.screen_coverage:.3f}",
        "dynamic_refs": [f"{s}.{i}" for s, i in cov.dynamic_refs],
    }

    # ---- 汇总 ----
    verdict = all(r["pass"] for r in results.values())
    summary = {"verdict": "PASS" if verdict else "FAIL",
               "elapsed_s": round(time.time() - t0, 2),
               "results": results}
    (OUT_DIR / "gate_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"M3 Gate: {summary['verdict']} ({summary['elapsed_s']}s)")
    for name, r in results.items():
        print(f"  {'✓' if r['pass'] else '✗'} {name}: {r['detail']}")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
