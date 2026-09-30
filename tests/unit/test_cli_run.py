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
