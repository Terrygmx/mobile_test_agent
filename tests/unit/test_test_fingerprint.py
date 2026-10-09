"""Task 3.3（P3-11）Test Fingerprint（设计 F8）。

口径（本次拍板，见 `docs/p3_data_audit.md` 的 Task 3.3 记录）：

- `test_fingerprint(tc) = stable_hash([screens, actions, assertions])`，三分量
  来自步骤序列的**规范化**，每个步骤恰好落入一个分量（见 `candidates/fingerprint.py`）。
- **同语义不同命名 → 同指纹**：`id` / `name` / `suite` / `tags` 等不进指纹。
- **只做精确哈希相等**（F8 明文），不做相似度。
- 「同一个函数/同一套逻辑」的显式断言（plan §2 第 1 条）：`test_fingerprint`
  必须复用 `source/hashing.py::stable_hash`——本文件既断言**同一对象**
  （import 级），也**钉住调用**（spy；review_p3_task32 P3-1 的教训：行为等价
  但不复用，import 级断言抓不住）。

注：被测 API 名字就叫 `test_fingerprint`，直接 import 会被 pytest 当测试收集，
故 alias 成 `fingerprint`。
"""
from __future__ import annotations

import pytest

from candidates.fingerprint import test_fingerprint as fingerprint
from source.hashing import stable_hash
from testcase.schema import parse_testcase_dict


def _case(cid: str = "c", **over) -> object:
    base = {
        "schema_version": "0.2",
        "id": cid,
        "name": cid,
        "steps": [
            {"action": "launch_app"},
            {"action": "tap", "target": "LoginView.login_button"},
            {"action": "input", "target": "LoginView.username_field",
             "value": "${TEST_USERNAME}"},
            {"action": "input", "target": "LoginView.password_field",
             "value": "${TEST_PASSWORD}", "sensitive": True},
            {"action": "tap", "target": "LoginView.login_button"},
            {"wait_for": {"target": "screen:HomeView", "condition": "active",
                          "timeout": 10}},
            {"assertion": {"target": "screen:HomeView", "condition": "exists",
                           "timeout": 5}},
        ],
    }
    base.update(over)
    return parse_testcase_dict(base)


def _one_step(step: dict) -> object:
    """取单步 TestCase 的 step 对象，用于替换 `_case()` 里的某一步。"""
    return parse_testcase_dict(
        {"schema_version": "0.2", "id": "x", "name": "x", "steps": [step]}
    ).steps[0]


# --- 同语义不同命名 → 同指纹 --------------------------------------------------


def test_naming_fields_do_not_affect_fingerprint():
    a = _case("login_001")
    b = _case("login_candidate_xyz")
    # name / suite / tags 也换掉
    b2 = _case("login_001", name="完全不同的名字", suite="regression",
               tags=["generated", "gap"])
    assert fingerprint(a) == fingerprint(b)
    assert fingerprint(a) == fingerprint(b2)


def test_candidate_provenance_fields_do_not_affect_fingerprint():
    """Candidate 溯源三字段描述「从哪来」，不描述「做什么」。"""
    a = _case("login_001")
    b = _case("login_001", status="CANDIDATE", generated_by="generator_agent",
              generation_evidence={"coverage_gap": ["Login -> Home"],
                                   "bug_history": [], "source_refs": ["LoginView.swift"]})
    assert fingerprint(a) == fingerprint(b)


def test_deterministic():
    a = _case("login_001")
    assert fingerprint(a) == fingerprint(a)


def test_fingerprint_is_16_hex():
    fp = fingerprint(_case())
    assert len(fp) == 16
    assert all(ch in "0123456789abcdef" for ch in fp)


# --- 三类输入各自改变指纹 -----------------------------------------------------


def test_screen_target_change_changes_fingerprint():
    a = _case("c")
    b = _case("c")
    # 把 wait_for 的屏从 HomeView 改成 SearchView
    b.steps[5] = _one_step(
        {"wait_for": {"target": "screen:SearchView", "condition": "active",
                      "timeout": 10}})
    assert fingerprint(a) != fingerprint(b)


def test_screen_wait_polarity_changes_fingerprint():
    """`screen:X exists` 与 `screen:X not_exists` 是不同用例。"""
    a = _case("c")
    b = _case("c")
    b.steps[6] = _one_step(
        {"assertion": {"target": "screen:HomeView", "condition": "not_exists",
                       "timeout": 5}})
    assert fingerprint(a) != fingerprint(b)


def test_action_change_changes_fingerprint():
    a = _case("c")
    b = _case("c", steps=[
        {"action": "launch_app"},
        {"action": "tap", "target": "LoginView.signin_button"},   # 目标变了
        {"wait_for": {"target": "screen:HomeView", "condition": "active",
                      "timeout": 10}},
    ])
    assert fingerprint(a) != fingerprint(b)


def test_input_value_changes_fingerprint():
    """不同测试数据 = 不同用例（否则「空用户名」与「空密码」两条负例会被误判重复）。"""
    a = _case("c")
    b = _case("c")
    b.steps[2] = _one_step(
        {"action": "input", "target": "LoginView.username_field", "value": ""})
    assert fingerprint(a) != fingerprint(b)


def test_assertion_expected_changes_fingerprint():
    a = _case("c")
    b = _case("c")
    b.steps[6] = _one_step(
        {"assertion": {"target": "HomeView.title", "condition": "text_equals",
                       "expected": "Welcome", "timeout": 5}})
    c = _case("c")
    c.steps[6] = _one_step(
        {"assertion": {"target": "HomeView.title", "condition": "text_equals",
                       "expected": "Hi", "timeout": 5}})
    assert fingerprint(a) != fingerprint(b)
    assert fingerprint(b) != fingerprint(c)


def test_element_wait_changes_fingerprint():
    """element 目标的 wait 走 assertions 分量——该分支此前零测试。"""
    a = parse_testcase_dict({"schema_version": "0.2", "id": "a", "name": "a",
                             "steps": [{"wait_for": {"target": "HomeView.title",
                                                     "condition": "exists",
                                                     "timeout": 5}}]})
    b = parse_testcase_dict({"schema_version": "0.2", "id": "b", "name": "b",
                             "steps": [{"wait_for": {"target": "HomeView.subtitle",
                                                     "condition": "exists",
                                                     "timeout": 5}}]})
    assert fingerprint(a) != fingerprint(b)


def test_screen_postcondition_changes_fingerprint():
    """动作的 screen 后置条件走 screens 分量——该分支此前零测试。"""
    def post(cond):
        return parse_testcase_dict({
            "schema_version": "0.2", "id": "x", "name": "x",
            "steps": [{"action": "tap", "target": "LoginView.login_button",
                       "postcondition": {"target": "screen:HomeView",
                                         "condition": cond, "timeout": 5}}]})
    assert fingerprint(post("active")) != fingerprint(post("exists"))


def test_element_postcondition_changes_fingerprint():
    """动作的 element 后置条件走 assertions 分量——该分支此前零测试。"""
    def post(target, cond):
        return parse_testcase_dict({
            "schema_version": "0.2", "id": "x", "name": "x",
            "steps": [{"action": "tap", "target": "LoginView.login_button",
                       "postcondition": {"target": target, "condition": cond,
                                         "timeout": 5}}]})
    assert fingerprint(post("HomeView.title", "exists")) != \
        fingerprint(post("HomeView.title", "not_exists"))
    assert fingerprint(post("HomeView.title", "exists")) != \
        fingerprint(post("HomeView.subtitle", "exists"))


def test_empty_steps_is_stable():
    a = parse_testcase_dict({"schema_version": "0.2", "id": "e", "name": "e"})
    b = parse_testcase_dict({"schema_version": "0.2", "id": "e2", "name": "e2"})
    assert fingerprint(a) == fingerprint(b)
    assert len(fingerprint(a)) == 16


# --- 字段边界：repr token 是有牙的（naive 拼接会撞） --------------------------


def test_token_encoding_is_injective_at_field_boundaries():
    """token 用 `repr(元组)` 而非 `":".join(...)`：编码必须**单射**。

    fixture 是**刻意对抗性**的（合法但极端）：两组用例的 naive `":".join`
    逐字相同，repr 元组不同。naive 拼接会把两条不同用例压成同一指纹——
    「不同用例撞指纹」是去重最不该犯的错（设计 6.1 SKIP 会吞掉一条真用例）。
    对照：`("input","element:f::x","","y")` vs `("input","element:f","","x::y")`
    都 naive 成 `input:element:f::x::y`。
    """
    # actions 边界：id / value 的切分点不同
    a = parse_testcase_dict({"schema_version": "0.2", "id": "a", "name": "a",
                             "steps": [{"action": "input",
                                        "target": {"type": "element", "id": "f::x"},
                                        "value": "y"}]})
    b = parse_testcase_dict({"schema_version": "0.2", "id": "b", "name": "b",
                             "steps": [{"action": "input",
                                        "target": {"type": "element", "id": "f"},
                                        "value": "x::y"}]})
    assert fingerprint(a) != fingerprint(b)

    # assertions 边界：id / expected 的切分点不同
    c = parse_testcase_dict({"schema_version": "0.2", "id": "c", "name": "c",
                             "steps": [{"assertion": {
                                 "target": {"type": "element", "id": "f"},
                                 "condition": "text_equals", "expected": "x:y",
                                 "timeout": 5}}]})
    d = parse_testcase_dict({"schema_version": "0.2", "id": "d", "name": "d",
                             "steps": [{"assertion": {
                                 "target": {"type": "element", "id": "f:x"},
                                 "condition": "text_equals", "expected": "y",
                                 "timeout": 5}}]})
    assert fingerprint(c) != fingerprint(d)


# --- 复用红线（plan §2 第 1 条） ----------------------------------------------


def test_fingerprint_uses_the_same_primitive_as_screen_fingerprint():
    import candidates.fingerprint as cf
    import source.hashing as sh
    import source.screen as sc
    assert cf.stable_hash is sh.stable_hash
    assert sc.stable_hash is sh.stable_hash


def test_fingerprint_actually_calls_the_shared_primitive(monkeypatch):
    """钉住**调用**，不只钉住 import（review_p3_task32 P3-1 的形态 ②）。"""
    import candidates.fingerprint as cf

    seen = []
    real = cf.stable_hash

    def spy(parts):
        seen.append(list(parts))
        return real(parts)

    monkeypatch.setattr(cf, "stable_hash", spy)
    cf.test_fingerprint(_case())
    assert seen, "test_fingerprint 没真的调用 source.hashing.stable_hash"


# --- 原语自身的闸门 -----------------------------------------------------------


def test_stable_hash_rejects_bare_str():
    with pytest.raises(TypeError):
        stable_hash("abc")            # 会被逐字符迭代 → 必须 fail-loud


def test_stable_hash_rejects_non_str_elements():
    with pytest.raises(TypeError):
        stable_hash(["a", 1])         # 不强转 1 → "1"，否则 1 与 "1" 撞指纹


def test_stable_hash_is_order_sensitive():
    assert stable_hash(["a", "b"]) != stable_hash(["b", "a"])
