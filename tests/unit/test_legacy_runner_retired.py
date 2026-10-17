"""退役守卫：旧 P0 `TestcaseRunner` 不得复活。

两套执行语义并存过很长一段时间（`runner/testcase_runner.py` 与
`runner/runner.py::StepRunner`），可选步骤、等待静止、precondition 的执行
口径都曾只在一边生效。2026-10-17 完成迁移后旧 runner 删除——本文件钉住
「删除」本身：文件不在、模块无法导入，且唯一执行管线仍可用。
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_legacy_runner_file_is_gone():
    assert not (ROOT / "runner" / "testcase_runner.py").exists(), \
        "旧 TestcaseRunner 已退役；执行语义唯一入口是 cli.pipeline + StepRunner"


def test_legacy_runner_module_not_importable():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("runner.testcase_runner")


def test_only_execution_pipeline_is_importable():
    """守卫的正面：替代路径真的在（否则「退役」只是把能力删了）。"""
    from cli.pipeline import SessionPipeline
    from runner.runner import StepRunner

    assert callable(SessionPipeline.run_case)
    assert callable(StepRunner.run_step)
