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
    """ElementDef 枚举字段逐个校验（str + 手查，报错信息可控、可读）。"""
    for (_screen, e_id), d in defs.items():
        if d.type is not None and d.type not in ELEMENT_TYPES:
            raise RepositoryLoaderError(
                f"element {e_id!r}: unknown type {d.type!r} (allowed: {ELEMENT_TYPES})"
            )
        if d.mode is not None and d.mode not in MODES:
            raise RepositoryLoaderError(
                f"element {e_id!r}: unknown mode {d.mode!r} (allowed: {MODES})"
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
    for d in defs.values():
        if d.kind_hint not in KIND_HINTS:
            raise RepositoryLoaderError(
                f"screen {d.id!r}: unknown kind_hint {d.kind_hint!r} "
                f"(allowed: {KIND_HINTS})"
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


def _dir_entries(directory: Path, suffix: str = ".yaml") -> list[tuple[str, str]]:
    if not directory.exists():
        return []
    return [(p.name, p.read_text()) for p in sorted(directory.glob(f"*{suffix}"))]


def load_element_file(
    path: str | Path,
) -> dict[tuple[str | None, str], ElementDef]:
    """目录下所有 *.yaml → {(screen, element_id): ElementDef}。"""
    return load_element_dir(_dir_entries(Path(path)))


def load_screen_file(path: str | Path) -> dict[str, ScreenDef]:
    """目录下所有 *.yaml → {screen_id: ScreenDef}。"""
    return load_screen_dir(_dir_entries(Path(path)))
