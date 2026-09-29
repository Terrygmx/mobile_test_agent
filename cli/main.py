"""mta CLI 骨架（设计 14.6，Task 1.3 / P1-01）。

子命令：lint（Task 1.3 完整实现）；run / review / report / repo 为占位，
按 plan Task 1.6+ 逐步填充。退出码表（8.4）：
  - lint：有 ERROR → 3；仅 WARNING / 干净 → 0；
  - 占位子命令 → 退出码 2（NotImplemented，避免误当成功）。
"""
from __future__ import annotations

import argparse
import sys
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

    # --- 占位子命令（Task 1.6+ 填充） ---
    for name, help_text in (
        ("run", "执行用例/套件（Task 1.6）"),
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


def cmd_placeholder(args: argparse.Namespace) -> int:
    print(f"mta {args.command}: not implemented yet (planned for a later task)")
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "lint":
        return cmd_lint(args)
    return cmd_placeholder(args)


if __name__ == "__main__":
    sys.exit(main())
