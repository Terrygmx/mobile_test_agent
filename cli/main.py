"""mta CLI 骨架（设计 14.6，Task 1.3 / P1-01）。

子命令：lint（Task 1.3 完整实现）；run / review / report / repo 为占位，
按 plan Task 1.6+ 逐步填充。退出码表（8.4）：
  - lint：有 ERROR → 3；仅 WARNING / 干净 → 0；
  - 占位子命令 → 退出码 2（NotImplemented，避免误当成功）。
"""
from __future__ import annotations

import argparse
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
    run_p.add_argument("--no-llm", action="store_true",
                       help="禁用 LLM recovery（调用数记 0，R15 P3-8）")
    run_p.add_argument("--junit", metavar="PATH",
                       help="输出 JUnit XML（8.5）")
    run_p.add_argument("--html", metavar="PATH",
                       help="输出 HTML 报告（14.5）")
    run_p.add_argument("--allow-metadata-mismatch", action="store_true",
                       help="build metadata 不匹配时放行（trace 记 override）")
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

    # --- 占位子命令（后续任务填充） ---
    for name, help_text in (
        ("review", "人工确认 RECOVERED（M2）"),
        ("report", "生成 HTML/JUnit 报告（M2）"),
        ("repo", "Repository 管理子命令（M3）"),
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

    退出码走 8.4 全表（compute_exit_code）。前置错误（发现失败/坏 YAML）
    → 3；运行结果按 RunResult.exit_code。
    """
    from cli.pipeline import PipelineDeps, SessionPipeline
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    # 1. 发现（前置错误 → 3）
    pipeline = SessionPipeline(suites_root=args.suites_root)
    try:
        cases = pipeline.discover(suite=args.suite, tag=args.tag,
                                  case=args.case)
    except SessionPipeline.PreflightError as e:
        print(f"PREFLIGHT ERROR: {e}")
        print("run: exit 3")
        return 3

    # 2. 组件装配（fake-driver：最小桩；真机：Appium 会话）
    run_id = f"run_{int(time.time())}"
    if args.fake_driver:
        from executor.guard import EnvKind, Guard

        class _StubEx:
            def find(self, strategies):
                # Executor 契约：返回单个元素；多匹配抛 AmbiguousElement
                return _StubElement()

            def perform(self, action, element, value=None):
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

        store = TraceStore(args.db)
        store.start_run(run_id, suite=args.suite)
        runner = StepRunner(_StubEx(), _StubDS(),
                            Guard(EnvKind.SANDBOX), run_id=run_id)
        lifecycle = Lifecycle(store=store)
        pipeline.store = store
    else:
        store = TraceStore(args.db)
        store.start_run(run_id, suite=args.suite)
        runner = None      # 真机组件装配在 M2 Gate（Task 2.7）接入
        lifecycle = Lifecycle(store=store)
        pipeline.store = store

    # 3. 跑
    results = []
    for tc in cases:
        results.append(pipeline.run_case(runner, lifecycle, tc,
                                         run_id=run_id))
    from runner.result import RunResult
    run = RunResult(run_id=run_id, suite=args.suite)
    for r in results:
        run.add(r)
    store.end_run(run_id, status=("PASS" if run.passed else "FAIL"),
                  exit_code=run.exit_code)

    # 4. 产物
    if args.junit:
        from report.junit import write_junit
        write_junit(run, args.junit)
        print(f"junit: {args.junit}")
    if args.html:
        from report.html import write_run_report
        write_run_report(run, args.html)
        print(f"html: {args.html}")

    for r in results:
        line = f"{r.testcase_id}: {r.status}"
        if r.failure_type:
            line += f" ({r.failure_type})"
        if r.detail.get("error"):
            line += f" -- {r.detail['error']}"
        print(line)
    print(f"run: exit {run.exit_code}")
    return run.exit_code


def cmd_placeholder(args: argparse.Namespace) -> int:
    print(f"mta {args.command}: not implemented yet (planned for a later task)")
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "lint":
        return cmd_lint(args)
    if args.command == "run":
        return cmd_run(args)
    return cmd_placeholder(args)


if __name__ == "__main__":
    sys.exit(main())
