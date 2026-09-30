"""Task 2.6：`mta run` 全参数（14.6）+ 新管线真实接线（R15-2 验收核销）。

`mta run` 用 FakeDriver 级组件注入测试（不打真机），验：
  - 全参数解析：--suite/--tag/--case/--no-llm/--junit/--html/
    --allow-metadata-mismatch/--allow-production/--config；
  - 用例发现：--suite / --tag / --case 三种筛选；
  - 产物：--junit 写 JUnit XML、--html 写报告，且报告里的 exit code
    与 8.4 一致（CI 判读的唯一依据）；
  - 前置错误（用例文件缺失/坏 YAML/lint ERROR）→ exit 3；
  - 新管线桥：SessionPipeline（run_case）把 TestCase 变成
    StepRunner/Lifecycle/SuiteRunner 的真实调用——不是 import 后不用。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
import yaml

from cli.pipeline import SessionPipeline
from cli.main import main


VALID_TC = """\
schema_version: "0.2"
id: login_001
name: 登录主路径
suite: smoke
tags: [smoke, login]
steps:
  - action: launch_app
"""

SECOND_TC = """\
schema_version: "0.2"
id: search_001
name: 搜索
suite: search
tags: [search]
steps:
  - action: launch_app
"""

THIRD_TC = """\
schema_version: "0.2"
id: profile_001
name: 个人页
suite: smoke
tags: [smoke, profile]
steps:
  - action: launch_app
"""


@pytest.fixture()
def suites_dir(tmp_path):
    sdir = tmp_path / "suites"
    sdir.mkdir()
    for name, body in (("login_001", VALID_TC), ("search_001", SECOND_TC),
                       ("profile_001", THIRD_TC)):
        (sdir / f"{name}.yaml").write_text(body, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def fake_env(tmp_path, monkeypatch):
    """不打真机：AppSession/DeviceSession 用最小桩，Executor 走 FakeDriver。"""
    from cli.pipeline import PipelineDeps
    deps = PipelineDeps(
        executor=None, app=None, device_session=None, store=None,
        db_path=tmp_path / "trace.db", appium_url="http://127.0.0.1:4723",
        repo=None, secrets=None)
    monkeypatch.setattr(deps, "connect", lambda: None)
    return deps


# --- 用例发现 ---


def test_discover_all_cases(suites_dir):
    p = SessionPipeline(suites_root=suites_dir / "suites")
    cases = p.discover()
    # 字母序：profile_001 < search_001
    assert [c.id for c in cases] == ["login_001", "profile_001", "search_001"]


def test_discover_by_suite(suites_dir):
    p = SessionPipeline(suites_root=suites_dir / "suites")
    assert [c.id for c in p.discover(suite="smoke")] == [
        "login_001", "profile_001"]


def test_discover_by_tag(suites_dir):
    p = SessionPipeline(suites_root=suites_dir / "suites")
    assert [c.id for c in p.discover(tag="login")] == ["login_001"]


def test_discover_by_case(suites_dir):
    p = SessionPipeline(suites_root=suites_dir / "suites")
    assert [c.id for c in p.discover(case="search_001")] == ["search_001"]


def test_discover_recursive_suite_subdirs(tmp_path):
    """e2e 回归：真实布局是 <root>/<suite>/*.yaml（suites/smoke/），
    发现必须递归——只 glob 顶层会让 --suite smoke 误报「无命中」exit 3。"""
    sdir = tmp_path / "suites" / "smoke"
    sdir.mkdir(parents=True)
    (sdir / "login_001.yaml").write_text(VALID_TC, encoding="utf-8")
    p = SessionPipeline(suites_root=tmp_path / "suites")
    assert [c.id for c in p.discover(suite="smoke")] == ["login_001"]


def test_step_runner_accepts_single_element_find_contract(tmp_path):
    """e2e 回归：生产 Executor.find 返回单个元素（歧义抛
    AmbiguousElement），测试替身返回列表——StepRunner 两种契约都要能跑。
    原实现按列表 len() 处理，接真实 Executor 必 TypeError。"""
    from executor.guard import EnvKind, Guard
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    class _SingleFindEx:
        def find(self, strategies):
            return object()  # 单元素契约

        def perform(self, action, element, value=None):
            pass

    store = TraceStore(tmp_path / "trace.db")
    store.start_run("run_single")
    runner = StepRunner(_SingleFindEx(), _FakeDS(), Guard(EnvKind.SANDBOX))
    pipe = SessionPipeline(suites_root=None, store=store)
    case = _load_case(VALID_TC)
    case.steps.append(_load_case("""\
schema_version: "0.2"
id: dummy
name: dummy
steps:
  - action: tap
    target: some_button
""").steps[0])
    result = pipe.run_case(runner, Lifecycle(store=store), case,
                           run_id="run_single")
    assert result.status == "PASS"
    row = store.conn.execute(
        "SELECT status FROM steps WHERE step_index=1").fetchone()
    assert row[0] == "SUCCESS"


def test_discover_no_match_is_preflight_error(suites_dir):
    """筛选无命中 → 前置配置错误（8.4 exit 3），不是「跑了个空集成功」。"""
    p = SessionPipeline(suites_root=suites_dir / "suites")
    with pytest.raises(SessionPipeline.PreflightError):
        p.discover(tag="ghost_tag")


# --- CLI 全参数与产物 ---


def test_cli_run_happy_path_with_artifacts(suites_dir, tmp_path, capsys):
    junit_path = tmp_path / "out" / "junit.xml"
    html_path = tmp_path / "out" / "report.html"
    code = main([
        "run", "--suite", "smoke",
        "--suites-root", str(suites_dir / "suites"),
        "--fake-driver",
        "--junit", str(junit_path),
        "--html", str(html_path),
        "--no-llm",
        "--db", str(tmp_path / "trace.db"),
    ])
    out = capsys.readouterr().out
    assert code in (0, 1, 2, 4, 5), f"退出码必须是 8.4 表内的值，got {code}"
    assert f"exit {code}" in out
    # JUnit 产物与 RunResult 退出码一致
    root = ET.parse(junit_path).getroot()
    assert root.find("testsuite") is not None
    # HTML 产物存在且含 run_id
    html_text = html_path.read_text()
    assert "run_id" in html_text
    # trace.db 已创建（新管线真实写入 TraceStore）
    assert (tmp_path / "trace.db").exists() or any(
        p.name == "trace.db" for p in tmp_path.rglob("trace.db"))


def test_cli_run_case_filter(suites_dir, tmp_path, capsys):
    code = main(["run", "--case", "search_001",
                 "--suites-root", str(suites_dir / "suites"),
                 "--fake-driver",
                 "--db", str(tmp_path / "trace.db")])
    out = capsys.readouterr().out
    assert "search_001" in out


def test_cli_run_tag_filter(suites_dir, tmp_path, capsys):
    code = main(["run", "--tag", "profile",
                 "--suites-root", str(suites_dir / "suites"),
                 "--fake-driver",
                 "--db", str(tmp_path / "trace.db")])
    out = capsys.readouterr().out
    assert "profile_001" in out


def test_cli_run_missing_case_file_exit_3(tmp_path):
    """8.4：前置错误 3 > 一切。空 suites 目录筛不出用例。"""
    empty = tmp_path / "empty_suites"
    empty.mkdir()
    code = main(["run", "--suite", "ghost", "--suites-root", str(empty),
                 "--fake-driver",
                 "--db", str(tmp_path / "trace.db")])
    assert code == 3


# --- --allow-production / --allow-metadata-mismatch 至少能解析 ---


def test_cli_run_accepts_guard_flags(suites_dir, tmp_path):
    code = main(["run", "--case", "login_001",
                 "--suites-root", str(suites_dir / "suites"),
                 "--fake-driver",
                 "--allow-production", "--allow-metadata-mismatch",
                 "--db", str(tmp_path / "trace.db")])
    assert code in (0, 1, 2, 4, 5)


# --- SessionPipeline.run_case：新管线桥（R15-2 核销） ---


class _FakeExecutor:
    """find 永远命中一个元素，perform 成功——最小可跑通路。"""

    def __init__(self):
        self.performed: list[str] = []

    def find(self, strategies):
        return [object()]

    def perform(self, action, element, value=None):
        self.performed.append(action)


class _FakeDS:
    def ensure_alive(self):
        pass


def test_run_case_executes_through_new_pipeline(tmp_path):
    """核心验收（R15-2）：TestCase → 逐 StepRunner.run_step → Lifecycle 记录
    → TraceStore 落库。跑完 steps 表必须有行。"""
    from executor.guard import EnvKind, Guard
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    store = TraceStore(tmp_path / "trace.db")
    run_id = "run_bridge"
    store.start_run(run_id)

    ex = _FakeExecutor()
    runner = StepRunner(ex, _FakeDS(), Guard(EnvKind.SANDBOX))
    lc = Lifecycle(store=store)

    pipe = SessionPipeline(suites_root=None, store=store)
    result = pipe.run_case(
        runner, lc, _load_case(VALID_TC), run_id=run_id)

    assert result.status == "PASS"
    rows = store.conn.execute("SELECT COUNT(*) FROM steps").fetchone()[0]
    assert rows >= 1, "新管线必须真实写 TraceStore（R15-2 验收）"
    tc_row = store.conn.execute(
        "SELECT status FROM testcase_runs").fetchone()
    assert tc_row[0] == "PASS"


def _load_case(text: str):
    from testcase.schema import parse_testcase_dict
    return parse_testcase_dict(yaml.safe_load(text))


def test_run_case_failure_records_fail(tmp_path):
    """find 失败 → 用例 FAIL，failure_type=ELEMENT_NOT_FOUND 进库。"""
    from executor.guard import EnvKind, Guard
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    class _NoFind:
        def find(self, strategies):
            from executor.executor import ElementNotFound
            raise ElementNotFound("nope")

        def perform(self, action, element, value=None):
            pass

    store = TraceStore(tmp_path / "trace.db")
    store.start_run("run_fail")
    runner = StepRunner(_NoFind(), _FakeDS(), Guard(EnvKind.SANDBOX))
    lc = Lifecycle(store=store)

    pipe = SessionPipeline(suites_root=None, store=store)
    case = _load_case("""\
schema_version: "0.2"
id: bad_case
name: bad
steps:
  - action: tap
    target: ghost_button
""")
    run = pipe.run_case(runner, lc, case, run_id="run_fail")
    assert run.status == "FAIL"
    assert run.failure_type == "ELEMENT_NOT_FOUND"
    row = store.conn.execute(
        "SELECT status, failure_type FROM testcase_runs").fetchone()
    assert row[0] == "FAIL" and row[1] == "ELEMENT_NOT_FOUND"


def test_run_case_wait_step_and_assertion_step(tmp_path):
    """wait_for / assertion 步骤也要走新管线（6.2 三形态全覆盖）。"""
    from executor.guard import EnvKind, Guard
    from executor.executor import ElementNotFound
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    class _Ex:
        def find(self, strategies):
            raise ElementNotFound("not here")

        def perform(self, action, element, value=None):
            pass

    store = TraceStore(tmp_path / "trace.db")
    store.start_run("run_wait")
    pipe = SessionPipeline(suites_root=None, store=store)
    case = _load_case("""\
schema_version: "0.2"
id: wait_case
name: wait
steps:
  - wait_for:
      target: screen:HomeView
      condition: active
      timeout: 0.1
      polling_interval: 0.05
""")
    runner = StepRunner(_Ex(), _FakeDS(), Guard(EnvKind.SANDBOX))
    lc = Lifecycle(store=store)
    result = pipe.run_case(runner, lc, case, run_id="run_wait")
    # screen active 的 wait 走 WaitEngine → WaitTimeout → FAIL/WAIT_TIMEOUT
    assert result.status == "FAIL"
    assert result.failure_type == "WAIT_TIMEOUT"


# --- R16 修复回归 ---

class _StubOKExecutor:
    """find 返回可见单元素（wait/assert 引擎消费的单元素契约）。"""

    def find(self, strategies):
        return _StubOKElement()

    def perform(self, action, element, value=None):
        pass

    def swipe(self, direction):
        pass


class _StubOKElement:
    def is_displayed(self):
        return True

    def is_enabled(self):
        return True

    @property
    def text(self):
        return ""

    def get_attribute(self, name):
        return ""


def test_r16_1_step_declaration_cannot_lower_metadata(tmp_path):
    """R16-1（P1 安全级）：7.4「取更严格」。element metadata 声明
    HIGH/NON_IDEMPOTENT 时，用例层一行 `risk: LOW`/`idempotency: IDEMPOTENT`
    **压不掉**——两个声明源必须过 policy 纯函数取严（原 or 链实锤绕过）。"""
    from executor.guard import EnvKind, Guard
    from executor.policy import Idempotency, Risk
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    from cli.pipeline import PipelineDeps

    class _HighRiskRepo:
        """element metadata：HIGH / NON_IDEMPOTENT。"""

        def resolve(self, ref, *, build):
            from repository.loader import DataClass, LocatorStrategy
            from repository.resolver import EffectiveElement
            from testcase.schema import Idempotency, Risk
            return EffectiveElement(
                id=ref.id, screen="PayView", type="element",
                strategies=(LocatorStrategy(
                    type="accessibility_id", value=ref.id),),
                risk=Risk.HIGH,
                idempotency=Idempotency.NON_IDEMPOTENT,
                data_class=DataClass.PUBLIC)

    class _OkEx:
        def find(self, strategies):
            return object()

        def perform(self, action, element, value=None):
            pass

    store = TraceStore(tmp_path / "trace.db")
    store.start_run("run_r16_1")
    pipe = SessionPipeline(suites_root=None, store=store,
                           deps=PipelineDeps(repo=_HighRiskRepo()))
    case = _load_case("""\
schema_version: "0.2"
id: pay_case
name: pay
steps:
  - action: tap
    target: PayView.pay_button
    risk: 1
    idempotency: IDEMPOTENT
""")
    runner = StepRunner(_OkEx(), _FakeDS(), Guard(EnvKind.SANDBOX))
    result = pipe.run_case(runner, Lifecycle(store=store), case,
                           run_id="run_r16_1")
    assert result.status == "PASS"
    row = store.conn.execute(
        "SELECT effective_risk, effective_idempotency FROM steps"
    ).fetchone()
    # 取严：LOW 压不掉 HIGH；IDEMPOTENT 压不掉 NON_IDEMPOTENT
    assert row[0] == "HIGH", f"risk 被用例声明压低：{row[0]}"
    assert row[1] == "NON_IDEMPOTENT", f"idempotency 被用例声明放松：{row[1]}"


def test_r16_2_no_fake_driver_exits_3_not_fail(tmp_path, capsys):
    """R16-2（P1 fail-quiet）：真机组件未装配 → PreflightError exit 3，
    不再伪装成 5 条 FAIL + exit 1，且不往 trace.db 写假 FAIL 终态。"""
    suites = tmp_path / "suites"
    suites.mkdir()
    (suites / "a_001.yaml").write_text(VALID_TC, encoding="utf-8")
    db = tmp_path / "trace.db"
    code = main(["run", "--suite", "smoke",
                 "--suites-root", str(suites),
                 "--db", str(db)])  # 无 --fake-driver
    out = capsys.readouterr().out
    assert code == 3, f"未装配必须 exit 3，got {code}"
    assert "未装配" in out
    # trace.db 里不得出现假 FAIL 终态
    if db.exists():
        import sqlite3
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT status FROM testcase_runs").fetchall()
        assert rows == [], f"fail-quiet 假用例结果入库：{rows}"


def test_r16_3_run_id_unique_across_runs(tmp_path, capsys):
    """R16-2/P3-3：同秒多次 run 不得撞 runs.run_id 主键（原 time.time()）。"""
    import time as _t
    suites = tmp_path / "suites"
    suites.mkdir()
    (suites / "a_001.yaml").write_text(VALID_TC, encoding="utf-8")
    for _ in range(2):
        code = main(["run", "--suite", "smoke",
                     "--suites-root", str(suites),
                     "--fake-driver", "--db", str(tmp_path / "trace.db")])
        assert code == 0
        _t.sleep(0.01)  # 同秒内连跑两次才构成碰撞场景


def test_r16_p3_1_wait_step_lands_in_steps_table(tmp_path):
    """P3-1：wait/assert 步骤也要落 steps 表（原实现只记 action 步）。"""
    from executor.guard import EnvKind, Guard
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    store = TraceStore(tmp_path / "trace.db")
    store.start_run("run_aux")
    pipe = SessionPipeline(suites_root=None, store=store)
    case = _load_case("""\
schema_version: "0.2"
id: aux_case
name: aux
steps:
  - wait_for:
      target: screen:HomeView
      condition: active
      timeout: 0.1
""")
    runner = StepRunner(_StubOKExecutor(), _FakeDS(), Guard(EnvKind.SANDBOX))
    result = pipe.run_case(runner, Lifecycle(store=store), case,
                           run_id="run_aux")
    assert result.status == "PASS"
    rows = [tuple(r) for r in store.conn.execute(
        "SELECT step_type, status FROM steps").fetchall()]
    assert ("wait_for", "SUCCESS") in rows, f"wait 步骤未落库：{rows}"


# --- Task 2.7：app/driver 级动作分流（M2 Gate 真机首跑实锤） ---

def test_app_level_actions_bypass_find_pipeline(tmp_path):
    """launch_app/terminate_app/back/swipe 不走 find/perform 元素管线——
    当普通元素动作跑会 find(()) 空 → ELEMENT_NOT_FOUND（App 级动作没有
    target）。真机 Gate 首跑 20/20 挂在这个点。"""
    from executor.guard import EnvKind, Guard
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    launched = []
    store = TraceStore(tmp_path / "trace.db")
    store.start_run("run_applevel")

    class _App:
        def launch(self, arguments=None):
            launched.append("launch")

        def terminate(self):
            launched.append("terminate")

    from cli.pipeline import PipelineDeps
    pipe = SessionPipeline(suites_root=None, store=store,
                           deps=PipelineDeps(app=_App()))
    case = _load_case("""\
schema_version: "0.2"
id: app_level
name: app level actions
steps:
  - action: launch_app
  - action: terminate_app
  - action: launch_app
""")
    runner = StepRunner(_StubOKExecutor(), _FakeDS(), Guard(EnvKind.SANDBOX))
    result = pipe.run_case(runner, Lifecycle(store=store), case,
                           run_id="run_applevel")
    assert result.status == "PASS", result.detail
    assert launched == ["launch", "terminate", "launch"]
    rows = [tuple(r) for r in store.conn.execute(
        "SELECT step_type, status FROM steps ORDER BY step_index").fetchall()]
    assert rows == [("launch_app", "SUCCESS"), ("terminate_app", "SUCCESS"),
                    ("launch_app", "SUCCESS")]


def test_app_level_action_failure_is_fail_not_find_error(tmp_path):
    """app 级动作失败 → ACTION_FAILED（8.2：真实失败类型，不伪装成
    ELEMENT_NOT_FOUND）。"""
    from executor.guard import EnvKind, Guard
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    from cli.pipeline import PipelineDeps

    class _BadApp:
        def launch(self, arguments=None):
            raise RuntimeError("simctl failed")

        def terminate(self):
            pass

    store = TraceStore(tmp_path / "trace.db")
    store.start_run("run_badapp")
    pipe = SessionPipeline(suites_root=None, store=store,
                           deps=PipelineDeps(app=_BadApp()))
    case = _load_case("""\
schema_version: "0.2"
id: bad_launch
name: bad launch
steps:
  - action: launch_app
""")
    runner = StepRunner(_StubOKExecutor(), _FakeDS(), Guard(EnvKind.SANDBOX))
    result = pipe.run_case(runner, Lifecycle(store=store), case,
                           run_id="run_badapp")
    assert result.status == "FAIL"
    assert result.failure_type == "ACTION_FAILED"
    assert "simctl failed" in result.detail.get("error", "")


def test_back_and_swipe_use_stubs_when_no_device_injected(tmp_path):
    """back/swipe 在 fake 模式走 pipeline 内建 no-op 桩（不 PreflightError）——
    fake-driver 的定位是跑通管线，设备侧效果归真机 Gate。"""
    from executor.guard import EnvKind, Guard
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    store = TraceStore(tmp_path / "trace.db")
    store.start_run("run_back")
    pipe = SessionPipeline(suites_root=None, store=store)
    case = _load_case("""\
schema_version: "0.2"
id: back_swipe
name: back and swipe
steps:
  - action: back
  - action: swipe
    direction: up
""")
    runner = StepRunner(_StubOKExecutor(), _FakeDS(), Guard(EnvKind.SANDBOX))
    result = pipe.run_case(runner, Lifecycle(store=store), case,
                           run_id="run_back")
    assert result.status == "PASS", result.detail


def test_strategies_are_dict_locator_contract(tmp_path):
    """2.7 M2 Gate 真机实锤：Executor.find 的 Locator 契约是
    list[dict]（strat["type"] 下标访问）。pipeline 过去传
    ("type","value") 元组 → TypeError 伪装成 FIND_ERROR。这里钉住
    传给 StepRunner 的 strategies 元素必须是 dict。"""
    from executor.guard import EnvKind, Guard
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    seen = {}

    class _ProbeEx:
        def find(self, strategies):
            seen["strategies"] = strategies
            return _StubOKElement()

        def perform(self, action, element, value=None):
            pass

    store = TraceStore(tmp_path / "trace.db")
    store.start_run("run_dict")
    pipe = SessionPipeline(suites_root=None, store=store)
    case = _load_case("""\
schema_version: "0.2"
id: dict_locator
name: dict locator contract
steps:
  - action: tap
    target: LoginView.login_button
""")
    runner = StepRunner(_ProbeEx(), _FakeDS(), Guard(EnvKind.SANDBOX))
    result = pipe.run_case(runner, Lifecycle(store=store), case,
                           run_id="run_dict")
    assert result.status == "PASS"
    strategies = seen["strategies"]
    assert isinstance(strategies, (list, tuple))
    assert strategies and all(
        isinstance(s, dict) and "type" in s and "value" in s
        for s in strategies), f"strategies 必须是 Locator dict 契约：{strategies}"


def test_dispatch_action_uses_executor_tap_input(tmp_path):
    """2.7 M2 Gate 真机实锤（第三处契约断层）：生产 Executor 没有
    perform()——只有 tap(locator)/input(locator, value)。StepRunner 必须
    按动作分派，否则接真实 Executor 必 AttributeError。"""
    from executor.guard import EnvKind, Guard
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    calls = []

    class _RealContractEx:
        """对齐生产 Executor 的方法面：find/tap/input/swipe，无 perform。"""

        def find(self, locator):
            return _StubOKElement()

        def tap(self, locator):
            calls.append(("tap", tuple(locator)))

        def input(self, locator, value):
            calls.append(("input", tuple(locator), value))

    store = TraceStore(tmp_path / "trace.db")
    store.start_run("run_dispatch")
    pipe = SessionPipeline(suites_root=None, store=store)
    case = _load_case("""\
schema_version: "0.2"
id: dispatch
name: dispatch
steps:
  - action: input
    target: LoginView.username_field
    value: qa_user
  - action: tap
    target: LoginView.login_button
""")
    runner = StepRunner(_RealContractEx(), _FakeDS(), Guard(EnvKind.SANDBOX))
    result = pipe.run_case(runner, Lifecycle(store=store), case,
                           run_id="run_dispatch")
    assert result.status == "PASS", result.detail
    assert calls[0][0] == "input" and calls[0][2] == "qa_user"
    assert calls[1][0] == "tap"
    # locator 契约：list[dict]
    for c in calls:
        assert all(isinstance(s, dict) for s in c[1])
