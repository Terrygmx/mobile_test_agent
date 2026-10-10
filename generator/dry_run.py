"""dry_run.py — 候选的 Dry Run（设计 6.1 / 7.4；Task 3.4 / P3-12）。

设计 6.1 原文：**Dry Run：仅在 `env.kind == sandbox` 执行一次（F7）**。
矩阵 #9 把它钉成一条硬判据：**非 sandbox（或 real_device）请求 → 拒绝执行**。

## 闸门在**函数内部**，不是「只在 CLI 里」

这是本模块存在的主要理由。F7 的纪律是「启动时校验」——但「启动」在
Dry Run 这个尺度上是**每次请求**：生成器可能在一批候选里循环调用它，而
「哪一批」由调用方决定。把判据留给调用方 = 每个调用方都要记得判一次，
漏一个就是一个能烧真机的入口。所以：

- `env_kind` 不是 `EnvKind.SANDBOX` → 抛 `DryRunRefused`（**不是**返回
  `DryRunResult(status="FAIL")`）；
- `device_type` 不是 `DeviceType.SIMULATOR` → 同上。

**为什么是抛异常而不是返回 FAIL**：`FAIL` 的语义是「跑了，没通过」——
它会被写进 `test_candidates.dry_run_status`，然后人工审核时看到「这条候选
Dry Run 失败」。而实际上它**根本没跑**。两者必须可分辨（本仓的固定纪律：
「没做」与「做了但结果为空」不许长得一样）。

## 设备侧的装配**注入**，本模块不 import `cli`

`execute` 是调用方给的 `Callable[[TestCase], TestcaseResult]`：

- 3.4 的测试给一个 Fake（不碰设备）；
- 3.5 的 `mta generate` / M3 Gate 给 `cli.pipeline.wire_device_stack` 配好的
  真栈（真机或 `--fake-driver`）。

这样 `generator/**` 的 import 面保持「纯逻辑 + 数据模型」，F13 的
「判据离设备」与 `planner/**` 的 import 守卫同款可机械验证。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from environment.capabilities import DeviceType, coerce_device_type
from executor.guard import EnvKind
from runner.result import TestcaseResult
from testcase.schema import TestCase

__all__ = ["DRY_RUN_PASS", "DRY_RUN_FAIL", "DryRunRefused", "DryRunResult",
           "run_candidate"]

DRY_RUN_PASS = "PASS"
DRY_RUN_FAIL = "FAIL"


class DryRunRefused(RuntimeError):
    """F7 / 矩阵 #9：非 sandbox 或非模拟器上的 Dry Run 请求。

    **不是** `DryRunResult`——见模块 docstring：「没跑」与「跑了没通过」
    必须可分辨。调用方（生成器）捕它并**不落候选**（拒绝留痕）。
    """


@dataclass(frozen=True)
class DryRunResult:
    """一次 Dry Run 的结论（`dry_run_status` 的取值域）。

    `detail` 装 `TestcaseResult` 的关键字段（`failure_type` / `attempt` /
    `cleanup_status`）——**不装整个对象**：这个结果会被写进
    `test_candidates.dry_run_status` 旁边的审计面，必须是可 JSON 化的值。
    """

    status: str
    detail: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in (DRY_RUN_PASS, DRY_RUN_FAIL):
            raise ValueError(f"unknown dry run status: {self.status!r}")


def run_candidate(tc: TestCase, *, env_kind, device_type,
                  execute: Callable[[TestCase], TestcaseResult]
                  ) -> DryRunResult:
    """在 **sandbox + simulator** 上跑一次候选（矩阵 #9 的闸门在此）。

    | 参数 | 说明 |
    |---|---|
    | `tc` | 待跑候选（P1 `TestCase`，F5） |
    | `env_kind` | `EnvKind` 或其裸值；**只接受 `sandbox`** |
    | `device_type` | `DeviceType` 或其裸值；**只接受 `simulator`** |
    | `execute` | 调用方注入的执行体（真栈或 Fake） |

    闸门顺序：**先环境、后设备**（与 `cli.main` 的 F7 前置同序——环境是更粗
    的粒度，先挡它能在设备装配**之前**就退出）。

    ⚠️ `env_kind` 走 `EnvKind(...)` 构造（坏值 → `ValueError`）——**不**用
    `getattr(env_kind, "value", env_kind)` 兜底：传错形态时那种写法会把
    `"production"` 与 `EnvKind.PRODUCTION` 都「恰好」放行，而一个 `None`
    会塌成 `"None"` 然后被拒——拒绝的理由就成了错的（「不是 sandbox」而不是
    「你传了个 None」）。
    """
    kind = env_kind if isinstance(env_kind, EnvKind) else EnvKind(env_kind)
    if kind is not EnvKind.SANDBOX:
        raise DryRunRefused(
            f"F7：Dry Run 只在 sandbox 执行（设计 6.1 / 矩阵 #9），"
            f"got env_kind={kind.value!r}——非 sandbox 的候选执行会真改环境，"
            f"拒绝执行（无豁免 flag）")

    device = coerce_device_type(device_type)
    if device is not DeviceType.SIMULATOR:
        raise DryRunRefused(
            f"矩阵 #9：Dry Run 只在模拟器执行（真机上跑生成器产出的候选会把"
            f"未审核的用例打到真机），got device_type={device.value!r}——拒绝执行")

    result = execute(tc)
    status = DRY_RUN_PASS if result.status == "PASS" else DRY_RUN_FAIL
    return DryRunResult(status=status, detail={
        "testcase_status": result.status,
        "failure_type": result.failure_type,
        "attempt": result.attempt,
        "cleanup_status": result.cleanup_status,
    })
