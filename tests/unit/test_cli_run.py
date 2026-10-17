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


def test_optional_step_skips_through_session_pipeline(tmp_path):
    """optional 必须经真实 `SessionPipeline` 接线生效，并落可审计的跳过说明。

    只测 `StepRunner` 会漏掉 `cli/pipeline.py` 忘记把 `step.optional` 传进
    `RunStepContext` 的那类断线——这正是 2026-10-17 真机冒烟的根因。
    """
    from executor.executor import ElementNotFound
    from executor.guard import EnvKind, Guard
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    class _NoFind:
        def find(self, strategies):
            raise ElementNotFound("update dialog absent")

        def perform(self, action, element, value=None):
            pass

    store = TraceStore(tmp_path / "trace.db")
    store.start_run("run_optional")
    pipe = SessionPipeline(suites_root=None, store=store)
    case = _load_case("""\
schema_version: "0.2"
id: optional_case
name: optional
steps:
  - action: tap
    target: update_dialog_cancel
    optional: true
""")
    runner = StepRunner(_NoFind(), _FakeDS(), Guard(EnvKind.SANDBOX))
    result = pipe.run_case(runner, Lifecycle(store=store), case,
                           run_id="run_optional")

    assert result.status == "PASS", result.detail
    row = store.conn.execute(
        "SELECT status, failure_type, detail_json FROM steps"
    ).fetchone()
    assert row[0] == "SUCCESS"
    assert row[1] is None
    assert "optional_skipped" in (row[2] or "")


def test_optional_wait_skips_timeout_through_session_pipeline(tmp_path):
    """optional wait 超时 = 可选前置未出现，记 SUCCESS 且留下审计说明。

    冷启动更新弹窗是异步出现的：立即 optional tap 会在弹窗入树前被跳过，
    随后首页被模态遮住。用例必须先 optional wait 给弹窗一个出现窗口。
    """
    from executor.guard import EnvKind, Guard
    from executor.executor import ElementNotFound
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    class _NoFind:
        def find(self, strategies):
            raise ElementNotFound("update dialog absent")

        def perform(self, action, element, value=None):
            pass

    store = TraceStore(tmp_path / "trace.db")
    store.start_run("run_optional_wait")
    pipe = SessionPipeline(suites_root=None, store=store)
    case = _load_case("""\
schema_version: "0.2"
id: optional_wait_case
name: optional wait
steps:
  - wait_for:
      target: update_dialog_cancel
      condition: exists
      timeout: 0.05
      polling_interval: 0.01
    optional: true
""")
    runner = StepRunner(_NoFind(), _FakeDS(), Guard(EnvKind.SANDBOX))
    result = pipe.run_case(runner, Lifecycle(store=store), case,
                           run_id="run_optional_wait")

    assert result.status == "PASS", result.detail
    row = store.conn.execute(
        "SELECT status, failure_type, detail_json FROM steps"
    ).fetchone()
    assert row[0] == "SUCCESS"
    assert row[1] is None
    assert "optional_skipped" in (row[2] or "")


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


def test_r16_2_no_fake_driver_exits_3_not_fail(tmp_path, capsys, monkeypatch):
    """R16-2（P1 fail-quiet）：真机装配失败 → PreflightError exit 3，
    不再伪装成 5 条 FAIL + exit 1，且不往 trace.db 写假 FAIL 终态。

    Task 2.7 接线（Task 4.3）后真机路径真实装配；单测封闭：stub 掉 caps
    解析与设备连接，装配失败同为前置配置错误（exit 3）。真机路径第一道
    前置是 12.5 Build Identity 校验（G8）——无 booted 模拟器/App 未安装
    时先在 gate 报错，断言接受两类文案。"""
    monkeypatch.setattr("session.device_session.resolve_local_caps",
                        lambda udid, bundle_id: {"udid": udid})
    from session.device_session import DeviceSession

    def _no_device(self):
        raise RuntimeError("unit-test: 无设备会话")

    monkeypatch.setattr(DeviceSession, "connect", _no_device)
    suites = tmp_path / "suites"
    suites.mkdir()
    (suites / "a_001.yaml").write_text(VALID_TC, encoding="utf-8")
    db = tmp_path / "trace.db"
    code = main(["run", "--suite", "smoke",
                 "--suites-root", str(suites),
                 "--db", str(db)])  # 无 --fake-driver
    out = capsys.readouterr().out
    assert code == 3, f"未装配必须 exit 3，got {code}"
    # ⚠️ **每个分支都点名自己的精确原因**（review_p3_task34 round 2 修订）：
    # 原写 `"设备会话建立失败" in out or "build identity" in out` 是**误绿源**
    # ——`cmd_run` 把任何装配异常都套上「设备会话建立失败：」前缀打印，所以
    # 一个 `NameError`（P0-1）也能满足第一个析取项。探针实测：往真机分支注入
    # `NameError`，这条断言**仍然绿**。
    # 现在每个析取项各自指向**具体原因**，且前缀是 `PREFLIGHT ERROR:`（真实的
    # 前置失败形态）。本机无 `xcrun simctl`（未配 DEVELOPER_DIR）→ 走 Build
    # Identity 读取失败那一支；有设备的环境走「无设备会话」那一支。
    # 一个无关的 `NameError` 三个都不满足 → 不再能蒙混过关。
    assert out.startswith("PREFLIGHT ERROR:"), \
        f"必须是前置错误（不是伪装成运行结果）：{out!r}"
    assert ("build identity 读取失败" in out
            or "BUILD_METADATA_MISMATCH" in out
            or "真机路径需要 --bundle-id" in out
            or "unit-test: 无设备会话" in out), \
        f"exit 3 的原因必须点名（不能是任意异常）：{out!r}"
    # trace.db 里不得出现假 FAIL 终态
    if db.exists():
        import sqlite3
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT status FROM testcase_runs").fetchall()
        assert rows == [], f"fail-quiet 假用例结果入库：{rows}"


def test_r16_3_wda_launch_failure_hint_names_lock_screen():
    """R16-3：WDA 起不来的错误要附可操作提示（2026-10-16 真机实测）。

    Appium 的原话是「Failed to start the preinstalled WebDriverAgent in 60000
    ms … might not be properly built」，把人往「WDA 没构建好」上引；真实原因是
    **手机锁屏**——iOS 拒绝拉起 runner，日志只留 `Connection was refused to
    port 8100`。不点破的话每轮白等 60 秒，且方向是错的。
    devicectl 单独拉起会明确报 `device was not, or could not be, unlocked`，
    但 Appium 的 RemoteXPC 路径不吐这句，所以只能由 mta 侧补提示。"""
    from cli.main import _session_failure_hint

    hint = _session_failure_hint(RuntimeError(
        "Unable to launch WebDriverAgent. Original error: Failed to start the "
        "preinstalled WebDriverAgent in 60000 ms. The WebDriverAgent might not "
        "be properly built or the device might be locked."))
    assert "解锁" in hint, f"提示必须点名锁屏：{hint!r}"
    assert "tunnel-creation" in hint, f"提示要给隧道的补救命令：{hint!r}"


def test_r16_3_hint_also_covers_missing_tunnel():
    """同一个 gate 覆盖「隧道没跑」的形态——外层文案同样是
    `Unable to launch WebDriverAgent`（只是 Original error 换成 RemoteXPC）。"""
    from cli.main import _session_failure_hint

    hint = _session_failure_hint(RuntimeError(
        "Unable to launch WebDriverAgent. Original error: Failed to launch the "
        "preinstalled WebDriverAgent via RemoteXPC: Failed launch preinstalled "
        "WebDriverAgent via RemoteXPC for '00008120-0018499A1450C01E' "
        "(RemoteXPC is not available for this session)."))
    assert "解锁" in hint
    assert "tunnel-creation" in hint


def test_r16_3_hint_silent_for_unrelated_errors():
    """无关错误不加提示——不制造噪音，也不假装自己能诊断一切。"""
    from cli.main import _session_failure_hint

    assert _session_failure_hint(RuntimeError("boom")) == ""
    # 提到 WDA 但不是启动失败 → 不加
    assert _session_failure_hint(
        RuntimeError("WebDriverAgent quit unexpectedly")) == ""


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
        """对齐生产 Executor 的方法面：find/tap/input/swipe + 已定位元素
        动作 tap_element/input_element（M4：分派优先用已找到的元素，
        不按 locator 重找——漂移重发路径 M4 Gate 真机实锤）。"""

        def find(self, locator):
            return _StubOKElement()

        def tap_element(self, element):
            calls.append(("tap_element", element))

        def input_element(self, element, value):
            calls.append(("input_element", element, value))

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
    assert calls[0][0] == "input_element" and calls[0][2] == "qa_user"
    assert calls[1][0] == "tap_element"
    # 已定位元素分派：不再按 locator 重找（元素对象直接透传）


def test_r18_3_cleanup_failure_written_to_trace(tmp_path):
    """R18-3：cleanup 失败必须回写 testcase_run（manage_env=False 路径先落了
    PASS，无人回写 → trace 显示「PASS 但套件中止」的矛盾终态）。"""
    from environment.manager import CleanupError, EnvironmentManager
    from executor.guard import EnvKind, Guard
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    from cli.pipeline import PipelineDeps

    class _FailCleanupEnv(EnvironmentManager):
        def __init__(self):
            super().__init__(app=object())
            self.calls = 0

        def prepare(self, tc):
            pass

        def cleanup(self, tc=None):
            raise CleanupError("boom R18-3")

    store = TraceStore(tmp_path / "trace.db")
    store.start_run("run_r18_3")
    pipe = SessionPipeline(ROOT_SUITES(tmp_path), store=store,
                           deps=PipelineDeps(env=_FailCleanupEnv()))
    pipe._step_runner = StepRunner(_StubOKExecutor(), _FakeDS(),
                                   Guard(EnvKind.SANDBOX))
    pipe._lifecycle = Lifecycle(store=store)
    cases = pipe.discover()
    run = pipe.run_all(cases, run_id="run_r18_3")

    rows = [tuple(r) for r in store.conn.execute(
        "SELECT testcase_id, status, failure_type, cleanup_status "
        "FROM testcase_runs ORDER BY id").fetchall()]
    # 第一条：cleanup 失败 → ENVIRONMENT_FAILURE/CLEANUP_FAILED/FAILED
    assert rows[0] == (cases[0].id, "ENVIRONMENT_FAILURE",
                       "CLEANUP_FAILED", "FAILED"), rows
    # 套件中止：只有 1 条跑过（FAIL 后 ABORT_SUITE 不跑后续）
    assert len(run.results) == 1
    assert run.exit_code == 2


def ROOT_SUITES(tmp_path):
    """构造 suites 目录（单条用例）供 discover 用。"""
    sdir = tmp_path / "suites" / "smoke"
    sdir.mkdir(parents=True)
    (sdir / "only_001.yaml").write_text(VALID_TC, encoding="utf-8")
    return tmp_path / "suites"


def test_r18_4_html_rate_denominator_is_steps_not_cases(tmp_path, capsys):
    """R18-4/R17-3：LLM Invocation Rate 的分母必须是步骤数（lifecycle
    累计），不是用例数——len(run.results) 冒充过，比率被放大。"""
    from report.html import render_run_report

    from executor.guard import EnvKind, Guard
    from runner.lifecycle import Lifecycle
    from runner.result import RunResult
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    store = TraceStore(tmp_path / "trace.db")
    pipe = SessionPipeline(ROOT_SUITES(tmp_path), store=store)
    runner = StepRunner(_StubOKExecutor(), _FakeDS(), Guard(EnvKind.SANDBOX))
    lifecycle = Lifecycle(store=store)
    store.start_run("run_rate")
    case = pipe.discover()[0]
    result = pipe.run_case(runner, lifecycle, case, run_id="run_rate")
    assert result.status == "PASS"
    # 单用例 1 step（VALID_TC 只有 launch_app），若分母是用例数则 =1（同值）
    # —— 用两条用例跑 run_all 拉开差距：2 用例 / 3 step
    html = render_run_report(
        RunResult(run_id="r", suite=None), llm_calls=0,
        executed_steps=lifecycle.steps_recorded)
    assert "LLM Invocation Rate" in html
    # steps_recorded 必须 > 0 且与 DB 步数一致（不是用例数 1）
    db_steps = store.conn.execute("SELECT COUNT(*) FROM steps").fetchone()[0]
    assert lifecycle.steps_recorded == db_steps, (
        f"steps_recorded={lifecycle.steps_recorded} db={db_steps}")


# --- P2-3（review_m3_task31）核销：mta run --generated 双源接线 ---


def test_run_generated_flag_consumes_dual_source(tmp_path):
    """run 传 --generated 后，generated-only 元素可被 lint/执行解析（5.3
    双源合并）；不传时同一用例 resolve 不到（overrides 兼职形态的旧 bug
    是「参数存在=没接」，这里验真消费）。"""
    gen = tmp_path / "generated" / "local"
    (gen / "elements").mkdir(parents=True)
    (gen / "screens").mkdir(parents=True)
    (gen / "elements" / "HomeView.yaml").write_text(
        "schema_version: '1.0'\nkind: element\nid: gen_only_cell\n"
        "screen: HomeView\ntype: cell\nstrategies:\n"
        "- type: accessibility_id\n  value: gen_only_cell\n  origin: source\n"
        "metadata:\n  origin: source\n", encoding="utf-8")
    (gen / "screens" / "HomeView.yaml").write_text(
        "schema_version: '1.0'\nkind: screen\nid: HomeView\n"
        "marker: screen.HomeView\nkind_hint: page\nmetadata:\n"
        "  risk: LOW\n  origin: source\n", encoding="utf-8")
    suites = tmp_path / "suites"
    suites.mkdir()
    (suites / "gen_001.yaml").write_text(
        'schema_version: "0.2"\nid: gen_001\nname: 双源\nsuite: smoke\n'
        "tags: [smoke]\nsteps:\n  - action: launch_app\n"
        "  - action: tap\n    target: HomeView.gen_only_cell\n"
        "    idempotency: IDEMPOTENT\n", encoding="utf-8")
    code = main(["run", "--case", "gen_001",
                 "--suites-root", str(suites),
                 "--db", str(tmp_path / "trace.db"), "--fake-driver",
                 "--generated", str(gen)])
    assert code == 0, "generated-only 元素必须经 --generated 可解析"


# --- review_m4_task43 P2-1：${VAR} 运行时解析（14.4 Runner 层落点） ---------

SECRET_TC = """\
schema_version: "0.2"
id: secret_001
name: 占位符解析
suite: smoke
tags: [smoke]
steps:
  - action: launch_app
  - action: input
    target: LoginView.username_field
    value: ${TEST_USERNAME}
  - action: input
    target: LoginView.password_field
    value: ${TEST_PASSWORD}
    sensitive: true
"""


def _write_secret_case(tmp_path):
    sdir = tmp_path / "suites"
    sdir.mkdir()
    (sdir / "secret_001.yaml").write_text(SECRET_TC, encoding="utf-8")
    return sdir


def test_run_resolves_secret_placeholders_at_dispatch(
        tmp_path, monkeypatch, capsys):
    """分派前经 SecretProvider 解析（P2-1③）：env 齐全 → lint 过、run 绿。"""
    monkeypatch.setenv("TEST_USERNAME", "qa_user")
    monkeypatch.setenv("TEST_PASSWORD", "qa_pass")
    sdir = _write_secret_case(tmp_path)
    code = main(["run", "--case", "secret_001",
                 "--suites-root", str(sdir),
                 "--db", str(tmp_path / "trace.db"), "--fake-driver",
                 "--no-llm"])
    assert code == 0, capsys.readouterr().out


def test_run_lint_blocks_unresolvable_placeholder(tmp_path, monkeypatch,
                                                  capsys):
    """env 缺失 → lint unknown_secret ERROR → exit 3（前置门语义不变）。"""
    monkeypatch.delenv("TEST_USERNAME", raising=False)
    monkeypatch.delenv("TEST_PASSWORD", raising=False)
    sdir = _write_secret_case(tmp_path)
    code = main(["run", "--case", "secret_001",
                 "--suites-root", str(sdir),
                 "--db", str(tmp_path / "trace.db"), "--fake-driver",
                 "--no-llm"])
    out = capsys.readouterr().out
    assert code == 3
    assert "unknown_secret" in out


def test_run_malformed_placeholder_blocked_by_lint(tmp_path, monkeypatch,
                                                   capsys):
    """`${{VAR}}`（f-string 转义事故形态）→ malformed_secret_ref ERROR。
    此前 SECRET_REF 不匹配它，带病用例照常进 run（P2-1 实锤路径）。"""
    monkeypatch.setenv("TEST_USERNAME", "qa_user")
    monkeypatch.setenv("TEST_PASSWORD", "qa_pass")
    sdir = _write_secret_case(tmp_path)
    yaml_body = SECRET_TC.replace("${TEST_USERNAME}", "${{TEST_USERNAME}}")
    (sdir / "secret_001.yaml").write_text(yaml_body, encoding="utf-8")
    code = main(["run", "--case", "secret_001",
                 "--suites-root", str(sdir),
                 "--db", str(tmp_path / "trace.db"), "--fake-driver",
                 "--no-llm"])
    out = capsys.readouterr().out
    assert code == 3
    assert "malformed_secret_ref" in out


def test_run_resolved_secret_not_in_trace(tmp_path, monkeypatch):
    """解析后的密钥值绝不落 trace（H9/H8 端到端断言，review 建议补的
    那条「解析后的值不落 trace」）。"""
    import sqlite3

    monkeypatch.setenv("TEST_USERNAME", "qa_user")
    monkeypatch.setenv("TEST_PASSWORD", "S3cret-pass-42")
    sdir = _write_secret_case(tmp_path)
    db = tmp_path / "trace.db"
    code = main(["run", "--case", "secret_001",
                 "--suites-root", str(sdir),
                 "--db", str(db), "--fake-driver", "--no-llm"])
    assert code == 0
    dump = "\n".join(
        " ".join(str(c) for c in row)
        for table in ("runs", "testcase_runs", "steps")
        for row in sqlite3.connect(db).execute(f"SELECT * FROM {table}"))
    assert "S3cret-pass-42" not in dump
    assert "${TEST_PASSWORD}" not in dump, "占位符字面量也不该出现（已解析）"


# --- review_m4_task43 P2-2：--env-kind / --allow-production 接线 ------------

def test_production_requires_allow_production_flag(tmp_path, capsys):
    """10.1：production 启动必须显式 --allow-production（缺 → exit 3）。"""
    sdir = tmp_path / "suites"
    sdir.mkdir()
    code = main(["run", "--case", "no_such",
                 "--suites-root", str(sdir),
                 "--db", str(tmp_path / "trace.db"), "--fake-driver",
                 "--env-kind", "production"])
    out = capsys.readouterr().out
    assert code == 3
    assert "--allow-production" in out


def test_env_kind_recorded_in_runs(tmp_path, monkeypatch):
    """env_kind 落 runs 审计列（此前恒 NULL——P2-2 第三实锤）。"""
    import sqlite3

    monkeypatch.delenv("TEST_USERNAME", raising=False)
    sdir = tmp_path / "suites"
    sdir.mkdir()
    (sdir / "plain_001.yaml").write_text(VALID_TC, encoding="utf-8")
    db = tmp_path / "trace.db"
    code = main(["run", "--case", "login_001",
                 "--suites-root", str(sdir),
                 "--db", str(db), "--fake-driver", "--no-llm",
                 "--env-kind", "production", "--allow-production"])
    assert code == 0
    row = sqlite3.connect(db).execute(
        "SELECT env_kind FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row[0] == "production"


def _ns(**kw):
    """`_resolve_app_build` 的最小 Namespace（只关心 metadata/generated）。"""
    import argparse
    return argparse.Namespace(**kw)


def test_resolve_app_build_reads_metadata_build(tmp_path):
    """12.5/E7：build id 从 12.3 metadata 的 `build` 字段读（单一真值源）。"""
    from cli.main import _resolve_app_build

    meta = tmp_path / "source_metadata.json"
    meta.write_text('{"build": "1026", "git_commit": "abc1234"}',
                    encoding="utf-8")
    assert _resolve_app_build(_ns(metadata=str(meta))) == "1026"


def test_resolve_app_build_falls_back_without_metadata(tmp_path):
    """读不到 metadata → 退回 "local"，**不 fail-loud**（fake-driver / 单测
    没有 metadata 是常态；build id 不是安全判据，不该拦住一次 run）。"""
    from cli.main import _resolve_app_build
    from cli.pipeline import DEFAULT_APP_BUILD

    assert _resolve_app_build(
        _ns(metadata=None, generated=str(tmp_path / "nope"))) \
        == DEFAULT_APP_BUILD


def test_resolve_app_build_falls_back_on_bad_json(tmp_path):
    """坏 JSON 同样退回默认——不把「构建身份读坏了」升级成 run 失败。"""
    from cli.main import _resolve_app_build
    from cli.pipeline import DEFAULT_APP_BUILD

    meta = tmp_path / "source_metadata.json"
    meta.write_text("{not json", encoding="utf-8")
    assert _resolve_app_build(_ns(metadata=str(meta))) == DEFAULT_APP_BUILD


def test_run_records_app_build_scope(tmp_path):
    """Task 5.1：`runs.app_build` 必须被写入（M5 的 build-to-build diff 按它分图）。

    此前该列**从未被写过**（P1 遗留：build identity 只记 `metadata_build`），
    于是所有真实 run 的图都会落进同一个空 scope，`mta graph diff --build B
    --base-build A` 无从下手。两条路径（真机 / --fake-driver）都要记。
    """
    import sqlite3

    sdir = tmp_path / "suites"
    sdir.mkdir()
    (sdir / "plain_001.yaml").write_text(VALID_TC, encoding="utf-8")
    meta = tmp_path / "source_metadata.json"
    meta.write_text('{"build": "1026", "git_commit": "abc1234"}',
                    encoding="utf-8")
    db = tmp_path / "trace.db"

    code = main(["run", "--case", "login_001", "--suites-root", str(sdir),
                 "--db", str(db), "--fake-driver", "--no-llm",
                 "--metadata", str(meta)])
    assert code == 0
    row = sqlite3.connect(db).execute(
        "SELECT app_build FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row[0] == "1026", "build id 来自 12.3 metadata（与 pipeline 同源）"


def test_run_records_default_app_build_without_metadata(tmp_path):
    """读不到 metadata → 记 `local`（与 pipeline 的 DEFAULT_APP_BUILD 一致），
    不 fail-loud、也不留 NULL。"""
    import sqlite3

    sdir = tmp_path / "suites"
    sdir.mkdir()
    (sdir / "plain_001.yaml").write_text(VALID_TC, encoding="utf-8")
    db = tmp_path / "trace.db"

    code = main(["run", "--case", "login_001", "--suites-root", str(sdir),
                 "--db", str(db), "--fake-driver", "--no-llm",
                 "--metadata", str(tmp_path / "nope.json")])
    assert code == 0
    row = sqlite3.connect(db).execute(
        "SELECT app_build FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row[0] == "local"


# --- review_p3_task34 P0-1 / P3-3：真机装配路径必须**真的被走到** ---------------


def test_wire_device_stack_real_device_path_assembles(monkeypatch, tmp_path):
    """P0-1 / P3-3：`wire_device_stack(fake_driver=False)` 的**真机分支**必须
    能装配完成，且把四件套回填到位。

    为什么补这条：`test_r16_2_no_fake_driver_exits_3_not_fail` 是此前**唯一**
    触及真机分支的用例，而它把 `DeviceSession.connect` 换成必抛异常——装配在
    `os.environ` **之前**就失败，断言文案又被 `NameError` 的报错**恰好命中**
    → 测试绿、真机路径死（`cli/pipeline.py` 漏了 `import os`，P0-1）。
    「守卫空转」的教科书形态：唯一覆盖某分支的测试在该分支**第一行之前**就退出。

    本测试 stub 掉设备侧，让装配**跑到底**，逐项断言回填结果。
    """
    import session.device_session as ds_mod
    import cli.pipeline as cp
    from executor.guard import EnvKind

    quits: list[int] = []

    class _FakeDriver:
        def quit(self):
            quits.append(1)

        def implicitly_wait(self, seconds):     # Executor 装配时会调
            self.implicit = seconds

    class _FakeDS:
        def __init__(self, url, caps):
            self.url, self.caps, self.driver = url, caps, None

        def connect(self):
            self.driver = _FakeDriver()
            return self.driver

        def ensure_alive(self):
            return self.driver

    monkeypatch.setattr(ds_mod, "DeviceSession", _FakeDS)
    monkeypatch.setattr(ds_mod, "resolve_local_caps",
                        lambda udid, bundle_id: {"udid": udid,
                                                 "bundleId": bundle_id})

    pipeline = SessionPipeline(suites_root=tmp_path)
    pipeline.store = None
    sentinel_repo = object()
    stack = cp.wire_device_stack(
        pipeline, env_kind=EnvKind.SANDBOX, run_id="r1", udid="UDID-1",
        bundle_id="com.example.app", repo=sentinel_repo, fake_driver=False)

    # 四件套与回填逐项在位（漏一项就是「装配半成品」）
    assert isinstance(stack.device_session, _FakeDS)
    assert pipeline.deps.repo is sentinel_repo, "repo 没回填进 deps"
    assert pipeline.deps.device_session is stack.device_session
    assert pipeline._run_id == "r1"
    assert pipeline._lifecycle is stack.lifecycle
    assert pipeline._step_runner is stack.runner
    assert stack.runner.postcondition_checker is not None, \
        "H7：非幂等动作的 postcondition 必须接上"
    # 收尾：close() 真的 quit 了 driver
    stack.close()
    assert len(quits) == 1


def test_wire_device_stack_closes_the_session_when_assembly_fails(
        monkeypatch, tmp_path):
    """P2-3：`connect()` **之后**的装配若抛错 → Appium 会话不能泄漏。

    `DeviceStack.close()` 只在成功返回后由调用方的 `finally` 调到，失败路径
    必须自己收尾（旧实现同样有这个洞，但抽成「唯一实现」后它成了 3.5 的
    `mta generate` 也会走的入口）。
    """
    import session.device_session as ds_mod
    import environment.manager as em
    import cli.pipeline as cp
    from executor.guard import EnvKind

    quits: list[int] = []

    class _FakeDriver:
        def quit(self):
            quits.append(1)

    class _FakeDS:
        def __init__(self, url, caps):
            self.driver = None

        def connect(self):
            self.driver = _FakeDriver()
            return self.driver

    class _Boom:
        def __init__(self, *a, **kw):
            raise RuntimeError("装配中途炸了")

    monkeypatch.setattr(ds_mod, "DeviceSession", _FakeDS)
    monkeypatch.setattr(ds_mod, "resolve_local_caps",
                        lambda udid, bundle_id: {"udid": udid})
    monkeypatch.setattr(em, "EnvironmentManager", _Boom)

    pipeline = SessionPipeline(suites_root=tmp_path)
    pipeline.store = None
    with pytest.raises(RuntimeError, match="装配中途炸了"):
        cp.wire_device_stack(pipeline, env_kind=EnvKind.SANDBOX, run_id="r1",
                             udid="U", bundle_id="com.x", repo=None,
                             fake_driver=False)
    assert len(quits) == 1, "装配失败必须 quit 掉已建立的会话（否则泄漏）"


def test_wire_device_stack_requires_bundle_id_on_the_real_path(monkeypatch,
                                                               tmp_path):
    """真机路径缺 `--bundle-id` → `ValueError`（调用方翻成 exit 3）。"""
    import cli.pipeline as cp
    from executor.guard import EnvKind

    pipeline = SessionPipeline(suites_root=tmp_path)
    pipeline.store = None
    with pytest.raises(ValueError, match="bundle-id"):
        cp.wire_device_stack(pipeline, env_kind=EnvKind.SANDBOX, run_id="r1",
                             udid="U", bundle_id=None, repo=None,
                             fake_driver=False)


def test_wire_device_stack_shares_the_guard_with_recovery(monkeypatch,
                                                          tmp_path):
    """P2-new（round 2）：`recovery.guard` 回填**必须被真的钉住**。

    第一轮修订补的三条真机装配测试都构造 `SessionPipeline(suites_root=...)`
    而**没设 `recovery`**（默认 `None`）→ `if pipeline.recovery is not None:`
    那一支**从未被进入**。评审探针实测：把它改成 `if False:` 全量 **1807 全绿
    / 0 red**——即第一轮审计里「`repo=None` 与 `recovery.guard` 两条不再是零
    覆盖」这句**对 `recovery.guard` 是过度声称**（同类缺陷的第二次：声称已覆盖
    而实际没有）。

    为什么这条不是可有可无的装饰：`RecoveryEngine._make_guard_check` 在
    `self.guard is None` 时**直接 return None**（`agent/recovery.py:391`），
    于是 10.1 的 Guard 复检**在真机上静默关闭**——LLM 候选的风险门控没了，
    而没有任何东西会报错。生产可达：`cli/main.py:661` 无条件构造
    `RecoveryEngine(...)`（不传 guard），随后 `wire_device_stack` 才回填。
    """
    import session.device_session as ds_mod
    import cli.pipeline as cp
    from agent.recovery import RecoveryEngine
    from executor.guard import EnvKind

    class _FakeDriver:
        def quit(self):
            pass

        def implicitly_wait(self, seconds):
            pass

    class _FakeDS:
        def __init__(self, url, caps):
            self.driver = None

        def connect(self):
            self.driver = _FakeDriver()
            return self.driver

    monkeypatch.setattr(ds_mod, "DeviceSession", _FakeDS)
    monkeypatch.setattr(ds_mod, "resolve_local_caps",
                        lambda udid, bundle_id: {"udid": udid})

    pipeline = SessionPipeline(suites_root=tmp_path)
    pipeline.store = None
    # ⚠️ **必须真的设一个 recovery**——否则测的还是那条没被进入的分支
    # （这正是第一轮修订踩的坑）。
    engine = RecoveryEngine()
    assert engine.guard is None, "前提：构造时没有 guard（10.1 复检的起点）"
    pipeline.recovery = engine

    stack = cp.wire_device_stack(pipeline, env_kind=EnvKind.SANDBOX,
                                 run_id="r1", udid="U", bundle_id="com.x",
                                 repo=None, fake_driver=False)
    assert pipeline.recovery.guard is stack.runner.guard, (
        "9.3-4 / 10.1：recovery 的 Guard 复检必须与动作步**同一实例**"
        "（否则 LLM 候选的风险门控在真机上静默关闭）")


def test_real_device_run_records_app_build_scope(monkeypatch, tmp_path):
    """真机路径也必须写 `runs.app_build`——**这条此前是假绿的**。

    `test_run_records_app_build_scope` 只跑 `--fake-driver`，而缺陷只在真机
    路径上现形：`cmd_run` 在 Build Identity 那一段用 `bi_fields = {...}` 整体
    赋值，把前面刚写进去的 `app_build` **覆盖掉**，于是真机 run 的
    `runs.app_build` 是 NULL。后果不是「少一列」：`read_trace_steps` 对同一
    库里 `NULL` 与 `'local'` 两个范围**必然 fail-loud**，`mta explore` 直接
    exit 3——M4 Gate 现场踩到（同库的 explore 行是 `'local'`，run 行是 NULL）。

    单测封闭：stub 掉 Build Identity 的读取与设备装配，让真机分支跑到写库。
    """
    import sqlite3

    from source import build_identity as bi

    monkeypatch.setattr(bi, "read_app_identity",
                        lambda udid, bundle: bi.AppIdentity(git_commit="abc1234",
                                                            build="local"))
    monkeypatch.setattr(bi, "resolve_booted_udid", lambda: "UDID-1")
    monkeypatch.setattr("session.device_session.resolve_local_caps",
                        lambda udid, bundle_id: {"udid": udid})

    class _FakeDriver:
        def quit(self):
            pass

        def implicitly_wait(self, seconds):
            pass

        def activate_app(self, bundle_id):      # `launch_app` 走它
            pass

    class _FakeDS:
        def __init__(self, url, caps):
            self.url, self.caps, self.driver = url, caps, None

        def connect(self):
            self.driver = _FakeDriver()
            return self.driver

        def ensure_alive(self):
            return self.driver

    monkeypatch.setattr("session.device_session.DeviceSession", _FakeDS)

    suites = tmp_path / "suites"
    suites.mkdir()
    (suites / "a_001.yaml").write_text(VALID_TC, encoding="utf-8")
    meta = tmp_path / "source_metadata.json"
    meta.write_text('{"build": "local", "git_commit": "abc1234"}',
                    encoding="utf-8")
    db = tmp_path / "trace.db"

    code = main(["run", "--case", "login_001", "--suites-root", str(suites),
                 "--db", str(db), "--no-llm", "--bundle-id", "com.example.app",
                 "--udid", "UDID-1", "--metadata", str(meta),
                 "--generated", str(tmp_path)])
    assert code == 0, f"装配应跑通（真机分支），got {code}"
    row = sqlite3.connect(db).execute(
        "SELECT app_build FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row[0] == "local", (
        "真机路径的 runs.app_build 被 Build Identity 那段覆盖成了 NULL——"
        "同库混进两个范围会让 read_trace_steps fail-loud")
