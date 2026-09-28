"""M0 基建冒烟：验证 pytest 可发现并运行项目内纯逻辑模块（H18 前提）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracer.redactor import redact


def test_redactor_importable():
    assert callable(redact)


def test_redactor_masks_password_field():
    out = redact({"password": "secret-value"})
    assert "secret-value" not in str(out)
