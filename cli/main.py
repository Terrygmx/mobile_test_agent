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
from experience import DEFAULT_EXPERIENCE_DB
from graph import DEFAULT_GRAPH_DB
from repository.resolver import Repository, Severity
from testcase.lint import lint, max_severity
from tracer.storage import ATTRIBUTIONS


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
    # R18-4 → Task 4.2：--no-llm 已接线（真实禁用 LLM Recovery，确定性恢复
    # 不受影响；见 cmd_run 内 3. --no-llm 语义段）。
    run_p.add_argument("--no-llm", action="store_true",
                       help="禁用 LLM Recovery（确定性恢复不受影响；"
                            "LLM 调用数记 0）")
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
    # Task 2.4：Experience Store 库路径。默认 out/experience.db（与
    # trace.db 分库——前者可变状态单写者，后者 append-only 流水）。
    run_p.add_argument("--exp-db", metavar="PATH",
                       default=str(DEFAULT_EXPERIENCE_DB),
                       help="Experience Store 库路径（默认 out/experience.db）")
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
    # P2-2（review_m4_task43）：env kind 真实来源 + Guard 接线。P1 无
    # 配置文件解析，用旗标显式给（默认 sandbox = 历史行为不变）。
    run_p.add_argument("--env-kind", choices=["sandbox", "staging",
                                              "production"],
                       default="sandbox",
                       help="环境种类（10.1；落 runs.env_kind 审计列；"
                            "production 拒 HIGH/CRITICAL，需 --allow-production）")
    run_p.add_argument("--allow-production", action="store_true",
                       help="允许 --env-kind production 启动（10.1 总闸；"
                            "不给则 production 连 LOW 都不跑；"
                            "HIGH/CRITICAL 仍被 Guard 拦）")
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
    rev_acc = rev_sub.add_parser(
        "accept", help="确认恢复 → 导出 overrides 补丁 + 建 Experience "
                       "Candidate（P2 设计 8.1 / E5）")
    rev_acc.add_argument("review_id", type=int)
    rev_acc.add_argument("--db", metavar="PATH", default="out/trace.db")
    rev_acc.add_argument("--reviewer", metavar="NAME",
                         default=None, help="确认人（缺省取 $USER）")
    rev_acc.add_argument("--note", metavar="TEXT", default=None)
    rev_acc.add_argument("--out", metavar="PATH",
                         help="补丁落盘路径（缺省 stdout；H15：写 Repository "
                              "由人工完成）")
    rev_acc.add_argument("--exp-db", metavar="PATH",
                         default=str(DEFAULT_EXPERIENCE_DB),
                         help="Experience 库路径（ACCEPT 触发 create_candidate；"
                              "默认 out/experience.db）")
    rev_res = rev_sub.add_parser(
        "reseed",
        help="补种：已 ACCEPT 但没建出 Candidate 的 review → create_candidate"
             "（E5；P3-1 的产品出口——create_candidate 曾失败时唯一出路）")
    rev_res.add_argument("review_id", type=int)
    rev_res.add_argument("--db", metavar="PATH", default="out/trace.db")
    rev_res.add_argument("--exp-db", metavar="PATH",
                         default=str(DEFAULT_EXPERIENCE_DB))
    rev_rej = rev_sub.add_parser("reject", help="拒绝恢复")
    rev_rej.add_argument("review_id", type=int)
    rev_rej.add_argument("--db", metavar="PATH", default="out/trace.db")
    rev_rej.add_argument("--reviewer", metavar="NAME", default=None)
    rev_rej.add_argument("--note", metavar="TEXT", required=True,
                         help="拒绝理由必填（triage 数据）")

    # --- report（14.6；P3-4 triage 工具通道先行，报告页后续任务） ---
    rep_p = sub.add_parser("report", help="报告与 triage（14.6）")
    rep_sub = rep_p.add_subparsers(dest="report_cmd")
    rep_tri = rep_sub.add_parser(
        "triage", help="人工归因 FAIL 用例（R14-4 消费入口；结构化审计"
                       "记录落 detail_json.triage）")
    rep_tri.add_argument("tc_run_id", type=int,
                         help="testcase_runs.id（mta report list 待补，"
                              "现阶段从 trace.db 查）")
    rep_tri.add_argument("--attribution", required=True,
                         choices=sorted(ATTRIBUTIONS),
                         help="归因（8.2 枚举）")
    rep_tri.add_argument("--note", required=True,
                         help="归由理由必填（审计数据）")
    rep_tri.add_argument("--db", metavar="PATH", default="out/trace.db")

    # --- mta experience（设计 13 节；Task 3.4 / Gate M3） -----------------
    exp_p = sub.add_parser(
        "experience", help="Experience 库运维子命令（13 节：list/show/"
                           "verify/revalidate/sweep；promote 在 M4 加入）")
    exp_sub = exp_p.add_subparsers(dest="experience_cmd", required=True)

    def _add_exp_db(p: argparse.ArgumentParser) -> None:
        p.add_argument("--exp-db", metavar="PATH",
                       default=str(DEFAULT_EXPERIENCE_DB),
                       help="Experience SQLite 路径（默认 out/experience.db）")

    exp_list = exp_sub.add_parser("list", help="列出 Experience（含 REJECTED"
                                               "——审计视角）")
    _add_exp_db(exp_list)
    exp_list.add_argument("--status", default="ALL",
                          choices=["CANDIDATE", "VERIFIED", "DEGRADED",
                                   "REJECTED", "ALL"],
                          help="按状态过滤（默认 ALL）")

    exp_show = exp_sub.add_parser("show", help="单条详情：字段 + 样本历史 + "
                                               "状态时间线")
    exp_show.add_argument("experience_id")
    _add_exp_db(exp_show)
    exp_show.add_argument("--runs-limit", type=int, default=10,
                          help="样本历史条数（默认最近 10 条）")

    exp_verify = exp_sub.add_parser(
        "verify", help="对全部非终态 Experience 跑 Verifier 并产出决策报告；"
                       "PROMOTE/DEGRADE 决策落库（E4：资格经 Repository 解析"
                       "元素判定，解析不到 = 不合格）")
    _add_exp_db(exp_verify)
    exp_verify.add_argument("--generated", metavar="DIR", default=None)
    exp_verify.add_argument("--overrides", metavar="DIR", default=None,
                            help="Repository 来源（E4 资格解析用；缺省 "
                                 "repository/overrides）")

    exp_rev = exp_sub.add_parser(
        "revalidate", help="显式重验证通过 → VERIFIED(REVALIDATED)。"
                           "只接受 DEGRADED（3.1 护栏）；--fingerprint 是"
                           "验证证据（E8 观测更新）")
    exp_rev.add_argument("experience_id")
    _add_exp_db(exp_rev)
    exp_rev.add_argument("--fingerprint", required=True,
                         help="重验证时观测到的屏指纹（必填非空——没有证据的"
                              "重验证不可留痕）")
    exp_rev.add_argument("--evidence-run-id", metavar="RUN_ID", default=None,
                         help="可选：本次重验证对应的 trace run id"
                              "（把证据挂到真实运行，增强留痕）")
    exp_rev.add_argument("--operator", metavar="NAME", default=None)

    exp_sweep = exp_sub.add_parser(
        "sweep", help="清理过期 Candidate（8.2：STALE）；只改状态不删证据")
    _add_exp_db(exp_sweep)
    exp_sweep.add_argument("--max-idle-days", type=int, default=90,
                           help="闲置天数阈值（默认 90，8.2 设计值）")

    exp_promote = exp_sub.add_parser(
        "promote", help="Promotion（9.1–9.3）：缺省生成 PENDING proposal"
                        "（人工审计，不写文件）；--approve 落地 = 写 "
                        "overrides（origin: experience）+ git commit + "
                        "promoted 记账。撤销 = git revert（E10）")
    exp_promote.add_argument("experience_id")
    _add_exp_db(exp_promote)
    exp_promote.add_argument("--approve", metavar="PROPOSAL_ID",
                             default=None,
                             help="approve 已生成的 proposal（两段式的"
                                  "第二段；只有 PENDING 可 approve）")
    exp_promote.add_argument("--manual-override", action="store_true",
                             help="9.5：非 VERIFIED 的显式人工路径"
                                  "（必须配 --reason）")
    exp_promote.add_argument("--reason", metavar="TEXT", default=None,
                             help="9.5 人工越权理由（必填当 "
                                  "--manual-override）")
    exp_promote.add_argument("--repo-root", metavar="DIR", default=".",
                             help="git 仓库根（overrides 写在 <root>/"
                                  "repository/overrides/elements/；默认 cwd）")
    exp_promote.add_argument("--operator", metavar="NAME", default=None)

    # --- mta graph（设计 12 / 13 节；Task 5.3） ---------------------------
    gph_p = sub.add_parser(
        "graph", help="UI State Graph 子命令（13 节：build/diff/show）")
    gph_sub = gph_p.add_subparsers(dest="graph_cmd", required=True)

    def _add_graph_scope(p: argparse.ArgumentParser) -> None:
        """三个子命令共用的范围参数（graph 库 + app_id + build）。

        `app_id` 默认取 `--bundle-id` 的仓库默认值；build 见各自的 help。
        """
        p.add_argument("--graph-db", metavar="PATH",
                       default=str(DEFAULT_GRAPH_DB),
                       help="Graph SQLite 路径（默认 out/graph.db）")
        p.add_argument("--bundle-id", metavar="ID", default="",
                       help="App bundle id（图的范围第一段；源图侧 metadata "
                            "不含 bundle id，必须显式给）")

    gph_build = gph_sub.add_parser(
        "build", help="建图并落库：--from-trace 建运行时图 / --from-source "
                      "建源图（可同批）")
    _add_graph_scope(gph_build)
    gph_build.add_argument("--db", metavar="PATH", default="out/trace.db",
                           help="TraceStore SQLite 路径（`--from-trace` 的"
                                "默认值；与 `mta run --db` 同默认）")
    gph_build.add_argument("--from-trace", metavar="PATH", default=None,
                           help="trace 库路径（默认 --db）")
    gph_build.add_argument("--from-source", metavar="PATH", default=None,
                           help="source_metadata.json 路径（默认 "
                                "<generated>/source_metadata.json）")
    gph_build.add_argument("--build", metavar="ID", default=None,
                           help="限定/指定 build id（运行时侧默认取 trace 里的"
                                "范围；源图侧默认取 metadata 的 build）")

    gph_diff = gph_sub.add_parser(
        "diff", help="五类差异（设计 12.2）：给 --base-build 则 build-to-build"
                     "（12.3），否则「源图 vs 运行时图」")
    _add_graph_scope(gph_diff)
    gph_diff.add_argument("--build", metavar="ID", required=True,
                          help="当前面 build")
    gph_diff.add_argument("--base-build", metavar="ID", default=None,
                          help="参照面 build（给了即 build-to-build）")
    gph_diff.add_argument("--save", action="store_true",
                          help="把差异落 graph_diffs 表（重跑按范围整体替换）")

    gph_show = gph_sub.add_parser("show", help="打印库里的图（范围 + 节点 + 转移）")
    _add_graph_scope(gph_show)
    gph_show.add_argument("--build", metavar="ID", default=None)

    return parser


def _resolve_app_build(args: argparse.Namespace) -> str:
    """本次 run 的 build id（12.5 / E7 的 `validated_builds` 用它）。

    **单一真值源**：12.3 metadata 的顶层 `build` 字段（`mta repo generate
    --build` 写下的构建身份），经 `source.build_identity.metadata_identity`
    读取——不自己解析 JSON（那是第二套读取逻辑）。

    读不到就退回 `DEFAULT_APP_BUILD`（"local"），**不 fail-loud**：
    `--fake-driver`、单元测试、没跑过 `repo generate` 的场景没有 metadata
    是常态，退回即既有行为。build id 缺失不该拦住一次 run——它不是安全
    判据，只是 E7 集合的一个元素。
    """
    from source import build_identity as bi

    meta_path = (Path(args.metadata) if getattr(args, "metadata", None)
                 else Path(getattr(args, "generated", None)
                           or "repository/generated/local")
                 / "source_metadata.json")
    try:
        # 解析规则与兜底都在 `build_identity.resolve_app_build`（单一入口）
        # ——源图侧（`build_source_graph`）用的是同一个函数，两面 scope 因此
        # 由构造保证一致（review_p2_task52 P3-1）。
        return bi.resolve_app_build(bi.read_metadata(meta_path))
    except Exception:  # noqa: BLE001 — 读不到/坏 JSON 都退回默认
        return bi.DEFAULT_APP_BUILD


def _collect_metrics_if_any(args: argparse.Namespace, pipeline):
    """报告用的 Experience 指标（Task 4.3 / 设计 17）。

    **库文件不存在就不构造 Store**：那说明本次 run 从没碰过经验库（引擎连
    `lookup` 都没发过），此时没有任何东西可报，而构造 Store 会在磁盘上留下
    一个空库文件——「失败却产生新文件」同款的最小副作用纪律
    （review_p2_task23 P3-6）。返回 None 时报告不带指标段。
    """
    if not Path(args.exp_db).exists():
        return None
    from report.experience_metrics import collect_experience_metrics
    return collect_experience_metrics(
        trace_db=args.db,
        experience_store=pipeline.recovery.experience_store)


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
    from executor.guard import EnvKind
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    # P2-2（review_m4_task43）：--env-kind 真实接线（10.1）。production
    # 启动必须显式 --allow-production——最前置 fail-loud，不带病建 run 行。
    env_kind = EnvKind(getattr(args, "env_kind", None) or "sandbox")
    if env_kind is EnvKind.PRODUCTION and not args.allow_production:
        print("PREFLIGHT ERROR: --env-kind production 需要显式 "
              "--allow-production（10.1 总闸；production 下 HIGH/CRITICAL "
              "仍被 Guard 拦）")
        print("run: exit 3")
        return 3
    # review_m4_task43 P2-1：lint 与运行时解析共用一个 Provider 实例——
    # ${VAR} 分派前经它解析（14.4 Runner 层落点=管线 ctx 装配处）。
    secrets = EnvSecretProvider()

    # 0. lint 前置（P3-7：8.4 exit 3 语义包含 lint ERROR——带病用例不进 run）
    from repository.resolver import Severity
    from testcase.lint import lint as lint_cases, max_severity

    # Task 5.1：本次 run 的 build id 在这里算一次，**两个消费者共用**——
    # pipeline（E7 validated_builds）与 runs 审计列（`runs.app_build`）。
    # 后者此前从未被写入（P1 遗留：build identity 只记 metadata_build），
    # 而 M5 的 build-to-build diff（设计 12.3）必须按 build 分图——没有它
    # 所有真实 run 的图都会落进同一个空 scope。
    app_build = _resolve_app_build(args)
    pipeline = SessionPipeline(suites_root=args.suites_root, secrets=secrets,
                               app_id=args.bundle_id or "",
                               app_build=app_build)
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
    # Task 2.4：Experience Store 接真（设计 7.1）。惰性构造——库文件在首次
    # 真正查询/写入时才出现。**注意别把「惰性」读成「--no-llm 就不建库」**：
    # Experience 命中本来就不需要 LLM，所以 --no-llm 的 run 恰恰会查经验库
    # （Gate M2 的 G6 实测 stages 里有 `miss`，即查过）。真正不建库的是
    # 「恢复流水线根本没进」（不可恢复 / 未接 store）的 run
    # （Task 2.4 评审 P3-2：此处注释曾写成「--no-llm 之类不碰经验」）。
    from agent.recovery import RecoveryEngine
    from llm.budget import LLMBudget
    llm = budget = None
    if not args.no_llm and os.environ.get("LLM_API_KEY"):
        from llm.provider import LLMProvider
        llm = LLMProvider()
        budget = LLMBudget()
    # Task 4.3 缓存接线（设计 7.3 / plan Task 3.3 接线定档）：进程内 LRU，
    # run 级生命周期（每个 `mta run` 一个新实例，跨 run 不共享——缓存不是
    # 权威数据，进程重启即空）。命中只省 Store 的磁盘 lookup，Guard 与记账
    # 走同一条代码（E1 延伸）；`validated_builds` 的裁决机制已拍板为「不检查」
    # （设计 §7.3 修订记录），接线不得新增 build 检查步骤。
    from experience.cache import RecoveryCache
    pipeline.recovery = RecoveryEngine(
        repo=repo, llm=llm, budget=budget, cache=RecoveryCache(),
        experience_store=_LazyExperienceStore(args.exp_db))
    issues = lint_cases(cases, repo, secrets)
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
    # Task 5.1：本次被测构建**两条路径都记**（fake-driver 也记——它同样是
    # 「哪一次构建」，只是没有设备身份可校验）。M5 的 build-to-build diff
    # （设计 12.3）按 build 分图，缺了这一列所有 run 的图都会落进空 scope。
    bi_fields["app_build"] = app_build
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
            store.start_run(run_id, suite=args.suite,
                            env_kind=env_kind.value)
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
            store.start_run(run_id, suite=args.suite, env_kind=env_kind.value,
                            **bi_fields)
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
    from executor.guard import EnvKind, Guard

    # review_p2_task11 P3-2：app_bundle_id 落 runs 行——Candidate 主键
    # 第一段（app_id, screen_id, target_id），种子清单不能是 NULL。
    if args.bundle_id:
        bi_fields.setdefault("app_bundle_id", args.bundle_id)
    store.start_run(run_id, suite=args.suite, env_kind=env_kind.value,
                    **bi_fields)
    if args.fake_driver:
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
        runner = StepRunner(ex, _StubDS(), Guard(env_kind), run_id=run_id)
    else:
        # Task 2.7 接线（M4 Gate 前置）：真机 Appium 会话组件装配。
        # caps 由 simctl 解析（M2/F5 时代是脚本内硬编码）；Guard 用
        # --env-kind（P2-2 接线，默认 sandbox——production 拦截语义
        # 见矩阵 #22 与 --allow-production）；DeviceSession 不挂
        # TraceStore 做 recorder（record_infra 契约不匹配——infra 落库
        # 由 pipeline 负责，JSONL 留作 debug 副本）。装配失败 = 前置配置
        # 错误 → exit 3（fail-loud）。
        from environment.manager import EnvironmentManager
        from executor.executor import Executor
        from session.app_session import AppSession
        from session.device_session import (DeviceSession,
                                            resolve_local_caps)

        udid = args.udid or bi.resolve_booted_udid()
        bundle_id = args.bundle_id or ""
        if not bundle_id:
            store.end_run(run_id, status="ABORTED", exit_code=3)
            print("PREFLIGHT ERROR: 真机路径需要 --bundle-id")
            print("run: exit 3")
            return 3
        try:
            appium_url = os.environ.get("APPIUM_URL",
                                        "http://127.0.0.1:4723")
            ds = DeviceSession(appium_url, resolve_local_caps(udid, bundle_id))
            ds.connect()
        except Exception as e:
            store.end_run(run_id, status="ABORTED", exit_code=3)
            print(f"PREFLIGHT ERROR: 设备会话建立失败：{e}")
            print("run: exit 3")
            return 3
        app = AppSession(ds, bundle_id)
        env = EnvironmentManager(app)
        ex = Executor(ds)
        runner = StepRunner(ex, ds, Guard(env_kind), run_id=run_id)

    pipeline.store = store
    if args.fake_driver:
        pipeline.deps = PipelineDeps(env=None)  # app 级动作走 pipeline 内建桩
    else:
        # 真机 deps（M2 Gate 同链路）：EnvironmentManager 真做
        # precondition.reset / cleanup（R16-4）。
        pipeline.deps = PipelineDeps(env=env, repo=repo, app=app,
                                     device_session=ds, executor=ex,
                                     store=store)
    pipeline._step_runner = runner
    # 9.3-4：LLM 候选的 Guard 复检与动作步同一实例（10.1：Guard 不受 LLM
    # 输出影响——同一策略对象才保证这一点）
    pipeline.recovery.guard = runner.guard
    lifecycle = Lifecycle(store=store)
    pipeline._lifecycle = lifecycle
    pipeline._run_id = run_id
    if not args.fake_driver:
        # H7 闭环：非幂等动作的 postcondition 真实执行（checker 复用
        # WaitEngine 条件矩阵，M2 Gate 同款）。
        from cli.pipeline import make_postcondition_checker
        runner.postcondition_checker = make_postcondition_checker(
            pipeline, runner)

    # 2. 跑（P3-5：统一走 run_all——H10 中止语义/未执行清单只在套件层可达）
    try:
        run = pipeline.run_all(cases, run_id=run_id)
    finally:
        if not args.fake_driver and ds.driver is not None:
            try:
                ds.driver.quit()
            except Exception:
                pass  # 会话收尾失败不影响结果判定

    # 3. --no-llm 语义（R18-4 最终定档，Task 4.2）：flag 真实禁用 LLM
    #    Recovery；确定性恢复（settle/postcondition/memo）不受影响。
    #    LLM 调用数取 budget 实数（报告 LLM Invocation Rate 的分子）。
    llm_calls = budget.calls_used if budget is not None else 0
    store.update_run_llm(run_id, llm_calls=llm_calls,
                         llm_enabled=budget is not None)
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
            llm_broken=(budget.broken if budget is not None else False),
            # Task 4.3 / 设计 17：Experience 指标段（见 _collect_metrics_if_any）
            experience_metrics=_collect_metrics_if_any(args, pipeline))
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


def cmd_report(args: argparse.Namespace) -> int:
    """14.6 report 组。triage（P3-4）：FAIL 用例人工归因的工具入口——
    走 TraceStore.triage_testcase（结构化审计记录落 detail_json.triage），
    不再裸 SQL 补写 trace。"""
    from tracer.storage import TraceStore

    if getattr(args, "report_cmd", None) != "triage":
        print("usage: mta report triage <tc_run_id> --attribution X "
              "--note \"...\" [--db PATH]")
        return 2
    store = TraceStore(args.db)
    try:
        store.triage_testcase(args.tc_run_id, args.attribution, args.note)
    except ValueError as e:
        print(f"TRIAGE ERROR: {e}")
        return 3
    except LookupError as e:
        print(f"TRIAGE ERROR: {e}")
        return 3
    print(f"triage: testcase_run {args.tc_run_id} → {args.attribution}"
          f"（审计记录已落 detail_json.triage）")
    return 0


class _LazyExperienceStore:
    """惰性 Experience Store（review_p2_task23 P3-6）。

    `review accept` 的 exp store 若在校验之前构造，种子链不全的失败路径也会
    在磁盘上留下一个空库文件——「失败却产生新文件」违反最小副作用。本代理
    把构造推迟到**首次真正使用**（即校验已通过、要写 Candidate 时）。

    只转发属性访问，不缓存接口形状：Store 加方法无需改本类。
    """

    def __init__(self, db_path):
        self._db_path = db_path
        self._impl = None

    def __getattr__(self, name):
        if self._impl is None:
            from experience import SQLiteExperienceStore
            self._impl = SQLiteExperienceStore(self._db_path)
        return getattr(self._impl, name)


def cmd_review(args: argparse.Namespace) -> int:
    """9.5 人工确认流程。accept 导出补丁（stdout 或 --out），任何路径都不
    写 repository/overrides（H15）。

    P2（设计 8.1 / E5）：accept 同时是 Experience Candidate 的**唯一入口**
    ——状态流转后立刻 `create_candidate`（ACCEPT 的全部消费前置条件在状态
    变更前一次校验，不齐则 fail-loud、状态不变、退出码 3）；`reseed` 是
    「已 ACCEPT 但没建出 Candidate」的补种出口（P3-1）。
    """
    import getpass

    from agent.review import ReviewError, decide_review, \
        export_overrides_patch, list_reviews, seed_candidate
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
            exp_store = _LazyExperienceStore(args.exp_db)
            candidate = decide_review(store, args.review_id, "ACCEPT",
                                      reviewer, args.note,
                                      experience_store=exp_store)
            patch = export_overrides_patch(store, args.review_id, reviewer)
            if candidate is not None:
                print(f"experience: Candidate {candidate.experience_id} "
                      f"[{candidate.status.value}] "
                      f"app={candidate.app_id} screen={candidate.screen_id} "
                      f"target={candidate.target_id} "
                      f"strategy={candidate.strategy.value} "
                      f"(seed review #{candidate.seed_recovery_review_id})")
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
        if args.review_cmd == "reseed":
            exp_store = _LazyExperienceStore(args.exp_db)
            candidate = seed_candidate(store, exp_store, args.review_id)
            print(f"experience: Candidate {candidate.experience_id} "
                  f"[{candidate.status.value}] "
                  f"app={candidate.app_id} screen={candidate.screen_id} "
                  f"target={candidate.target_id} "
                  f"strategy={candidate.strategy.value} "
                  f"(seed review #{candidate.seed_recovery_review_id})")
            print(f"review: #{args.review_id} 补种完成（已 ACCEPT；"
                  "幂等——已存在的 Candidate 直接返回）")
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


def cmd_experience(args: argparse.Namespace) -> int:
    """设计 13 节 Experience 运维子命令（Task 3.4 / Gate M3）。

    职责边界：本命令组是**库的读/巡检/显式操作**入口——判定全部来自
    experience 包的纯函数（evaluate / eligible_for_auto_verification /
    is_stale），CLI 不复制任何判据；执行路径的 Guard 接线归引擎（Task 2.4），
    缓存接线归 Task 4.3（plan 定档）。

    E4 单点（review_p2_task31 P3-7 接线前置①）：`verify` 对 CANDIDATE 的
    资格必须经 Repository 解析出目标元素再调 `eligible_for_auto_verification`
    ——解析不到（无 Repository / 元素未登记）= 不合格（fail-closed），
    决策报告写明原因。非 CANDIDATE 状态 evaluate 不消费资格（VERIFIED 走
    E6 窗口、DEGRADED 等显式重验证），传 False 即「不适用」，非「不合格」。
    """
    from experience import (
        SQLiteExperienceStore,
        StalenessPolicy,
        apply_outcome,
        eligible_for_auto_verification,
        evaluate,
        revalidate as verifier_revalidate,
        sweep_stale_candidates,
    )
    from experience.models import ExperienceStatus, VerificationPolicy

    store = SQLiteExperienceStore(args.exp_db)

    if args.experience_cmd == "list":
        status = (None if args.status == "ALL"
                  else ExperienceStatus(args.status))
        exps = store.list(status)
        if not exps:
            print("experience list: no experiences")
            return 0
        for e in exps:
            print(f"{e.experience_id} [{e.status.value}] "
                  f"app={e.app_id} screen={e.screen_id} target={e.target_id} "
                  f"samples={e.sample_count} rate={e.success_rate:.2f} "
                  f"updated={e.updated_at.isoformat()}")
        return 0

    if args.experience_cmd == "show":
        exp = store.get_experience(args.experience_id)
        if exp is None:
            print(f"EXPERIENCE ERROR: no such experience: "
                  f"{args.experience_id}")
            return 3
        print(f"experience {exp.experience_id}")
        print(f"  status={exp.status.value} origin={exp.origin}")
        print(f"  app={exp.app_id} screen={exp.screen_id} "
              f"target={exp.target_id}")
        print(f"  strategy={exp.strategy.type}:{exp.strategy.value}")
        print(f"  samples={exp.sample_count} "
              f"(success={exp.success_count} failure={exp.failure_count}) "
              f"rate={exp.success_rate:.4f}")
        print(f"  validated_builds={exp.validated_builds} "
              f"fingerprint={exp.last_screen_fingerprint}")
        print(f"  promoted={exp.promoted} commit={exp.promoted_commit}")
        print(f"  created={exp.created_at.isoformat()} "
              f"updated={exp.updated_at.isoformat()}")
        runs = store.get_runs(exp.experience_id,
                              limit=args.runs_limit)
        if runs:
            print(f"  runs (latest {len(runs)}):")
            for r in runs:
                print(f"    {r.run_id} {r.result} build={r.app_build} "
                      f"guard={r.guard_reason} at={r.created_at.isoformat()}")
        events = store.get_state_events(exp.experience_id)
        if events:
            print("  state_events:")
            for ev in events:
                print(f"    {ev.from_status.value if ev.from_status else '-'}"
                      f"→{ev.to_status.value} {ev.reason} "
                      f"by={ev.operator} at={ev.created_at.isoformat()}")
        return 0

    if args.experience_cmd == "verify":
        exps = [e for e in store.list()
                if e.status is not ExperienceStatus.REJECTED]
        if not exps:
            print("experience verify: no experiences to verify")
            return 0
        repo = None
        repo_available = True
        try:
            repo = _load_repository(args)
        except Exception:  # noqa: BLE001 — 库不可用 → 全员 fail-closed
            repo_available = False
        from cli.pipeline import DEFAULT_APP_BUILD

        def _resolve_element(exp):
            """E4 资格的元素解析（review_p2_task31 P3-7 前置①）。

            先按 `{screen}.{target}` 限定名解析；失败回退**裸 target_id**
            再试一次（review_p2_task34 P3-1：P1 P3-6 的教训——container
            struct 名 ≠ marker 名，screen 归属分裂的 App 会被限定名静默
            挡在门外）。两次都失败才算 ELEMENT_UNRESOLVED（fail-closed）。
            """
            try:
                return repo.resolve(f"{exp.screen_id}.{exp.target_id}",
                                    build=DEFAULT_APP_BUILD)
            except Exception:  # noqa: BLE001 — 限定名失败 → 裸名重试
                return repo.resolve(exp.target_id, build=DEFAULT_APP_BUILD)

        counts: dict[str, int] = {}
        applied = 0
        for exp in exps:
            runs = store.get_runs(exp.experience_id)
            # 三态直传（review_p2_task34 P3-2）：False=不合格（fail-closed）、
            # None=不适用（非 CANDIDATE 分支不消费）——不压成 bool，
            # 将来 evaluate 若消费资格（如 revalidate 资格）二者可区分。
            eligible: bool | None = None
            why = "OK"
            if exp.status is ExperienceStatus.CANDIDATE:
                if not repo_available:
                    eligible, why = False, "REPOSITORY_UNAVAILABLE"
                else:
                    try:
                        element = _resolve_element(exp)
                        eligible = eligible_for_auto_verification(element)
                    except Exception:  # noqa: BLE001
                        eligible, why = False, "ELEMENT_UNRESOLVED"
            outcome = evaluate(exp, runs, VerificationPolicy(),
                               auto_verify_eligible=eligible)
            counts[outcome.reason] = counts.get(outcome.reason, 0) + 1
            extra = (f" eligible={eligible}" + (f" ({why})" if why != "OK"
                                                else "")) \
                if exp.status is ExperienceStatus.CANDIDATE else ""
            print(f"{exp.experience_id} [{exp.status.value}] "
                  f"{outcome.decision.value}/{outcome.reason} "
                  f"samples={outcome.detail['sample_count']} "
                  f"rate={outcome.detail['success_rate']} "
                  f"distinct={outcome.detail['distinct_runs']}{extra}")
            if apply_outcome(store, exp, outcome, operator="cli-verify"):
                applied += 1
                print(f"  ↳ applied: {exp.status.value} → "
                      f"{outcome.decision.value}（experience_state_events "
                      f"已留痕，operator=cli-verify）")
        print(f"experience verify: {len(exps)} checked, {applied} transitions,"
              f" decisions={counts}")
        return 0

    if args.experience_cmd == "revalidate":
        import getpass
        exp = store.get_experience(args.experience_id)
        if exp is None:
            print(f"EXPERIENCE ERROR: no such experience: "
                  f"{args.experience_id}")
            return 3
        if not (args.fingerprint or "").strip():
            # P2-1（review_p2_task34）：DEGRADED→VERIFIED 的唯一出口凭
            # 证据留痕完成，空指纹等于零证据——argparse 的 required 挡不住
            # 空串，这里显式拒。
            print("EXPERIENCE ERROR: --fingerprint 必须非空"
                  "（重验证证据不可为空串）")
            return 3
        operator = args.operator or getpass.getuser()
        try:
            verifier_revalidate(store, exp, fingerprint=args.fingerprint,
                                run_id=args.evidence_run_id,
                                operator=operator)
        except ValueError as e:
            # 3.1 护栏的 CLI 出口（review_p2_task31 P2-1 接线前置②）：
            # 错误信息必须给出下一步，不是一句裸报错。
            print(f"EXPERIENCE ERROR: {e}")
            if exp.status is ExperienceStatus.CANDIDATE:
                print("下一步：CANDIDATE 走 `mta experience verify`——"
                      "经 E4 资格 + 4.5 门槛升级，不走重验证后门")
            elif exp.status is ExperienceStatus.REJECTED:
                print("下一步：REJECTED 是终态，重新走 `mta review accept`"
                      " 建 Candidate（E5 重新学习路径）")
            else:  # VERIFIED：本就无需重验证
                print("下一步：已是 VERIFIED，无需重验证"
                      "（显式重验证只用于恢复 DEGRADED）")
            return 3
        evidence = (f"evidence_run={args.evidence_run_id}, "
                    if args.evidence_run_id else "")
        print(f"experience revalidate: {exp.experience_id} "
              f"DEGRADED → VERIFIED (reason=REVALIDATED, "
              f"fingerprint={args.fingerprint}, operator={operator}, "
              f"{evidence}证据为操作者自报——operator 留痕)")
        return 0

    if args.experience_cmd == "sweep":
        swept = sweep_stale_candidates(
            store, StalenessPolicy(max_idle_days=args.max_idle_days))
        if not swept:
            print("experience sweep: no stale candidates")
            return 0
        for sid in swept:
            print(f"swept {sid} → REJECTED(STALE)")
        print(f"experience sweep: {len(swept)} cleaned"
              "（只改状态不删证据——11.2 保留规则）")
        return 0

    if args.experience_cmd == "promote":
        # 9.3 两段式：generate（本命令缺省形态，只落 proposal 表）→
        # 人工 review diff → --approve 落地。人工 promote 必须走 proposal
        # 审计，没有直写 overrides 的入口（review_p2_task34 建议动作 3）。
        import getpass

        from experience.promoter import approve_proposal, generate_proposal

        operator = args.operator or getpass.getuser()
        if args.approve:
            try:
                approved, sha = approve_proposal(
                    store, args.approve, repo_root=Path(args.repo_root),
                    committer=operator)
            except (ValueError, RuntimeError) as e:
                print(f"EXPERIENCE ERROR: {e}")
                return 3
            print(f"experience promote: proposal {approved.proposal_id} "
                  f"APPROVED")
            print(f"  overrides 已写 + commit {sha}；撤销 = "
                  f"git revert {sha}（E10，Experience Store 历史不受影响）")
            print(f"  experience {approved.experience_id} "
                  f"promoted=True promoted_commit={sha}"
                  "（status 不变——9.4 两条独立时间线）")
            return 0
        exp = store.get_experience(args.experience_id)
        if exp is None:
            print(f"EXPERIENCE ERROR: no such experience: "
                  f"{args.experience_id}")
            return 3
        try:
            proposal = generate_proposal(
                store, exp, manual_override=args.manual_override,
                reason=args.reason, reviewer=operator)
        except ValueError as e:
            print(f"EXPERIENCE ERROR: {e}")
            if exp.status is ExperienceStatus.REJECTED:
                print("下一步：REJECTED 不可 promote——重新走 "
                      "`mta review accept` 建 Candidate（E5 重新学习）")
            return 3
        print(f"experience promote: proposal {proposal.proposal_id} "
              f"PENDING（9.3 审计——approve 前不写任何文件）")
        print(f"  evidence: {proposal.evidence_summary}")
        print("  diff（人工 review）:")
        print(proposal.diff, end="")
        print(f"  approve: `mta experience promote {args.experience_id} "
              f"--approve {proposal.proposal_id} --repo-root <git根>`")
        return 0

    print(f"experience: unknown subcommand {args.experience_cmd!r}")
    return 2


def _graph_metadata_path(args: argparse.Namespace) -> Path:
    """`--from-source` 缺省：<generated>/source_metadata.json（与 12.3 布局一致）。"""
    if args.from_source:
        return Path(args.from_source)
    return Path(getattr(args, "generated", None)
                or "repository/generated/local") / "source_metadata.json"


def _graph_build(args: argparse.Namespace) -> int:
    """`mta graph build`：建图并落库（运行时面 / 源面，可同批）。

    判定全在 `graph.builder`（纯函数），这里只做「读 → 建 → 写」。
    """
    from graph import GraphStore, build_runtime_graph, build_source_graph
    from graph import read_source_metadata, read_trace_steps

    store = GraphStore(args.graph_db)
    built: list[str] = []
    # 两个 flag 都没给 → 默认两边都建（CLI 的常见用法：一条命令把两面备齐）
    do_trace = args.from_trace is not None or args.from_source is None
    do_source = args.from_source is not None or args.from_trace is None

    if do_trace:
        trace_db = args.from_trace or args.db
        try:
            steps, app_id, build = read_trace_steps(
                trace_db, app_build=args.build)
        except ValueError as e:
            print(f"GRAPH ERROR: {e}")
            return 3
        g = build_runtime_graph(steps, app_id=args.bundle_id or app_id,
                                app_build=build)
        store.upsert_graph(g)
        built.append(f"runtime: {len(g.nodes)} 节点 / "
                     f"{len(g.transitions)} 转移（app={g.app_id!r} "
                     f"build={g.app_build!r}）")
        if not g.nodes:
            print("GRAPH WARN: 运行时图为空——trace 里没有任何屏观测"
                  "（既没有成功的 `wait_for screen:X`，也没有恢复期屏识别）")

    if do_source:
        meta_path = _graph_metadata_path(args)
        try:
            metadata = read_source_metadata(meta_path)
        except (FileNotFoundError, ValueError) as e:
            print(f"GRAPH ERROR: {e}")
            return 3
        g = build_source_graph(metadata, app_id=args.bundle_id,
                               app_build=args.build)
        store.upsert_graph(g)
        built.append(f"source: {len(g.nodes)} 节点 / "
                     f"{len(g.transitions)} 转移（app={g.app_id!r} "
                     f"build={g.app_build!r}）")
        if not g.nodes:
            # 「如实为空」是设计允许的，但**必须说出来**：多数现成 metadata
            # 没有 `screens` 键，静默的空源图会让 diff 的判读完全跑偏。
            print(f"GRAPH WARN: 源图为空——{meta_path} 没有声明任何屏"
                  f"（缺 `screens` 键）；此时 diff 会判 UNKNOWN（无法判定）")

    for line in built:
        print(f"graph build: {line}")
    print(f"graph db: {args.graph_db}")
    return 0


def _other_builds_with(store, app_id: str, build: str,
                       source_of: str) -> list[str]:
    """同一 app 的**其它 build** 上有没有这一面的图（scope 没对齐的诊断用）。"""
    return sorted(b for (a, b, so) in store.list_scopes()
                  if a == app_id and so == source_of and b != build)


def _graph_diff(args: argparse.Namespace) -> int:
    """`mta graph diff`：五类差异（设计 12.2）/ build-to-build（12.3）。

    **scope 对齐守卫**（review_p2_task52 P3-1 的接线前置）：要比较的那一面在
    请求的 build 上没有图、但**在别的 build 上有** → fail-loud 并点名那个
    build。此时安静地判 UNKNOWN（或更糟：满屏 NOT_OBSERVED）会把「scope 没
    对齐」伪装成「声明缺失」，而前者是可操作的配置问题。
    """
    from graph import GraphStore, RUNTIME, SOURCE, diff_graphs

    store = GraphStore(args.graph_db)
    if args.base_build is not None:
        # build-to-build：同一面（默认运行时）在两个 build 之间比
        sides = [("base", args.base_build, RUNTIME),
                 ("new", args.build, RUNTIME)]
    else:
        # 源图（声明）vs 运行时图（观测）——五类判定
        sides = [("base(source)", args.build, SOURCE),
                 ("new(runtime)", args.build, RUNTIME)]
    for label, build, source_of in sides:
        g = store.load_graph(args.bundle_id, build, source_of)
        if g.is_empty:
            others = _other_builds_with(store, args.bundle_id, build, source_of)
            if others:
                print(f"GRAPH ERROR: {label} 面在 build={build!r} 上没有图，"
                      f"但同一 app 在 {others} 上有——两面 scope 未对齐。"
                      f"用 `--build <其中一个>` 重跑，或先用 "
                      f"`mta graph build` 把该 build 的图建出来。")
                return 3
    if args.base_build is not None:
        base = store.load_graph(args.bundle_id, args.base_build, RUNTIME)
        new = store.load_graph(args.bundle_id, args.build, RUNTIME)
        diff = diff_graphs(base or None, new or None, allow_build_change=True)
    else:
        base = store.load_graph(args.bundle_id, args.build, SOURCE)
        new = store.load_graph(args.bundle_id, args.build, RUNTIME)
        diff = diff_graphs(base or None, new or None)

    counts = diff.counts
    print(f"graph diff: app={diff.app_id!r} "
          f"base_build={diff.base_build!r} build={diff.build!r} "
          f"（base={diff.base_source_of or '-'} → new={diff.source_of or '-'}）")
    if diff.reason:
        print(f"  判定：UNKNOWN —— {diff.reason}")
        # 首次使用最容易踩的路径：只建了一面就来 diff。给出可操作的下一步，
        # 别让人把 UNKNOWN 读成「用例没走到」。
        print(f"  下一步：先 `mta graph build --graph-db {args.graph_db} "
              f"--bundle-id {args.bundle_id or '<id>'} "
              f"--build {args.build!r}` 把两面都建出来再比")
    for e in diff.entries:
        print("  " + e.render())
    print("  合计：" + ", ".join(f"{k}={counts[k]}" for k in
                                 ("ADDED", "REMOVED", "CHANGED",
                                  "NOT_OBSERVED", "UNKNOWN")))
    if args.save:
        n = store.record_diff(diff)
        print(f"  已落库 graph_diffs：{n} 行（按范围整体替换）")
    # 退出码：有真实变化（非 UNKNOWN）→ 1（CI 可用它判断「图变了」）；
    # 无变化或仅 UNKNOWN → 0（UNKNOWN 是「没判定」，不是「有变化」）。
    return 1 if diff.changed else 0


def _graph_show(args: argparse.Namespace) -> int:
    """`mta graph show`：打印库里的图。"""
    from graph import GraphStore, RUNTIME, SOURCE

    store = GraphStore(args.graph_db)
    scopes = store.list_scopes()
    if not scopes:
        print("graph show: 库里没有任何图（先跑 `mta graph build`）")
        return 0
    for app_id, build, source_of in scopes:
        if args.bundle_id and app_id != args.bundle_id:
            continue
        if args.build is not None and build != args.build:
            continue
        g = store.load_graph(app_id, build, source_of)
        print(f"graph show: app={app_id!r} build={build!r} "
              f"source_of={source_of} — {len(g.nodes)} 节点 / "
              f"{len(g.transitions)} 转移")
        for n in g.nodes:
            ev = ",".join(n.evidence) or "-"
            print(f"  node {n.screen_id} visits={n.visit_count} ev={ev} "
                  f"first={n.first_seen or '-'} last={n.last_seen or '-'}")
        for t in g.transitions:
            print(f"  {t.from_screen} -> {t.to_screen} "
                  f"trigger={t.trigger or '-'} count={t.count}")
    return 0


def cmd_graph(args: argparse.Namespace) -> int:
    """设计 13 节的 `mta graph` 子命令组（Task 5.3）。

    职责边界：判定全部来自 `graph` 包的纯函数（`build_runtime_graph` /
    `build_source_graph` / `diff_graphs`），CLI 不复制任何判据。
    """
    if args.graph_cmd == "build":
        return _graph_build(args)
    if args.graph_cmd == "diff":
        return _graph_diff(args)
    if args.graph_cmd == "show":
        return _graph_show(args)
    print(f"graph: unknown subcommand {args.graph_cmd!r}")
    return 2


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
    if args.command == "report":
        return cmd_report(args)
    if args.command == "experience":
        return cmd_experience(args)
    if args.command == "graph":
        return cmd_graph(args)
    # 全部子命令已实现——占位分发随 review_m5_task51 P3-4 退役
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
