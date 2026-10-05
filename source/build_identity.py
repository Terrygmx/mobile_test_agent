"""build_identity.py — Build Identity（12.5 / P1-10 前半 / plan Task 3.3 第 1 步）。

设计 12.5 契约：
- App 的 Info.plist 构建期注入 `MTA_GIT_COMMIT` / `MTA_BUILD_ID`
  （Makefile `p1-build`：GENERATE_INFOPLIST_FILE=YES 下经
  INFOPLIST_KEY_* 由 xcodebuild 合并）；
- 运行开始时读取被测 App 的这两个值与 metadata（12.3 顶层扁平键
  git_commit / build）对比，任一不一致 → `BUILD_METADATA_MISMATCH`
  （8.4 前置配置错误，退出码 3，未启动用例）；
- 本地开发 `--allow-metadata-mismatch` 放行（默认关闭），Trace 必须记
  `metadata_mismatch=1, override=1`（G8 可审计）；CI（`CI=true`）下需要
  第二显式开关 `MTA_ALLOW_METADATA_MISMATCH_CI=1`。

fail-closed 语义（G8「App / Metadata / commit 可关联，不一致 fail closed」）：
- App 没注入（键缺失）或 metadata 缺身份键 = **不可关联 ≠ 一致**，一律
  mismatch；放行开关只豁免「可关联但不一致」，不豁免「根本没身份」——
  否则未注入的旧 App 永远绕过校验。
- App 未安装 / simctl 失败 / Info.plist 不可解析 / metadata 文件缺失 →
  BuildIdentityError（fail-loud），调用方 exit 3。「读不到」绝不当成
  「一致」（12.2 同源纪律：没扫到 ≠ 不存在）。

H18 边界：`evaluate` / `override_allowed` / `gate_run_start` 纯逻辑离设备；
设备访问隔离在 `read_app_identity` / `resolve_booted_udid`（subprocess），
`gate_run_start(read=...)` 注入点供单测与 Gate 使用。
"""
from __future__ import annotations

import json
import plistlib
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

APP_COMMIT_KEY = "MTA_GIT_COMMIT"
APP_BUILD_KEY = "MTA_BUILD_ID"

DEFAULT_APP_BUILD = "local"
"""build id 读不到时的兜底（`mta run` 与源图构建共用）。

**它不是安全判据**（E7 `validated_builds` 的一个元素），所以读不到时退回默认
而不是 fail-loud；但**兜底值必须只有一处**——运行时侧（pipeline 的 app_build /
`runs.app_build`）与源图侧（`build_source_graph` 的 scope）各写一份，就会在
「metadata 没有 build」时分叉成 `"local"` vs `""`，于是 diff 找不到同一 scope
的两面（review_p2_task52 P3-1 的实测）。
"""
# 12.5：CI=true 时 --allow-metadata-mismatch 需要的第二显式开关。
CI_OVERRIDE_ENV = "MTA_ALLOW_METADATA_MISMATCH_CI"


class BuildIdentityError(RuntimeError):
    """App 身份读取失败（App 未安装 / simctl 非零 / plist 不可解析 /
    metadata 缺失）。fail-loud：调用方不得把「读不到」当成「一致」。"""


@dataclass(frozen=True)
class AppIdentity:
    """App 或 metadata 侧的 build 身份（键缺失记 None，不在此层报错）。

    两侧共用同一形态（12.5 比对的就是同一种 build 标识）。
    """
    git_commit: str | None = None
    build: str | None = None


@dataclass(frozen=True)
class Verdict:
    matched: bool
    mismatches: tuple[str, ...] = ()


@dataclass(frozen=True)
class GateResult:
    """`gate_run_start` 的结论；字段直通 runs 表审计列（G8）。"""
    blocked: bool            # True → 调用方 exit 3，未启动用例
    mismatch: bool           # True → runs.metadata_mismatch=1
    override: bool           # True → runs.metadata_mismatch_override=1
    mismatches: tuple[str, ...] = ()
    app: AppIdentity | None = None
    meta: AppIdentity | None = None


def evaluate(app: AppIdentity, meta: Identity) -> Verdict:
    """纯判定：任一侧缺身份或对应键不相等 → mismatch（fail-closed）。"""
    mismatches: list[str] = []
    if app.git_commit is None or app.build is None:
        mismatches.append("app_missing_injection")
    if meta.git_commit is None or meta.build is None:
        mismatches.append("metadata_missing_identity")
    if None not in (app.git_commit, meta.git_commit) \
            and app.git_commit != meta.git_commit:
        mismatches.append("git_commit")
    if None not in (app.build, meta.build) and app.build != meta.build:
        mismatches.append("build")
    return Verdict(matched=not mismatches, mismatches=tuple(mismatches))


def override_allowed(allow: bool, env: Mapping[str, str]) -> bool:
    """12.5 放行规则：默认关；CI=true 需第二显式开关。"""
    if not allow:
        return False
    if env.get("CI", "").strip().lower() == "true":
        return env.get(CI_OVERRIDE_ENV) == "1"
    return True


def read_metadata(metadata_path: str | Path) -> dict:
    """读 12.3 metadata → dict。**全仓唯一的 metadata JSON 解析点**。

    不可读 / 非法 JSON / 顶层非对象 → `BuildIdentityError`（fail-loud，
    12.2 同源纪律：读不到 ≠ 不存在）。`metadata_identity` 与 CLI 的 build id
    解析都走它，避免同一种文件被解析三遍、格式一改就漏改一处。
    """
    path = Path(metadata_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise BuildIdentityError(
            f"metadata 不可读：{path}（{e}）；先 `mta repo generate`") from e
    except json.JSONDecodeError as e:
        raise BuildIdentityError(f"metadata 非 JSON：{path}（{e}）") from e
    if not isinstance(data, dict):
        raise BuildIdentityError(
            f"metadata 顶层必须是对象：{path}（实得 {type(data).__name__}）")
    return data


def resolve_app_build(metadata: Mapping | None) -> str:
    """**build id 的唯一解析规则**：metadata 的 `build` → 非空白字符串，否则
    `DEFAULT_APP_BUILD`。

    两个调用面共用它：运行时侧（`mta run` 的 pipeline app_build + `runs.app_build`）
    与源图侧（`build_source_graph` 的 scope）。两处各写一份兜底 → 兜底值不同 →
    源图与运行时图 scope 不对齐（review_p2_task52 P3-1）。
    """
    build = (metadata or {}).get("build") if metadata else None
    if isinstance(build, str) and build.strip():
        return build
    return DEFAULT_APP_BUILD


def metadata_identity(metadata_path: str | Path) -> AppIdentity:
    """从 12.3 metadata（顶层扁平 git_commit / build）取身份。"""
    data = read_metadata(metadata_path)
    return AppIdentity(git_commit=data.get("git_commit"), build=data.get("build"))


def read_app_identity(udid: str, bundle_id: str, *,
                      simctl_bin: str = "xcrun") -> AppIdentity:
    """读取已安装 App 的 MTA_GIT_COMMIT / MTA_BUILD_ID（设备访问唯一入口）。

    `simctl get_app_container <udid> <bundle_id> app` → Info.plist（plistlib
    直读，二进制/XML 通吃）。**键缺失不是错误**——未注入的旧 App 要能被读
    出来再由 evaluate 判 fail-closed；读取层抛异常会吞掉现场。
    """
    try:
        proc = subprocess.run(
            [simctl_bin, "simctl", "get_app_container", udid, bundle_id,
             "app"], capture_output=True, text=True)
    except OSError as e:
        raise BuildIdentityError(f"simctl 不可用：{e}") from e
    if proc.returncode != 0:
        raise BuildIdentityError(
            f"simctl get_app_container {udid} {bundle_id} 失败："
            f"{(proc.stderr or proc.stdout).strip()}")
    plist_path = Path(proc.stdout.strip()) / "Info.plist"
    try:
        with plist_path.open("rb") as f:
            info = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException) as e:
        raise BuildIdentityError(f"Info.plist 不可解析：{plist_path}"
                                 f"（{e}）") from e
    return AppIdentity(git_commit=info.get(APP_COMMIT_KEY),
                    build=info.get(APP_BUILD_KEY))


def resolve_booted_udid(*, simctl_bin: str = "xcrun") -> str | None:
    """第一个 booted 模拟器 UDID；无 booted 设备返回 None（调用方报错）。"""
    try:
        proc = subprocess.run(
            [simctl_bin, "simctl", "list", "devices"],
            capture_output=True, text=True)
    except OSError as e:
        raise BuildIdentityError(f"simctl 不可用：{e}") from e
    if proc.returncode != 0:
        raise BuildIdentityError(
            f"simctl list devices 失败：{(proc.stderr or proc.stdout).strip()}")
    for line in proc.stdout.splitlines():
        if "(Booted)" not in line:
            continue
        start = line.find("(")
        end = line.find(")", start)
        if 0 <= start < end:
            return line[start + 1:end]
    return None


def gate_run_start(*, metadata_path: str | Path, udid: str, bundle_id: str,
                   allow: bool, env: Mapping[str, str],
                   read: Callable[[str, str], AppIdentity] | None = None,
                   simctl_bin: str = "xcrun") -> GateResult:
    """`mta run` 启动时编排（12.5「运行开始时」）：读 App + 读 metadata →
    evaluate → 放行规则。读取失败抛 BuildIdentityError（调用方 exit 3）。

    `read` 注入点：单测/Gate 传 stub；默认 `read_app_identity`（**函数内
    迟绑定**，monkeypatch 模块属性必须生效——默认参数会在 def 时固化）。
    """
    if not bundle_id:
        raise BuildIdentityError("真机路径需要 --bundle-id（被测 App）")
    reader = read if read is not None else read_app_identity
    meta = metadata_identity(metadata_path)
    app = reader(udid, bundle_id)
    verdict = evaluate(app, meta)
    if verdict.matched:
        return GateResult(blocked=False, mismatch=False, override=False,
                          app=app, meta=meta)
    # 「未注入 / metadata 缺身份」= 不可关联，不在放行豁免范围内（G8）：
    # 放行开关只豁免「可关联但不一致」，否则旧 App 永远绕过校验。
    overridable = all(m not in ("app_missing_injection",
                                "metadata_missing_identity")
                      for m in verdict.mismatches)
    allowed = overridable and override_allowed(allow, env)
    return GateResult(blocked=not allowed, mismatch=True, override=allowed,
                      mismatches=verdict.mismatches, app=app, meta=meta)
