"""mta CLI 骨架（设计 14.6，Task 1.3 / P1-01）。

子命令：lint（Task 1.3 完整实现）；run / review / report / repo 为占位，
按 plan Task 1.6+ 逐步填充。退出码表（8.4）：
  - lint：有 ERROR → 3；仅 WARNING / 干净 → 0；
  - 占位子命令 → 退出码 2（NotImplemented，避免误当成功）。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Sequence

import yaml

from environment.secrets import EnvSecretProvider
from repository.resolver import Repository, Severity
from testcase.lint import lint, max_severity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mta",
        description="Mobile test agent CLI（设计 14.6）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- mta lint ---
    lint_p = sub.add_parser("lint", help="运行前静态检查（6.4；ERROR → 退出码 3）")
    lint_p.add_argument("testcases", nargs="+", metavar="TESTCASE_YAML",
                        help="用例 YAML 文件（可多个）")
    lint_p.add_argument("--overrides", metavar="DIR",
                        help="repository overrides 根目录（{DIR}/elements, {DIR}/screens）")
    lint_p.add_argument("--generated", metavar="DIR", default=None,
                        help="generated 根目录（M3 前通常为空）")

    # --- mta run（Task 2.6：全参数 14.6） ---
    run_p = sub.add_parser("run", help="执行用例/套件（7.1 新管线）")
    target = run_p.add_mutually_exclusive_group(required=True)
    target.add_argument("--suite", metavar="NAME")
    target.add_argument("--tag", metavar="TAG")
    target.add_argument("--case", metavar="CASE_ID")
    # R18-4：--no-llm 尚未接线（Recovery 属 M4 语义）。help 如实标注现状
    # ——「参数存在≠功能存在」（R17-1/2）。不摘除：调用方（脚本/CI）传了
    # 不该报错，而应 fail-loud 于 warning 并在 trace 记「flag 未生效」。
    run_p.add_argument("--no-llm", action="store_true",
                       help="[未接线/M4] 禁用 LLM recovery；当前管线无 "
                            "Recovery 调用故 LLM 数恒 0")
    run_p.add_argument("--junit", metavar="PATH",
                       help="输出 JUnit XML（8.5）")
    run_p.add_argument("--html", metavar="PATH",
                       help="输出 HTML 报告（14.5）")
    # 12.5 Build Identity（Task 3.3）：默认 fail-closed；放行必须留痕。
    run_p.add_argument("--allow-metadata-mismatch", action="store_true",
                       help="build metadata 不一致时放行（默认关闭）；"
                            "trace 记 metadata_mismatch=1, override=1；"
                            "CI=true 下还需 MTA_ALLOW_METADATA_MISMATCH_CI=1"
                            "（12.5 第二开关）")
    run_p.add_argument("--bundle-id", metavar="BID",
                       help="被测 App bundle id（真机路径必填，12.5 身份"
                            "读取用；如 com.phaset0.logindemo）")
    run_p.add_argument("--udid", metavar="UDID",
                       help="目标模拟器 UDID（默认自动发现 booted 设备）")
    run_p.add_argument("--metadata", metavar="PATH",
                       help="12.3 source_metadata.json 路径（默认 "
                            "<generated>/source_metadata.json 或 "
                            "repository/generated/local/）")
    # P2-3（review_m3_task31）核销：run 接 --generated，运行时 Repository
    # 从「overrides 兼职 generated」升为双源合并（5.3 语义）。
    run_p.add_argument("--generated", metavar="DIR",
                       help="generated 根目录（5.3 双源合并：overrides > "
                            "generated；同时作为 build identity 的 metadata"
                            " 默认来源）")
    run_p.add_argument("--allow-production", action="store_true",
                       help="允许 production 环境（10.1 总闸；HIGH/CRITICAL 仍拦）")
    run_p.add_argument("--config", metavar="PATH", default="mta.yaml",
                       help="mta.yaml 配置（15 节）")
    run_p.add_argument("--suites-root", metavar="DIR", default="suites",
                       help="用例 YAML 根目录（默认 ./suites）")
    run_p.add_argument("--overrides", metavar="DIR",
                       help="repository overrides 根目录")
    run_p.add_argument("--db", metavar="PATH", default="out/trace.db",
                       help="TraceStore SQLite 路径")
    run_p.add_argument("--fake-driver", action="store_true",
                       help="不连真机，组件注入最小桩（开发/CI 冒烟）")

    # --- mta repo（M3：Repository 管理；generate 为 P1-09 第一子命令）---
    repo_p = sub.add_parser("repo", help="Repository 管理子命令（M3）")
    repo_sub = repo_p.add_subparsers(dest="repo_command")
    gen_p = repo_sub.add_parser(
        "generate",
        help="扫描源码 → repository/generated/<build>/source_metadata.json"
             "（12.3/12.4；H16：generated 不手改，只能本命令重新生成）")
    gen_p.add_argument("sources", nargs="+", metavar="FILE",
                       help="源文件/目录（.swift/.m/.h；目录递归 rglob）")
    gen_p.add_argument("--out", metavar="DIR",
                       default="repository/generated",
                       help="generated 根目录（默认 repository/generated）")
    gen_p.add_argument("--build", metavar="ID", default="local",
                       help="build 标识（落 generated/<build>/，默认 local）")
    gen_p.add_argument("--app-version", metavar="V", default="debug")
    gen_p.add_argument("--check", action="store_true",
                       help="生成后立即跑一致性 Gate（overrides vs generated，"
                            "12.6）；缺口非空 → exit 3")
    gen_p.add_argument("--overrides", metavar="DIR",
                       default="repository/overrides",
                       help="--check 比对的手写 overrides 根目录"
                            "（默认 repository/overrides）")

    # --- mta source（M3 Source Intelligence：coverage 12.7 / diff 12.6）---
    src_p = sub.add_parser(
        "source", help="源码情报子命令（M3：coverage / diff）")
    src_sub = src_p.add_subparsers(dest="source_command")
    cov_p = src_sub.add_parser(
        "coverage",
        help="Source Coverage 报告（12.7）：用例引用的 identifier 有多少被 "
             "metadata 解析（literal+constant）")
    cov_p.add_argument("--metadata", metavar="PATH",
                       help="source_metadata.json（12.3/12.4 产物）")
    cov_p.add_argument("--suites-root", metavar="DIR",
                       help="用例根目录（默认 ./suites）")
    cov_p.add_argument("--json", metavar="PATH",
                       help="指标 JSON 落盘（默认 out/source_coverage.json）")
    cov_p.add_argument("--html", metavar="PATH",
                       help="HTML 覆盖率报告（14.5 报告页的 coverage 段）")
    cov_p.add_argument("--strict", action="store_true",
                       help="把 dynamic 也算作未达标（exit 3）。默认与 Gate M3 "
                            "G6 判据一致：只对 unknown/ambiguous/missing 卡 "
                            "exit 3——dynamic 是 12.2 预期形态（插值不猜值），"
                            "判红会逼人猜值")
    dif_p = src_sub.add_parser(
        "diff", help="Build-level source diff（12.6）：比两个 build 的 "
                     "source_metadata.json，范围仅限用例实际到达的 Screen")
    dif_p.add_argument("--old", metavar="PATH", required=True,
                       help="基线 build 的 source_metadata.json")
    dif_p.add_argument("--new", metavar="PATH",
                       help="新 build 的 source_metadata.json"
                            "（默认 repository/generated/local/"
                            "source_metadata.json）")
    dif_p.add_argument("--suites-root", metavar="DIR", default="suites",
                       help="用例根目录（决定 diff 范围，默认 ./suites）")
    dif_p.add_argument("--json", metavar="PATH",
                       help="diff 结果 JSON 落盘")
    dif_p.add_argument("--fail-on-drift", action="store_true",
                       help="REMOVED 非空也返回非零（默认不阻塞：12.6 "
                            "「独立任务，不阻塞日常回归」）")

    # --- mta review（9.5 / Task 4.2；H15：只导出补丁，绝不自动写入） ---
    review_p = sub.add_parser(
        "review", help="人工确认 RECOVERED（9.5；accept 只导出 overrides "
                       "补丁供人工合入，工具不写 Repository——H15）")
    rev_sub = review_p.add_subparsers(dest="review_cmd", required=True)
    rev_list = rev_sub.add_parser("list", help="列出待确认恢复")
    rev_list.add_argument("--db", metavar="PATH", default="out/trace.db",
                          help="TraceStore SQLite 路径（默认 out/trace.db）")
    rev_list.add_argument("--status", default="PENDING",
                          choices=["PENDING", "ACCEPT", "REJECT", "ALL"],
                          help="按状态过滤（默认 PENDING）")
    rev_acc = rev_sub.add_parser("accept", help="确认恢复 → 导出 overrides 补丁")
    rev_acc.add_argument("review_id", type=int)
    rev_acc.add_argument("--db", metavar="PATH", default="out/trace.db")
    rev_acc.add_argument("--reviewer", metavar="NAME",
                         default=None, help="确认人（缺省取 $USER）")
    rev_acc.add_argument("--note", metavar="TEXT", default=None)
    rev_acc.add_argument("--out", metavar="PATH",
                         help="补丁落盘路径（缺省 stdout；H15：写 Repository "
                              "由人工完成）")
    rev_rej = rev_sub.add_parser("reject", help="拒绝恢复")
    rev_rej.add_argument("review_id", type=int)
    rev_rej.add_argument("--db", metavar="PATH", default="out/trace.db")
    rev_rej.add_argument("--reviewer", metavar="NAME", default=None)
    rev_rej.add_argument("--note", metavar="TEXT", required=True,
                         help="拒绝理由必填（triage 数据）")

    # --- 占位子命令（后续任务填充） ---
    for name, help_text in (
        ("report", "生成 HTML/JUnit 报告（M2）"),
    ):
        sub.add_parser(name, help=help_text)

    return parser


def _load_repository(args: argparse.Namespace) -> Repository:
    overrides = getattr(args, "overrides", None)
    generated = getattr(args, "generated", None)
    if overrides is None and generated is None:
        # 默认布局：{cwd}/repository/overrides
        default = Path("repository/overrides")
        overrides = str(default) if default.exists() else None
    return Repository.from_dirs(generated_root=generated, overrides_root=overrides)


def cmd_lint(args: argparse.Namespace) -> int:
    repo = _load_repository(args)
    secrets = EnvSecretProvider()
    testcases: list = []
    # R7-1：文件级错误（缺文件/坏 YAML）是前置配置错误 → exit 3（8.4 语义：
    # exit 1 只留给「存在 FAIL」，不能被加载失败占用误导 CI 判读）。
    for path in args.testcases:
        try:
            data = yaml.safe_load(Path(path).read_text())
        except FileNotFoundError:
            print(f"ERROR load_failure: testcase file not found: {path}")
            print("lint: exit 3")
            return 3
        except yaml.YAMLError as exc:
            print(f"ERROR load_failure: invalid YAML in {path}: {exc}")
            print("lint: exit 3")
            return 3
        if isinstance(data, list):
            testcases.extend(data)
        else:
            testcases.append(data)
    issues = lint(testcases, repo, secrets)
    for i in issues:
        prefix = "ERROR" if i.severity is Severity.ERROR else "WARN "
        print(f"{prefix} {i.code}: {i.message}")
    if not issues:
        print("lint: OK (0 issues)")
    # R7-3：退出码只经 max_severity 一处判定（不与 lint_exit_code 重复实现）。
    code = 3 if max_severity(issues) is Severity.ERROR else 0
    print(f"lint: exit {code}")
    return code


def cmd_run(args: argparse.Namespace) -> int:
    """Task 2.6：新管线真实接线（R15-2 核销）+ JUnit/HTML 产物。

    退出码走 8.4 全表（compute_exit_code）。前置错误（发现失败/坏 YAML/
    lint ERROR/组件未装配）→ 3；运行结果按 RunResult.exit_code。
    """
    import uuid

    from cli.pipeline import PipelineDeps, SessionPipeline
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    # 0. lint 前置（P3-7：8.4 exit 3 语义包含 lint ERROR——带病用例不进 run）
    from repository.resolver import Severity
    from testcase.lint import lint as lint_cases, max_severity

    pipeline = SessionPipeline(suites_root=args.suites_root)
    try:
        cases = pipeline.discover(suite=args.suite, tag=args.tag,
                                  case=args.case)
    except SessionPipeline.PreflightError as e:
        print(f"PREFLIGHT ERROR: {e}")
        print("run: exit 3")
        return 3
    repo = _load_repository(args)
    # Task 4.2：RecoveryEngine 装配。LLM_API_KEY 缺省 = 无 LLM（确定性半边
    # 照常工作）；--no-llm 显式禁用（R18-4 语义就此定档：flag 真实生效，
    # 不再是「参数存在=功能存在」）。budget 默认值即 10.5。
    from agent.recovery import RecoveryEngine
    from llm.budget import LLMBudget
    llm = budget = None
    if not args.no_llm and os.environ.get("LLM_API_KEY"):
        from llm.provider import LLMProvider
        llm = LLMProvider()
        budget = LLMBudget()
    pipeline.recovery = RecoveryEngine(repo=repo, llm=llm, budget=budget)
    issues = lint_cases(cases, repo, EnvSecretProvider())
    for i in issues:
        prefix = "ERROR" if i.severity is Severity.ERROR else "WARN "
        print(f"{prefix} {i.code}: {i.message}")
    if max_severity(issues) is Severity.ERROR:
        print("run: lint ERROR → exit 3")
        return 3

    # R16-2/P3-3：run_id 用 uuid——同秒碰撞会覆盖 TraceStore runs 主键。
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    store = TraceStore(args.db)

    # 1. Build Identity（12.5 / Task 3.3 第 1 步）：真机路径启动前
    #    fail-closed 校验（G8：App/Metadata/commit 可关联）。--fake-driver
    #    无设备身份可读，跳过且不宣称校验过（H18 边界）。拦截/放行均落
    #    runs 审计列（metadata_mismatch / metadata_mismatch_override）。
    from source import build_identity as bi

    bi_fields: dict[str, object] = {}
    if not args.fake_driver:
        try:
            udid = args.udid or bi.resolve_booted_udid()
            if not udid:
                raise bi.BuildIdentityError(
                    "无 booted 模拟器（用 --udid 显式指定或启动一个）")
            meta_path = (Path(args.metadata) if args.metadata else
                         Path(args.generated or "repository/generated/local")
                         / "source_metadata.json")
            gr = bi.gate_run_start(
                metadata_path=meta_path, udid=udid,
                bundle_id=args.bundle_id,
                allow=args.allow_metadata_mismatch, env=os.environ)
        except bi.BuildIdentityError as e:
            store.start_run(run_id, suite=args.suite)
            store.end_run(run_id, status="ABORTED", exit_code=3)
            print(f"PREFLIGHT ERROR: build identity 读取失败：{e}")
            print("run: exit 3")
            return 3
        bi_fields = {
            "app_git_commit": gr.app.git_commit,
            "metadata_git_commit": gr.meta.git_commit,
            "metadata_build": gr.meta.build,
            "metadata_mismatch": int(gr.mismatch),
            "metadata_mismatch_override": int(gr.override),
        }
        if gr.blocked:
            store.start_run(run_id, suite=args.suite, **bi_fields)
            store.end_run(run_id, status="ABORTED", exit_code=3)
            print(f"BUILD_METADATA_MISMATCH: {', '.join(gr.mismatches)}")
            print(f"  app={gr.app.git_commit}/{gr.app.build}  "
                  f"metadata={gr.meta.git_commit}/{gr.meta.build}")
            print(f"  （12.5 fail-closed；本地放行：--allow-metadata-mismatch；"
                  f"CI 需 {bi.CI_OVERRIDE_ENV}=1）")
            print("run: exit 3")
            return 3
        if gr.mismatch:
            print(f"note: build identity 不一致已放行"
                  f"（{', '.join(gr.mismatches)}）——trace 记 "
                  "metadata_mismatch=1, override=1")

    # 2. 组件装配（fake-driver：最小桩；真机：Appium 会话，Task 2.7 接线）
    store.start_run(run_id, suite=args.suite, **bi_fields)
    if args.fake_driver:
        from executor.guard import EnvKind, Guard

        class _StubEx:
            def find(self, strategies):
                # Executor 契约：返回单个元素；多匹配抛 AmbiguousElement
                return _StubElement()

            def perform(self, action, element, value=None):
                pass

            def swipe(self, direction):
                pass

        class _StubElement:
            """wait/assertion 引擎对元素的最小契约：可见、可用。"""

            def is_displayed(self):
                return True

            def is_enabled(self):
                return True

            @property
            def text(self):
                return ""

            def get_attribute(self, name):
                return ""

        class _StubDS:
            def ensure_alive(self):
                pass

        ex = _StubEx()
        runner = StepRunner(ex, _StubDS(),
                            Guard(EnvKind.SANDBOX), run_id=run_id)
    else:
        # R16-2：真机组件未装配是前置配置错误 → exit 3，fail-loud。
        # 原实现继续执行，5 条 NoneType AttributeError 伪装成 FAIL + exit 1，
        # 且假终态写进了 trace.db（8.2 纪律：配置错误不得伪装成测试失败）。
        store.end_run(run_id, status="ABORTED", exit_code=3)
        print("PREFLIGHT ERROR: 真机组件未装配（Task 2.7 接线）——"
              "当前请使用 --fake-driver")
        print("run: exit 3")
        return 3

    pipeline.store = store
    pipeline.deps = PipelineDeps(env=None)  # app 级动作走 pipeline 内建桩
    pipeline._step_runner = runner
    # 9.3-4：LLM 候选的 Guard 复检与动作步同一实例（10.1：Guard 不受 LLM
    # 输出影响——同一策略对象才保证这一点）
    pipeline.recovery.guard = runner.guard
    lifecycle = Lifecycle(store=store)
    pipeline._lifecycle = lifecycle
    pipeline._run_id = run_id

    # 2. 跑（P3-5：统一走 run_all——H10 中止语义/未执行清单只在套件层可达）
    run = pipeline.run_all(cases, run_id=run_id)

    # 3. --no-llm 语义（R18-4 最终定档，Task 4.2）：flag 真实禁用 LLM
    #    Recovery；确定性恢复（settle/postcondition/memo）不受影响。
    #    LLM 调用数取 budget 实数（报告 LLM Invocation Rate 的分子）。
    llm_calls = budget.calls_used if budget is not None else 0
    if args.no_llm:
        print("note: --no-llm：LLM Recovery 已禁用（确定性恢复不受影响）")

    # 4. 产物
    if args.junit:
        from report.junit import write_junit
        write_junit(run, args.junit)
        print(f"junit: {args.junit}")
    if args.html:
        from report.html import write_run_report
        write_run_report(
            run, args.html, llm_calls=llm_calls,
            # R18-4/R17-3：分母是**步骤数**（lifecycle 累计），不是用例数
            executed_steps=lifecycle.steps_recorded,
            wda_restarts=0,
            # 10.5：熔断首页告警
            llm_broken=(budget.broken if budget is not None else False))
        print(f"html: {args.html}")

    for r in run.results:
        line = f"{r.testcase_id}: {r.status}"
        if r.failure_type:
            line += f" ({r.failure_type})"
        if r.detail.get("error"):
            line += f" -- {r.detail['error']}"
        print(line)
    print(f"run: exit {run.exit_code}")
    return run.exit_code


def cmd_repo(args: argparse.Namespace) -> int:
    """mta repo（M3）。`generate`：源码扫描 → 12.3 metadata（P1-09）。

    一致性 Gate（--check，12.6/plan Task 3.1 第 5 步）失败语义：缺口是
    **前置配置问题** → exit 3（与 lint ERROR 同档），不是 exit 1——生成成
    功但与人工 overrides 有 diff 要人来看，CI 不能当「工具坏了」。
    """
    if getattr(args, "repo_command", None) != "generate":
        print("usage: mta repo generate <FILE...> [--out DIR] [--build ID] "
              "[--check]")
        return 3
    from source.consistency import check, format_report
    from source.export import export_generated
    from source.metadata import ScanError, build_metadata

    # 目录参数递归展开（rglob 全部 .swift/.m/.h），排序保证确定性输出
    files: list[Path | str] = []
    for src in args.sources:
        p = Path(src)
        if p.is_dir():
            files.extend(sorted(
                f for ext in ("*.swift", "*.m", "*.h")
                for f in p.rglob(ext)))
        else:
            files.append(p)
    if not files:
        print(f"repo generate: no source files matched: {args.sources}")
        return 3

    out = Path(args.out) / args.build / "source_metadata.json"
    try:
        meta = build_metadata(files, out, app_version=args.app_version,
                              build=args.build)
    except ScanError as e:
        print(f"repo generate FAILED: {e}")
        return 3
    print(f"generated: {out} "
          f"({len(meta['screen_elements'])} screens, "
          f"{sum(len(s['elements']) for s in meta['screen_elements'])} elements)")

    # 桥接到 Repository 布局（12.3 → 5.1 elements/screens YAML）。
    # 这是 TODO(M3, R10-5.2) 的核心一步：generated 从此**可被 loader 消费**，
    # Repository 不再需要把 overrides 当 generated 用（4.1 resolve 的两源合并
    # 语义自此真正成立）。
    gen_dir = Path(args.out) / args.build
    counts = export_generated(meta, gen_dir)
    print(f"exported: {gen_dir}/elements + /screens "
          f"({counts['elements']} elements, {counts['screens']} screens, "
          f"origin: source)")

    if args.check:
        report = check(meta, args.overrides)
        print(format_report(report))
        return 0 if report.ok else 3
    return 0


def cmd_source(args: argparse.Namespace) -> int:
    """`mta source coverage`（12.7）：用例 identifier 引用 vs metadata 解析率。

    退出码：metadata/suites 读不到 → 3（前置配置错误，fail-loud——绝不能把
    「没读到」显示成「覆盖率 0%」，那会把构建问题伪装成覆盖率问题，坑 10d
    同款纪律）；有 **unknown/ambiguous/missing** → 3；`--strict` 时 dynamic
    也算未达标。

    dynamic **默认不判红**（review P2-1）：它是 12.2 的预期形态（插值不猜
    值、人工登记实例），拿它卡 exit 会让本命令在设计预期的仓库上永远红，
    逼人去猜值——那是设计禁止的。判据与 Gate M3 的 G6 逐项对齐（同一份
    语义的两个消费点，不许一个卡一个不卡）。要卡就显式 `--strict`。
    """
    if getattr(args, "source_command", None) == "diff":
        return cmd_source_diff(args)
    if getattr(args, "source_command", None) != "coverage":
        print("usage: mta source coverage --metadata PATH "
              "[--suites-root DIR] [--json PATH] [--html PATH] [--strict]")
        return 3

    import json as _json

    from source.coverage import compute_coverage, format_report

    meta_path = Path(args.metadata or "repository/generated/local/"
                                       "source_metadata.json")
    suites_root = Path(args.suites_root or "suites")
    if not meta_path.is_file():
        print(f"PREFLIGHT ERROR: source metadata 不存在: {meta_path} "
              f"(先跑 mta repo generate)")
        print("source coverage: exit 3")
        return 3
    if not suites_root.is_dir():
        print(f"PREFLIGHT ERROR: suites root 不存在: {suites_root}")
        print("source coverage: exit 3")
        return 3

    meta = _json.loads(meta_path.read_text(encoding="utf-8"))
    # 复用 pipeline 的发现 + strict 解析（6.3）：坏 YAML / schema 不兼容在此
    # 抛 PreflightError，不能让覆盖率命令自己写第二套 YAML 读取逻辑。
    from cli.pipeline import SessionPipeline
    try:
        cases = SessionPipeline(suites_root=suites_root).discover()
    except SessionPipeline.PreflightError as e:
        print(f"PREFLIGHT ERROR: {e}")
        print("source coverage: exit 3")
        return 3

    print(f"inputs: metadata={meta_path} suites_root={suites_root}")
    report = compute_coverage(meta, cases)
    print(format_report(report))

    json_path = Path(args.json or "out/source_coverage.json")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        _json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8")
    print(f"json: {json_path}")
    if args.html:
        from report.html import write_source_coverage_report
        write_source_coverage_report(report, args.html)
        print(f"html: {args.html}")

    # 判据与 Gate M3 G6 同源（review P2-1）：dynamic 是 12.2 预期形态，
    # 默认不判红；unknown/ambiguous/missing 是真缺口，一律卡 exit 3。
    # **唯一豁免**是 screen 缺口——用例里 `screen:X` 引用未登记的 X 时
    # current_screen 永远判不出（13.2），但那是「页面还没做」的待办而非
    # 解析缺口，Gate G6 因此也不卡它；这里对齐，不自作主张加严。
    gaps = (report.unknown, report.ambiguous, report.missing)
    if args.strict:
        gaps = gaps + (report.dynamic,)
    n_gaps = sum(gaps)
    if n_gaps:
        print(f"source coverage: {n_gaps} 个 identifier 未被 metadata 解析"
              f"（unknown/ambiguous/missing"
              f"{' + dynamic(--strict)' if args.strict else ''}）"
              f"→ Gate 未达标")
        print("source coverage: exit 3")
        return 3
    if report.dynamic:
        # 豁免但**必须可见**（不失败 ≠ 不显示，consistency prefix_matched
        # 同纪律）：否则 dynamic 会在 CI 日志里彻底消失。
        print(f"note: {report.dynamic} 个 dynamic 未计入未达标"
              f"（12.2 插值不猜值，人工登记实例）——需要卡它请加 --strict")
    print("source coverage: exit 0")
    return 0


def cmd_source_diff(args: argparse.Namespace) -> int:
    """`mta source diff`（12.6 Build-level Reconciliation）。

    与 `repo generate --check` **不是一回事**（3.1 consistency 比的是
    generated-vs-overrides；这里比的是 old build metadata vs new build
    metadata），不能互相顶替。

    退出码（8.4）：路径/用例读不到 → 3（配置错误，fail-loud）；UNKNOWN
    非空 → 1（用例引用了不可判定的屏，必须人工确认——不阻塞回归但不能
    当通过）；REMOVED 默认 **0**（12.6「独立任务，不阻塞日常回归」），
    `--fail-on-drift` 时 → 1。
    """
    import json as _json

    from cli.pipeline import SessionPipeline
    from source.build_diff import diff_builds, format_report

    old_path = Path(args.old)
    new_path = Path(args.new or "repository/generated/local/"
                              "source_metadata.json")
    suites_root = Path(args.suites_root)
    for label, p in (("--old", old_path), ("--new", new_path)):
        if not p.is_file():
            print(f"PREFLIGHT ERROR: {label} metadata 不存在: {p}")
            print("source diff: exit 3")
            return 3
    if not suites_root.is_dir():
        print(f"PREFLIGHT ERROR: suites root 不存在: {suites_root} "
              f"（diff 范围由用例决定，缺了就没法算范围）")
        print("source diff: exit 3")
        return 3

    try:
        cases = SessionPipeline(suites_root=suites_root).discover()
    except SessionPipeline.PreflightError as e:
        print(f"PREFLIGHT ERROR: {e}")
        print("source diff: exit 3")
        return 3

    old = _json.loads(old_path.read_text(encoding="utf-8"))
    new = _json.loads(new_path.read_text(encoding="utf-8"))
    report = diff_builds(old, new, cases, fail_on_drift=args.fail_on_drift)
    print(f"inputs: old={old_path} new={new_path} "
          f"suites_root={suites_root}")
    print(format_report(report))

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps(report.to_dict(), indent=2,
                                 ensure_ascii=False), encoding="utf-8")
        print(f"json: {p}")

    if not report.ok:
        print("source diff: exit 1")
        return 1
    print("source diff: exit 0")
    return 0


def cmd_placeholder(args: argparse.Namespace) -> int:
    print(f"mta {args.command}: not implemented yet (planned for a later task)")
    return 2


def cmd_review(args: argparse.Namespace) -> int:
    """9.5 人工确认流程。accept 导出补丁（stdout 或 --out），任何路径都不
    写 repository/overrides（H15）。"""
    import getpass

    from agent.review import ReviewError, decide_review, \
        export_overrides_patch, list_reviews
    from tracer.storage import TraceStore

    store = TraceStore(args.db)
    reviewer = getattr(args, "reviewer", None) or getpass.getuser()
    try:
        if args.review_cmd == "list":
            rows = list_reviews(store, args.status)
            if not rows:
                print(f"review: no {args.status} items")
                return 0
            for r in rows:
                print(f"#{r['review_id']} [{r['review_status']}] "
                      f"recovery={r['recovery_id']} kind={r['kind']} "
                      f"target={r['expected_target']} -> "
                      f"{r['candidate_target']} (screen={r['screen']})")
                if r.get("note"):
                    print(f"    note: {r['note']}")
            return 0
        if args.review_cmd == "accept":
            decide_review(store, args.review_id, "ACCEPT", reviewer,
                          args.note)
            patch = export_overrides_patch(store, args.review_id, reviewer)
            if args.out:
                Path(args.out).write_text(patch, encoding="utf-8")
                print(f"review: #{args.review_id} ACCEPT；补丁已写 "
                      f"{args.out}（请人工合入 repository/overrides/，"
                      "工具不代写——H15）")
            else:
                print(patch, end="")
                print(f"# review: #{args.review_id} ACCEPT（补丁见上；"
                      "人工合入 Repository——H15）")
            return 0
        if args.review_cmd == "reject":
            decide_review(store, args.review_id, "REJECT", reviewer,
                          args.note)
            print(f"review: #{args.review_id} REJECT（note 已留痕）")
            return 0
        print(f"review: unknown subcommand {args.review_cmd!r}")
        return 2
    except ReviewError as e:
        print(f"REVIEW ERROR: {e}")
        return 3


def main(argv: Sequence | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "lint":
        return cmd_lint(args)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "repo":
        return cmd_repo(args)
    if args.command == "source":
        return cmd_source(args)
    if args.command == "review":
        return cmd_review(args)
    return cmd_placeholder(args)


if __name__ == "__main__":
    sys.exit(main())
