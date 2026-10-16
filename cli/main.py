"""mta CLI 骨架（设计 14.6，Task 1.3 / P1-01）。

子命令：lint（Task 1.3 完整实现）；run / review / report / repo 为占位，
按 plan Task 1.6+ 逐步填充。退出码表（8.4）：
  - lint：有 ERROR → 3；仅 WARNING / 干净 → 0；
  - 占位子命令 → 退出码 2（NotImplemented，避免误当成功）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Sequence

import yaml

from environment.secrets import EnvSecretProvider
from agents.storage import DEFAULT_AGENT_DB
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
                       help="目标设备 UDID（模拟器默认自动发现 booted；真机"
                            "用物理 UDID，如 00008120-0018499A1450C01E）")
    run_p.add_argument("--app-path", metavar="PATH",
                       help="真机路线：本地产物 .app 目录（identity 从它的 "
                            "Info.plist 读——物理设备没有 simctl 等价物；"
                            "如 build/lst-device-uat/Build/Products/"
                            "Debug-iphoneos/nfcPushOnline.app）。模拟器"
                            "路线不需要，给了则以它为准")
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

    # --- mta plan（Task 2.4 / P3-08：**首个自主命令**，设计 12 节 / §5.3）---
    # 前置（`--env-kind` + `_check_autonomous_env`）在 `cmd_plan` 的第一行——
    # F7 是「启动时校验」，不是「跑一半才拦」。
    plan_p = sub.add_parser(
        "plan",
        help="按 git diff 产出可解释的回归优先级 Plan（P3-07/08；"
             "自主命令，production 下不可用）")
    plan_p.add_argument("--build", metavar="ID",
                        help="本次规划的 build id（默认取 metadata 顶层 build；"
                             "它同时是 resolve/知识装配的 build 作用域）")
    plan_p.add_argument("--base-build", metavar="ID",
                        help="基线 build id：取其 metadata 的 git_commit 作 diff "
                             "base，并把那份 metadata 作为 drift 回退面"
                             "（不给 → base=HEAD~1，无回退面）")
    plan_p.add_argument("--metadata", metavar="PATH",
                        help="12.3 source_metadata.json 路径（默认 "
                             "<--generated>/source_metadata.json 或 "
                             "repository/generated/local/）")
    plan_p.add_argument("--generated", metavar="DIR",
                        default="repository/generated/local",
                        help="generated **build 目录**（默认 "
                             "repository/generated/local，与 metadata 的默认同一处；"
                             "`--base-build` 的根取它的父目录）")
    plan_p.add_argument("--overrides", metavar="DIR",
                        default="repository/overrides",
                        help="repository overrides 根目录（默认 "
                             "repository/overrides；不存在则该来源为空）")
    plan_p.add_argument("--suites-root", metavar="DIR", default="suites",
                        help="用例 YAML 根目录（默认 ./suites）")
    plan_p.add_argument("--repo-root", metavar="DIR", default=".",
                        help="git 仓库根（默认 CWD；`git -C` 用它）")
    plan_p.add_argument("--db", metavar="PATH", default="out/trace.db",
                        help="TraceStore SQLite 路径（历史失败率的来源）")
    plan_p.add_argument("--exp-db", metavar="PATH",
                        default=str(DEFAULT_EXPERIENCE_DB),
                        help="Experience Store 库路径（默认 out/experience.db）")
    plan_p.add_argument("--agent-db", metavar="PATH",
                        default=str(DEFAULT_AGENT_DB),
                        help="agent.db 路径（Plan 落 test_plans 表）")
    plan_p.add_argument("--policy", metavar="PATH",
                        help="policy.yaml 路径（不给 → 读默认 "
                             "config/policy.yaml，读不到用内置默认值；"
                             "**显式给了却不存在 → 报错**）")
    plan_p.add_argument("--env-kind", choices=["sandbox", "staging",
                                               "production"],
                        default="sandbox",
                        help="环境种类（F7：production 下自主命令整体不可用，"
                             "**无豁免 flag**）")
    plan_p.add_argument("--no-llm", action="store_true",
                        help="不调 LLM（reasons 只用规则摘要；确定性排序不变）")
    plan_p.add_argument("--json", action="store_true",
                        help="结构化输出（默认人类可读）")

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
    gph_diff.add_argument("--cases", metavar="DIR", default=None,
                          help="用例根目录；给了就再算**影响面**并打印 "
                               "UI Change Report（12.3 = diff × 影响用例）")

    gph_show = gph_sub.add_parser("show", help="打印库里的图（范围 + 节点 + 转移）")
    _add_graph_scope(gph_show)
    gph_show.add_argument("--build", metavar="ID", default=None)

    # --- mta generate（Task 3.5 / P3-13：自主命令，设计 6.1 / §12）---
    # F7 前置在 `cmd_generate` 的第一行（M1 的 AST 守卫扫 `cmd_*` 的函数体）。
    gen_p = sub.add_parser(
        "generate",
        help="按 plan 现场重算 coverage gap → LLM 生成候选 → 校验/去重/Dry Run "
             "→ 落 test_candidates（自主命令，production 下不可用）")
    gen_p.add_argument("--gap-from", metavar="PLAN_ID", required=True,
                       help="plan id（`mta plan` 的产物）——**追溯锚点**；"
                            "gap 一律现场重算（planner 目前不产 generated_gap 任务）")
    gen_p.add_argument("--env-kind", default="sandbox",
                       choices=["sandbox", "staging", "production"],
                       help="运行环境（F7；generate 只在 sandbox 可跑）")
    gen_p.add_argument("--suite", metavar="NAME",
                       help="候选声明的目标套件（ACCEPT 落盘到 suites/<suite>/；"
                            "审核期不许补，故生成时必须给）")
    gen_p.add_argument("--requirement", metavar="TEXT", default="",
                       help="需求描述（进 prompt 的可信上下文）")
    gen_p.add_argument("--bundle-id", metavar="ID", default=None,
                       help="App bundle id（trace 图的范围键；不给则从 trace "
                            "自身归一化的 app_id 取——与 trace 不一致会前置报错）")
    gen_p.add_argument("--udid", metavar="UDID", default=None)
    gen_p.add_argument("--fake-driver", action="store_true",
                       help="不连真机，组件注入最小桩（Dry Run 冒烟）")
    gen_p.add_argument("--generated", metavar="DIR",
                       default="repository/generated/local")
    gen_p.add_argument("--build", metavar="ID",
                       help="build 作用域（默认取 metadata 顶层 build；"
                            "它同时是 coverage 图与知识装配的 build 范围）")
    gen_p.add_argument("--metadata", metavar="PATH", default=None)
    gen_p.add_argument("--overrides", metavar="DIR",
                       default="repository/overrides")
    gen_p.add_argument("--suites-root", metavar="DIR", default="suites")
    gen_p.add_argument("--db", metavar="PATH", default="out/trace.db",
                       help="TraceStore SQLite 路径（coverage 图的来源，12.1）")
    gen_p.add_argument("--agent-db", metavar="PATH", default=str(DEFAULT_AGENT_DB),
                       help="agent.db（test_candidates；默认 out/agent.db）")
    gen_p.add_argument("--exp-db", metavar="PATH",
                       default=str(DEFAULT_EXPERIENCE_DB))
    gen_p.add_argument("--repo-root", metavar="DIR", default=".")
    gen_p.add_argument("--policy", metavar="PATH", default=None,
                       help="policy.yaml 路径（不给则读默认；给了但不存在 → 报错）")
    gen_p.add_argument("--no-llm", action="store_true",
                       help="禁用 LLM（生成必须有 LLM，故等于「不生成」）")

    # --- mta explore（Task 4.6 / P3-19：Bounded Exploration，设计 7 节）---
    exp_p = sub.add_parser(
        "explore", help="Bounded Exploration（设计 7 节；**仅 sandbox + "
                        "模拟器**，F7/§7.4）")
    exp_p.add_argument("--goal", metavar="TEXT", required=True,
                       help="探索目标（进 `agent_tasks.goal`——审计要能回答"
                            "「这次为什么探」，必填）")
    exp_p.add_argument("--budget", metavar="FILE", default=None,
                       help="探索四维预算 YAML（同 exploration 段结构；缺省取 "
                            "policy.yaml 的 exploration 段）")
    exp_p.add_argument("--generated", metavar="DIR", default=None,
                       help="generated 根目录（缺省 repository/generated/local"
                            "——metadata 与元素都从这里读）")
    exp_p.add_argument("--overrides", metavar="DIR", default=None,
                       help="overrides 根目录（缺省 repository/overrides）")
    exp_p.add_argument("--suites-root", metavar="DIR", default="suites")
    exp_p.add_argument("--db", metavar="PATH", default="out/trace.db",
                       help="trace 库（观测面：已到达过的屏与转移）")
    exp_p.add_argument("--graph-db", metavar="PATH",
                       default=str(DEFAULT_GRAPH_DB),
                       help="Graph SQLite 路径（声明面：frontier 的 source 侧）")
    exp_p.add_argument("--agent-db", metavar="PATH",
                       default=str(DEFAULT_AGENT_DB),
                       help="agent 库（探索任务 / 发现事件 / 每步审计）")
    exp_p.add_argument("--bundle-id", metavar="ID", default=None,
                       help="App bundle id（图的范围第一段；真机/模拟器会话"
                            "也需要它）")
    exp_p.add_argument("--build", metavar="ID", default=None,
                       help="本次探索的 build id（缺省取 metadata 的 build）")
    exp_p.add_argument("--udid", metavar="UDID", default=None,
                       help="模拟器 UDID（缺省取第一个 booted 设备）")
    exp_p.add_argument("--suite", metavar="NAME", default=None,
                       help="只用于 trace 的 runs 行标注（探索不跑用例）")
    exp_p.add_argument("--policy", metavar="PATH", default=None,
                       help="policy.yaml 路径（缺省读默认路径）")
    exp_p.add_argument("--env-kind", choices=["sandbox", "staging",
                                              "production"],
                       default="sandbox",
                       help="环境（**探索只接受 sandbox**，staging 也拒——"
                            "F7/§7.4；本命令没有豁免 flag）")
    exp_p.add_argument("--device-type", choices=["simulator", "real_device"],
                       default="simulator",
                       help="设备端别（**探索只接受 simulator**——真机只跑"
                            "已审核用例）")

    # --- mta diagnose（Task 5.6 / P3-25：Failure Diagnosis，设计 8 节）---
    diag_p = sub.add_parser(
        "diagnose", help="Failure Diagnosis（设计 8 节；自主命令——读 trace、"
                         "产出带证据的 Bug 候选，不自动归因）")
    diag_p.add_argument("--run-id", metavar="ID", required=True,
                        help="要诊断的失败 run（`runs.run_id`，如 `run_ab12cd34`）")
    diag_p.add_argument("--step", metavar="N", type=int, default=None,
                        help="只诊断该 step_index（缺省 = 该 run 的第一条失败步）")
    diag_p.add_argument("--base-build", metavar="ID", default=None,
                        help="基线 build（给则算 build-to-build 的图 diff，喂 "
                             "UI_DRIFT 规则；**不给则不算 dedup_key**——那三元组"
                             "里的 source_change 来自 diff）")
    diag_p.add_argument("--db", metavar="PATH", default="out/trace.db",
                        help="trace 库（失败现场与历史）")
    diag_p.add_argument("--graph-db", metavar="PATH",
                        default=str(DEFAULT_GRAPH_DB),
                        help="Graph SQLite 路径（运行时图 + 基线图）")
    diag_p.add_argument("--agent-db", metavar="PATH",
                        default=str(DEFAULT_AGENT_DB),
                        help="agent 库（Bug 候选落 `bug_candidates`）")
    diag_p.add_argument("--exp-db", metavar="PATH",
                        default=str(DEFAULT_EXPERIENCE_DB),
                        help="经验库（诊断只读：相关历史经验）")
    diag_p.add_argument("--generated", metavar="DIR", default=None,
                        help="generated 根目录（缺省 repository/generated/local）")
    diag_p.add_argument("--overrides", metavar="DIR", default=None,
                        help="overrides 根目录（缺省 repository/overrides）")
    diag_p.add_argument("--suites-root", metavar="DIR", default="suites",
                        help="用例 YAML 根目录（复现重跑要按 id 找到用例）")
    diag_p.add_argument("--bundle-id", metavar="ID", default=None,
                        help="App bundle id（图的范围第一段；缺省取 trace 的）")
    diag_p.add_argument("--build", metavar="ID", default=None,
                        help="本次 build id（缺省取 metadata 的 build）")
    diag_p.add_argument("--udid", metavar="UDID", default=None,
                        help="模拟器 UDID（复现重跑用；缺省取第一个 booted 设备）")
    diag_p.add_argument("--bundle-dir", metavar="DIR", default="out",
                        help="Evidence Bundle JSON 的落盘目录（缺省 out/；"
                             "只在人工 ACCEPT 后写）")
    diag_p.add_argument("--repo-root", metavar="DIR", default=".",
                        help="git 仓库根（`--base-build` 的 source_change 取"
                             "它上面的 git diff）")
    # Batch B（设计 §8.1 的「App/Crash/Network Logs」）：**调用方取好的原始
    # 材料**（与 `build_context` 的形参口径一致）。**不自动采集**——`diagnose`
    # 是离线命令，自动采会让它隐式连设备；采集由 `mta run` 侧或人工给。
    diag_p.add_argument("--logs", metavar="FILE", default=None,
                        help="App 日志文件（每行一条；采集见 telemetry/"
                             "collectors.py 的 SyslogCollector）")
    diag_p.add_argument("--crash-report", metavar="FILE", default=None,
                        help="崩溃报告 .ips 文件（App 崩溃的判定证据）")
    diag_p.add_argument("--no-llm", action="store_true",
                        help="禁用 LLM（规则无结论时不补假设，确定性半边照常）")
    diag_p.add_argument("--policy", metavar="PATH", default=None,
                        help="policy.yaml 路径（缺省读默认路径）")
    diag_p.add_argument("--env-kind", choices=["sandbox", "staging",
                                               "production"],
                        default="sandbox",
                        help="环境（F7：production 下自主命令不可用）")
    diag_p.add_argument("--device-type", choices=["simulator", "real_device"],
                        default="simulator",
                        help="设备端别（诊断只读 trace，复现重跑在模拟器）")

    # --- mta agent（Task 6.5 / P3-30：Agent Evaluation，设计 12/13 节）---
    # `"agent"` 已在 AUTONOMOUS_COMMANDS（M1 预登记）——F7 启动期拒绝在
    # `cmd_agent` 的第一行生效，与 plan/generate/explore/diagnose 同款。
    agent_p = sub.add_parser(
        "agent", help="Agent 自身评估（设计 13.2：benchmark run/compare；"
                      "Agent 的质量回归检测，与 App 回归并列）")
    agent_sub = agent_p.add_subparsers(dest="agent_cmd", required=True)
    bench_p = agent_sub.add_parser("benchmark", help="Agent Benchmark 子命令")
    bench_sub = bench_p.add_subparsers(dest="bench_cmd", required=True)
    bench_run_p = bench_sub.add_parser(
        "run", help="跑固定 7 类 case（离设备，F13）并存一行快照")
    bench_run_p.add_argument("--label", metavar="TAG", default="bench",
                             help="版本标签（compare 用它寻行；如 v1）")
    bench_run_p.add_argument("--agent-db", metavar="PATH",
                             default=str(DEFAULT_AGENT_DB),
                             help="agent 库（benchmark_runs 表）")
    bench_run_p.add_argument("--policy", metavar="PATH", default=None,
                             help="policy.yaml 路径（缺省读默认路径）")
    bench_run_p.add_argument("--env-kind", choices=["sandbox", "staging",
                                                    "production"],
                             default="sandbox",
                             help="环境（F7：production 下自主命令不可用）")
    bench_cmp_p = bench_sub.add_parser(
        "compare", help="两版对比：关键指标下降超阈值 → AGENT_REGRESSION（exit 1）")
    bench_cmp_p.add_argument("baseline", metavar="V1",
                             help="基线版本标签")
    bench_cmp_p.add_argument("candidate", metavar="V2",
                             help="候选版本标签")
    bench_cmp_p.add_argument("--threshold", metavar="F", type=float,
                             default=0.05,
                             help="关键指标下降阈值（初版 0.05）")
    bench_cmp_p.add_argument("--agent-db", metavar="PATH",
                             default=str(DEFAULT_AGENT_DB),
                             help="agent 库（benchmark_runs 表）")
    bench_cmp_p.add_argument("--policy", metavar="PATH", default=None,
                             help="policy.yaml 路径（缺省读默认路径）")
    bench_cmp_p.add_argument("--env-kind", choices=["sandbox", "staging",
                                                    "production"],
                             default="sandbox",
                             help="环境（F7：production 下自主命令不可用）")

    # ⚠️ **不是**自主命令（不在 `AUTONOMOUS_COMMANDS`）：审核是人工命令，
    # 不过 F7 的启动环境闸（与 `mta review` 同款）。
    cand_p = sub.add_parser(
        "candidate", help="候选资产审核（F6；list/accept/reject，动词与 "
                          "`mta review` 对齐）")
    cand_sub = cand_p.add_subparsers(dest="candidate_cmd", required=True)
    cand_rev = cand_sub.add_parser("review", help="候选审核子命令")
    cand_rev_sub = cand_rev.add_subparsers(dest="candidate_review_cmd",
                                           required=True)
    cr_list = cand_rev_sub.add_parser("list", help="列出候选（默认 PENDING）")
    cr_list.add_argument("--status", default="PENDING",
                         choices=["PENDING", "ACCEPT", "REJECT", "ALL"])
    cr_list.add_argument("--rejections", action="store_true",
                         help="附列生成期拒绝/SKIP 留痕（矩阵 #7/#8 的 CLI 面）")
    cr_list.add_argument("--db", metavar="PATH", default=str(DEFAULT_AGENT_DB))
    cr_list.add_argument("--suites-root", metavar="DIR", default="suites")
    cr_acc = cand_rev_sub.add_parser(
        "accept", help="接受候选 → 落盘 suites/<suite>/<id>.yaml + git commit")
    cr_acc.add_argument("candidate_id")
    cr_acc.add_argument("--kind", default="test",
                        choices=["test", "discovery", "bug"],
                        help="候选类型（test 落盘用例 / discovery 登记图 / "
                             "bug 确认并落 Evidence Bundle）")
    cr_acc.add_argument("--bundle-dir", metavar="DIR", default="out",
                        help="kind=bug 的 Evidence Bundle JSON 落盘目录")
    cr_acc.add_argument("--note", metavar="TEXT", default=None)
    cr_acc.add_argument("--reviewer", metavar="NAME", default=None,
                        help="审核人（缺省取 $USER）")
    cr_acc.add_argument("--db", metavar="PATH", default=str(DEFAULT_AGENT_DB))
    cr_acc.add_argument("--suites-root", metavar="DIR", default="suites")
    cr_acc.add_argument("--generated", metavar="DIR",
                        default="repository/generated/local")
    cr_acc.add_argument("--overrides", metavar="DIR",
                        default="repository/overrides")
    cr_acc.add_argument("--repo-root", metavar="DIR", default=".")
    cr_acc.add_argument("--export-patch", action="store_true",
                        help="只导出将写入的 YAML 文本（不落盘、不 commit，H15）")
    cr_rej = cand_rev_sub.add_parser("reject", help="拒绝候选（--note 必填）")
    cr_rej.add_argument("candidate_id")
    cr_rej.add_argument("--kind", default="test",
                        choices=["test", "discovery", "bug"])
    cr_rej.add_argument("--note", metavar="TEXT", required=True,
                        help="拒绝理由必填（triage 数据）")
    cr_rej.add_argument("--reviewer", metavar="NAME", default=None)
    cr_rej.add_argument("--db", metavar="PATH", default=str(DEFAULT_AGENT_DB))

    return parser


# --- P3 自主命令的公共前置（F7；设计 7.4） ----------------------------------

# 自主命令全集（plan 第 12 节）。**单点**：Task 2.4（`mta plan`）起逐个接线时
# 用本元组登记解析器、并在 `main` 里统一过 `_check_autonomous_env`——两处各写
# 一份名单必然漂移。
AUTONOMOUS_COMMANDS = ("plan", "generate", "explore", "diagnose", "agent")


def _resolve_env_kind(args: argparse.Namespace) -> "EnvKind":
    """`--env-kind` 的**唯一解析点**（`run` 与自主命令共用；10.1）。

    语义沿用 P1：「用旗标显式给，默认 sandbox = 历史行为不变」。
    review_p3_task12 P3-3：自主命令的 `env_kind` 来源此前**没有落点**——若
    Task 2.4 顺手硬编码 `env_kind="sandbox"`，F7 会退化成**永真**（判据还在、
    测试还绿、但永远不拒）。故把解析抽成这里：自主命令的 `--env-kind` 与 `run`
    同款（Task 2.4 的接线前置，见 `docs/p3_data_audit.md`），调用方只许写
    `env_kind=_resolve_env_kind(args)`。
    """
    from executor.guard import EnvKind

    return EnvKind(getattr(args, "env_kind", None) or "sandbox")


def _check_autonomous_env(policy, *, env_kind, command: str) -> None:
    """自主命令的公共前置：**启动时**校验环境与总开关（F7 / 设计 §7.4）。

    设计 §7.4 原文：「`production`: Autonomous Agent 整体不可用（policy.yaml
    启动时校验，不是运行时才拦）」——所以这是**能不能启动**的判定，不是
    「风险高就拦」。三条判据，任一不过即 `SystemExit`（消息含 `F7` 便于检索）：

    1. `env_kind` **不是**合法环境（含 `None` / 拼错 / 大小写不对）→ 拒；
    2. `env_kind == production` → 拒，**且没有豁免 flag**；
    3. `policy.autonomous.enabled is False` → 拒（连 sandbox 也拒）。

    第 1 条是 review_p3_task12 P3-4 补的：`EnvKind(bad)` 抛的是 `ValueError`
    ——那是**第三个出口**，CLI 上会打一整段 traceback，而且**任何用
    `except Exception` 兜底的调用方都会把否决一起吞掉**。安全闸门只许有一个
    出口：`SystemExit`。

    ⚠️ **与 `mta run --allow-production` 的区别**（别把两者混成一条规则）：
    `run` 是 P1/P2 的**单次运行总闸**，允许在 production 下显式豁免跑
    **已审核**的用例；自主命令是「在 production 下整体不存在」，故**不设**
    豁免参数——本函数签名里没有、也不该有 `allow_production`。两者判据不同、
    语义不同，是**两条规则**，不违反「同一概念只许一处实现」。

    通过即返回 `None`（无返回值），由调用方继续；`env_kind` 复用 P1 的
    `executor.guard.EnvKind`（字符串或枚举都收），不新建环境枚举。

    调用点：Task 2.4 起各自主命令在 `cmd_*` 的第一行调用本函数
    （`policy = load_policy(args.policy)` 是**将来时**——`--policy` 这个 flag
    目前不存在，归 Task 2.4 与 `--env-kind` 一并接线；在那之前用
    `load_policy()` 的默认解析）。**不另写第二套环境校验**。
    """
    from executor.guard import EnvKind

    try:
        kind = EnvKind(env_kind)
    except ValueError:
        # 安全闸门只许一个出口（SystemExit）：坏值若以 ValueError 漏出去，
        # 调用方的 `except Exception` 会把否决一起吞掉（P3-4）。
        raise SystemExit(
            f"F7: 未知环境 {env_kind!r}——只接受 "
            f"{[k.value for k in EnvKind]}。自主命令的 env 来源是 "
            f"`--env-kind`（与 `mta run` 同款）；拿不到合法值时不假设 "
            f"sandbox，直接拒。") from None
    if kind is EnvKind.PRODUCTION:
        raise SystemExit(
            f"F7: `mta {command}` 是自主命令，production 环境下 Autonomous "
            f"Agent 整体不可用（设计 7.4：环境就不允许启动，不是风险高才拦）。"
            f"本命令**没有**豁免 flag——`mta run --allow-production` 是 P2 的"
            f"单次运行总闸（只跑已审核用例），语义不同、不适用于自主命令。"
            f"请在 sandbox 或 staging 下运行。")
    if not policy.autonomous.enabled:
        raise SystemExit(
            f"F7: policy.yaml 的 `autonomous.enabled=false`，`mta {command}` "
            f"被禁用（这是配置层面的整体开关，与 env.kind={kind.value} 无关）。"
            f"把 `autonomous.enabled` 改回 `true` 后重试。")
    return None


def _check_exploration_env(policy, *, env_kind, device_type,
                          command: str) -> None:
    """`mta explore` 的**专属**前置：sandbox + simulator，**无例外**（F7 / §7.4）。

    设计 §7.4 原文：

    ```text
    Exploration 默认只能: Simulator + UITest Build + env.kind == sandbox
    真机: 只运行已审核的 Test Plan，不运行自由探索
    ```

    ⚠️ **为什么不复用 `_check_autonomous_env`**（口径必守，review_p3_task12 §4）：
    那条是**自主命令的公共前置**，它只做「production 一票否决」，拒绝消息对用户
    说「请在 **sandbox 或 staging** 下运行」——那句话对 `plan`/`generate` 准确
    （它们确实允许 staging），对 `explore` **不准确**：设计 §7.4 要求探索
    **sandbox-only 且不能有例外**。若让 `explore` 只过公共前置，staging 下它
    会被放行，而文案还写着「sandbox 或 staging」——**文案与行为打架**。
    故这里显式再挡一层：**staging 也拒**。

    调用顺序（在 `cmd_explore` 里）：先 `_check_autonomous_env`（总开关 +
    production 一票否决），再本函数（sandbox-only + simulator）。两层是**与**
    的关系，任一层拒即拒——不合并成一个函数，因为它们的判据集合本就不同
    （公共前置对所有自主命令一致，本层只对 explore 成立）。

    三条判据，任一不过即 `SystemExit`（消息含 `F7` 便于检索）：

    1. `env_kind` 不是合法环境（含 `None` / 拼错 / 大小写不对）→ 拒；
    2. `env_kind != sandbox` → 拒（**staging / production 一律拒**，无豁免 flag）；
    3. `device_type` 不是 simulator → 拒（真机只跑已审核用例）。

    唯一出口是 `SystemExit`：坏值若以 `ValueError` 漏出去，调用方的
    `except Exception` 会把否决一起吞掉（review_p3_task12 P3-4 同款）。
    """
    from environment.capabilities import DeviceType
    from executor.guard import EnvKind

    try:
        kind = EnvKind(env_kind)
    except ValueError:
        raise SystemExit(
            f"F7: 未知环境 {env_kind!r}——只接受 "
            f"{[k.value for k in EnvKind]}。`mta {command}` 的 env 来源是 "
            f"`--env-kind`（与 `mta run` 同款）；拿不到合法值时不假设 "
            f"sandbox，直接拒。") from None
    if kind is not EnvKind.SANDBOX:
        raise SystemExit(
            f"F7: `mta {command}` 只能在 **sandbox** 下运行（设计 7.4："
            f"Exploration 默认只能 Simulator + UITest Build + env.kind == "
            f"sandbox），got env_kind={kind.value!r}——**staging 也不例外**"
            f"（这与 `plan`/`generate` 不同：那两条允许 staging，探索不允许）。"
            f"本命令没有豁免 flag；真机与 staging 只运行**已审核**的用例。")
    try:
        device = DeviceType(str(device_type).strip().lower().replace("-", "_"))
    except ValueError:
        raise SystemExit(
            f"F7: 未知设备类型 {device_type!r}——只接受 "
            f"{[d.value for d in DeviceType]}。拿不到合法值时直接拒。") from None
    if device is not DeviceType.SIMULATOR:
        raise SystemExit(
            f"F7: `mta {command}` 只在**模拟器**上运行（设计 7.4：真机只运行"
            f"已审核的 Test Plan，不运行自由探索），got "
            f"device_type={device.value!r}——拒绝启动（无豁免 flag）。")
    return None


def _metadata_path(args: argparse.Namespace) -> Path:
    """12.3 metadata 的**唯一路径规则**（`run` 与 `plan` 共用）。

    `--metadata` 显式给 → 用它；否则 `<--generated 或 repository/generated/local>/
    source_metadata.json`。⚠️ `--generated` 在本项目里的既有语义是 **build 目录**
    （`Repository.from_dirs(generated_root=…)` 与 `_resolve_app_build` 都这样用；
    解析器的 help 文本写成「generated 根目录」是**措辞不一致**，见
    `docs/p3_data_audit.md` 的 Task 2.4 登记）。

    抽出来的理由：`_resolve_app_build` 与 `cmd_plan` 原先各写一份同样的表达式
    —— 两处各写一遍必然漂移（Task 2.3 的 `require_paths` 是同一课的第一次）。
    """
    explicit = getattr(args, "metadata", None)
    if explicit:
        return Path(explicit)
    return Path(getattr(args, "generated", None)
                or "repository/generated/local") / "source_metadata.json"


def _read_infra_events(trace_db: str | Path, run_id: str) -> tuple:
    """该 run 的 `infra_events` 行（dict 元组）——诊断的输入材料之一。

    **库不存在 / 读不到 → 空元组**（不是错误）：`diagnose` 的前置错误已经由
    「该 run 取不到任何步骤」那条给出，这里再炸一次只会把同一个前置问题报两遍。
    空元组是**如实的**：「没有 infra 事件」。
    """
    import sqlite3

    if not Path(trace_db).exists():
        return ()
    try:
        conn = sqlite3.connect(str(trace_db))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT event_type, step_index, timestamp, detail_json"
                " FROM infra_events WHERE run_id=?", (run_id,)).fetchall()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        # 库在但没初始化（没有 infra_events 表）→ 如实空，前置错误由别处报
        return ()
    out = []
    for r in rows:
        item = {"event_type": r["event_type"], "step_index": r["step_index"],
                "timestamp": r["timestamp"]}
        if r["detail_json"]:
            try:
                item["detail"] = json.loads(r["detail_json"])
            except (json.JSONDecodeError, TypeError):
                pass
        out.append(item)
    return tuple(out)


def _read_log_file(path: str | Path | None) -> tuple:
    """`--logs`：每行一条（原样保留，不解析——`LogLine` 的解析留给需要结构化
    的调用方；诊断规则读的是文本）。

    文件不存在 / 没给 → 空元组。**给了但读不到 → 前置错误**（`PreflightError`
    的调用方语义）：用户明确指了一个文件，读不到就是配置错，不该静默变空。
    """
    if path is None:
        return ()
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"--logs 指的文件不存在：{p}")
    return tuple(p.read_text(encoding="utf-8", errors="replace").splitlines())


def _read_crash_report_file(path: str | Path | None) -> dict:
    """`--crash-report`：解析一份 `.ips`（两段式，`telemetry.collectors`）。"""
    if path is None:
        return {}
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"--crash-report 指的文件不存在：{p}")
    from telemetry.collectors import parse_crash_report
    return parse_crash_report(p.read_text(encoding="utf-8")).as_dict()


def _trace_app_id(trace_db: str | Path) -> str:
    """trace 库自己归一化的 `(app_id, app_build)` 范围内的 **app_id**。

    给 `mta generate` 的 `--bundle-id` 缺省兜底（review_p3_task35 P1-1）：
    coverage 图的权威范围键是 trace 的 `runs.app_bundle_id`（12.1），故
    「不给 `--bundle-id`」不等于「没有 app」——只是让工具自己去问那张图。
    `read_trace_steps` 的判定（多范围 → `ValueError`、空库 → `""`）就是
    这里要的语义：多范围时**不猜**（异常冒给调用方转前置错误），空库时返回
    空串（调用方判空后报「无法确定 app_id」）。

    ⚠️ **库文件不存在时直接返回空串，不 `connect`**：`sqlite3.connect` 对
    不存在的路径会**建库文件**——一次「读」不该在磁盘上留下新文件（本仓的
    最小副作用纪律，`_collect_metrics_if_any` 同款）。文件不存在与空库在
    调用方眼里是同一件事：「trace 里没有 app_id」。
    """
    from graph.builder import read_trace_steps

    if not Path(trace_db).exists():
        return ""
    _steps, app_id, _build = read_trace_steps(trace_db)
    return app_id


def _trace_has_runs(trace_db: str | Path) -> bool:
    """trace 库里有没有**任何观测**（库不存在 / 表没建 / 零 run → `False`）。

    只读探测：`mode=ro` 打开，**不创建**库文件（一次读不该在磁盘上留文件）。
    「文件不是 sqlite 库」这类真错误**不在这里吞**——`sqlite3.DatabaseError`
    冒给调用方转前置错误；它与「还没跑过」是两回事。
    """
    import sqlite3

    p = Path(trace_db)
    if not p.exists():
        return False
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'"
        ).fetchone()
        if has_table is None:
            return False
        return conn.execute("SELECT 1 FROM runs LIMIT 1").fetchone() is not None
    finally:
        conn.close()


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

    try:
        # 解析规则与兜底都在 `build_identity.resolve_app_build`（单一入口）
        # ——源图侧（`build_source_graph`）用的是同一个函数，两面 scope 因此
        # 由构造保证一致（review_p2_task52 P3-1）。
        return bi.resolve_app_build(bi.read_metadata(_metadata_path(args)))
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


def _session_failure_hint(e: Exception) -> str:
    """把 Appium 的原始错误翻成可操作的下一步（真机接入踩过的坑，§3.10）。

    WDA 起不来时 Appium 的原话是「60 秒没起来 / 可能没构建好」，但真机上最
    常见的两个原因都不在这个方向上：① **手机锁屏**——iOS 直接拒绝拉起 runner，
    日志只留 `Connection was refused to port 8100`（实测：devicectl 单独拉起会
    明确报 `device was not, or could not be, unlocked`，而 Appium 路径不吐这句）；
    ② RemoteXPC 隧道没在跑。两种形态都等满 60 秒才超时，值得一句话点破。
    两种错误 mta 看到的外层文案都是 `Unable to launch WebDriverAgent`，据此 gate。
    """
    if "Unable to launch WebDriverAgent" not in str(e):
        return ""
    return ("\n  提示：真机 WDA 起不来，按概率查两件事——"
            "① 手机是否已解锁且没有自动锁屏（锁屏时 iOS 拒绝拉起 runner，"
            "只报 Connection was refused to port 8100）；"
            "② RemoteXPC 隧道是否还在跑"
            "（sudo appium driver run xcuitest tunnel-creation）。"
            "详见 docs/lst_update_playbook.md §3.10")


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

    from cli.pipeline import SessionPipeline
    from executor.guard import EnvKind
    from tracer.storage import TraceStore

    # P2-2（review_m4_task43）：--env-kind 真实接线（10.1）。production
    # 启动必须显式 --allow-production——最前置 fail-loud，不带病建 run 行。
    # 解析单点在 `_resolve_env_kind`（review_p3_task12 P3-3：自主命令的
    # env 来源与 run 同款，两处各写一份必然漂移）。
    env_kind = _resolve_env_kind(args)
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
                    "无 booted 模拟器（真机路线：--udid <物理UDID> 加 "
                    "--app-path <产物.app>；模拟器：启动一个或用 --udid）")
            meta_path = (Path(args.metadata) if args.metadata else
                         Path(args.generated or "repository/generated/local")
                         / "source_metadata.json")
            gr = bi.gate_run_start(
                metadata_path=meta_path, udid=udid,
                bundle_id=args.bundle_id,
                allow=args.allow_metadata_mismatch, env=os.environ,
                app_path=args.app_path)
        except bi.BuildIdentityError as e:
            store.start_run(run_id, suite=args.suite,
                            env_kind=env_kind.value)
            store.end_run(run_id, status="ABORTED", exit_code=3)
            print(f"PREFLIGHT ERROR: build identity 读取失败：{e}")
            print("run: exit 3")
            return 3
        # ⚠️ **`update` 而不是整体赋值**（2026-10-11 实测修）：上面刚写进去的
        # `app_build` 若在这里被覆盖掉，真机路径的 `runs.app_build` 就是
        # **NULL**——而这正是上面那段注释声称已经修好的 P1 遗留。实证：M4
        # Gate 的 `trace.db` 里 `mta run` 的行 `app_build=None`，而同库的
        # `mta explore` 行是 `'local'`；两者一混进 `read_trace_steps`，多范围
        # 判定就 fail-loud（Gate 现场踩到）。只读的那条路径（`--fake-driver`）
        # 没这个覆盖，所以缺陷只在真机路径上现形。
        bi_fields.update({
            "app_git_commit": gr.app.git_commit,
            "metadata_git_commit": gr.meta.git_commit,
            "metadata_build": gr.meta.build,
            "metadata_mismatch": int(gr.mismatch),
            "metadata_mismatch_override": int(gr.override),
        })
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
    #    ⚠️ **装配本体在 `cli.pipeline.wire_device_stack`**（Task 3.4 抽出）：
    #    Dry Run（`generator/dry_run.py` 的调用方）需要同一套装配，各写一份
    #    必然漂移。本命令只负责「解参数 + 报前置错误」，装配语义在那边。
    from cli.pipeline import wire_device_stack
    from executor.guard import EnvKind

    # review_p2_task11 P3-2：app_bundle_id 落 runs 行——Candidate 主键
    # 第一段（app_id, screen_id, target_id），种子清单不能是 NULL。
    if args.bundle_id:
        bi_fields.setdefault("app_bundle_id", args.bundle_id)
    store.start_run(run_id, suite=args.suite, env_kind=env_kind.value,
                    **bi_fields)
    pipeline.store = store
    udid = args.udid
    if not args.fake_driver:
        if not args.bundle_id:
            store.end_run(run_id, status="ABORTED", exit_code=3)
            print("PREFLIGHT ERROR: 真机路径需要 --bundle-id")
            print("run: exit 3")
            return 3
        udid = udid or bi.resolve_booted_udid()
    try:
        stack = wire_device_stack(
            pipeline, env_kind=env_kind, run_id=run_id, udid=udid,
            bundle_id=args.bundle_id, repo=repo,
            fake_driver=args.fake_driver)
    except Exception as e:  # noqa: BLE001 — 设备会话建立失败 = 前置配置错误
        store.end_run(run_id, status="ABORTED", exit_code=3)
        print(f"PREFLIGHT ERROR: 设备会话建立失败：{e}{_session_failure_hint(e)}")
        print("run: exit 3")
        return 3

    # 1b. Batch B（设计 P1 §8.2 的第三条信号）：崩溃探测。
    #     ⚠️ **只真机注入**——fake-driver 没有设备也没有 bundle，注入一个
    #     探不到东西的探针只会白等 12 秒且恒 None（等于没接线却看起来接了）。
    #     注入后，**每个失败出口**都会问一句「App 崩了吗」（`_crash_report`，
    #     每用例至多探测一次），是则记 `APP_CRASH`。否则 `crash_detected`
    #     权重 5 恒 False：App 崩溃先被记成基础设施故障（§8.3），后又被记成
    #     普通步骤失败（§10 的真机实测——App 死了最常见的表现恰恰是
    #     「元素找不到 / 屏认不出来」，根本不经过 `except InfraError`）。
    if not args.fake_driver and args.bundle_id:
        from telemetry.collectors import CrashCollector
        from telemetry.probe import CrashProbe

        pipeline.crash_probe = CrashProbe(
            collector=CrashCollector(), bundle_id=args.bundle_id)

    # 2. 跑（P3-5：统一走 run_all——H10 中止语义/未执行清单只在套件层可达）
    try:
        run = pipeline.run_all(cases, run_id=run_id)
    finally:
        stack.close()

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
            executed_steps=stack.lifecycle.steps_recorded,
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


# --- mta plan（Task 2.4 / P3-08：**首个自主命令**） --------------------------

def _plan_range(args: argparse.Namespace) -> tuple[str, str, dict | None]:
    """`plan` 的 git diff 范围 `(base, head, base_metadata)`。

    - `head` = 本次规划 build 的 `git_commit`（12.3 metadata 顶层），读不到 → `HEAD`；
    - `base` = `--base-build <id>` 的 metadata 的 `git_commit`；**没给 → `HEAD~1`**
      （「上一次提交」——Gate 演示与最常见的用法）。`--base-build` 的 metadata 在
      `<generated 的父目录>/<id>/source_metadata.json`（12.4 布局）；
    - 第三个返回值是基线 metadata（给 `planner.plan(base_metadata=…)` 用），
      没给 `--base-build` 时是 `None`（**没有回退面**，不是「空回退面」）。

    ⚠️ **解析结果一律打给用户**（`_render_plan`）：默认 `HEAD~1` 是「口径」不是
    「猜」——但口径必须可见，否则「我以为是和 main 比」与「其实是和上一次提交比」
    长得一模一样（Task 2.3 的 `unmatched_changed_files` 同一条纪律）。
    """
    from source import build_identity as bi

    metadata = bi.read_metadata(_metadata_path(args))
    head = metadata.get("git_commit") or "HEAD"
    if not args.base_build:
        return "HEAD~1", head, None
    root = (Path(args.generated).parent if getattr(args, "generated", None)
            else Path("repository/generated"))
    base_path = root / args.base_build / "source_metadata.json"
    base_meta = bi.read_metadata(base_path)     # 不存在/坏 JSON → BuildIdentityError
    return (base_meta.get("git_commit") or "HEAD~1"), head, base_meta


def _render_plan(result, *, args, app_build: str, base: str, head: str,
                 changed: tuple[str, ...]) -> None:
    """人读 / `--json` 两种输出（矩阵 #6 的「明示」与矩阵 #5 的「留痕」都在这里可见）。"""
    import json

    plan = result.plan
    if args.json:
        print(json.dumps({
            **plan.model_dump(mode="json"),
            "range": {"base": base, "head": head},
            "changed_files": list(changed),
            # 过程产物（**不落库**，见 `planner/planner.py` 的偏离登记）：
            "notes": list(result.notes),
            "unrankable": [{"testcase_id": u.testcase_id, "reason": u.reason}
                           for u in result.unrankable],
            "audit": list(result.audit),
        }, ensure_ascii=False, indent=2))
        return

    print(f"plan: {plan.plan_id}")
    print(f"  build={app_build}  git_commit={plan.git_commit}")
    print(f"  range: {base}..{head}（改动 {len(changed)} 个文件）")
    if not plan.tasks:
        print("  EMPTY PLAN（原因见下面的 notes）")
    for i, task in enumerate(plan.tasks, 1):
        print(f"  #{i:<3} {task.testcase_id}  priority={task.priority}"
              f"  [{task.source}]")
        for reason in task.reasons:
            print(f"        - {reason}")
    for note in result.notes:
        print(f"  note: {note}")
    for item in result.unrankable:
        print(f"  unrankable: {item.testcase_id} -- {item.reason}")
    for entry in result.audit:
        print(f"  audit: {entry}")
    print(f"plan: {len(plan.tasks)} tasks")


def cmd_plan(args: argparse.Namespace) -> int:
    """`mta plan` —— 设计 §5.3 的 CLI 落点（**首个自主命令**）。

    退出码：0 成功（**含空 Plan**——矩阵 #6 允许「产出空 Plan 并明示原因」，
    空不是错误）；3 前置错误（env/政策/输入读不到/坏 git ref）。

    **调用顺序照 F7 的语义**：第一行过前置（`load_policy` → `_check_autonomous_env`），
    过了才做别的事——「启动时校验」不是「跑一半才拦」。
    """
    from agents.policy_config import load_policy

    # 1. F7 前置（**第一行**）。`env_kind` 走唯一解析点（不许字面量：
    #    硬编码 "sandbox" 会让 F7 退化成永真，有守卫钉这条）。
    #    `--policy` 没给 → 不传参（= `_UNSET` 语义：读默认路径，读不到用内置
    #    默认值，矩阵 #4）；给了 → 该路径**不存在即报错**（Task 1.2 的 P3-2 语义）。
    policy = (load_policy(args.policy) if args.policy is not None
              else load_policy())
    _check_autonomous_env(policy, env_kind=_resolve_env_kind(args),
                          command="plan")

    from cli.pipeline import SessionPipeline
    from experience import SQLiteExperienceStore
    from knowledge import build_knowledge
    from planner.models import PlannerInput
    from planner.planner import plan as run_plan
    from source import build_identity as bi
    from source.git_diff import GitDiffError, changed_files
    from source.vcs import GitError, run_git

    # 2. 输入装配。三类前置错误各自 fail-loud（都带可诊断的消息）：
    #    metadata 读不到 / 用例发现失败 / git 范围不合法。
    try:
        metadata = bi.read_metadata(_metadata_path(args))
    except Exception as e:  # noqa: BLE001 — BuildIdentityError / OSError 都归前置
        print(f"PREFLIGHT ERROR: 读不到 12.3 metadata：{e}")
        print("plan: exit 3")
        return 3
    app_build = args.build or bi.resolve_app_build(metadata)
    try:
        base, head_ref, base_meta = _plan_range(args)
    except Exception as e:  # noqa: BLE001 — 基线 metadata 读不到同样归前置
        print(f"PREFLIGHT ERROR: --base-build 的 metadata 读不到：{e}")
        print("plan: exit 3")
        return 3

    repo_root = args.repo_root
    try:
        changeset = changed_files(repo_root, base=base, head=head_ref)
        # `git_commit` 必须是**确定的 sha**：`plan_id` 由 (app_build, git_commit,
        # changed_files) 内容寻址，而 `HEAD`/`HEAD~1` 是**移动的 ref**——两个
        # 不同的提交会算出同一个 plan_id，后写的**静默覆盖**前一份
        # （review_p3_task14 登记过的那条：M2 要做「Plan 可追溯到确定的 commit」
        # 需额外一次 rev-parse）。
        git_commit = run_git(repo_root, "rev-parse", head_ref).strip()
    except (GitDiffError, GitError) as e:
        print(f"PREFLIGHT ERROR: 取 git 改动失败：{e}")
        print("plan: exit 3")
        return 3
    changed = changeset.changed_files

    pipeline = SessionPipeline(suites_root=args.suites_root,
                               secrets=EnvSecretProvider(),
                               # `app_id` 是经验库主键的第一段（`mta run` 用
                               # `--bundle-id`）。`plan` 只调 `impact_of` /
                               # `trace_history`，**不碰** experience_lookup /
                               # graph_query，所以这里不需要它——不为了「以后
                               # 可能用」加一个 flag（无消费者就不加）。
                               app_id="", app_build=app_build)
    try:
        cases = pipeline.discover()
    except SessionPipeline.PreflightError as e:
        print(f"PREFLIGHT ERROR: {e}")
        print("plan: exit 3")
        return 3

    repository = Repository.from_dirs(
        generated_root=getattr(args, "generated", None),
        overrides_root=args.overrides)
    # F3：知识底座**只能**经 `knowledge.build_knowledge` 装配（有守卫钉「构造点
    # 唯一」）。本命令是它的**第一个生产调用者**。
    knowledge = build_knowledge(
        experience_store=SQLiteExperienceStore(args.exp_db),
        trace_db=args.db, cases=cases, app_build=app_build)

    # 3. LLM 与 `mta run` 同款：`--no-llm` 或没有 LLM_API_KEY → 无 LLM
    #    （确定性半边照常，reasons 只剩规则摘要——plan Steps 的降级要求）。
    llm = budget = None
    if not args.no_llm and os.environ.get("LLM_API_KEY"):
        from llm.budget import LLMBudget
        from llm.provider import LLMProvider

        llm, budget = LLMProvider(), LLMBudget()

    # 3b. 「没有历史」与「历史读不出来」必须分清（`planner.plan` 的
    #     `no_history_reason` 边界）：
    #     - trace 库**不存在** → 合法（还没跑过用例）：历史项按 0.0 计，
    #       原因进 notes + 每条 reasons 的 history 行；
    #     - 库存在但读不了（坏 schema / 表缺失 / 多 build 歧义）→ 交给
    #       `plan()` 抛，下面 catch 成 exit 3（fail-loud，不按 0.0 兜）。
    no_history_reason = None
    if not Path(args.db).exists():
        no_history_reason = (
            f"trace 库尚不存在（{args.db}）→ 历史失败率按无历史（0.0）计；"
            f"先跑一次 `mta run` 才有历史，或用 --db 指向已有的 trace 库")

    # 4. 规划（编排层纯逻辑 + 一次可选 LLM 调用）
    try:
        result = run_plan(
            PlannerInput(app_build=app_build, git_commit=git_commit,
                         changed_files=list(changed)),
            knowledge=knowledge, cases=cases, metadata=metadata,
            repository=repository, llm=llm, budget=budget,
            base_metadata=base_meta, no_history_reason=no_history_reason)
    except ValueError as e:
        # `trace_history` 的域错误（库不可读 / 多 build 歧义）——**前置输入问题**，
        # 不是规划结论。不按「无历史」兜（那正是 `no_history_reason` 划出去的边界）。
        print(f"PREFLIGHT ERROR: 读 trace 历史失败：{e}")
        print("plan: exit 3")
        return 3

    # 5. 落库（`test_plans`，upsert；`plan_id` 内容寻址 → 同输入重跑幂等）。
    #    `notes`/`unrankable`/`audit` 是**过程产物**，不进这张表（它没有那些列）
    #    ——所以「必须随 Plan 复核」的结论都在 `reasons` 里（见 Task 2.3 的
    #    drift 标记）。
    from agents.storage import SQLiteAgentStore

    SQLiteAgentStore(args.agent_db).save_plan(**result.plan.to_store_dict())

    # 6. 输出
    _render_plan(result, args=args, app_build=app_build, base=base,
                 head=head_ref, changed=changed)
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    """`mta generate` —— 设计 §6.1 主流程的 CLI 落点（Task 3.5 / P3-13）。

    退出码：0 成功（**含零 gap**——空不是错误，与矩阵 #6 的空 Plan 同款）；
    3 前置错误（env/政策/plan 不存在/metadata 读不到/设备装配失败）。

    **调用顺序照 F7 的语义**：第一行过前置（`load_policy` →
    `_check_autonomous_env`），过了才做别的事——M1 的 AST 守卫扫本函数体。

    **sandbox 硬前置**（在任何写库/装配之前）：`run_candidate` 的闸门在生成
    循环**内部**抛 `DryRunRefused`，而 `generate` 不接它——不前置拦，staging
    下会先入库一部分候选再炸（部分写入正是本仓最防的形态）。
    """
    from agents.policy_config import load_policy

    # 1. F7 前置（**第一行**）。env_kind 走唯一解析点（不许字面量）。
    policy = (load_policy(args.policy) if args.policy is not None
              else load_policy())
    env_kind = _resolve_env_kind(args)
    _check_autonomous_env(policy, env_kind=env_kind, command="generate")

    from executor.guard import EnvKind
    if env_kind is not EnvKind.SANDBOX:
        # Dry Run 只在 sandbox（F7 / 矩阵 #9）。这里的拒绝是**前置**的：让
        # 「环境不对」在写任何东西之前就 fail-loud，而不是跑一半才抛。
        print(f"PREFLIGHT ERROR: generate 只在 sandbox 运行（F7 / 矩阵 #9），"
              f"got --env-kind {env_kind.value}——Dry Run 会真跑用例")
        print("generate: exit 3")
        return 3

    from cli.pipeline import SessionPipeline, wire_device_stack
    from experience import SQLiteExperienceStore
    from generator.coverage import coverage_gap
    from generator.generator import generate as run_generate
    from knowledge import build_knowledge
    from source import build_identity as bi

    # 2. 输入前置：plan 必须存在（追溯锚点）。
    from agents.storage import SQLiteAgentStore
    agent_store = SQLiteAgentStore(args.agent_db)
    plan = agent_store.get_plan(args.gap_from)
    if plan is None:
        print(f"PREFLIGHT ERROR: plan {args.gap_from!r} 不存在"
              f"（--gap-from 取 `mta plan` 的输出；库 {args.agent_db}）")
        print("generate: exit 3")
        return 3

    try:
        metadata = bi.read_metadata(_metadata_path(args))
    except Exception as e:  # noqa: BLE001 — 与 cmd_plan 同款前置
        print(f"PREFLIGHT ERROR: 读不到 12.3 metadata：{e}")
        print("generate: exit 3")
        return 3
    app_build = args.build or bi.resolve_app_build(metadata)

    pipeline = SessionPipeline(suites_root=args.suites_root,
                               secrets=EnvSecretProvider(),
                               app_id=args.bundle_id or "",
                               app_build=app_build)
    try:
        cases = pipeline.discover()
    except SessionPipeline.PreflightError as e:
        print(f"PREFLIGHT ERROR: {e}")
        print("generate: exit 3")
        return 3

    repository = Repository.from_dirs(
        generated_root=getattr(args, "generated", None),
        overrides_root=args.overrides)
    knowledge = build_knowledge(
        experience_store=SQLiteExperienceStore(args.exp_db),
        trace_db=args.db, cases=cases, app_build=app_build)

    # 3. coverage gap **现场重算**（planner 目前不产 generated_gap 任务，
    #    `planner/planner.py:102` 明文——故 plan 只作追溯锚点，不读它的 tasks
    #    当 gap 来源）。图从 trace 现建（12.1 定档）。
    #    ⚠️ `graph_query` 的第一参是 **app_id**（= `runs.app_bundle_id`），
    #    不是 build：`P2KnowledgeSources.graph_query` 会 fail-loud 挡住跨 app
    #    混图。真机路径的 `--bundle-id` 与 trace 里的值必须一致（同 `mta run`）。
    #
    #    ⚠️ **app_id 是前置，不是「缺省空串」**（review_p3_task35 P1-1）：
    #    `--bundle-id` 的 help 早先写「--fake-driver 时不需要」，于是最常用的
    #    本地路径（fake driver + 真工程 trace）会拿 `""` 去查 `com.demo` 的图
    #    → `graph_query` 的 fail-loud `ValueError` **穿透成 traceback**（工具
    #    契约要求前置错误 exit 3）。缺省时从 trace 自己归一化的 app_id 取——
    #    那是「本次要查的那张图属于谁」的权威来源。
    #
    #    ⚠️ trace **为空**（或库文件还不存在）时 app_id 是 `""`，与 trace 的
    #    `""` 相符 → 空图 → 零 gap → exit 0 + note。这**不是**错误：矩阵 #6
    #    的语义就是「没有历史观测 = 没有可算的 gap」，不该因为「没给
    #    --bundle-id」把它变成前置失败。而 `graph_query` 对**未初始化**的库
    #    会 fail-loud（`no such table: runs`）——那是给「库被指错」的诊断，
    #    不是给「还没跑过」的；两者在这里靠**只读探测**分开（前者照旧 exit 3）。
    # ⚠️ `app_id` 的解析**必须在 try 之内**（review_p3_task35 round 2 P1-A）：
    # `_trace_app_id` 自己就 `sqlite3.connect` + 查询，目录/非 sqlite 文件/多范围库
    # 三种坏输入都由**它**抛（`OperationalError` / `DatabaseError` / `ValueError`）。
    # 写在 try 之外时，同样三种输入给了 `--bundle-id` 是 exit 3、不给就是 traceback
    # rc=1——P1-1「前置错误不再 traceback」只在一半输入上成立。
    # 捕 `sqlite3.Error`（父类）而不是 `DatabaseError`（子类）：P3-4 的同一判据，
    # 「文件不是 sqlite 库」抛的是父类本身。
    import sqlite3

    from graph.builder import build_runtime_graph

    try:
        app_id = args.bundle_id or _trace_app_id(args.db)
        graph = (knowledge.graph_query(app_id) if _trace_has_runs(args.db)
                 else build_runtime_graph([], app_id=app_id,
                                          app_build=app_build))
    except (ValueError, sqlite3.Error) as e:
        # `graph_query` / `read_trace_steps` 的 fail-loud 是**正确**的
        # （不跨 app 混图、两个 build 不混进一张图）；非法的是「调用方拿一个
        # 没核对过的 app_id 来查」或「库根本读不了」。前置错误 → exit 3。
        print(f"PREFLIGHT ERROR: 建不了 coverage 图：{e}")
        print("generate: exit 3")
        return 3
    gaps = coverage_gap(graph, cases)
    print(f"generate: plan={args.gap_from}  build={app_build}  "
          f"gaps={len(gaps)}（现场重算）")

    # 4. LLM：与 `mta run` / `mta plan` 同款的门槛（`--no-llm` 或没有
    #    LLM_API_KEY → 无 LLM；生成必须有 LLM，故等于「不生成」）。
    llm = budget = None
    if not args.no_llm and os.environ.get("LLM_API_KEY"):
        from llm.budget import LLMBudget
        from llm.provider import LLMProvider

        llm, budget = LLMProvider(), LLMBudget()

    # 5. 设备侧装配（3.4 抽的公共入口；`--fake-driver` 直通）。Dry Run 的
    #    `execute` 注入 `pipeline.run_case`——单用例执行入口唯一，不新写路径。
    from environment.capabilities import DeviceType
    import uuid

    run_id = f"gen_{uuid.uuid4().hex[:8]}"
    # ⚠️ **两个 store，两个身份**（同 `cmd_run`）：
    #   - `agent_store`（agent.db）= 候选/审核的落点（`generate` 的 store 参数）；
    #   - `trace_store`（trace.db）= **Lifecycle 的记录面**——`Lifecycle` 调的是
    #     `start_testcase`/`record_step`/`end_testcase`，那是 TraceStore 的契约。
    # 给 `pipeline.store` 塞 agent_store 会让 Dry Run 的第一次 `begin_testcase`
    # 以 `AttributeError` 炸掉（Lifecycle 的 store 参数**必须**是 TraceStore）。
    from tracer.storage import TraceStore
    trace_store = TraceStore(args.db)
    trace_store.start_run(run_id, suite=args.suite or "", device_type="simulator",
                          app_build=app_build, app_bundle_id=app_id)
    pipeline.store = trace_store
    udid = args.udid
    if not args.fake_driver:
        if not args.bundle_id:
            print("PREFLIGHT ERROR: 真机路径需要 --bundle-id")
            print("generate: exit 3")
            return 3
        udid = udid or bi.resolve_booted_udid()
    try:
        stack = wire_device_stack(
            pipeline, env_kind=env_kind, run_id=run_id, udid=udid,
            bundle_id=args.bundle_id, repo=repository,
            fake_driver=args.fake_driver)
    except Exception as e:  # noqa: BLE001 — 设备会话建立失败 = 前置配置错误
        trace_store.end_run(run_id, status="ABORTED", exit_code=3)
        print(f"PREFLIGHT ERROR: 设备会话建立失败：{e}{_session_failure_hint(e)}")
        print("generate: exit 3")
        return 3

    def _execute(tc):
        return pipeline.run_case(stack.runner, stack.lifecycle, tc,
                                 run_id=run_id)

    try:
        result = run_generate(
            gaps, knowledge=knowledge, app_id=app_id,
            existing_cases=cases, repository=repository,
            secrets=EnvSecretProvider(), store=agent_store,
            llm=llm, budget=budget, execute=_execute,
            env_kind=env_kind, device_type=DeviceType.SIMULATOR,
            suite=args.suite, requirement=args.requirement)
        trace_store.end_run(run_id, status="PASS", exit_code=0)
    except Exception as e:  # noqa: BLE001 — 生成器炸了，run 行必须收口
        # ⚠️ review_p3_task35 P3-8：`end_run` 早先在外层、异常穿透时**不执行**
        # → trace 里留下 `status='RUNNING'` / `end_time=NULL` 的行（「跑了一半」
        # 的持久化痕迹，与 `cmd_run` 的成对语义不一致）。生成器的异常一律
        # ABORTED + 前置错误 exit 3（`generate` 的退出码契约只有 0/3）。
        trace_store.end_run(run_id, status="ABORTED", exit_code=3)
        print(f"PREFLIGHT ERROR: 生成失败：{type(e).__name__}: {e}")
        print("generate: exit 3")
        return 3
    finally:
        stack.close()

    # 6. 输出
    for c in result.candidates:
        print(f"candidate: {c.candidate_id}  dry_run={c.dry_run_status}"
              f"  review={c.review_status}  suite={args.suite or '(未声明)'}")
    for r in result.rejections:
        extra = f" duplicate_of={r['duplicate_of']}" if r.get("duplicate_of") else ""
        print(f"rejected: {r.get('candidate_id') or '(无 id)'}"
              f"  reason={r['reason']}{extra}")
    for note in result.notes:
        print(f"  note: {note}")
    print(f"generate: {len(result.candidates)} candidates, "
          f"{len(result.rejections)} rejected/skipped, "
          f"{result.llm_calls} LLM calls")
    return 0


# `mta explore` 的 trigger 词表（F13 纯函数；`graph/builder.py` 定义的
# `<step_type>:<target_id>` 形状）。**只把「驱动 App 走一步」的步骤翻成动作**：
# `wait_for` / `assert_*` 是**观测**不是动作——再 wait 一次不会让 App 走到新屏，
# 翻成动作只会让探索空转；`launch_app` 没有 target 也没有可重放的元素。
_EXPLORE_TRIGGER_TOOLS = {"tap", "input"}


def _explore_action_for(transition):
    """一条 source 转移 → 可重放的探索动作（`None` = 翻不成，跳过）。

    ⚠️ **不能把 trigger 整串当 target**：trigger 是 `<step_type>:<target_id>`
    （`graph/builder._trigger_of`），`tap:HomeView.go_profile` 整串交给
    `Repository.resolve` 会被拆成 screen=`tap:HomeView` + elem=`go_profile`
    → 解析失败。这里按 `graph/impact.py::trigger_element` 的同一口径拆开
    （**不手写第二份解析**——那条函数就是为这个形状写的）。

    返回值是 `explorer.policy.ProposedAction`：`make_frontier_decider` 的
    `action_for` 契约（`None` = 跳过，不是结束）。
    """
    from explorer.policy import ProposedAction
    from graph.impact import trigger_element

    raw = (getattr(transition, "trigger", "") or "")
    step_type, sep, tail = raw.partition(":")
    if not sep:
        return None
    if step_type in _EXPLORE_TRIGGER_TOOLS:
        target = trigger_element(raw)
        # 元素 id 单独出现会在跨屏同名时歧义（`Repository.resolve` 的短名
        # 规则要求全局唯一）——转移两端都在手里，用限定名 `Screen.elem`
        # （`resolve` 的 `Screen.elem` 分支还会校验归属，写错了 fail-loud）。
        if not target:
            return None
        if "." not in target:
            # 短名 → 限定名。**from_screen 为空时返回 None**：补出来的
            # `.elem` 会被 `Repository.resolve` 拆成 screen="" + elem，
            # 而 `AgentToolkit.call` 对 `ValueError` 子类**故意原样重抛**
            # （坏参数 = 调用方写错，不该被翻成「工具执行失败」），于是它会
            # 从 `Explorer.run()` 冒出去、把整场探索打死。当前 `graph/` 的
            # 构造器不会产出空 from_screen，这里是**契约兜底**：纯函数不
            # 制造自己都知道解析不了的值。
            screen = getattr(transition, "from_screen", "") or ""
            if not screen:
                return None
            target = f"{screen}.{target}"
        return ProposedAction(tool=step_type, target=target)
    if step_type == "swipe":
        return ProposedAction(tool="swipe", direction=(tail or "up"))
    if step_type == "back":
        return ProposedAction(tool="back")
    return None


def cmd_explore(args: argparse.Namespace) -> int:
    """`mta explore` —— Bounded Exploration 的 CLI 落点（Task 4.6 / P3-19）。

    退出码：0 成功（**含 `*_BUDGET_EXCEEDED` 与 `LOOP_DETECTED`**——F11/矩阵
    #11/#12：预算耗尽与循环检测是**结论**不是失败，与矩阵 #6 的空 Plan 同款）；
    3 前置错误（env/政策/输入读不到/预算文件坏/设备装配失败）。

    **调用顺序照 F7 的语义**：第一行过前置（`load_policy` →
    `_check_autonomous_env` → `_check_exploration_env`），过了才做别的事
    ——「启动时校验」不是「跑一半才拦」。两层前置是**与**的关系、各管各的
    判据（见 `_check_exploration_env` 的 docstring）：公共前置管总开关与
    production 一票否决，探索专属前置管 **sandbox-only（staging 也拒）** 与
    simulator。

    装配与 `mta run` 同路：设备侧一律经 `cli.pipeline.wire_device_stack`
    （**唯一实现**），探索动作一律经 `AgentToolkit`（F12）。
    """
    from agents.policy_config import PolicyConfigError, load_policy

    # 1. F7 前置（**第一行**）。`env_kind` 走唯一解析点（不许字面量：
    #    硬编码 "sandbox" 会让 F7 退化成永真，有守卫钉这条）。
    policy = (load_policy(args.policy) if args.policy is not None
              else load_policy())
    _check_autonomous_env(policy, env_kind=_resolve_env_kind(args),
                          command="explore")
    _check_exploration_env(policy, env_kind=_resolve_env_kind(args),
                           device_type=args.device_type, command="explore")

    from agents.storage import SQLiteAgentStore
    from cli.pipeline import SessionPipeline, wire_device_stack
    from environment.capabilities import DeviceType
    from explorer.agent import Explorer
    from explorer.frontier import make_frontier_decider
    from explorer.state import ExplorationBudget
    from graph import SOURCE, GraphStore
    from graph.builder import read_trace_steps
    from llm.budget import LLMBudget
    from repository.resolver import Repository
    from tracer.storage import TraceStore

    # 2. 预算（F11）。**在任何写库之前**：坏文件是前置错误，不该留下半张库。
    try:
        budget = (ExplorationBudget.from_file(args.budget)
                  if args.budget is not None
                  else ExplorationBudget.from_policy(policy.exploration))
    except PolicyConfigError as e:
        print(f"PREFLIGHT ERROR: 预算不可用：{e}")
        print("explore: exit 3")
        return 3
    problem = _budget_problem(budget)
    if problem is not None:
        print(f"PREFLIGHT ERROR: 预算不可用：{problem}")
        print("explore: exit 3")
        return 3

    # 3. 装配（与 `mta run` 同路：Repository / trace 观测面 / 声明面）。
    #
    # ⚠️ **trace 库不存在 ≠ 前置错误**（与 `cmd_generate` 的 P1-1 同款处置）：
    # 没跑过 `mta run` 的工程照样可以探索——观测面为空而已。`read_trace_steps`
    # 对**未初始化**的库会 `no such table: runs`（那是给「库被指错」的诊断），
    # 故先用只读探测 `_trace_has_runs` 把「还没跑过」与「库读不了」分开。
    # 真正读不了（坏 schema / 多范围歧义）仍 fail-loud → exit 3。
    import sqlite3

    repository = _load_repository(args)
    steps: list = []
    trace_app_id = ""
    trace_build = ""
    # `_trace_has_runs` **本身**也可能炸（「文件不是 sqlite 库」这类真错误
    # 它按契约不吞，见其 docstring），故探测也在 try 里——`cmd_generate`
    # 是把两个调用包进同一个 try 的，这里同样不许有裸奔的调用。
    try:
        has_runs = _trace_has_runs(args.db)
        if has_runs:
            steps, trace_app_id, trace_build = read_trace_steps(args.db)
    except (ValueError, sqlite3.Error) as e:
        # 多范围歧义 / 库坏 / 文件不是库：图必须限定单一范围，前置错误。
        print(f"PREFLIGHT ERROR: 读 trace 失败：{e}")
        print("explore: exit 3")
        return 3

    # app_id 是图的范围第一段。缺 `--bundle-id` 时从 trace 归一化的值取
    # （与 `cmd_generate` 的 P1-1 同一条：trace 的 `runs.app_bundle_id` 是
    # 「这张图属于谁」的权威来源）；两处都没有 → 前置错误（**不猜**）。
    app_id = args.bundle_id or trace_app_id
    if not app_id:
        print("PREFLIGHT ERROR: 拿不到 app_id——给 `--bundle-id`，或先跑一次 "
              "`mta run`（trace 的 runs.app_bundle_id 是图的权威范围）")
        print("explore: exit 3")
        return 3

    # build 是图的范围第二段，**必须与声明面对齐**——不对齐的后果是
    # `load_graph` 查不到那一面 → frontier 恒空 → 零发现，而输出上它长得像
    # 「真的探完了」（review_p3_task12 P3-4 的「配置错误表现为功能退化」）。
    # 所以这里**不做 `_resolve_app_build` 的静默兜底**：`--build` 没给就必须
    # 读得到 metadata（`mta run` 那边 build 只是个 E7 集合元素，兜底无害；
    # 这边它是判据）。
    from source import build_identity as bi

    if args.build:
        app_build = args.build
    else:
        try:
            app_build = bi.resolve_app_build(bi.read_metadata(_metadata_path(args)))
        except Exception as e:  # noqa: BLE001 — 读不到/坏 JSON 都是前置
            print(f"PREFLIGHT ERROR: 读不到 12.3 metadata（它是 `--build` 的"
                  f"来源，build 决定声明面的范围）：{e}")
            print("explore: exit 3")
            return 3

    # trace 的**观测面**必须与本次的声明面同范围。`read_trace_steps` 只在
    # 「同一个库里混了多个 (app_id, app_build)」时 fail-loud——若库里只有
    # **一个陈旧的**范围（比如上一版 `v2` 的 trace）而本次 `--build local`，
    # 那个守卫不会响，运行时图的 `transition_key` 却全来自 `v2`：
    # `select_frontier` 会把**已经走过的边**当成没走过（novelty 误判、重走
    # 已知路径）。所以在这里显式对齐（不静默挑一个）。
    if trace_build and trace_build != app_build:
        print(f"PREFLIGHT ERROR: trace 的观测范围是 build={trace_build!r}，"
              f"与本次 build={app_build!r} 不符——观测面与声明面必须同范围")
        print("explore: exit 3")
        return 3

    graph_store = GraphStore(args.graph_db)
    source_graph = graph_store.load_graph(app_id, app_build, SOURCE)
    runtime_graph = _runtime_graph_for(args.db, app_id=app_id,
                                       app_build=app_build,
                                       steps=steps)
    if source_graph.is_empty:
        # 声明面为空 → frontier 恒空 → 探索一步都走不了。**说清楚为什么**
        # （静默的 `FRONTIER_EMPTY` 与「真的探完了」在输出上长得一样）。
        print(f"note: 声明面为空（{args.graph_db} 的 app={app_id!r} "
              f"build={app_build!r}）→ 没有可探索的 frontier 项。"
              f"先 `mta graph build --bundle-id {app_id} --build {app_build}`"
              f"（或确认发现事件已被人工 ACCEPT 登记）")

    # 4. 任务行（`agent_tasks` 是每步审计的根，F1）。task_id 带随机后缀：
    #    同一次探索重跑要各自留痕，不能被 upsert 覆盖掉上一轮的 trace。
    #    ⚠️ `SQLiteAgentStore(...)` **推迟到这里**：它的 `__init__` 会建库 +
    #    跑迁移（在磁盘上留文件）。放在前面会让「拿不到 app_id」这类**纯前置
    #    错误**也留下一个新 agent.db——违反本仓「一次失败的读不该产生新文件」
    #    的纪律（`_trace_has_runs` / `_collect_metrics_if_any` 同款）。
    import uuid

    agent_db = SQLiteAgentStore(args.agent_db)
    task_id = f"exp_{uuid.uuid4().hex[:8]}"
    agent_db.create_task(task_id, agent_type="explorer", goal=args.goal,
                         constraints={"budget": {
                             "max_steps": budget.max_steps,
                             "max_duration_seconds": budget.max_duration_seconds,
                             "max_llm_calls": budget.max_llm_calls,
                             "max_repeated_state": budget.max_repeated_state,
                         }})

    # 5. 设备侧装配（**唯一实现**：`cli.pipeline.wire_device_stack`）。
    run_id = f"explore_{uuid.uuid4().hex[:8]}"
    trace_store = TraceStore(args.db)
    trace_store.start_run(run_id, suite=args.suite or "", env_kind="sandbox",
                          device_type="simulator", app_build=app_build,
                          app_bundle_id=app_id)
    pipeline = SessionPipeline(suites_root=args.suites_root,
                               secrets=EnvSecretProvider(), app_id=app_id,
                               app_build=app_build)
    pipeline.store = trace_store
    try:
        stack = wire_device_stack(
            pipeline, env_kind=_resolve_env_kind(args), run_id=run_id,
            udid=args.udid, bundle_id=args.bundle_id, repo=repository)
    except Exception as e:  # noqa: BLE001 — 设备会话建立失败 = 前置配置错误
        trace_store.end_run(run_id, status="ABORTED", exit_code=3)
        agent_db.update_task_state(task_id, "ESCALATED",
                                   outcome="DEVICE_UNAVAILABLE")
        print(f"PREFLIGHT ERROR: 设备会话建立失败：{e}{_session_failure_hint(e)}")
        print("explore: exit 3")
        return 3

    # 5b. **把 App 带到前台**（设计 §7.1 的第一步是 Observe，而 Observe 的前提
    #     是「App 在屏上」）。不做这一步的后果在 M4 Gate 上实测到了：冷模拟器
    #     上 `activate_app` 之前 `page_source` 是 **springboard**（App 根本没起），
    #     `current_screen` 恒 `CURRENT_SCREEN_UNKNOWN` → frontier 恒空 →
    #     输出 `outcome=FRONTIER_EMPTY steps=0`——**与「真的探完了」逐字相同**。
    #     这是本仓最防的形态（配置错误伪装成功能退化）。
    #     `AppSession.launch()` 无参走 `activate_app`：已在前台时**幂等**，
    #     不会把调用方手工导航到的状态重置掉（`-UITestReset` 只在带参时生效）。
    try:
        pipeline.deps.app.launch()
    except Exception as e:  # noqa: BLE001 — 起不来 = 前置配置错误
        trace_store.end_run(run_id, status="ABORTED", exit_code=3)
        agent_db.update_task_state(task_id, "ESCALATED",
                                   outcome="APP_LAUNCH_FAILED")
        # ⚠️ `stack.close()` **必须在这里显式调**：`finally` 只覆盖它后面的
        # 主循环（`wire_device_stack` 已经 `connect()` 成功，Appium 会话是
        # 活的）。漏掉它就是 `wire_device_stack` 刚修过的那个泄漏（P2-3），
        # 在被抬高一层的调用点上重犯。
        stack.close()
        print(f"PREFLIGHT ERROR: App 起不来（{app_id}）：{e}")
        print("explore: exit 3")
        return 3

    # 6. 工具层（F12：探索动作的唯一通路）。**只注入它真用得上的依赖**：
    #    executor 是设备；locator_for / repository 是定位与风险（`tap` 的
    #    Guard 输入）；wait_engine / assertion_engine 给 `wait` / `assert_*`；
    #    agent_db + task_id 是 BLOCK 留痕（缺了 `ToolkitAuditError`）。
    from agents.tools import AgentToolkit
    from executor.assertion import AssertionEngine
    from executor.guard import Guard
    from executor.wait import WaitEngine

    locator_for = pipeline._locator_for(stack.runner)
    toolkit = AgentToolkit(
        guard=Guard(_resolve_env_kind(args)), executor=stack.runner.ex,
        locator_for=locator_for, repository=repository,
        device_session=stack.device_session,
        wait_engine=WaitEngine(stack.runner.ex, locator_for),
        assertion_engine=AssertionEngine(stack.runner.ex, locator_for),
        agent_db=agent_db, task_id=task_id, build=app_build)

    # 7. 跑（主循环的每一步都落 agent_trace）。
    decider = make_frontier_decider(source_graph, runtime_graph,
                                    action_for=_explore_action_for)
    explorer = Explorer(
        toolkit=toolkit, graph=source_graph, agent_db=agent_db,
        task_id=task_id, decider=decider, repository=repository,
        build=app_build, budget=budget,
        llm_budget=LLMBudget(max_calls_per_run=budget.max_llm_calls),
        discovery_enabled=True)
    try:
        result = explorer.run()
        # ⚠️ **成功路径也必须收口 run 行**（review_p3_task35 P3-8 的同一条，
        # 在 `cmd_generate` 上修过、这里又漏了）：不写这一行，`runs` 里留下的
        # 是 `status='RUNNING'` / `end_time=NULL`——而 `TraceStore.__init__`
        # 会把**所有** RUNNING 行扫成 `FAIL`（那是给「进程异常中断」的兜底）。
        # 于是一次**成功**的探索在库里显示为 FAIL：M4 Gate 现场实测，同库的
        # explore 行全是 `status='FAIL' exit_code=None`。
        # 终态词用 `*_BUDGET_EXCEEDED` / `LOOP_DETECTED` 这些**探索结论**：
        # 它们是 F11/矩阵 #11/#12 的正常收束，落 `PASS` 才是如实的。
        trace_store.end_run(run_id, status="PASS", exit_code=0)
    except Exception as e:  # noqa: BLE001 — 探索炸了，run 行必须收口
        trace_store.end_run(run_id, status="ABORTED", exit_code=3)
        # `Explorer.run()` 一开始就把 task 置 `EXECUTING`，只在**正常**返回时
        # 才写 `IDLE`。中途抛错若不在这里收口，`agent_tasks` 会永远停在
        # `EXECUTING`/`outcome=NULL`——审计根上挂着一个「还在跑」的僵尸任务
        # （与上面两条前置失败路径同款收口）。
        agent_db.update_task_state(task_id, "ESCALATED",
                                   outcome="EXPLORATION_FAILED")
        print(f"PREFLIGHT ERROR: 探索失败：{type(e).__name__}: {e}")
        print("explore: exit 3")
        return 3
    finally:
        stack.close()

    # 8. 输出（终态词是 F11/矩阵 #11/#12 的**结论**，退出码不区分它们）。
    #    ⚠️ **只列本次任务的事件**：`list_discovery_events()` 返回的是**整库**
    #    （含历史探索的），直接拿它当「本次发现」会让输出在同一个库上重跑时
    #    报出别人的发现——M4 Gate 现场实测：G4/G5 两轮各自打印了 G3 那条
    #    `disc_0731f877…`，而它们各自的探索根本没撞见新屏。审计输出必须是
    #    「这一轮做了什么」，不是「这个库里有什么」。
    events = [e for e in agent_db.list_discovery_events()
              if e.get("exploration_task_id") == task_id]
    for e in events:
        detail = e.get("detail") or {}
        print(f"discovery: {e['event_id']}  kind={e['kind']}  "
              f"screen={detail.get('screen_id')}  review={e['review_status']}")
    for entry in _explore_audit(agent_db, task_id):
        print(f"  audit: {entry}")
    print(f"explore: task={task_id}  outcome={result.outcome.value}  "
          f"steps={result.steps}  screens={len(result.visited_fingerprints)}  "
          f"discoveries={len(events)}  budget=steps<={budget.max_steps}")
    return 0


def _budget_problem(budget) -> str | None:
    """四维预算里**模型层收不下**的取值（当前只有 `max_repeated_state`）。

    `ExplorationPolicy` 对四维都只约束 `ge=0`（负值 / 错型在模型层就 fail-loud），
    但 `max_repeated_state` 的「必须为正」住在 `explorer.loop_detector.is_loop`
    ——它**在探索跑到第一步之后**才抛。若不在前置挡住，`--budget` 里写
    `max_repeated_state: 0` 会先建库、建任务行、起设备、拉起 App，然后才以
    `探索失败` 收场：正是「坏文件是前置错误、不该留下半张库」要防的形态。
    这里按 `is_loop` 的**同一口径**（阈值必须为正）在启动期判定，不写第二份
    语义——两者对同一配置的结论必须一致。

    返回人读的错误串；合法则 `None`。
    """
    if budget.max_repeated_state <= 0:
        return (f"max_repeated_state 必须为正，got {budget.max_repeated_state}"
                f"（重复状态阈值是 loop detector 的判据，0 让它永不生效）")
    return None


def _runtime_graph_for(trace_db, *, app_id: str, app_build: str, steps):
    """trace 观测面（**空库也要有图对象**——`select_frontier` 需要两面）。

    `read_trace_steps` 已经在上面读过（前置错误要在装配之前暴露），这里只把
    行翻成图——**不重复读库**（同一份数据读两遍必漂移）。
    """
    from graph.builder import build_runtime_graph

    return build_runtime_graph(steps, app_id=app_id, app_build=app_build)


def _explore_audit(agent_db, task_id) -> list[str]:
    """探索的审计摘要（给人读的几行；原始留痕在 `agent_trace` 里）。"""
    out: list[str] = []
    for entry in agent_db.get_trace(task_id):
        if entry.guard_result == "BLOCK":
            out.append(f"step {entry.step_index} BLOCK "
                       f"risk={entry.decision.get('risk')} "
                       f"target={entry.decision.get('target')}")
        elif entry.decision.get("discovery") == "RECORDED":
            out.append(f"step {entry.step_index} DISCOVERY "
                       f"{entry.decision.get('event_id')}")
        elif entry.decision.get("reason"):
            out.append(f"step {entry.step_index} STOP "
                       f"{entry.decision.get('reason')}")
    return out


def cmd_diagnose(args: argparse.Namespace) -> int:
    """`mta diagnose` —— Failure Diagnosis（设计 §8；Task 5.6 / P3-25）。

    退出码：0 成功（**含「规则无结论 / 没复现出来」**——那些是**结论**，
    与矩阵 #11/#12 的预算停止同款：没有 Bug 候选也是一种诊断结果，不是失败）；
    3 前置错误（env/政策/输入读不到/run_id 不存在/设备装配失败）。

    **调用顺序照 F7**：第一行过前置（`load_policy` → `_check_autonomous_env`），
    过了才做别的事。

    产出：一条 `bug_candidates` 行，状态推进到 **`HUMAN_REVIEW`**（送上审核台；
    人工 ACCEPT 经 `mta candidate review --kind bug accept` → `CONFIRMED`）。
    **系统不自动归因**（F10）：`attribution_hypothesis` 只装分数过阈值的
    **建议**，真正的 `failure_attribution` 只经人工 triage 写。
    """
    from agents.policy_config import load_policy

    # 1. F7 前置（**第一行**）。env_kind 走唯一解析点（守卫钉「不许字面量」）。
    policy = (load_policy(args.policy) if args.policy is not None
              else load_policy())
    _check_autonomous_env(policy, env_kind=_resolve_env_kind(args),
                          command="diagnose")

    import uuid

    from agents.storage import SQLiteAgentStore
    from diagnosis.analyzer import analyze
    from diagnosis.context import DiagnosisContextError, build_context
    from diagnosis.deduplicator import dedup_key as compute_dedup_key
    from diagnosis.evidence import suggested_attribution
    from diagnosis.models import BugCandidateDraft, EvidenceItem
    from cli.pipeline import SessionPipeline
    from diagnosis.reproducer import reproduce
    from experience import SQLiteExperienceStore
    from graph import SOURCE, GraphStore
    from knowledge import build_knowledge
    from source import build_identity as bi

    # 2. 范围装配（app_id / build）。app_id 缺省取 trace 的权威范围（不猜）。
    app_id = args.bundle_id or _trace_app_id(args.db)
    if not app_id:
        print("PREFLIGHT ERROR: 拿不到 app_id——给 `--bundle-id`，或先跑一次 "
              "`mta run`（trace 的 runs.app_bundle_id 是权威范围）")
        print("diagnose: exit 3")
        return 3
    if args.build:
        app_build = args.build
    else:
        try:
            app_build = bi.resolve_app_build(bi.read_metadata(_metadata_path(args)))
        except Exception as e:  # noqa: BLE001 — 读不到 metadata 是前置错误
            print(f"PREFLIGHT ERROR: 读不到 12.3 metadata（build 的来源）：{e}")
            print("diagnose: exit 3")
            return 3

    repository = _load_repository(args)
    pipeline = SessionPipeline(suites_root=args.suites_root,
                               secrets=EnvSecretProvider(), app_id=app_id,
                               app_build=app_build)
    # 诊断**不消费** `impact_of`（那是 planner 的），故 knowledge 先以空 cases
    # 建——用例只在**复现重跑**时才需要（按 id 找到用例）。这样「suites 目录
    # 为空」不会挡住诊断（主输入是 trace），而**前置错误（run_id 不存在）仍然
    # 排在所有 note 之前**（错误面在前，note 在后）。
    knowledge = build_knowledge(
        experience_store=SQLiteExperienceStore(args.exp_db),
        trace_db=args.db, cases=[], app_build=app_build)

    # 3. 基线图（`--base-build`）→ build-to-build 的图 diff（喂 UI_DRIFT 规则
    #    与 dedup_key 的 source_change）。**不给就没有 diff**（不是空 diff）——
    #    没有 diff 就没有 source_change，dedup_key 也就算不出来（见下）。
    base_graph = None
    if args.base_build:
        base_graph = GraphStore(args.graph_db).load_graph(
            app_id, args.base_build, SOURCE)

    # 3b. Batch B（设计 §8.1 的「App/Crash/Network Logs」）：三条**原始材料**。
    #     ⚠️ `infra_events` 一直是 `build_context` 的形参，但**这个调用点从来
    #     没传过它**——于是 `crash_detected` 的第二条判据（infra 崩溃事件）
    #     在真机上永远拿不到输入。这类「形参存在、调用点漏了」的缺口单测
    #     看不见（单测自己传参当然绿），故有 `test_cli_diagnose.py` 的
    #     调用点级钉子。
    infra_events = _read_infra_events(args.db, args.run_id)
    try:
        logs = _read_log_file(args.logs)
        crash_info = _read_crash_report_file(args.crash_report)
    except FileNotFoundError as e:
        # 用户**明确指了**一个文件却读不到 = 前置配置错（exit 3 的干净通道，
        # 与 `--db` 指向坏库同款处置）；与「没给这个旗标」（如实空）严格区分。
        print(f"PREFLIGHT ERROR: {e}")
        print("diagnose: exit 3")
        return 3

    # 4. 装配诊断上下文（F3：全部经 knowledge）。
    try:
        ctx = build_context(
            knowledge=knowledge, run_id=args.run_id, app_id=app_id,
            app_build=app_build, base_graph=base_graph,
            infra_events=infra_events, logs=logs, crash_info=crash_info)
    except DiagnosisContextError as e:
        print(f"PREFLIGHT ERROR: 建不了诊断上下文：{e}")
        print("diagnose: exit 3")
        return 3
    except ValueError as e:
        # ⚠️ M5 Gate 的 G2a 实测抓到：`--db` 指向一个还没有表的库时，
        # `KnowledgeSources.trace_history` 抛 `ValueError`（「trace 库不可读」），
        # 它不在上面那个 `except` 里 → 一路冒到 `main()`，用户看到一屏
        # traceback 和 exit 1。这是「指错了库」/「还没跑过 run」，是**前置
        # 错误**（用户能给 corrective action），该走 exit 3 的干净通道。
        print(f"PREFLIGHT ERROR: 读不到 trace 现场：{e}")
        print("diagnose: exit 3")
        return 3

    # 4b. `--step`：把现场收窄到那一步（缺省 = 整个 run）。给了但该步不存在
    #     → 前置错误（不静默退化成「整个 run」——那会诊断出别的东西）。
    if args.step is not None:
        import dataclasses
        picked = [s for s in ctx.steps if s.step_index == args.step]
        if not picked:
            print(f"PREFLIGHT ERROR: run {args.run_id} 里没有 step_index="
                  f"{args.step}（可用：{sorted(s.step_index for s in ctx.steps)}）")
            print("diagnose: exit 3")
            return 3
        ctx = dataclasses.replace(ctx, steps=tuple(picked))

    # 5. 规则优先（F10；crash 场景 LLM 零调用）→ 空则 LLM。
    llm = budget = None
    if not args.no_llm and os.environ.get("LLM_API_KEY"):
        from llm.provider import LLMProvider
        llm, budget = LLMProvider(), LLMBudget()
    analysis = analyze(ctx, llm=llm, budget=budget)
    for note in analysis.notes:
        print(f"  note: {note}")

    if not analysis.hypotheses:
        # **没有假设也是一种结论**（规则无结论且 LLM 未启用/未产出）——exit 0，
        # 不落一条空候选（空证据的候选在 `Hypothesis` 层就被拒了，F10）。
        print(f"diagnose: run={args.run_id} 无假设产出（规则无结论）→ 不落 Bug 候选")
        print("diagnose: exit 0")
        return 0

    # 6. 复现判定（设计 §8.4）——**在排序与归因之前**（见下面 6b 的口径登记）。
    #    设备可用才真重跑；否则如实记「无法确认」（**不**伪装成「复现不了」
    #    ——reproducer 自己区分两者）。
    #    ⚠️ suites **不是**诊断的必需输入：主输入是 trace，用例只在重跑时要。
    #    目录不存在/为空 → 照常诊断（复现那步如实记「无法重跑」）；**但**目录
    #    在、内容坏（坏 YAML）是真配置错误，仍然 fail-loud。
    cases_by_id: dict = {}
    if Path(args.suites_root).is_dir() and any(
            Path(args.suites_root).rglob("*.yaml")):
        try:
            cases_by_id = {c.id: c for c in pipeline.discover()}
        except SessionPipeline.PreflightError as e:
            print(f"PREFLIGHT ERROR: 读 suites 失败：{e}")
            print("diagnose: exit 3")
            return 3
    else:
        print(f"  note: suites 目录为空/不存在（{args.suites_root}）→ 只诊断"
              f"不复现（没有可重跑的用例）")
    repro = _run_reproduction(
        ctx, args=args, pipeline=pipeline, repository=repository,
        cases_by_id=cases_by_id, app_build=app_build, policy=policy)
    print(f"diagnose: 复现 reproducible={repro.reproducible} "
          f"count={repro.reproduction_count} attempts={repro.attempts}")
    for note in repro.notes:
        print(f"  note: {note}")

    # 6b. **复现证据并进每条假设**，然后才排序与归因。
    #
    # ⚠️ handover §9 的「5.6 开工前置①」在这一步真正闭合：崩溃/漂移这类**最强**
    # 的规则证据单独都只值 5 / 3 分，低于阈值 10（`suggested_attribution`
    # 的两道闸门之一）。设计 §8.3 让它们配 `reproducible_3_times`（5 分）才
    # 过线——**前提是复现证据真的进了同一个证据集**。早先本函数先算归因、
    # 后跑复现，于是哪怕真的重跑了三次，落库的 `attribution_hypothesis`
    # 仍是 `UNTRIAGED`（M5 Gate 实测：分数 10 但归因空，自相矛盾）。
    #
    # 排序键不受影响：给每条假设**加同一组**证据等于加同一个常数，次序不变
    # （`rank_hypotheses` 的键是 `(-score, statement)`）。
    # `same_issue_multiple_tests`（权重 3）从 `ctx.history` 来——**唯一生产者**
    # 是 `diagnosis/evidence.py::history_corroboration`（M5 Gate 补上：此前
    # 权重表里有这一项、没有任何代码产出它，真机漂移现场分数上限只有 8）。
    from diagnosis.evidence import (history_corroboration, rank_hypotheses,
                                    tests_hit_by_failure)
    corroboration = tuple(history_corroboration(ctx))
    for item in corroboration:
        print(f"  note: 多用例佐证 {item.kind}：{item.detail}")
    augmented = [_with_extra_evidence(h, repro.evidence + corroboration)
                 for h in analysis.hypotheses]
    ranked = rank_hypotheses(augmented,
                             weights=policy.diagnosis_evidence_weights)
    top_hypothesis, top_score = ranked[0]
    attribution = suggested_attribution(
        top_hypothesis, weights=policy.diagnosis_evidence_weights,
        threshold=policy.evidence.attribution_threshold)
    print(f"diagnose: {len(ranked)} 条假设，最高分 {top_score}"
          f"（阈值 {policy.evidence.attribution_threshold}）"
          f" → 归因建议 {attribution or '（不达标，保持 UNTRIAGED）'}")

    # 7. 证据装配与 dedup_key（`top_hypothesis` 已含复现证据，不再加第二遍）。
    evidence = list(top_hypothesis.evidence)
    failure_type = _failure_type_of(ctx)
    screen, runtime_diff, source_change = _dedup_components(ctx, args=args)
    draft = BugCandidateDraft(
        title=_diagnosis_title(ctx, failure_type),
        failure_type=failure_type or "",
        severity=_severity_for(top_hypothesis, repro),
        attribution_hypothesis=attribution or "UNTRIAGED",
        evidence=evidence, evidence_score=top_score,
        reproducible=repro.reproducible,
        reproduction_count=repro.reproduction_count,
        affected_tests=tests_hit_by_failure(ctx),
        screen=screen, runtime_diff=runtime_diff, source_change=source_change)
    # ⚠️ **没有 `--base-build` 就不写 `dedup_key`**（口径登记）：三元组里的
    #    `source_change` 来自 build-to-build 的图 diff，没有基线就没有它——
    #    此时算出的 key 是「半个三元组」，把两次**无关**的失败判成同根因
    #    （错误合并比漏合并更贵：它会把两个 Bug 悄悄并成一个）。
    if args.base_build:
        draft = draft.model_copy(
            update={"dedup_key": compute_dedup_key(draft)})

    # 9. 落库（DISCOVERED）→ 推进到 HUMAN_REVIEW（送上审核台）。
    agent_db = SQLiteAgentStore(args.agent_db)
    candidate_id = f"bug_{uuid.uuid4().hex[:8]}"
    _persist_bug_candidate(agent_db, candidate_id, draft, args=args)
    row = agent_db.get_bug_candidate(candidate_id)
    assert row is not None
    print(f"diagnose: bug 候选 {candidate_id} status={row['status']} "
          f"score={row['evidence_score']} dedup_key={row['dedup_key']}")
    print(f"  title: {row['title']}")
    print(f"  下一步：`mta candidate review --kind bug accept {candidate_id} "
          f"--bundle-dir {args.bundle_dir}`（人工确认后才落 Evidence Bundle）")
    print("diagnose: exit 0")
    return 0


def _run_reproduction(ctx, *, args, pipeline, repository, cases_by_id,
                      app_build, policy):
    """复现判定（设计 §8.4）。设备可用才真重跑，否则如实记「无法确认」。

    三处注入（口径登记）：① `runner` 回调——toolkit 的 `run_testcase` 通路
    **M6 才接线**（现在 `NOT_WIRED`），故这里注入一个基于 `SessionPipeline`
    的真回调；② `postcondition_checker`——复用
    `cli/pipeline.py::make_postcondition_checker`；③ `probe_for`——把**用例
    声明**的 `postcondition` 并进 trace 三列（trace 里没有声明，见
    `reproducer._probe_from_step`）。
    """
    from diagnosis.reproducer import ReproResult, reproduce
    from llm.budget import LLMBudget

    llm_budget = LLMBudget()   # 重跑预算（F4：就是 P1 的计数器，不新写）
    try:
        from agents.tools import AgentToolkit
        from cli.pipeline import wire_device_stack
        from executor.assertion import AssertionEngine
        from executor.guard import Guard
        from executor.wait import WaitEngine

        stack = wire_device_stack(
            pipeline, env_kind=_resolve_env_kind(args), run_id=args.run_id,
            udid=args.udid, bundle_id=args.bundle_id or ctx.app_id,
            repo=repository)
    except Exception as e:  # noqa: BLE001 — 设备不可用 → 不声称复现结论
        return ReproResult(
            reproducible=None, attempts=0,
            notes=(f"设备会话不可用（{type(e).__name__}: {e}）→ 记「无法确认"
                   f"复现」（不声称「复现不了」）",))

    try:
        locator_for = pipeline._locator_for(stack.runner)
        toolkit = AgentToolkit(
            guard=Guard(_resolve_env_kind(args)), executor=stack.runner.ex,
            locator_for=locator_for, repository=repository,
            device_session=stack.device_session,
            wait_engine=WaitEngine(stack.runner.ex, locator_for),
            assertion_engine=AssertionEngine(stack.runner.ex, locator_for),
            build=app_build)

        def _runner(testcase_id):
            tc = cases_by_id.get(testcase_id)
            if tc is None:
                raise ValueError(f"用例 {testcase_id} 不在 suites 里，无法重跑")
            return pipeline.run_case(stack.runner, stack.lifecycle, tc,
                                     run_id=args.run_id, manage_env=True)

        def _probe_for(step):
            from diagnosis.reproducer import _probe_from_step
            tc = cases_by_id.get(getattr(step, "testcase_id", "") or "")
            post = None
            if tc is not None:
                idx = getattr(step, "step_index", 0)
                if 0 <= idx < len(tc.steps):
                    post = getattr(tc.steps[idx], "postcondition", None)
            return _probe_from_step(step, postcondition=post)

        from cli.pipeline import make_postcondition_checker
        return reproduce(ctx, toolkit=toolkit, llm_budget=llm_budget,
                         runner=_runner,
                         postcondition_checker=make_postcondition_checker(
                             pipeline, stack.runner),
                         probe_for=_probe_for)
    finally:
        stack.close()


def _persist_bug_candidate(agent_db, candidate_id, draft, *, args) -> None:
    """落 `bug_candidates`（`save_bug_candidate` 只写 DISCOVERED）→ 推进到
    `HUMAN_REVIEW`（`decide_bug_candidate`，状态机校验）。

    推进走 `candidates/bug.advance` 的**同一张转换表**（不写第二份）：
    DISCOVERED → ANALYZING → CANDIDATE → HUMAN_REVIEW 是诊断流水线的三个内部
    跳（系统推进，不写审核三列），HUMAN_REVIEW 才是「送上审核台」。
    """
    from candidates.bug import advance

    agent_db.save_bug_candidate(
        candidate_id, title=draft.title,
        evidence=[e.model_dump(mode="json") for e in draft.evidence],
        failure_type=draft.failure_type or "",
        severity=draft.severity,
        attribution_hypothesis=draft.attribution_hypothesis,
        evidence_score=draft.evidence_score, reproducible=draft.reproducible,
        reproduction_count=draft.reproduction_count, dedup_key=draft.dedup_key,
        affected_tests=draft.affected_tests)
    status = "DISCOVERED"
    for nxt in ("ANALYZING", "CANDIDATE", "HUMAN_REVIEW"):
        status = advance(status, nxt)
        agent_db.decide_bug_candidate(candidate_id, status=status)


def _failure_type_of(ctx) -> str | None:
    first = ctx.first_failure
    return getattr(first, "failure_type", None) if first else None


def _diagnosis_title(ctx, failure_type) -> str:
    """候选标题：失败类型 + 用例 id（审核队列里要能一眼辨认）。"""
    first = ctx.first_failure
    tc = getattr(first, "testcase_id", "") or "?"
    ft = failure_type or "FAILED"
    return f"{ft} in {tc}（run={ctx.run_id}）"


def _severity_for(hypothesis, repro) -> str:
    """严重度初版：崩溃 → HIGH，其余 → MEDIUM（复现成功再抬一档留给校准）。"""
    kinds = {e.kind for e in hypothesis.evidence}
    if "crash_detected" in kinds:
        return "HIGH"
    return "MEDIUM"


def _with_extra_evidence(hypothesis, extra):
    """`hypothesis` + `extra` 证据（复现产出的那些）→ 新假设。

    ⚠️ **不改原对象**（`Hypothesis` 是 pydantic 模型，可变；原地加证据会让
    `analysis.hypotheses` 里的那条也被改，于是「排序前的分数」与「落库的
    分数」是同一个对象上的两个时刻——审计里看不出谁加了什么）。

    ⚠️ **去重**：复现证据与假设自带的证据可能是同一类（`score_evidence`
    按类别求和，重复不影响分），但**条目**重复会让 `evidence` 列表里出现
    两条一模一样的证据（人工看 bundle 时会以为有两个独立来源）。
    """
    if not extra:
        return hypothesis
    merged = list(hypothesis.evidence)
    seen = {(e.kind, e.detail, e.source) for e in merged}
    for e in extra:
        key = (e.kind, e.detail, e.source)
        if key not in seen:
            seen.add(key)
            merged.append(e)
    return hypothesis.model_copy(update={"evidence": merged})


def _dedup_components(ctx, *, args) -> tuple[str, list[str], str]:
    """`dedup_key` 三元组 `(screen, runtime_diff, source_change)`（设计 §8.5）。

    - `screen`：失败步所在的屏（`detail.screen` / `screen_id` 推不出就空）；
    - `runtime_diff`：build-to-build 的**图** diff token（`--base-build` 才有）；
    - `source_change`：**源码**改动（`--base-build` 时取 metadata 的
      `git_commit` 与当前 HEAD 之间的 `git diff --name-status`，与 `plan` 的
      范围算法同源）。

    三者缺一时对应分量留空（`dedup_key` 的规范化会把空分量稳定处理）。
    """
    from diagnosis.deduplicator import diff_tokens

    first = ctx.first_failure
    detail = getattr(first, "detail", None) or {}
    screen = detail.get("screen") or detail.get("screen_id") or ""
    tokens: list[str] = []
    source_change = ""
    if args.base_build:
        if ctx.graph_diff is not None:
            tokens = diff_tokens(ctx.graph_diff)
        source_change = _source_change(args)
    return screen, tokens, source_change


def _source_change(args) -> str:
    """`--base-build` 的源码改动（`git diff --name-status base..HEAD`，只取文件名）。

    读不到 git（非仓库 / 坏 ref）→ 空串（**不 fail-loud**）：`source_change`
    只是 dedup 三元组的一个分量，缺它不该让整场诊断失败——诊断的**主产物**
    （假设 + 证据 + 复现）与 git 无关。空分量在 `dedup_key` 里是稳定值。
    """
    from source import build_identity as bi
    from source.git_diff import changed_files

    try:
        base_meta = bi.read_metadata(_base_metadata_path(args))
        base = base_meta.get("git_commit") or "HEAD~1"
        return ",".join(sorted(changed_files(args.repo_root, base=base)
                               .changed_files))
    except Exception:  # noqa: BLE001 — git 读不到 → 空分量（不阻断诊断）
        return ""


def _base_metadata_path(args) -> Path:
    """`--base-build` 的 metadata 路径（12.4 布局，与 `plan` 同源）。"""
    root = (Path(args.generated).parent if getattr(args, "generated", None)
            else Path("repository/generated"))
    return root / args.base_build / "source_metadata.json"


def cmd_candidate(args: argparse.Namespace) -> int:
    """`mta candidate review …` —— 统一候选审核（Task 3.5 / F6 首个消费者）。

    动词/字段/错误面与 P2 的 `mta review`（`cmd_review:1258`）对齐：同一个人
    在两处看到的动词应该一样（F6 的「范式一致」是给人记忆用的）。**不是**
    自主命令——审核是人工操作，不过 F7 的启动环境闸。
    """
    from candidates.review import CandidateReviewError, decide_candidate, \
        export_candidate_patch

    # ⚠️ 两组 `if` 分支（`candidate_cmd != "review"` / `cmd not in (...)`）早先
    # 在此处各有一个 `return 2` —— 实测**不可达**：两处子解析器都是
    # `required=True`，非法动词由 argparse 以 `SystemExit(2)` 挡在 handler 之前
    # （review_p3_task35 P3-3：探针把两处 `return 2` 改成 `0`，0 red）。删掉的
    # 不是「防御」，是**假的可达性**：留着会让人以为 handler 自己守住了动词面。
    cmd = args.candidate_review_cmd
    import sqlite3

    from agents.storage import SQLiteAgentStore

    if cmd == "list":
        from testcase.schema import parse_testcase_dict

        status = None if args.status == "ALL" else args.status
        try:
            store = SQLiteAgentStore(args.db)
            rows = store.list_candidates(review_status=status)
            rejections = store.list_rejections() if args.rejections else []
        except sqlite3.Error as e:
            # ⚠️ `sqlite3.Error` **不是** `RuntimeError`：早先只接域错误的
            # except 漏掉它，`--db` 指向一个目录时裸 `OperationalError` 直接
            # 漏成 traceback（review_p3_task35 P3-4）。库读不了 = 前置配置错误。
            print(f"CANDIDATE ERROR: 读不了候选库 {args.db}：{e}")
            return 3
        for r in rows:
            tc = None
            try:
                tc = parse_testcase_dict(yaml.safe_load(r["testcase_yaml"]))
            except Exception:  # noqa: BLE001 — 坏 YAML 也要能列出来（可见性）
                pass
            suite = (tc.suite if tc else None) or "(未声明)"
            fp = (r["fingerprint"] or "")[:8]
            print(f"{r['candidate_id']} [{r['review_status']}] "
                  f"dry={r['dry_run_status']} fp={fp} suite={suite} "
                  f"— {r['created_at']}")
        for rej in rejections:
            dup = f" duplicate_of={rej['duplicate_of']}" \
                if rej["duplicate_of"] else ""
            print(f"  rejection {rej['rejection_id']} "
                  f"candidate={rej['candidate_id']} "
                  f"reason={rej['reason']}{dup}")
        print(f"candidate review: {len(rows)} 条（status={args.status}）")
        return 0

    try:
        store = SQLiteAgentStore(args.db)
    except sqlite3.Error as e:
        print(f"CANDIDATE ERROR: 打不开候选库 {args.db}：{e}")
        return 3

    import getpass
    reviewer = args.reviewer or getpass.getuser()

    if cmd == "accept" and args.export_patch:
        # H15 备选：只导出文本，零写入。
        try:
            print(export_candidate_patch(store, args.kind, args.candidate_id,
                                         suites_root=args.suites_root))
        except (CandidateReviewError, sqlite3.Error) as e:
            print(f"CANDIDATE ERROR: {e}")
            return 3
        return 0

    if cmd == "accept":
        # 现算正式用例 id 集（`candidates/review.py` 不 import cli——依赖单向）。
        from cli.pipeline import SessionPipeline
        try:
            cases = SessionPipeline(suites_root=args.suites_root,
                                    secrets=EnvSecretProvider()).discover()
        except Exception as e:  # noqa: BLE001 — 用例发现失败 = 前置输入问题
            print(f"CANDIDATE ERROR: 读 suites 失败：{e}")
            return 3
        existing = frozenset(c.id for c in cases)
        repository = Repository.from_dirs(
            generated_root=getattr(args, "generated", None),
            overrides_root=args.overrides)
        try:
            row, sha = decide_candidate(
                store, args.kind, args.candidate_id, "ACCEPT", reviewer,
                args.note, repo_root=Path(args.repo_root),
                suites_root=args.suites_root, existing_case_ids=existing,
                repository=repository, secrets=EnvSecretProvider(),
                bundle_dir=getattr(args, "bundle_dir", None))
        except (CandidateReviewError, RuntimeError, sqlite3.Error) as e:
            print(f"CANDIDATE ERROR: {e}")
            return 3
        print(f"candidate: {row['candidate_id']} ACCEPT（reviewer={reviewer}）")
        if args.kind == "bug":
            print(f"  status={row['status']}；Evidence Bundle 已落 "
                  f"{args.bundle_dir}/{row['candidate_id']}.json")
        elif args.kind == "discovery":
            print(f"  status={row['status']}；发现已登记进 Source Graph")
        else:
            print(f"  suites 已写 + commit {sha}；撤销 = git revert {sha}")
        return 0

    # reject
    try:
        row, _ = decide_candidate(
            store, args.kind, args.candidate_id, "REJECT", reviewer,
            args.note, repo_root=Path("."), repository=None, secrets=None)
    except (CandidateReviewError, RuntimeError, sqlite3.Error) as e:
        print(f"CANDIDATE ERROR: {e}")
        return 3
    print(f"candidate: {row['candidate_id']} REJECT（note 已留痕）")
    return 0


def _common_source_root(sources: list[str]) -> Path:
    """输入参数的公共祖先目录（resolved）——git_commit 的 source_root。

    单目录参数即自身；多路径取 os.path.commonpath。文件/目录不存在时
    不崩（rev-parse 失败 → metadata 记 unknown，fail-loud 交给 G8）。
    """
    paths = []
    for s in sources:
        p = Path(s)
        paths.append(p if p.is_dir() else p.parent)
    if not paths:
        return Path(".")
    resolved = [p.resolve() for p in paths]
    if len(resolved) == 1:
        return resolved[0]
    try:
        return Path(os.path.commonpath(resolved))
    except ValueError:  # 不同盘符/跨根（Windows）——退 CWD 语义
        return Path(".")


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
    # source_root：被测源码仓库根——metadata 的 git_commit 从**它**取，而不是
    # CWD（外部源码场景：乐速通在 ~/Desktop/workspace/ios，走 CWD 会把平台
    # 自己的 commit 写进 metadata，与 App 里注入的 commit 永不相同 → G8 拦）。
    # 取输入参数的公共祖先（目录即自身；多路径/文件取最深公共目录）。
    source_root = _common_source_root(args.sources)
    try:
        meta = build_metadata(files, out, app_version=args.app_version,
                              build=args.build, source_root=source_root)
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

    if args.cases:
        # 设计 12.3：diff 关联 Impact Analysis → UI Change Report。
        # 用例读不到（目录不存在 / preflight 失败）→ 3（配置错误，fail-loud：
        # 把「没读到用例」显示成「影响面为空」会把配置问题伪装成无需回归）。
        from pathlib import Path as _P
        root = _P(args.cases)
        if not root.is_dir():
            print(f"GRAPH ERROR: suites root 不存在: {root}")
            return 3
        try:
            from cli.pipeline import SessionPipeline
            cases = SessionPipeline(suites_root=root).discover()
        except Exception as e:  # noqa: BLE001 — preflight 失败同 3
            print(f"GRAPH ERROR: 用例加载失败: {e}")
            return 3
        from graph.impact import build_change_report, format_report
        report = build_change_report(diff, cases=cases)
        print()
        print(format_report(report))
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


def cmd_agent(args: argparse.Namespace) -> int:
    """`mta agent benchmark run|compare`（设计 §12/§13.2；Task 6.5 / P3-30）。

    F7 前置在第一行（`"agent"` 在 `AUTONOMOUS_COMMANDS` 里，M1 预登记）。
    退出码：run → 0（benchmark 是评估不是验收，跑完即成）；compare →
    无回归 0 / **AGENT_REGRESSION 1**（供 CI 判定，设计原文）。
    """
    from agents.policy_config import load_policy

    policy = (load_policy(args.policy) if args.policy is not None
              else load_policy())
    _check_autonomous_env(policy, env_kind=_resolve_env_kind(args),
                          command="agent")

    from agents.storage import SQLiteAgentStore

    if args.agent_cmd != "benchmark":
        print(f"agent: unknown subcommand {args.agent_cmd!r}")
        return 2
    if args.bench_cmd == "run":
        from agents.benchmark import run_benchmark

        store = SQLiteAgentStore(args.agent_db)
        result = run_benchmark(store=store, label=args.label)
        print(f"benchmark: {len(result.cases)} 类 case，"
              f"diagnosis_accuracy={result.diagnosis_accuracy:.3f} "
              f"llm_path_ok={result.llm_path_ok} "
              f"llm_calls={result.llm_calls} "
              f"duration={result.duration_ms:.1f}ms")
        for c in result.cases:
            mark = "PASS" if c.correct else "FAIL"
            print(f"  {mark} {c.case_id}: expected={c.expected} got={c.got}")
        print(f"snapshot: label={args.label} → {args.agent_db}"
              f"（benchmark_runs）")
        return 0
    if args.bench_cmd == "compare":
        store = SQLiteAgentStore(args.agent_db)
        baseline = store.latest_benchmark_run(args.baseline)
        candidate = store.latest_benchmark_run(args.candidate)
        if baseline is None or candidate is None:
            missing = (args.baseline if baseline is None
                       else args.candidate)
            print(f"AGENT BENCHMARK ERROR: 标签 {missing!r} 没有快照——"
                  f"先 `mta agent benchmark run --label {missing}`")
            return 3
        from agents.benchmark import compare_benchmarks

        regressions = compare_benchmarks(baseline, candidate,
                                         threshold=args.threshold)
        print(f"compare: {args.baseline} → {args.candidate} "
              f"accuracy {baseline.diagnosis_accuracy:.3f} → "
              f"{candidate.diagnosis_accuracy:.3f}（阈值 {args.threshold}）")
        if regressions:
            print("AGENT_REGRESSION")
            for r in regressions:
                print(f"  - {r}")
            return 1
        print("无回归")
        return 0
    print(f"agent: unknown benchmark subcommand {args.bench_cmd!r}")
    return 2


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
    if args.command == "plan":
        return cmd_plan(args)
    if args.command == "generate":
        return cmd_generate(args)
    if args.command == "explore":
        return cmd_explore(args)
    if args.command == "diagnose":
        return cmd_diagnose(args)
    if args.command == "agent":
        return cmd_agent(args)
    if args.command == "candidate":
        return cmd_candidate(args)
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
