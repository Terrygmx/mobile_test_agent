"""Batch B：`mta run` 装配崩溃探测（让 `APP_CRASH` 真的落到 trace 上）。

上一步（`test_pipeline_crash_verdict.py`）证明「**注入探测**时判据对」；
本文件证明「**`mta run` 真的注入了**」——两者缺一，链子还是断的：
判据对但没人注入 = 生产路径照旧记 `WDA_FAILURE`。

口径：**只有真机路径注入**（fake-driver 没有设备，探测无从谈起）。
"""
from __future__ import annotations

import pytest

from cli.main import main


@pytest.fixture()
def proj(tmp_path):
    (tmp_path / "suites" / "smoke").mkdir(parents=True)
    (tmp_path / "suites" / "smoke" / "a.yaml").write_text(
        'schema_version: "0.2"\nid: a_001\nname: A\nsuite: smoke\n'
        'steps:\n  - action: launch_app\n', encoding="utf-8")
    gen = tmp_path / "repository" / "generated" / "local"
    gen.mkdir(parents=True)
    (gen / "source_metadata.json").write_text(
        '{"app_version":"debug","build":"local","git_commit":"deadbeef",'
        '"screens":[],"screen_elements":[]}', encoding="utf-8")
    return {"tmp": tmp_path, "suites": tmp_path / "suites", "gen": gen,
            "db": tmp_path / "out" / "trace.db"}


def _argv(proj, *extra):
    (proj["tmp"] / "out").mkdir(exist_ok=True)
    return ["run", "--suites-root", str(proj["suites"]),
            "--generated", str(proj["gen"]), "--db", str(proj["db"]),
            "--suite", "smoke", "--fake-driver", *extra]


def test_fake_driver_run_does_not_wire_a_crash_probe(proj, monkeypatch):
    """fake-driver 没有设备、没有 bundle——**不注入**（注入一个探不到东西的
    探针只会白等 12 秒，且 `latest()` 恒 None，等于没接线却看起来接了）。"""
    seen = {}
    import cli.pipeline as pl
    real = pl.SessionPipeline.__init__

    def _spy(self, *a, **kw):
        real(self, *a, **kw)
        seen["pipeline"] = self

    monkeypatch.setattr(pl.SessionPipeline, "__init__", _spy)
    main(_argv(proj))
    # 装配在**构造之后**（`cmd_run` 拿到 bundle 才建探针），故查属性不查形参
    assert seen["pipeline"].crash_probe is None


def test_real_device_run_wires_a_crash_probe(proj, monkeypatch):
    """真机路径**必须注入**（否则本批等于没做）。

    不打真机：把 `wire_device_stack` 换成桩，只验**装配那一行**。
    """
    seen = {}
    import cli.pipeline as pl
    import cli.main as m

    real_init = pl.SessionPipeline.__init__

    def _init(self, *a, **kw):
        real_init(self, *a, **kw)
        seen["pipeline"] = self

    class _Stack:
        def __init__(self):
            from runner.runner import StepRunner
            from executor.guard import EnvKind, Guard
            from runner.lifecycle import Lifecycle

            class _Ex:
                def find(self, s): return _El()
                def perform(self, *a, **k): pass
                def swipe(self, d): pass
            self.runner = StepRunner(_Ex(), _DS(), Guard(EnvKind.SANDBOX),
                                     run_id="r")
            self.lifecycle = Lifecycle(store=None)
            self.device_session = _DS()
            self.executor = self.runner.ex

        def close(self): pass

    class _El:
        def is_displayed(self): return True
        def is_enabled(self): return True
        text = ""
        def get_attribute(self, n): return ""

    class _DS:
        def ensure_alive(self): return self

    monkeypatch.setattr(pl.SessionPipeline, "__init__", _init)
    monkeypatch.setattr(m, "wire_device_stack", lambda *a, **k: _Stack(),
                        raising=False)
    monkeypatch.setattr(pl, "wire_device_stack", lambda *a, **k: _Stack())
    import source.build_identity as bi
    from source.build_identity import AppIdentity, GateResult
    monkeypatch.setattr(bi, "resolve_booted_udid", lambda: "UDID-X")
    monkeypatch.setattr(
        bi, "gate_run_start",
        lambda **kw: GateResult(blocked=False, mismatch=False, override=False,
                                app=AppIdentity(git_commit="deadbeef",
                                                build="local"),
                                meta=AppIdentity(git_commit="deadbeef",
                                                 build="local")))
    argv = [a for a in _argv(proj) if a != "--fake-driver"]
    argv += ["--bundle-id", "com.demo", "--udid", "UDID-X"]
    main(argv)
    probe = seen["pipeline"].crash_probe
    assert probe is not None, "真机路径必须注入崩溃探测"
    assert probe.bundle_id == "com.demo"
