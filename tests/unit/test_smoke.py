"""M0 基建冒烟：验证 pytest 可发现并运行项目内纯逻辑模块（H18 前提）。

import 依赖根级 conftest.py（review R4-1）：不要在本文件写 sys.path hack。
"""

from tracer.redactor import redact


def test_redactor_importable():
    assert callable(redact)


def test_redactor_masks_password_field():
    out = redact({"password": "secret-value"})
    assert "secret-value" not in str(out)
