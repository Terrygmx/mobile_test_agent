"""Testcase schema + loader（Stage 5）。ponytail: pydantic 只建用到的字段。"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


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
