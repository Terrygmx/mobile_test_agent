"""Testcase schema + loader（Stage 5）。ponytail: pydantic 只建用到的字段。"""

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
    data = yaml.safe_load(Path(path).read_text())
    return TestCase(**data)


def load_testcase_from_dict(data: dict) -> "SchemaTestCase":
    """设计 6.3 loader 入口：缺失/未知 schema_version 明确报错（版本分发在 schema.parse_testcase_dict）。"""
    from testcase.schema import parse_testcase_dict

    return parse_testcase_dict(data)
