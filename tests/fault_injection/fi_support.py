"""fi_support — 故障注入共享替身（Task 4.3 收口整合）。

此前 test_wda_and_recovery / test_llm_matrix 各持一份 FakeExecutor/FakeDS/
FakeLLM——三份平行副本会各自漂移（本项目对「平行实现」的一贯教训）。
矩阵 24 行（test_matrix_01_12 / test_matrix_13_24）与既有文件统一从这里
取替身；两个旧文件已改为消费本模块，本地副本删除。

全部替身无网络无真机（H18 边界内的 FakeDriver 层）。
"""
from __future__ import annotations

import json

import yaml

from cli.pipeline import PipelineDeps, SessionPipeline
from executor.guard import EnvKind, Guard
from repository.resolver import Repository
from runner.lifecycle import Lifecycle
from runner.runner import StepRunner
from tracer.storage import TraceStore


class El:
    """wait/assert/recovery 消费的最小元素契约（类型可编排）。"""

    def __init__(self, etype="XCUIElementTypeButton"):
        self._t = etype

    def get_attribute(self, name):
        return self._t if name == "type" else ""

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True

    @property
    def text(self):
        return ""


class FakeExecutor:
    """可编排故障的 Executor：find 按调用序弹脚本（Exception 抛出、耗尽
    后重复最后一项），tap 前 N 次可炸（POST_DISPATCH 故障）。

    `find_all`（Task 2.4 新增）：Experience Runtime Guard 的数量观测端。
    默认 `[El()]`（恰一命中 = 可执行）——不设脚本的行走 happy path；
    要构造 NOT_FOUND / AMBIGUOUS 的行显式给脚本（`[]` / `[El(), El()]`）。
    """

    def __init__(self, find_script=None, tap_fail_times=0,
                 page_source=("<App><Node name='screen.HomeView' "
                              "visible='true'/></App>"),
                 find_all_script=None):
        self.find_script = list(find_script or [])
        self.find_calls = 0
        self.tap_calls = 0
        self.input_calls = 0
        self.tap_fail_times = tap_fail_times
        self._page = page_source
        self.find_all_script = list(find_all_script or [])
        self.find_all_calls = 0

    def find(self, strategies):
        idx = min(self.find_calls, len(self.find_script) - 1)
        self.find_calls += 1
        if not self.find_script:
            return El()
        item = self.find_script[idx]
        if isinstance(item, Exception):
            raise item
        return item

    def find_all(self, locator):
        idx = min(self.find_all_calls, len(self.find_all_script) - 1)
        self.find_all_calls += 1
        if not self.find_all_script:
            return [El()]
        item = self.find_all_script[idx]
        if isinstance(item, Exception):
            raise item
        return list(item)

    def tap(self, strategies):
        self.tap_calls += 1
        if self.tap_calls <= self.tap_fail_times:
            raise RuntimeError("dispatch died mid-tap")

    def input(self, strategies, value):
        self.input_calls += 1

    # M4：分派优先走已定位元素（与生产 Executor 契约对齐），计数同源
    def tap_element(self, element):
        self.tap_calls += 1
        if self.tap_calls <= self.tap_fail_times:
            raise RuntimeError("dispatch died mid-tap")

    def input_element(self, element, value):
        self.input_calls += 1

    def swipe(self, direction):
        pass

    def page_source(self):
        return self._page


class FakeDS:
    """ensure_alive 第 die_on 次（集合编排，跨 attempt/用例累加）抛
    InfraError；restart_wda 计数。"""

    def __init__(self, die_on=None):
        self.ensure_calls = 0
        self.die_on = ({die_on} if isinstance(die_on, int)
                       else set(die_on or ()))
        self.restarts = 0

    def ensure_alive(self):
        self.ensure_calls += 1
        if self.ensure_calls in self.die_on:
            from session.device_session import InfraError
            raise InfraError("WDA session died")

    def restart_wda(self):
        self.restarts += 1


class FakeLLM:
    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls: list[str] = []

    def complete(self, prompt: str, timeout: int = 20) -> str:
        self.calls.append(prompt)
        item = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


def llm_json(value="signin_button", conf=0.93, action="tap", **extra):
    """10.4 输出契约的合法 JSON；**extra 供注入额外字段（如 risk_level，
    矩阵 #8 的 H4 断言用）。"""
    out = {"action": action,
           "target": {"type": "accessibility_id", "value": value},
           "scope": "HomeView", "reason": "renamed", "confidence": conf}
    out.update(extra)
    return json.dumps(out)


def drift_repo(tmp_path):
    """漂移形态 Repository（真实 loader 消费 generated 目录）：
    login_button（旧 build 登记，lint 可过）+ signin_button（新 build 元素）
    + pay_button（HIGH，矩阵 #7/#8）+ ghost_button（ProfileView，#6）。"""
    gen = tmp_path / "generated" / "local"
    (gen / "elements").mkdir(parents=True, exist_ok=True)
    (gen / "screens").mkdir(parents=True, exist_ok=True)
    (gen / "elements" / "HomeView.yaml").write_text(
        "schema_version: '1.0'\nkind: element\nid: login_button\n"
        "screen: HomeView\ntype: button\nstrategies:\n"
        "- type: accessibility_id\n  value: login_button\n  origin: source\n"
        "metadata:\n  origin: source\n  risk: LOW\n"
        "---\n"
        "schema_version: '1.0'\nkind: element\nid: signin_button\n"
        "screen: HomeView\ntype: button\nstrategies:\n"
        "- type: accessibility_id\n  value: signin_button\n  origin: source\n"
        "metadata:\n  origin: source\n  risk: LOW\n"
        "---\n"
        "schema_version: '1.0'\nkind: element\nid: pay_button\n"
        "screen: HomeView\ntype: button\nstrategies:\n"
        "- type: accessibility_id\n  value: pay_button\n  origin: source\n"
        "metadata:\n  origin: source\n  risk: HIGH\n", encoding="utf-8")
    (gen / "elements" / "ProfileView.yaml").write_text(
        "schema_version: '1.0'\nkind: element\nid: ghost_button\n"
        "screen: ProfileView\ntype: button\nstrategies:\n"
        "- type: accessibility_id\n  value: ghost_button\n  origin: source\n"
        "metadata:\n  origin: source\n  risk: LOW\n", encoding="utf-8")
    (gen / "screens" / "HomeView.yaml").write_text(
        "schema_version: '1.0'\nkind: screen\nid: HomeView\n"
        "marker: screen.HomeView\nkind_hint: page\nmetadata:\n"
        "  risk: LOW\n  origin: source\n", encoding="utf-8")
    (gen / "screens" / "ProfileView.yaml").write_text(
        "schema_version: '1.0'\nkind: screen\nid: ProfileView\n"
        "marker: screen.ProfileView\nkind_hint: page\nmetadata:\n"
        "  risk: LOW\n  origin: source\n", encoding="utf-8")
    # DetailView：矩阵 #12 需要「目标屏已登记但页面上是别的两个 marker」
    (gen / "screens" / "DetailView.yaml").write_text(
        "schema_version: '1.0'\nkind: screen\nid: DetailView\n"
        "marker: screen.DetailView\nkind_hint: page\nmetadata:\n"
        "  risk: LOW\n  origin: source\n", encoding="utf-8")
    # 矩阵 #22：CRITICAL 元素（production 下 Guard 必拦）
    (gen / "elements" / "PayView.yaml").write_text(
        "schema_version: '1.0'\nkind: element\nid: confirm_pay_button\n"
        "screen: HomeView\ntype: button\nstrategies:\n"
        "- type: accessibility_id\n  value: confirm_pay_button\n"
        "  origin: source\n"
        "metadata:\n  origin: source\n  risk: CRITICAL\n", encoding="utf-8")
    return Repository.from_dirs(generated_root=str(gen))


def load_case(text: str):
    from testcase.schema import parse_testcase_dict
    return parse_testcase_dict(yaml.safe_load(text))


def run_matrix(tmp_path, case_yaml, *, ex=None, repo=None, recovery=None,
               cases=None, guard=None, ds=None, lifecycle=None,
               failure_policy="ABORT_SUITE", env=None, bundle_id=None,
               app_id=None, app_build=None):
    """矩阵行装配：run_all 全链（lint 不在此——矩阵行的 YAML 都先保证
    schema 可解析；lint 语义行 #23 单独走 mta lint）。

    `env`：EnvironmentManager 替身（#20/#24 的 cleanup 失败注入；
    None = no-op 桩）。
    `bundle_id`：落 runs.app_bundle_id——P2（Task 2.3）的 Candidate 主键
    第一段；走 accept→create_candidate 的矩阵行必须给（缺了 E5 种子字段
    不齐，accept 会 fail-loud）。默认 None = 历史行为不变。
    `app_id`：Experience Store 主键第一段（Task 2.4）。缺省跟 `bundle_id`
    ——生产 `mta run` 也是这么接的（同一个 --bundle-id）。要测「app_id
    缺失」的行显式给 `app_id=""`。
    `app_build`：12.5/E7 的 build id（Task 2.4 终审 P3-4）。缺省 None =
    走 `SessionPipeline` 的默认（"local"），历史行为不变；要测「真实 build
    贯通 validated_builds」的行显式给（如 `app_build="1026"`）。
    """
    store = TraceStore(tmp_path / "trace.db")
    store.start_run("run_matrix", app_bundle_id=bundle_id)
    sdir = tmp_path / "suites"
    sdir.mkdir(exist_ok=True)
    (sdir / "matrix.yaml").write_text(case_yaml, encoding="utf-8")
    pipe_kw = {} if app_build is None else {"app_build": app_build}
    pipe = SessionPipeline(suites_root=sdir, store=store, recovery=recovery,
                           app_id=(bundle_id or "") if app_id is None
                           else app_id, **pipe_kw)
    pipe.deps = PipelineDeps(env=env, repo=repo)
    ds = ds or FakeDS()
    ex = ex or FakeExecutor()
    runner = StepRunner(ex, ds, guard or Guard(EnvKind.SANDBOX))
    pipe._step_runner = runner
    lifecycle = lifecycle or Lifecycle(store=store)
    pipe._lifecycle = lifecycle
    cases = cases if cases is not None else pipe.discover()
    run = pipe.run_all(cases, run_id="run_matrix",
                       failure_policy=failure_policy)
    return run, store, ds, ex


def db_rows(tmp_path, sql, params=()):
    import sqlite3
    conn = sqlite3.connect(tmp_path / "trace.db")
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()
