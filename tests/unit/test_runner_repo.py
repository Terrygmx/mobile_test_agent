"""Task 1.6（P1-04）runner 适配测试：Repository target 解析路径。

口径：
  - 传 repository 时 `_locator_chain` 走 4.1 语义解析，strategies 顺序保持；
  - screen target（screen:Name）→ marker accessibility_id 单策略；
  - 限定名 Screen.elem 可解析跨屏同名 element；
  - 未登记引用 → TestFailure（4.1 不猜，不做 recovery）；
  - 不传 repository 回落 P0 硬编码链（P0 回归不受影响）。
"""
from __future__ import annotations

import pytest

from runner.testcase_runner import TestFailure, TestcaseRunner, _str_target
from repository.loader import load_element_dir, load_screen_dir
from repository.resolver import Repository
from testcase.schema import TargetRef


def _repo() -> Repository:
    gen_el = load_element_dir([("HomeView.yaml", """\
kind: element
id: go_search
screen: HomeView
type: button
strategies:
  - {type: accessibility_id, value: go_search, origin: manual}
  - {type: predicate, value: "label == '搜索'", origin: manual}
metadata:
  risk: LOW
"""), ("SearchView.yaml", """\
kind: element
id: cell_beta
screen: SearchView
type: cell
strategies:
  - {type: accessibility_id, value: cell_beta, origin: manual}
metadata:
  risk: LOW
""")])
    gen_sc = load_screen_dir([("HomeView.yaml", """\
kind: screen
id: HomeView
marker: screen.HomeView
kind_hint: page
""")])
    return Repository(generated_elements=gen_el, generated_screens=gen_sc)


class _FakeSecrets:
    def get(self, key: str) -> str | None:
        return "v" if key == "KNOWN" else None


class _FakeRecorder:
    def record_step(self, *a, **k):
        return 1

    def start_run(self, tc):
        return "r1"


def _runner(repo: Repository | None) -> TestcaseRunner:
    return TestcaseRunner(
        executor=None, app=None, recorder=_FakeRecorder(),
        secrets=_FakeSecrets(), repository=repo,
    )


def test_repo_resolves_targetref_strategies_in_order():
    r = _runner(_repo())
    loc = r._locator_chain(TargetRef(id="HomeView.go_search"))
    assert loc == [
        {"type": "accessibility_id", "value": "go_search"},
        {"type": "predicate", "value": "label == '搜索'"},
    ]


def test_repo_resolves_screen_to_marker():
    r = _runner(_repo())
    loc = r._locator_chain(TargetRef(type="screen", id="HomeView"))
    assert loc == [{"type": "accessibility_id", "value": "screen.HomeView"}]


def test_repo_resolves_str_sugar():
    r = _runner(_repo())
    assert r._locator_chain("screen:HomeView") == [
        {"type": "accessibility_id", "value": "screen.HomeView"}]
    assert r._locator_chain("HomeView.go_search")[0]["value"] == "go_search"


def test_repo_unknown_reference_raises_test_failure():
    """引用错误在 _locator_chain 处暴露（_run_step 包装为 TestFailure；
    这里直接验证 resolve 层的异常类型传播）。"""
    from repository.resolver import UnknownReferenceError
    r = _runner(_repo())
    with pytest.raises(UnknownReferenceError):
        r._locator_chain(TargetRef(id="no_such_elem"))


def test_no_repo_falls_back_to_p0_chain():
    r = _runner(None)
    loc = r._locator_chain("login_button")
    assert loc[0] == {"type": "accessibility_id", "value": "login_button"}
    assert loc[1] == {"type": "predicate", "value": "name == 'login_button'"}


def test_str_target_sugar():
    assert _str_target("screen:HomeView").type == "screen"
    assert _str_target("login_button").type == "element"
