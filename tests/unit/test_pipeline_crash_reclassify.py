"""Batch B 修正：崩溃判定必须覆盖**所有**失败出口，不只是 `except InfraError`。

## 真机实测暴露的缺口（`docs/p3_data_audit.md` §10）

上一版把「App 崩了吗」只挂在 `except InfraError` 上。真机实测（`mta run`
+ 后台 `kill -SEGV`）打脸：App 确实崩了（`LoginDemo-*.ips` 里
`EXC_CRASH`/`SIGSEGV` 在案），管线却记成

```text
login_again_001: FAIL (CURRENT_SCREEN_UNKNOWN)
```

——**因为 App 死了最常见的表现不是 WDA 报错，而是「元素找不到 / 屏认不出来」
这类普通步骤失败**，它根本不经过 `except InfraError`。单测看不见这类缺口：
它们只喂 `InfraError`，而真机喂的是 `WaitTimeout`。

## 判据方向：**排除表**而不是白名单

上一版的教训正是「没想到的症状形态溜过了白名单」。若改判用白名单，下次出现
一个新的症状名字，同一条链会**再断一次**——而且是静默的。

故本函数用**排除表**：`_CRASH_UNEXPLAINABLE` 逐条列出「**有别的账主**、崩溃
解释不了」的失败类型（断言值不符归 P1、Guard 拦截在分派前、环境/变量解析归
环境……），每条带一句理由；**其余一律可改判**。

误报面被两件事夹住：① 必须有**时间窗内**的真实崩溃报告（权重 5 的强证据）；
② 原症状在 `detail.crash_reclassified.failure_type` 里留痕（可审计、可回退）。

## 反钉

没探测 / 探测说没崩 / 非 FAIL 终态 / 排除表命中 → **一律原样**，不许把
「接线」退化成「总是记 APP_CRASH」。
"""
from __future__ import annotations

import pytest

from cli.pipeline import (SessionPipeline, _crash_reclassify,
                          _CRASH_UNEXPLAINABLE)
from telemetry.collectors import CrashReport


def _report():
    return CrashReport(path="/x/a.ips", bundle_id="com.demo",
                       timestamp="2026-10-11 20:00:00.00 +0800",
                       incident_id="INC-1", exception_type="EXC_CRASH",
                       signal="SIGSEGV", termination="Segmentation fault: 11",
                       faulting_thread=0, top_frame="main")


# ======================================================================
# 1. `_crash_reclassify`：纯函数（F13——判据离设备可验证）
# ======================================================================


@pytest.mark.parametrize("ftype", [
    "ELEMENT_NOT_FOUND", "WAIT_TIMEOUT", "CURRENT_SCREEN_UNKNOWN",
    "SCREEN_AMBIGUOUS", "AMBIGUOUS_ELEMENT", "ACTION_FAILED",
    "ACTION_OUTCOME_UNKNOWN",
])
def test_a_crash_reclassifies_crash_explainable_symptoms(ftype):
    """这些症状都能是「App 死了」的后果——记成症状就是把 App 的账记到别处。"""
    assert _crash_reclassify("FAIL", ftype, _report()) == ("FAIL", "APP_CRASH")


def test_an_unanticipated_symptom_name_still_reclassifies():
    """**排除表的核心价值**：没见过的失败类型名照样改判。

    白名单版在这里会静默漏掉——而真机恰恰只会给你没预料过的形态
    （本批的 `CURRENT_SCREEN_UNKNOWN` 就是这么漏的）。
    """
    assert _crash_reclassify("FAIL", "WebDriverException", _report()) \
        == ("FAIL", "APP_CRASH")
    assert _crash_reclassify("FAIL", "SomeFutureType", _report()) \
        == ("FAIL", "APP_CRASH")


@pytest.mark.parametrize("ftype", sorted(_CRASH_UNEXPLAINABLE))
def test_a_crash_does_not_reclassify_types_with_another_owner(ftype):
    """**反钉**：排除表里的每一条都有别的账主，崩溃不该改写它。"""
    assert _crash_reclassify("FAIL", ftype, _report()) == ("FAIL", ftype)


def test_every_excluded_type_carries_a_reason():
    """排除表是**口径**不是随手加的名单——每条必须说清「为什么不改判」。"""
    assert _CRASH_UNEXPLAINABLE, "排除表不该是空的"
    for ftype, reason in _CRASH_UNEXPLAINABLE.items():
        assert ftype.isupper() and "_" in ftype or ftype.isupper(), ftype
        assert len(reason) > 10, f"{ftype} 的理由太短，等于没写：{reason!r}"


def test_the_assertion_mismatch_is_excluded_because_p1_owns_it():
    """点名一条最要紧的：断言读到过值 = 那一刻 App 活着；且设计 §8.2 末条
    明文「断言值不符归 P1，不进诊断」。改判成 APP_CRASH 会同时错两处。"""
    assert "ASSERTION_VALUE_MISMATCH" in _CRASH_UNEXPLAINABLE


def test_no_report_means_no_reclassification():
    """旁路证据缺席 → 旧行为逐字保留（这是 `crash_probe=None` 的兼容基础）。"""
    assert _crash_reclassify("FAIL", "CURRENT_SCREEN_UNKNOWN", None) \
        == ("FAIL", "CURRENT_SCREEN_UNKNOWN")


def test_reclassification_is_idempotent():
    """已经判成 APP_CRASH 的再过一遍不改。"""
    assert _crash_reclassify("FAIL", "APP_CRASH", _report()) \
        == ("FAIL", "APP_CRASH")


def test_the_infra_path_does_not_get_reclassified_a_second_time(tmp_path):
    """`except InfraError` 与收尾改判是**两个**消费点，同一条用例都会走到。

    InfraError 分支已经判成 `APP_CRASH` 了；收尾那一遍必须**认出**这一点、
    什么都不做——否则同一条用例上会多出一条 `crash_reclassified` 审计记录，
    读者会以为「改了两次」（而且步骤行会被无谓地重写一遍）。
    """
    import json
    from session.device_session import InfraError

    class _DeadDS:
        def ensure_alive(self):
            raise InfraError("WDA session died")

        def restart_wda(self):
            pass

    # ⚠️ 必须用**动作步**：`ensure_alive` 只在 `StepRunner.run_step` 里调，
    # wait/assert 走引擎、不碰设备层。用 WAIT_CASE 会走成「wait 失败 + 收尾
    # 改判」，那是另一条路径（第一版就是这么写的，断言因此不成立）。
    probe = _Probe(_report())
    result, store = _run_with_ds(tmp_path, probe, _DeadDS(), case_yaml=TAP_CASE)
    assert (result.status, result.failure_type) == ("FAIL", "APP_CRASH")
    # ⚠️ 这条断言是**两个消费点共用一次探测**的唯一证据（A/B 探针 R7/R12/R13
    # 实测：去掉缓存、或任一处改成自己探，别的测试全绿）。这条用例是唯一会
    # 走到**两个**消费点的形态——InfraError 分支先判一次，收尾改判再判一次。
    assert len(probe.calls) == 1, \
        f"两个消费点必须共用一次探测，实际探了 {len(probe.calls)} 次"
    # 步骤行是 InfraError 分支直接写对的，**不该**再被打上改判痕迹
    row = store.conn.execute(
        "SELECT failure_type, detail_json FROM steps").fetchone()
    assert row["failure_type"] == "APP_CRASH"
    assert "crash_reclassified" not in (row["detail_json"] or ""), \
        "InfraError 路径已判过，收尾不该再改一次"


@pytest.mark.parametrize("status", ["PASS", "RECOVERED", "BLOCKED",
                                    "ENVIRONMENT_FAILURE", "INFRA_FAILURE"])
def test_only_a_failed_testcase_is_reclassified(status):
    """**反钉**：非 FAIL 终态一律不动。BLOCKED 是安全策略终态（exit 4），
    ENVIRONMENT_FAILURE 是环境的账（11.2）——崩溃不该改写它们。"""
    assert _crash_reclassify(status, "CURRENT_SCREEN_UNKNOWN", _report()) \
        == (status, "CURRENT_SCREEN_UNKNOWN")


def test_a_missing_failure_type_is_left_alone():
    """不知道原来是什么症状 → 不猜（改了就没法审计了）。"""
    assert _crash_reclassify("FAIL", None, _report()) == ("FAIL", None)


# ======================================================================
# 2. 全链：真机那次的形态（wait 失败 + 崩溃报告）→ 用例行与**步骤**行都要改
# ======================================================================


class _Probe:
    def __init__(self, report=None):
        self.report = report
        self.calls = []

    def crashed_since(self, since):
        self.calls.append(since)
        return self.report


class _FakeDS:
    def ensure_alive(self):
        return self


class _NoFindEx:
    """find 恒失败（App 死了 → 元素找不到）+ page_source 可用（WDA 还活着）。

    这正是真机那次的形态：**WDA 没死，App 死了**。
    """

    def find(self, strategies):
        from executor.executor import ElementNotFound
        raise ElementNotFound("no element")

    def perform(self, action, element, value=None):
        pass

    def page_source(self):
        return "<App><Node name='not_a_marker'/></App>"


TAP_CASE = """\
schema_version: "0.2"
id: tap_case
name: tap
steps:
  - action: tap
    target: HomeView.login_button
"""


WAIT_CASE = """\
schema_version: "0.2"
id: crash_case
name: crash
steps:
  - wait_for:
      target: screen:HomeView
      condition: active
      timeout: 0.1
      polling_interval: 0.05
"""


def _run_with_ds(tmp_path, probe, ds, *, case_yaml=WAIT_CASE):
    """跑一条用例，用给定的 DeviceSession 替身（InfraError 路径需要它）。"""
    return _run(tmp_path, probe, case_yaml=case_yaml, ds=ds)


def _run(tmp_path, probe, *, case_yaml=WAIT_CASE, ex=None, ds=None):
    from executor.guard import EnvKind, Guard
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    store = TraceStore(tmp_path / "trace.db")
    store.start_run("run_crash")
    pipe = SessionPipeline(suites_root=None, store=store, crash_probe=probe)
    runner = StepRunner(ex or _NoFindEx(), ds or _FakeDS(),
                        Guard(EnvKind.SANDBOX))
    lc = Lifecycle(store=store)
    case = _load_case(case_yaml)
    result = pipe.run_case(runner, lc, case, run_id="run_crash")
    return result, store


def _load_case(text):
    import yaml
    from testcase.schema import parse_testcase_dict
    return parse_testcase_dict(yaml.safe_load(text))


def test_a_real_crash_lands_as_app_crash_on_both_rows(tmp_path):
    """**本文件的核心**：App 崩了、症状是普通步骤失败 → 两行都得是 APP_CRASH。

    ⚠️ **步骤**行必须也改：`diagnosis/classifier.py::crash_detected` 的第一条
    判据读的是**步骤**的 `failure_type`（`ctx.steps` 来自 `steps` 表）。只改
    用例行的话，规则引擎仍然看不见崩溃——真机实测就是这么断的。
    """
    result, store = _run(tmp_path, _Probe(_report()))
    assert (result.status, result.failure_type) == ("FAIL", "APP_CRASH")

    tc = store.conn.execute(
        "SELECT status, failure_type FROM testcase_runs").fetchone()
    assert tuple(tc) == ("FAIL", "APP_CRASH")

    failed = store.conn.execute(
        "SELECT step_type, status, failure_type, detail_json FROM steps"
        " WHERE status='FAILED'").fetchall()
    assert failed, "失败步骤必须落库（P3-1）"
    for row in failed:
        assert row["failure_type"] == "APP_CRASH", dict(row)


def test_the_original_symptom_is_kept_in_the_audit_note(tmp_path):
    """改判必须留痕：错误文本说找不到元素、类型却是 APP_CRASH——没有这条
    审计记录，事后会以为那是脏数据。"""
    import json

    result, store = _run(tmp_path, _Probe(_report()))
    row = store.conn.execute(
        "SELECT detail_json FROM steps WHERE status='FAILED'").fetchone()
    note = json.loads(row["detail_json"])["crash_reclassified"]
    assert note["failure_type"] == "WAIT_TIMEOUT", "原症状要留着"
    assert note["crash"]["signal"] == "SIGSEGV"
    assert note["ts"]


def test_without_a_crash_report_the_symptom_is_untouched(tmp_path):
    """**反钉**：探测说没崩 → 旧行为逐字保留（真机上「WDA 抖动」与
    「App 崩了」必须继续分得开）。"""
    result, store = _run(tmp_path, _Probe(None))
    assert (result.status, result.failure_type) == ("FAIL", "WAIT_TIMEOUT")
    row = store.conn.execute(
        "SELECT failure_type, detail_json FROM steps WHERE status='FAILED'"
    ).fetchone()
    assert row["failure_type"] == "WAIT_TIMEOUT"
    assert "crash_reclassified" not in (row["detail_json"] or "")


def test_no_probe_keeps_the_old_behaviour(tmp_path):
    """**零改动兼容**：`crash_probe=None`（既有全部调用点）→ 旧行为。"""
    result, store = _run(tmp_path, None)
    assert (result.status, result.failure_type) == ("FAIL", "WAIT_TIMEOUT")


class _MismatchEx:
    """find 成功但文本不符 → `AssertionValueMismatch`（值不符的**唯一**来源）。

    ⚠️ 不能用 `_NoFindEx` 造这条：find 失败会走 `AssertionEngine` 的
    `_drift` 分支 → `AssertionTargetDrift` → 映射成 `ELEMENT_NOT_FOUND`
    （那**在**可改判集里，App 死了也解释得通）。要钉排除表，必须造出
    **值不符**这个有别的账主的形态。
    """

    def __init__(self, text=""):
        self._text = text

    def find(self, strategies):
        return _El(self._text)

    def perform(self, action, element, value=None):
        pass

    def page_source(self):
        return "<App><Node name='not_a_marker'/></App>"


class _El:
    def __init__(self, text):
        self.text = text

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True

    def get_attribute(self, name):
        return "XCUIElementTypeButton" if name == "type" else ""


def test_an_assertion_mismatch_is_not_reclassified_even_when_a_crash_exists(
        tmp_path):
    """断言值不符归 P1（§8.2 末条）——即使窗口内真有一份崩溃报告也不改判。

    这条把「排除表真的被消费」钉在**全链**上，而不只是纯函数上。
    """
    case_yaml = """\
schema_version: "0.2"
id: assert_case
name: assert
steps:
  - assertion:
      target: HomeView.login_button
      condition: text_equals
      expected: "hello"
"""
    probe = _Probe(_report())
    result, store = _run(tmp_path, probe, case_yaml=case_yaml,
                         ex=_MismatchEx(text="goodbye"))
    assert result.failure_type == "ASSERTION_VALUE_MISMATCH", result.failure_type
    # 排除表命中的类型**连探测都不发**（有界等待默认 12s）：纯函数不改判是
    # 一回事，调用点提前短路是另一回事——后者才省下这 12 秒。
    assert probe.calls == [], "排除表命中却仍去探测了"
    row = store.conn.execute(
        "SELECT failure_type FROM testcase_runs").fetchone()
    assert row["failure_type"] == "ASSERTION_VALUE_MISMATCH"
    # ⚠️ 这里**不断言** `steps` 行：断言值不符的出口**一行都不落**
    # （`cli/pipeline.py` 把 `AssertionValueMismatch` 原样 re-raise 给外层
    # `except Exception`，那条分支不落步骤行）。这是**本批新发现的既存缺口**
    # （与 §9.3 同款：单测看不见——它只断言用例行），登记在
    # `docs/p3_data_audit.md` §10.4，不在本批修（改的是 P1 的落库路径）。
    assert store.conn.execute(
        "SELECT COUNT(*) FROM steps WHERE status='FAILED'").fetchone()[0] == 0


def test_the_whole_chain_closes_from_trace_to_the_rule_engine(tmp_path):
    """**本批真正的验收**：崩溃 → trace → 规则引擎判出崩溃假设。

    §9.6 曾声称这条链「在真机上是通的」——真机实测打脸（§10）。真正的判据
    不是「用例行写了 APP_CRASH」，而是**诊断侧从 trace 读出来的东西**能让
    `crash_detected` 为真。所以这里从 `read_trace_steps` 起，一路走到
    `rule_based_hypotheses`——中间任何一环断了这条测试都会红。
    """
    from diagnosis.classifier import crash_detected, rule_based_hypotheses
    from diagnosis.context import DiagnosisContext
    from graph.builder import read_trace_steps

    result, store = _run(tmp_path, _Probe(_report()))
    steps, app_id, build = read_trace_steps(tmp_path / "trace.db")
    assert [s.failure_type for s in steps] == ["APP_CRASH"], \
        "trace 里读出来的必须已经是 APP_CRASH（规则引擎读的是这里）"
    ctx = DiagnosisContext(run_id="run_crash", app_id=app_id,
                           app_build=build, steps=tuple(steps))
    assert crash_detected(ctx) is True
    kinds = [e.kind for h in rule_based_hypotheses(ctx) for e in h.evidence]
    assert "crash_detected" in kinds, "规则引擎必须判出崩溃假设"


PASS_CASE = """\
schema_version: "0.2"
id: pass_case
name: pass
steps:
  - action: launch_app
"""


class _OKEx:
    def find(self, strategies):
        return _El("hi")

    def perform(self, action, element, value=None):
        pass

    def page_source(self):
        return "<App><Node name='not_a_marker'/></App>"


def test_a_passing_testcase_never_probes(tmp_path):
    """探测是**有界等待**（默认 12s）。通过 20 条用例的套件若每条都探一遍，
    白等 4 分钟——所以调用点只在 `status == "FAIL"` 时才问。

    这条是那个调用点条件的**唯一**证据：纯函数对非 FAIL 本来就不改判，
    所以拿掉调用点条件不会让任何别的测试变红（A/B 探针实测）。
    """
    probe = _Probe(_report())
    result, _ = _run(tmp_path, probe, case_yaml=PASS_CASE, ex=_OKEx())
    assert result.status == "PASS"
    assert probe.calls == [], f"通过的用例不该探测，却探了 {len(probe.calls)} 次"


def test_the_probe_is_given_a_time_window(tmp_path):
    """`since` 必须是**真实时间点**：探测按它收窄（`CrashCollector.latest`
    的 `since`）。传 None 会让上一次 run 的旧崩溃报告也算进来——权重 5 的
    证据，误报代价高（勘察 §8.2 明文）。"""
    probe = _Probe(_report())
    _run(tmp_path, probe)
    assert len(probe.calls) == 1
    assert probe.calls[0] is not None, "时间窗没设 → 旧崩溃会被算成这一次的"


def test_the_probe_is_consulted_at_most_once_per_testcase(tmp_path):
    """有界等待很贵（默认 12s）：InfraError 路径与收尾改判**共用**一次探测。

    两次消费两探测的话，一次真机崩溃要多挂 12 秒——而且两次结果可能不同
    （第二次才等到报告），同一个用例上出现两个互相矛盾的结论。
    """
    probe = _Probe(_report())
    _run(tmp_path, probe)
    assert len(probe.calls) == 1, f"探测了 {len(probe.calls)} 次"


# ======================================================================
# 3. `TraceStore.mark_steps_app_crash`：存储层自己也要钉（不只靠管线调用）
# ======================================================================


def _store_with_failed_steps(tmp_path, types):
    from tracer.storage import TraceStore

    store = TraceStore(tmp_path / "trace.db")
    store.start_run("r")
    tc = store.start_testcase("r", "tc")
    for i, ftype in enumerate(types):
        store.record_step(tc, step_index=i, step_type="tap",
                          status="FAILED", failure_type=ftype,
                          detail={"error": f"symptom {ftype}"})
    return store, tc


def test_mark_steps_respects_the_exclude_list(tmp_path):
    """排除表在**存储层**真的生效（管线侧传了不等于存储层照做）。"""
    store, tc = _store_with_failed_steps(
        tmp_path, ["WAIT_TIMEOUT", "ASSERTION_VALUE_MISMATCH",
                   "SECURITY_BLOCKED"])
    n = store.mark_steps_app_crash(
        tc, exclude=("ASSERTION_VALUE_MISMATCH", "SECURITY_BLOCKED"),
        crash=_report().as_dict())
    assert n == 1
    got = dict(store.conn.execute(
        "SELECT failure_type, COUNT(*) FROM steps GROUP BY failure_type"))
    assert got == {"APP_CRASH": 1, "ASSERTION_VALUE_MISMATCH": 1,
                   "SECURITY_BLOCKED": 1}, got


def test_mark_steps_defaults_to_reclassifying_everything(tmp_path):
    """默认空排除表 = 全部改判（与管线侧「排除表方向」同向）。"""
    store, tc = _store_with_failed_steps(
        tmp_path, ["WAIT_TIMEOUT", "CURRENT_SCREEN_UNKNOWN"])
    assert store.mark_steps_app_crash(tc) == 2
    assert store.conn.execute(
        "SELECT COUNT(*) FROM steps WHERE failure_type='APP_CRASH'"
    ).fetchone()[0] == 2


def test_mark_steps_leaves_a_note_with_the_original_symptom(tmp_path):
    """原症状必须留在审计记录里——错误文本说「找不到元素」而类型是
    `APP_CRASH`，没这条记录事后会以为那是脏数据。"""
    import json

    store, tc = _store_with_failed_steps(tmp_path, ["WAIT_TIMEOUT"])
    store.mark_steps_app_crash(tc, crash=_report().as_dict())
    d = json.loads(store.conn.execute(
        "SELECT detail_json FROM steps").fetchone()[0])
    assert d["crash_reclassified"]["failure_type"] == "WAIT_TIMEOUT"
    assert d["crash_reclassified"]["crash"]["signal"] == "SIGSEGV"
    assert d["error"] == "symptom WAIT_TIMEOUT", "原有 detail 不能被覆盖掉"


def test_mark_steps_only_touches_failed_rows(tmp_path):
    """**反钉**：通过的步骤不动——把 SUCCESS 改成 APP_CRASH 会伪造出
    「每一步都崩了」的现场。"""
    store, tc = _store_with_failed_steps(tmp_path, ["WAIT_TIMEOUT"])
    store.record_step(tc, step_index=9, step_type="tap", status="SUCCESS")
    assert store.mark_steps_app_crash(tc) == 1
    got = dict(store.conn.execute(
        "SELECT status, COUNT(*) FROM steps GROUP BY status"))
    assert got == {"FAILED": 1, "SUCCESS": 1}
    assert store.conn.execute(
        "SELECT COUNT(*) FROM steps WHERE status='SUCCESS'"
        " AND failure_type IS NOT NULL").fetchone()[0] == 0
