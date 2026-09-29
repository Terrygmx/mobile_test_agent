"""Repository 定义加载器（设计 5.1~5.4，Task 1.2 / P1-02）。

职责：
  - 把 overrides/（或 M3 后 generated/）目录下的 screen/element YAML 解析为
    强类型定义（ElementDef/ScreenDef），失败明确报 RepositoryLoaderError；
  - element 以 `(screen, id)` 键控：语义 ID 允许跨 Screen 同名（4.1 歧义规则的
    前提），只有同 Screen 同 id 才算重复定义；
  - 文件支持 `---` 多文档（5.1 布局是每 Screen 一个文件，文件内可含多条定义）。

低层入口 `load_element_dir/load_screen_dir` 接受 `(filename, yaml_text)` 序列，
便于单测不经磁盘注入；文件入口 `load_element_file/load_screen_file` 给运行时用。
"""
from __future__ import annotations

import enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

ELEMENT_TYPES = (
    "button", "textfield", "securetextfield", "text", "cell", "switch", "other",
)
STRATEGY_TYPES = (
    "accessibility_id", "predicate", "class_chain", "xpath", "id",
)
ORIGINS = ("source", "manual")
# V2 预留 origin: experience（5.3），P1 不产生。
MODES = ("replace", "prepend", "append")
KIND_HINTS = ("page", "modal", "overlay")
# 5.2 metadata 枚举（R6-3：typo 在 loader 层拦截，不漏到 resolve 时裸 KeyError）
RISKS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
IDEMPOTENCIES = ("IDEMPOTENT", "NON_IDEMPOTENT", "UNKNOWN")
DATA_CLASSES = ("PUBLIC", "INTERNAL", "SENSITIVE", "SECRET")


class RepositoryLoaderError(ValueError):
    """定义文件解析/校验失败（含 Pydantic ValidationError 聚合）。"""


class DataClass(str, enum.Enum):
    """脱敏分级（5.2 metadata / 14.4 Redactor 消费）。"""

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    SENSITIVE = "SENSITIVE"
    SECRET = "SECRET"


class LocatorStrategy(BaseModel):
    """5.2 一条定位策略；每条必须记录 origin（source / manual）。"""

    model_config = ConfigDict(extra="forbid")

    type: str
    value: str
    origin: str = "source"
    source_file: str | None = None
    source_line: int | None = None


class ElementDef(BaseModel):
    """5.2 Element 定义（generated 与 override 共用；override 多 mode 键）。

    screen/type 可省略——仅当该定义是 override 且要合并到 generated 时
    （设计 5.3 示例省略 screen）；overrides-only（M1 起点）必须声明 screen，
    由 Repository 合并入口强制检查。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str | None = None  # 设计 5.2 示例带 "1.0"；loader 不冻结版本
    kind: str  # 固定 "element"，loader 校验
    id: str
    screen: str | None = None
    type: str | None = None
    strategies: list[LocatorStrategy] = Field(min_length=1)
    mode: str | None = None  # 仅 override 声明；None = replace（默认）
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def effective_mode(self) -> str:
        return self.mode or "replace"


class ScreenDef(BaseModel):
    """5.4 Screen 定义。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: str | None = None
    kind: str  # 固定 "screen"
    id: str
    marker: str
    kind_hint: str = "page"
    metadata: dict[str, Any] = Field(default_factory=dict)
    includes: list[str] = Field(default_factory=list)


def _load_docs(entries: list[tuple[str, str]]) -> list[tuple[str, dict]]:
    """每文件支持 `---` 多文档；空文档跳过；非 mapping 报错。"""
    docs: list[tuple[str, dict]] = []
    for filename, text in entries:
        for data in yaml.safe_load_all(text):
            if data is None:
                continue
            if not isinstance(data, dict):
                raise RepositoryLoaderError(
                    f"{filename}: expected a YAML mapping, got {type(data).__name__}"
                )
            docs.append((filename, data))
    return docs


def _build_defs(
    docs: list[tuple[str, dict]],
    model_cls: type[ElementDef] | type[ScreenDef],
    expected_kind: str,
) -> dict[Any, Any]:
    defs: dict[Any, Any] = {}
    for filename, data in docs:
        try:
            d = model_cls.model_validate(data)
        except ValidationError as exc:
            raise RepositoryLoaderError(f"{filename}: {exc}") from exc
        if d.kind != expected_kind:
            raise RepositoryLoaderError(
                f"{filename}: kind must be {expected_kind!r}, got {d.kind!r}"
            )
        # element：语义 ID 允许跨 Screen 同名（4.1）→ (screen, id) 键控；
        # screen：全局唯一 → id 键控。
        key = (getattr(d, "screen", None), d.id) if expected_kind == "element" else d.id
        if key in defs:
            raise RepositoryLoaderError(
                f"{filename}: duplicate definition key={key!r} "
                f"(already defined by an earlier document)"
            )
        defs[key] = d
    return defs


def _validate_element_semantics(defs: dict[tuple[str | None, str], ElementDef]) -> None:
    """ElementDef 枚举字段逐个校验（str + 手查，报错信息可控、可读）。

    R6-3：metadata 的 risk/idempotency/data_class 枚举在此前移校验——
    若漏到 resolve 时会以裸 KeyError 暴露，脱离 RepositoryLoaderError 体系。
    """
    for (_screen, e_id), d in defs.items():
        if d.id and "." in d.id:
            # 限定名 `Screen.elem` 语法保留字：语义 ID 含 `.` 会让短名解析被
            # 误判为限定名（P3）。
            raise RepositoryLoaderError(
                f"element id {d.id!r} must not contain '.' "
                f"(reserved for qualified name 'Screen.elem')"
            )
        if d.type is not None and d.type not in ELEMENT_TYPES:
            raise RepositoryLoaderError(
                f"element {e_id!r}: unknown type {d.type!r} (allowed: {ELEMENT_TYPES})"
            )
        if d.mode is not None and d.mode not in MODES:
            raise RepositoryLoaderError(
                f"element {e_id!r}: unknown mode {d.mode!r} (allowed: {MODES})"
            )
        for key, allowed in (
            ("risk", RISKS), ("idempotency", IDEMPOTENCIES), ("data_class", DATA_CLASSES),
        ):
            if key in d.metadata and d.metadata[key] not in allowed:
                raise RepositoryLoaderError(
                    f"element {e_id!r}: metadata {key}={d.metadata[key]!r} "
                    f"invalid (allowed: {allowed})"
                )
        for s in d.strategies:
            if s.type not in STRATEGY_TYPES:
                raise RepositoryLoaderError(
                    f"element {e_id!r}: unknown strategy type {s.type!r} "
                    f"(allowed: {STRATEGY_TYPES})"
                )
            if s.origin not in ORIGINS:
                raise RepositoryLoaderError(
                    f"element {e_id!r}: unknown strategy origin {s.origin!r} "
                    f"(allowed: {ORIGINS})"
                )


def _validate_screen_semantics(defs: dict[str, ScreenDef]) -> None:
    # R8-1：marker 是 current_screen/marker_visible/Reconciliation 的身份锚点，
    # 全局唯一是 13.2 判定的隐含前提；重复会让 current_screen 静默选边（dict
    # update 后写者赢）。loader 层拒绝，宁可 fail fast。
    marker_owners: dict[str, str] = {}
    for d in defs.values():
        if d.id and "." in d.id:
            raise RepositoryLoaderError(
                f"screen id {d.id!r} must not contain '.' "
                f"(reserved for qualified name 'Screen.elem')"
            )
        if d.marker in marker_owners:
            raise RepositoryLoaderError(
                f"screen {d.id!r}: marker {d.marker!r} already claimed by "
                f"screen {marker_owners[d.marker]!r} (markers must be globally unique)"
            )
        marker_owners[d.marker] = d.id
        if d.kind_hint not in KIND_HINTS:
            raise RepositoryLoaderError(
                f"screen {d.id!r}: unknown kind_hint {d.kind_hint!r} "
                f"(allowed: {KIND_HINTS})"
            )
        # R6-4：includes（5.4 子组件归属）M1 加载但不消费——M1 无 generated
        # 多组件来源；TODO(Task 4.x/Source Intelligence)：elements_of 合并
        # includes 指向的子组件元素。留在此处记账，M1 末冻结盘点不漏。
        if d.includes:
            raise RepositoryLoaderError(
                f"screen {d.id!r}: includes is not consumed yet in P1 "
                f"(TODO M1-end freeze accounting, R6-4); remove or leave empty"
            )


def load_element_dir(
    entries: list[tuple[str, str]],
) -> dict[tuple[str | None, str], ElementDef]:
    """(filename, yaml_text) 序列 → {(screen, element_id): ElementDef}。"""
    defs = _build_defs(_load_docs(entries), ElementDef, "element")
    _validate_element_semantics(defs)
    return defs


def load_screen_dir(entries: list[tuple[str, str]]) -> dict[str, ScreenDef]:
    """(filename, yaml_text) 序列 → {screen_id: ScreenDef}。"""
    defs = _build_defs(_load_docs(entries), ScreenDef, "screen")
    _validate_screen_semantics(defs)
    return defs


def _dir_entries(directory: Path) -> list[tuple[str, str]]:
    if not directory.exists():
        return []
    return [
        (p.name, p.read_text())
        for p in sorted(directory.iterdir())
        if p.suffix in (".yaml", ".yml")
    ]


def load_elements(path: str | Path) -> dict[tuple[str | None, str], ElementDef]:
    """磁盘目录入口：目录下所有 *.yaml/*.yml → {(screen, element_id): ElementDef}。"""
    return load_element_dir(_dir_entries(Path(path)))


def load_screens(path: str | Path) -> dict[str, ScreenDef]:
    """磁盘目录入口：目录下所有 *.yaml/*.yml → {screen_id: ScreenDef}。"""
    return load_screen_dir(_dir_entries(Path(path)))
