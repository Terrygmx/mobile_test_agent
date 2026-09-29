"""Effective 定义解析与合并（设计 5.3/5.5，Task 1.2 / P1-02）。

原则：
  - 优先级 overrides > generated；replace 完全取代、prepend/append 与 generated 合并；
  - 不能静默覆盖：合并结果的每条策略保留 origin；override 值与 generated 冲突 →
    EffectiveElement.warnings 记 `override_shadows_source`（Trace 消费）；
  - 短名解析要求全局唯一（跨 Screen 同名 → AmbiguousReferenceError，4.1：
    必须用 `Screen.elem` 限定名，不允许运行时猜）；
  - lint 只做 Repository 侧静态检查（引用存在性 / 短名歧义）；用例 schema、
    sleep、secret 等全量 6.4 检查归 Task 1.3 的 testcase.lint。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from repository.loader import (
    DataClass,
    ElementDef,
    ScreenDef,
    load_element_file,
    load_screen_file,
)
from testcase.schema import Idempotency, Risk, TargetRef, TestCase

ElementKey = tuple[str | None, str]


class UnknownReferenceError(KeyError):
    """target 引用的 element/screen 不存在（4.1：lint 报错）。"""


class AmbiguousReferenceError(ValueError):
    """短名在多个 Screen 下存在（4.1：必须用限定名，不允许运行时猜）。"""


@dataclass(frozen=True)
class LintIssue:
    """lint 产出的最小结构；severity/退出码归 Task 1.3 的 `mta lint` 汇总。"""

    code: str  # ambiguous_target / unknown_target
    message: str
    testcase_id: str
    step_index: int | None = None


@dataclass(frozen=True)
class EffectiveElement:
    """5.5 合并结果：element → 运行时可用的定位定义。"""

    id: str
    screen: str
    type: str
    strategies: tuple  # tuple[LocatorStrategy, ...]（保持顺序 = 尝试顺序）
    risk: Risk | None
    idempotency: Idempotency | None
    data_class: DataClass
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class EffectiveScreen:
    """5.5 合并结果：screen_id → marker/kind_hint。"""

    id: str
    marker: str
    kind_hint: str
    warnings: tuple[str, ...] = ()


def _merge_element(
    generated: ElementDef | None,
    override: ElementDef | None,
    element_id: str,
) -> EffectiveElement:
    """5.3 合并单条 element。override 的 screen/type 省略时沿用 generated。"""
    if override is not None:
        base = override
        other = generated
        mode = base.effective_mode
    else:
        assert generated is not None  # 调用方保证二者至少其一
        base = generated
        other = None
        mode = "replace"

    if mode == "replace" or other is None:
        strategies = tuple(base.strategies)
    elif mode == "prepend":
        strategies = tuple(base.strategies) + tuple(other.strategies)
    else:  # append
        strategies = tuple(other.strategies) + tuple(base.strategies)

    warnings: list[str] = []
    if other is not None:
        # 不能静默覆盖：override 的 accessibility_id 值与 generated 不同 → warning（5.3）。
        gen_access = [s.value for s in other.strategies if s.type == "accessibility_id"]
        for s in base.strategies:
            if s.type == "accessibility_id" and gen_access and s.value not in gen_access:
                warnings.append(
                    f"override_shadows_source: {element_id} accessibility_id "
                    f"{s.value!r} shadows source {gen_access[0]!r}"
                )

    meta = dict(other.metadata if other is not None else {})
    meta.update(base.metadata)  # overrides > generated

    return EffectiveElement(
        id=element_id,
        screen=base.screen or (other.screen if other is not None else None) or "",
        type=base.type or (other.type if other is not None else None) or "other",
        strategies=strategies,
        risk=Risk[str(meta["risk"])] if meta.get("risk") is not None else None,
        idempotency=(
            Idempotency[str(meta["idempotency"])]
            if meta.get("idempotency") is not None
            else None
        ),
        data_class=DataClass(meta.get("data_class", "PUBLIC")),
        warnings=tuple(warnings),
    )


def _merge_screen(
    generated: ScreenDef | None,
    override: ScreenDef | None,
    screen_id: str,
) -> EffectiveScreen:
    assert generated is not None or override is not None  # 调用方保证
    base = override if override is not None else generated
    assert base is not None
    warnings: list[str] = []
    if generated is not None and override is not None:
        if override.marker != generated.marker:
            warnings.append(
                f"override_shadows_source: screen {screen_id} marker "
                f"{override.marker!r} shadows source {generated.marker!r}"
            )
    meta = dict(generated.metadata if generated is not None else {})
    meta.update(base.metadata)
    return EffectiveScreen(
        id=screen_id,
        marker=base.marker,
        kind_hint=base.kind_hint,
        warnings=tuple(warnings),
    )


class Repository:
    """generated + overrides → Effective 定义（5.5）。

    element dicts 以 `(screen, id)` 键控（语义 ID 允许跨 Screen 同名，4.1）；
    screen dicts 以 `id` 键控。
    """

    def __init__(
        self,
        generated_elements: dict[ElementKey, ElementDef] | None = None,
        generated_screens: dict[str, ScreenDef] | None = None,
        override_elements: dict[ElementKey, ElementDef] | None = None,
        override_screens: dict[str, ScreenDef] | None = None,
    ) -> None:
        self.generated_elements = generated_elements or {}
        self.generated_screens = generated_screens or {}
        self.override_elements = override_elements or {}
        self.override_screens = override_screens or {}

    @classmethod
    def from_dirs(
        cls,
        generated_root: str | None = None,
        overrides_root: str | None = None,
    ) -> "Repository":
        """5.1 目录布局：{root}/elements/*.yaml + {root}/screens/*.yaml。"""

        def _sub(root: str | None, name: str) -> Path | None:
            if root is None:
                return None
            p = Path(root) / name
            return p if p.exists() else None

        gen_el = load_element_file(p) if (p := _sub(generated_root, "elements")) else None
        gen_sc = load_screen_file(p) if (p := _sub(generated_root, "screens")) else None
        ov_el = load_element_file(p) if (p := _sub(overrides_root, "elements")) else None
        ov_sc = load_screen_file(p) if (p := _sub(overrides_root, "screens")) else None
        return cls(
            generated_elements=gen_el or {},
            generated_screens=gen_sc or {},
            override_elements=ov_el or {},
            override_screens=ov_sc or {},
        )

    # --- 合并（5.3） ---

    def _element(self, element_id: str, screen: str | None = None) -> EffectiveElement:
        """按 id（可选限定 screen）合并。screen=None 时要求全局唯一（4.1）。"""
        gen_matches = [
            d for (s, i), d in self.generated_elements.items()
            if i == element_id and (screen is None or s == screen)
        ]
        ov_matches = [
            d for (s, i), d in self.override_elements.items()
            if i == element_id and (screen is None or s == screen)
        ]
        if not gen_matches and not ov_matches:
            raise UnknownReferenceError(element_id)
        # 逻辑匹配数：override 省略 screen 时并入 generated 的 screen，
        # 不构成第二个匹配；独立计数只看显式 screen 的 (screen, id) 组合。
        explicit_screens = {
            d.screen for d in gen_matches + ov_matches if d.screen is not None
        }
        screenless_override = any(
            d.screen is None for d in ov_matches
        )
        if screen is None and (len(explicit_screens) > 1 or (not explicit_screens and len(ov_matches) > 1)):
            raise AmbiguousReferenceError(
                f"element id {element_id!r} ambiguous across screens "
                f"{sorted(explicit_screens)}; use qualified name 'Screen.{element_id}'"
            )
        if screen is None and screenless_override and not gen_matches:
            # overrides-only 且未声明 screen：无法确定归属，拒绝
            raise AmbiguousReferenceError(
                f"override for element {element_id!r} must declare screen "
                f"(no generated definition to inherit from)"
            )
        generated = gen_matches[0] if gen_matches else None
        override = ov_matches[0] if ov_matches else None
        # override 省略 screen 时沿用 generated 的 screen（5.3 合并语义）
        if override is not None and override.screen is None and generated is not None:
            override = override.model_copy(update={"screen": generated.screen})
        return _merge_element(generated, override, element_id)

    def _screen(self, screen_id: str) -> EffectiveScreen:
        generated = self.generated_screens.get(screen_id)
        override = self.override_screens.get(screen_id)
        if generated is None and override is None:
            raise UnknownReferenceError(screen_id)
        return _merge_screen(generated, override, screen_id)

    # --- 5.5 接口 ---

    def resolve(
        self, ref: str | TargetRef, *, build: str
    ) -> EffectiveElement | EffectiveScreen:
        """4.1 三种写法：短名（须全局唯一）/ `Screen.elem` 限定名 / `screen:<Name>`。

        `build` 进入签名以固化 5.5 接口（M3 generated/<app_build>/ 按构建选择）；
        P1 overrides-only 阶段尚不消费。
        """
        if isinstance(ref, TargetRef):
            if ref.type == "screen":
                return self._screen(ref.id)
            return self._element(ref.id)

        text = ref
        if text.startswith("screen:"):
            return self._screen(text.removeprefix("screen:"))
        if "." in text:
            screen, _, element_id = text.partition(".")
            eff = self._element(element_id, screen=screen)
            if eff.screen != screen:
                raise UnknownReferenceError(
                    f"{text!r}: element {element_id!r} lives on screen "
                    f"{eff.screen!r}, not {screen!r}"
                )
            return eff
        return self._element(text)

    def elements_of(self, screen: str) -> list[EffectiveElement]:
        """5.5：某 Screen 下全部 element（合并后），id 升序保证确定性。"""
        ids = {
            e_id
            for (s, e_id) in (*self.generated_elements.keys(),
                              *self.override_elements.keys())
            if s == screen
        }
        return sorted((self._element(e_id, screen=screen) for e_id in ids),
                      key=lambda e: e.id)

    # --- lint（Repository 侧静态检查；全量 6.4 在 Task 1.3 testcase.lint） ---

    def lint(self, testcases: list[TestCase]) -> list[LintIssue]:
        issues: list[LintIssue] = []
        for tc in testcases:
            for idx, step in enumerate(tc.steps):
                target = getattr(step, "target", None)
                wait = getattr(step, "wait_for", None)
                assertion = getattr(step, "assertion", None)
                candidates = [
                    t for t in (
                        target,
                        wait.target if wait is not None else None,
                        assertion.target if assertion is not None else None,
                    ) if t is not None
                ]
                for t in candidates:
                    issues.extend(self._lint_ref(tc.id, idx, t))
        return issues

    def _lint_ref(
        self, testcase_id: str, step_index: int, ref: TargetRef
    ) -> list[LintIssue]:
        if ref.type == "screen":
            if ref.id not in self.generated_screens and ref.id not in self.override_screens:
                return [LintIssue("unknown_target", f"screen {ref.id!r} not defined",
                                  testcase_id, step_index)]
            return []
        # element：限定名 / 短名
        if "." in ref.id:
            screen, _, element_id = ref.id.partition(".")
            try:
                eff = self._element(element_id, screen=screen)
            except UnknownReferenceError:
                eff = None
            if eff is None or eff.screen != screen:
                return [LintIssue(
                    "unknown_target",
                    f"qualified target {ref.id!r} does not resolve",
                    testcase_id, step_index,
                )]
            return []
        try:
            self._element(ref.id)  # 短名：要求全局唯一
        except AmbiguousReferenceError:
            return [LintIssue(
                "ambiguous_target",
                f"short name {ref.id!r} exists on multiple screens; "
                f"use 'Screen.{ref.id}'",
                testcase_id, step_index,
            )]
        except UnknownReferenceError:
            return [LintIssue("unknown_target", f"element {ref.id!r} not defined",
                              testcase_id, step_index)]
        return []


class RepositoryProtocol(Protocol):
    """5.5 签名契约（结构化，供运行时与测试双查）。"""

    def resolve(
        self, ref: TargetRef, *, build: str
    ) -> EffectiveElement | EffectiveScreen: ...

    def elements_of(self, screen: str) -> list[EffectiveElement]: ...

    def lint(self, testcases: list[TestCase]) -> list[LintIssue]: ...
