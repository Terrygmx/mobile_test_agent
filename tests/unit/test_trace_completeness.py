"""两处**既存缺口**的收口（`docs/p3_data_audit.md` §10.4 第 1、2 条）。

两条都是「单测看不见」那一类：它们不改变任何**用例级**结论，只是让 trace
少一行 / 让词表少一个值——而 trace 的完整性正是排障的地基。

## 缺口 1：断言值不符的出口一行步骤都不落

`cli/pipeline.py` 的 assert 分支对 `AssertionValueMismatch` 直接 `raise` 给
外层 `except Exception`，那条分支只落**用例**行、不落**步骤**行。P3-1
「失败步骤也落库」的纪律没覆盖到这个出口——而它是最常见的断言失败形态。

## 缺口 2：`ACTION_FAILED` 不在 `FAILURE_TYPES` 词表里

App 级动作（launch/terminate/back/swipe）失败写的是 `"ACTION_FAILED"`
（**故意的**代码路径，不是兜底），而 `tracer/storage.py::FAILURE_TYPES` 没有
这个值。写入没做词表校验所以不报错，但「词表是口径」这句话对它不成立。

⚠️ 词表**尾部是开放的**（`_map_exception` 故意用 `type(e).__name__` 兜底，
fail-loud 不静默归类成 `ELEMENT_NOT_FOUND`），所以不能加运行时 CHECK——
只能把「**故意**生产的那些」钉进词表，见第 3 节。
"""
from __future__ import annotations

import pytest
import yaml

from cli.pipeline import SessionPipeline
from tracer.storage import FAILURE_TYPES


class _El:
    def __init__(self, text):
        self.text = text

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True

    def get_attribute(self, name):
        return "XCUIElementTypeButton" if name == "type" else ""


class _MismatchEx:
    """find 成功但文本不符 → `AssertionValueMismatch`。"""

    def __init__(self, text=""):
        self._text = text

    def find(self, strategies):
        return _El(self._text)

    def perform(self, action, element, value=None):
        pass

    def page_source(self):
        return "<App><Node name='not_a_marker'/></App>"


class _FakeDS:
    def ensure_alive(self):
        return self


ASSERT_CASE = """\
schema_version: "0.2"
id: assert_case
name: assert
steps:
  - assertion:
      target: HomeView.login_button
      condition: text_equals
      expected: "hello"
"""


def _run(tmp_path, case_yaml=ASSERT_CASE, ex=None, ds=None):
    from executor.guard import EnvKind, Guard
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    store = TraceStore(tmp_path / "trace.db")
    store.start_run("run_tc")
    pipe = SessionPipeline(suites_root=None, store=store)
    runner = StepRunner(ex or _MismatchEx(text="goodbye"), ds or _FakeDS(),
                        Guard(EnvKind.SANDBOX))
    case = __import__("testcase.schema", fromlist=["p"]).parse_testcase_dict(
        yaml.safe_load(case_yaml))
    return pipe.run_case(runner, Lifecycle(store=store), case,
                         run_id="run_tc"), store


# ======================================================================
# 1. 断言值不符：步骤行必须落（P3-1 的纪律覆盖到所有失败出口）
# ======================================================================


def test_an_assertion_value_mismatch_records_a_step_row(tmp_path):
    """**本文件的核心**：断言值不符的出口也要落步骤行。

    修前：`steps` 表**一行都没有**——trace 上「这一步没跑过」，排障时既看不到
    是哪个 target、也看不到 expected/actual（那些只在**用例**行的 detail 里，
    而报告按步骤渲染）。
    """
    result, store = _run(tmp_path)
    assert result.failure_type == "ASSERTION_VALUE_MISMATCH"
    rows = store.conn.execute(
        "SELECT step_index, step_type, target_id, status, failure_type,"
        " detail_json FROM steps").fetchall()
    assert len(rows) == 1, f"断言失败必须落步骤行，实际 {len(rows)} 行"
    row = rows[0]
    assert row["step_index"] == 0
    assert row["step_type"] == "assert"
    assert row["status"] == "FAILED"
    assert row["failure_type"] == "ASSERTION_VALUE_MISMATCH"
    assert row["target_id"], "步骤行要带目标（不然排障不知道断言的是谁）"


def test_the_step_row_does_not_duplicate_rows_already_written(tmp_path):
    """**反钉**：已经落过行的失败出口不能写第二行。

    `InfraError` 分支、wait 分支、assert 漂移分支都在**内部**落了行再
    `raise`；外层 handler 若无条件补写，那些路径就会一个步骤两行——
    trace 上看起来像「跑了两遍」。

    ⚠️ 判据是「**每个 `testcase_run`** 每个 step_index 至多一行」，不是
    「全库一行」：`InfraError` 会触发 WDA 重跑，attempt=2 是**另一条**
    `testcase_run`（各自该有一行，那正是「跑了两遍」的如实记录）。
    第一版把判据写成「全库一行」，于是重跑被误读成重复行。
    """
    from session.device_session import InfraError

    class _DeadEx:
        def find(self, strategies):
            raise InfraError("WDA died")

        def perform(self, action, element, value=None):
            pass

    class _DS(_FakeDS):
        def restart_wda(self):
            pass

    case = """\
schema_version: "0.2"
id: tap_case
name: tap
steps:
  - action: tap
    target: HomeView.login_button
"""
    result, store = _run(tmp_path, case_yaml=case, ex=_DeadEx(), ds=_DS())
    dupes = store.conn.execute(
        "SELECT testcase_run_id, step_index, COUNT(*) AS n FROM steps"
        " WHERE status='FAILED'"
        " GROUP BY testcase_run_id, step_index HAVING n > 1").fetchall()
    assert not dupes, f"同一次用例尝试里同一步骤落了多行：{[dict(r) for r in dupes]}"
    # 重跑是真的发生了（不是判据把它盖掉了）：两个 attempt 各一行
    rows = store.conn.execute(
        "SELECT testcase_run_id, step_index FROM steps WHERE status='FAILED'"
    ).fetchall()
    assert len({r["testcase_run_id"] for r in rows}) == len(rows), \
        "每个 testcase_run 只该有一行"


def test_a_branch_that_already_wrote_a_row_does_not_get_a_second_one(tmp_path):
    """**反钉（这条是 A/B 探针 T2 逼出来的）**：内部已落行再 `raise` 的分支，
    外层兜底不能再写一行。

    形态：wait 超时 → 进恢复管线 → 恢复失败 → **内部落行** → `raise` 到外层。
    （`rec is None` 就够：`_aux_recover` 在没有 recovery 引擎时返回 `None`。）
    外层若无条件补写，这一个步骤会有**两行**，trace 上看起来像跑了两遍。

    ⚠️ 第一版漏了这条：`_run` 的默认 executor 走的是**断言**路径（外层
    唯一该补行的出口），而「内部已落行」的出口一个都没被覆盖——A/B 探针
    把查重去掉（T2）时全绿。
    """
    from executor.executor import ElementNotFound

    class _NoFindEx:
        def find(self, strategies):
            raise ElementNotFound("no element")

        def perform(self, action, element, value=None):
            pass

        def page_source(self):
            return "<App><Node name='not_a_marker'/></App>"

    case = """\
schema_version: "0.2"
id: wait_case
name: wait
steps:
  - wait_for:
      target: screen:HomeView
      condition: active
      timeout: 0.1
      polling_interval: 0.05
"""
    # 不给 recovery → `_aux_recover` 返回 None → 内部落行后 raise
    result, store = _run(tmp_path, case_yaml=case, ex=_NoFindEx())
    assert result.status == "FAIL"
    dupes = store.conn.execute(
        "SELECT testcase_run_id, step_index, COUNT(*) AS n FROM steps"
        " GROUP BY testcase_run_id, step_index HAVING n > 1").fetchall()
    assert not dupes, f"内部已落行的分支被外层补写了第二行：{[dict(r) for r in dupes]}"
    assert store.conn.execute(
        "SELECT COUNT(*) FROM steps").fetchone()[0] == 1


def test_a_passing_assertion_still_records_exactly_one_row(tmp_path):
    """通过路径不受影响（这条改动只碰失败出口）。"""
    class _OKEx(_MismatchEx):
        def find(self, strategies):
            return _El("hello")

    result, store = _run(tmp_path, ex=_OKEx())
    assert result.status == "PASS"
    rows = store.conn.execute("SELECT status FROM steps").fetchall()
    assert [r["status"] for r in rows] == ["SUCCESS"]


# ======================================================================
# 2. `ACTION_FAILED` 进词表
# ======================================================================


def test_action_failed_is_in_the_vocabulary():
    """`ACTION_FAILED` 是**故意的**生产者（`_run_app_level_action` 的 FAIL
    语义，8.4 exit 1），词表必须认它。"""
    assert "ACTION_FAILED" in FAILURE_TYPES


def test_an_app_level_action_failure_lands_in_the_vocabulary(tmp_path):
    """全链：App 级动作失败 → 落库的值确实在词表里（不只是常量对齐）。"""
    from executor.guard import EnvKind, Guard
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    class _BadApp:
        def launch(self):
            raise RuntimeError("launch blew up")

        def terminate(self):
            pass

    class _Ex:
        def find(self, strategies):
            return _El("x")

        def perform(self, action, element, value=None):
            pass

    case = """\
schema_version: "0.2"
id: launch_case
name: launch
steps:
  - action: launch_app
"""
    store = TraceStore(tmp_path / "trace.db")
    store.start_run("run_al")
    pipe = SessionPipeline(suites_root=None, store=store)
    pipe.deps = __import__("cli.pipeline", fromlist=["PipelineDeps"]).PipelineDeps(
        app=_BadApp())
    runner = StepRunner(_Ex(), _FakeDS(), Guard(EnvKind.SANDBOX))
    tc = __import__("testcase.schema", fromlist=["p"]).parse_testcase_dict(
        yaml.safe_load(case))
    result = pipe.run_case(runner, Lifecycle(store=store), tc, run_id="run_al")

    assert result.failure_type == "ACTION_FAILED"
    row = store.conn.execute(
        "SELECT failure_type FROM steps WHERE status='FAILED'").fetchone()
    assert row["failure_type"] in FAILURE_TYPES, \
        f"落库的 {row['failure_type']!r} 不在词表里——「词表是口径」对它不成立"
    assert store.conn.execute(
        "SELECT failure_type FROM testcase_runs").fetchone()[0] in FAILURE_TYPES


# ======================================================================
# 3. 「故意生产的失败类型」⊆ 词表（这条守卫才会抓到 ACTION_FAILED 这类漂移）
# ======================================================================


#: 管线**故意**产出的 failure_type（不含 `_map_exception` 的兜底 `type(e).__name__`）。
#: 新增一处故意产出就加到这里——守卫会当场告诉你它有没有进词表。
PIPELINE_PRODUCED: frozenset[str] = frozenset({
    "ACTION_FAILED",              # `_run_app_level_action` 的 FAIL 语义
    "ASSERTION_VALUE_MISMATCH",   # 断言值不符（H6 终态）
    "ELEMENT_NOT_FOUND",          # find 失败 / AssertionTargetDrift
    "AMBIGUOUS_ELEMENT",
    "WAIT_TIMEOUT",
    "CURRENT_SCREEN_UNKNOWN",     # 13.2 屏分类
    "SCREEN_AMBIGUOUS",
    "APP_CRASH",                  # Batch B 崩溃判定
    "WDA_FAILURE",                # InfraError
    "PREPARE_FAILED",             # 11.2 环境准备
    "CLEANUP_FAILED",             # H10
    "SECRET_NOT_FOUND",           # H9
    "SECURITY_BLOCKED",           # 8.4 Guard
    "ACTION_OUTCOME_UNKNOWN",     # postcondition 不成立
})


def test_every_pipeline_produced_failure_type_is_in_the_vocabulary():
    """**口径守卫**：管线故意产出的每一个失败类型都必须在词表里。

    这正是抓 `ACTION_FAILED` 的那条断言——它漏了整整一个 P1/P2/P3 周期，
    因为「词表」与「生产者」之间**没有任何机械联系**（写入不做校验）。
    这里把联系建起来：生产者清单是显式的，漏进词表就当场红。
    """
    missing = sorted(PIPELINE_PRODUCED - FAILURE_TYPES)
    assert not missing, (
        f"这些失败类型管线会产出、但不在 FAILURE_TYPES 里：{missing}"
        f"——要么加进词表，要么它其实不该被产出")


def test_the_crash_exclude_list_only_names_real_vocabulary_values():
    """**反向守卫**：`_CRASH_UNEXPLAINABLE` 的键必须是真词表值。

    排除表是「崩溃解释不了的类型」清单——若里面出现一个词表里没有的名字，
    它永远匹配不上任何 `failure_type`（拼错、或写了个已废弃的值），于是
    **该被排除的类型会静默地落到「可改判」一侧**。这正是 `ACTION_FAILED`
    那种漂移的镜像形态，只是后果更隐蔽。
    """
    from cli.pipeline import _CRASH_UNEXPLAINABLE

    stray = sorted(set(_CRASH_UNEXPLAINABLE) - FAILURE_TYPES)
    assert not stray, (
        f"排除表里有 {stray}，它们不在 FAILURE_TYPES 里——"
        f"永远匹配不上，等于没排除")


def test_the_vocabulary_has_no_duplicate_spellings():
    """词表里不许有「同一个症状两种拼法」——那是漂移的温床。

    `ACTION_FAILED` vs `APPIUM_FAILURE`、`PREPARE_FAILED` vs
    `ENV_RESET_FAILED` 这类近义并存是**允许**的（语义确实不同，见设计 §8.2
    的修订记录）；但拼写层面（大小写、下划线）必须唯一。
    """
    normalized = [t.upper().replace("-", "_") for t in FAILURE_TYPES]
    assert len(normalized) == len(set(normalized)), "词表里有拼写重复"
    for t in FAILURE_TYPES:
        assert t == t.upper(), f"{t!r} 不是全大写（词表纪律）"
