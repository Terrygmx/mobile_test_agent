"""Testcase loader：P0 松散模型兼容层 + 设计 6.3 schema 0.1 分发入口（P1-01）。

- 旧 `Step/TestCase/load_testcase`：P0 链路（run_phase0_demo / verify_stage5/9）仍在用；
- `load_testcase_from_dict`：设计 6.3 strict 入口（extra=forbid + 版本分发）；
- `load_testcase` 按 `schema_version` 键分流（R5-2：strict 不被文件级入口旁路）。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel

if TYPE_CHECKING:
    from testcase.schema import TestCase as SchemaTestCase


class Step(BaseModel):
    action: str
    target: str | None = None
    value: str | None = None
    direction: str | None = None
    timeout: int | None = None


class TestCase(BaseModel):
    id: str
    name: str
    precondition: dict = {}
    steps: list[Step]


def load_testcase(path: str | Path) -> TestCase:
    """文件级入口：带 schema_version 的新用例走 0.1 strict 分发，否则回落 P0 松散模型。

    R5-2：防止 strict 校验被文件级入口静默旁路。
    """
    data = yaml.safe_load(Path(path).read_text())
    if isinstance(data, dict) and "schema_version" in data:
        return load_testcase_from_dict(data)
    return TestCase(**data)


def load_testcase_from_dict(data: dict) -> "SchemaTestCase":
    """设计 6.3 loader 入口：缺失/未知 schema_version 明确报错（版本分发在 schema.parse_testcase_dict）。"""
    from testcase.schema import parse_testcase_dict

    return parse_testcase_dict(data)
