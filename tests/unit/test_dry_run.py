"""Task 3.4（P3-12）：Dry Run 的闸门与结论映射（矩阵 #9）。

矩阵 #9 的原文是「**Dry Run 在非 sandbox（或 real_device）请求 → 拒绝执行**」。
本文件把它钉成**函数内部**的判据——不是「调用方记得判一次」：

- `env_kind != sandbox` → `DryRunRefused`（**抛**，不是返回 FAIL）；
- `device_type != simulator` → `DryRunRefused`。

「抛 vs 返回 FAIL」是本文件最要紧的一条：`FAIL` 会被写进
`test_candidates.dry_run_status`，人工审核时看到的是「这条候选 Dry Run 失败」——
而它**根本没跑**。两者必须可分辨。
"""
from __future__ import annotations

import pytest

from executor.guard import EnvKind
from environment.capabilities import DeviceType
from generator.dry_run import (DRY_RUN_FAIL, DRY_RUN_PASS, DryRunRefused,
                               DryRunResult, run_candidate)
from runner.result import TestcaseResult
from testcase.schema import parse_testcase_dict


def _case():
    return parse_testcase_dict({
        "schema_version": "0.2", "id": "c", "name": "c",
        "steps": [{"action": "launch_app"}]})


def _result(status: str, **kw) -> TestcaseResult:
    return TestcaseResult(testcase_id="c", status=status, **kw)


# --- 矩阵 #9：闸门 -----------------------------------------------------------


@pytest.mark.parametrize("env_kind", [
    EnvKind.PRODUCTION, EnvKind.STAGING, "production", "staging",
])
def test_non_sandbox_env_is_refused(env_kind):
    called = []

    def execute(tc):
        called.append(tc)
        return _result("PASS")

    with pytest.raises(DryRunRefused) as e:
        run_candidate(_case(), env_kind=env_kind,
                      device_type=DeviceType.SIMULATOR, execute=execute)
    assert "F7" in str(e.value)
    assert called == [], "闸门必须在 execute **之前**——拒了就不许碰设备"


@pytest.mark.parametrize("device", [
    DeviceType.REAL_DEVICE, "real_device", "real-device",
])
def test_real_device_is_refused(device):
    called = []
    with pytest.raises(DryRunRefused) as e:
        run_candidate(_case(), env_kind=EnvKind.SANDBOX, device_type=device,
                      execute=lambda tc: called.append(tc))
    assert "矩阵 #9" in str(e.value)
    assert called == []


def test_unknown_env_kind_is_fail_loud_not_silently_refused():
    """坏值 → `ValueError`（不是 `DryRunRefused`）。

    用 `getattr(env_kind, "value", env_kind)` 兜底的写法会把 `None` 塌成
    `"None"` 然后被「不是 sandbox」拒掉——拒绝的**理由就成了错的**（人以为
    是环境问题，实际是调用方传错了）。
    """
    with pytest.raises(ValueError):
        run_candidate(_case(), env_kind="SANDBOX!!",
                      device_type=DeviceType.SIMULATOR,
                      execute=lambda tc: _result("PASS"))


def test_unknown_device_type_is_fail_loud():
    from session.app_session import UnsupportedResetError
    with pytest.raises(UnsupportedResetError):
        run_candidate(_case(), env_kind=EnvKind.SANDBOX, device_type="ipad",
                      execute=lambda tc: _result("PASS"))


def test_bare_strings_are_accepted():
    """CLI 传的是裸字符串（`--env-kind sandbox`）——两条路径都要能用。"""
    seen = []
    out = run_candidate(_case(), env_kind="sandbox", device_type="simulator",
                        execute=lambda tc: (seen.append(tc), _result("PASS"))[1])
    assert out.status == DRY_RUN_PASS and len(seen) == 1


# --- 结论映射 ----------------------------------------------------------------


def test_pass_maps_to_pass():
    out = run_candidate(_case(), env_kind=EnvKind.SANDBOX,
                        device_type=DeviceType.SIMULATOR,
                        execute=lambda tc: _result("PASS"))
    assert out.status == DRY_RUN_PASS
    assert out.detail["testcase_status"] == "PASS"


@pytest.mark.parametrize("status,failure_type", [
    ("FAIL", "ELEMENT_NOT_FOUND"),
    ("RECOVERED", None),
    ("INFRA_FAILURE", "WDA_FAILURE"),
    ("ENVIRONMENT_FAILURE", None),
    ("BLOCKED", None),
    ("SKIPPED", None),
    ("ABORTED", None),
])
def test_non_pass_statuses_map_to_fail(status, failure_type):
    """**只有 `PASS` 是 PASS**（H5 的同一态度：RECOVERED ≠ PASS）。

    `dry_run_status` 的取值域只有 PASS/FAIL（迁移 002 的 CHECK）——把一个
    RECOVERED 候选当 PASS 入库，等于让「靠恢复跑通的候选」混进人工审核队列，
    而它恰恰是最该被人工看一眼的那类。
    """
    out = run_candidate(_case(), env_kind=EnvKind.SANDBOX,
                        device_type=DeviceType.SIMULATOR,
                        execute=lambda tc: _result(status,
                                                   failure_type=failure_type))
    assert out.status == DRY_RUN_FAIL
    assert out.detail["testcase_status"] == status


def test_detail_carries_diagnosable_fields():
    out = run_candidate(_case(), env_kind=EnvKind.SANDBOX,
                        device_type=DeviceType.SIMULATOR,
                        execute=lambda tc: _result(
                            "FAIL", failure_type="WAIT_TIMEOUT", attempt=2,
                            cleanup_status="OK"))
    assert out.detail == {"testcase_status": "FAIL",
                          "failure_type": "WAIT_TIMEOUT",
                          "attempt": 2, "cleanup_status": "OK"}


def test_dry_run_result_rejects_unknown_status():
    with pytest.raises(ValueError):
        DryRunResult(status="MAYBE")


def test_refusal_is_not_a_dry_run_result():
    """`DryRunRefused` **不是** `DryRunResult`——「没跑」与「跑了没通过」分离。"""
    assert not issubclass(DryRunRefused, DryRunResult)
    assert issubclass(DryRunRefused, RuntimeError)
