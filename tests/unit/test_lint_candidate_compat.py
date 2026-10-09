"""Task 3.1（P3-09）lint 对 Candidate 溯源字段的兼容（设计 6.4 / F5）。

口径：
  - 新字段是**用例级**溯源标注，不是 step 级声明 → 不触发 lint 的任何 `_check_*`；
  - `lint()` 的裸 dict 路径与已解析 `TestCase` 路径对候选都**零告警**；
  - 正式 suites/ 全量 lint 不因 schema 扩展产生 `schema_invalid`。

⚠️ 不冻结「正式语料的总 issue 数」：其余 issue 码（`unknown_secret` /
`unknown_target`）由环境变量与 `repository/generated/` 内容决定，与本任务无关。
本任务只钉「扩展没让语料变得不可解析」这一条。实测口径记在
`docs/p3_data_audit.md` 的 Task 3.1 记录里。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from environment.secrets import SecretProvider
from repository.resolver import Repository
from testcase.lint import lint
from testcase.schema import parse_testcase_dict

_ROOT = Path(__file__).resolve().parents[2]

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


class _KnownSecrets(SecretProvider):
    """dict 驱动的 SecretProvider 替身（真实链路是 EnvSecretProvider）。"""

    def __init__(self, known: set[str] | None = None) -> None:
        self.known = known or set()

    def get(self, key: str) -> str:
        if key not in self.known:
            raise KeyError(f"secret {key} not found")
        return "x"


def _repo() -> Repository:
    return Repository.from_dirs(generated_root=str(_ROOT / "repository/generated/local"))


# --- 候选：两条 lint 入口都零告警 ---

def test_candidate_dict_lints_clean_via_raw_dict_path():
    """`mta lint` 走的是裸 dict 路径（cmd_lint 先 yaml.safe_load 再 lint）。"""
    assert lint([CANDIDATE], _repo(), _KnownSecrets()) == []


def test_parsed_candidate_lints_clean():
    tc = parse_testcase_dict(CANDIDATE)
    assert lint([tc], _repo(), _KnownSecrets()) == []


# --- 正式语料：扩展未使任何用例不可解析 ---

def test_real_suites_lint_without_schema_errors():
    paths = sorted((_ROOT / "suites").rglob("*.yaml"))
    cases = [yaml.safe_load(p.read_text()) for p in paths]
    assert len(cases) >= 20  # 防空转下界
    issues = lint(cases, _repo(), _KnownSecrets({"TEST_USERNAME", "TEST_PASSWORD"}))
    schema_errors = [i for i in issues if i.code == "schema_invalid"]
    assert schema_errors == [], schema_errors
