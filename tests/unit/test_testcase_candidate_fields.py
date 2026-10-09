"""Task 3.1（P3-09）Candidate Test 溯源字段（设计 6.2 / F5）。

口径：
  - Candidate Test = **P1 TestCase + 三个可选字段**（`status` / `generated_by` /
    `generation_evidence`）；扩展方式是 **Modify 既有模型**，不新建平行模型
    （`extra="forbid"` 下平行模型会拒新键，且违反 F5「同一 Schema」）；
  - 既有正式用例（不带新字段）解析**语义不变**，新字段一律 `None`；
  - `generation_evidence` 的三个列表**必填**：设计 6.2 示例三者俱在（空即写 `[]`），
    「没给」与「显式为空」由此可分辨（本仓反复踩的「形式合法、语义等于没有」）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from testcase.schema import GenerationEvidence, TestCase, parse_testcase_dict

_ROOT = Path(__file__).resolve().parents[2]

# 设计 6.2 示例 + `name`：示例漏写了 P1 的必填字段 `name`（P1 契约不改，
# 夹具补上；schema 不为设计文档的疏漏放宽 `name`）。
CANDIDATE = {
    "schema_version": "0.2",
    "id": "search_candidate_empty_result",
    "name": "search empty result",
    "status": "CANDIDATE",
    "generated_by": "generator_agent",
    "generation_evidence": {
        "coverage_gap": ["Search -> EmptyResultState"],
        "bug_history": [],
        "source_refs": ["SearchView.swift"],
    },
    "suite": "smoke",
    "steps": [{"action": "launch_app"}],
}


# --- 设计 6.2 示例经 strict parser 通过 ---

def test_design_6_2_candidate_example_parses():
    tc = parse_testcase_dict(CANDIDATE)
    assert tc.status == "CANDIDATE"
    assert tc.generated_by == "generator_agent"
    assert tc.generation_evidence is not None
    assert tc.generation_evidence.coverage_gap == ["Search -> EmptyResultState"]
    assert tc.generation_evidence.bug_history == []
    assert tc.generation_evidence.source_refs == ["SearchView.swift"]


# --- 既有用例：新字段默认 None，语义不变 ---

def test_candidate_fields_default_to_none():
    tc = parse_testcase_dict(
        {"schema_version": "0.2", "id": "t", "name": "t", "steps": []}
    )
    assert tc.status is None
    assert tc.generated_by is None
    assert tc.generation_evidence is None


def test_existing_case_semantics_unchanged_by_new_fields():
    """不带新字段的用例 round-trip 恒等，既有取值逐字段不变。"""
    raw = {
        "schema_version": "0.2",
        "id": "login_001",
        "name": "用户登录",
        "suite": "smoke",
        "tags": ["login"],
        "precondition": {"reset": "RESET_STATE"},
        "steps": [
            {"action": "tap", "target": {"type": "element", "id": "login_button"}}
        ],
        "cleanup": {"reset": "RESET_STATE"},
    }
    tc = parse_testcase_dict(raw)
    assert tc == TestCase.model_validate(tc.model_dump())
    assert tc.precondition == {"reset": "RESET_STATE"}
    assert tc.cleanup == {"reset": "RESET_STATE"}
    assert tc.tags == ["login"]
    assert (tc.status, tc.generated_by, tc.generation_evidence) == (None, None, None)


# --- status 只认 CANDIDATE ---

@pytest.mark.parametrize("bad", ["VERIFIED", "candidate", "CANDIDATE ", "PENDING"])
def test_status_accepts_only_candidate_literal(bad):
    with pytest.raises(ValidationError):
        parse_testcase_dict({**CANDIDATE, "status": bad})


# --- generation_evidence 的结构闸门 ---

def test_generation_evidence_rejects_unknown_key():
    with pytest.raises(ValidationError):
        parse_testcase_dict({
            **CANDIDATE,
            "generation_evidence": {
                "coverage_gap": [],
                "bug_history": [],
                "source_refs": [],
                "typo_field": 1,
            },
        })


@pytest.mark.parametrize("missing", ["coverage_gap", "bug_history", "source_refs"])
def test_generation_evidence_requires_all_three_lists(missing):
    evidence = {"coverage_gap": [], "bug_history": [], "source_refs": []}
    del evidence[missing]
    with pytest.raises(ValidationError):
        parse_testcase_dict({**CANDIDATE, "generation_evidence": evidence})


def test_generation_evidence_lists_reject_non_str_elements():
    with pytest.raises(ValidationError):
        GenerationEvidence(coverage_gap=[1], bug_history=[], source_refs=[])


# --- 正式语料：扩展不改变既有 20 个用例的解析 ---

def test_real_suites_parse_with_candidate_fields_absent():
    paths = sorted((_ROOT / "suites").rglob("*.yaml"))
    assert len(paths) >= 20  # 防空转下界（0 个文件时断言无意义）
    for p in paths:
        tc = parse_testcase_dict(yaml.safe_load(p.read_text()))
        assert (tc.status, tc.generated_by, tc.generation_evidence) == (None, None, None), p
        assert TestCase.model_validate(tc.model_dump()) == tc, p
