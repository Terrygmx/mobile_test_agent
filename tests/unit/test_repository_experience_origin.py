"""Task 4.1 / P2-09：`origin: experience` 进 Repository（5.2/5.3）。

plan step 1 的失败测试清单：
  - loader 接受 `origin: experience`（接线前置：`ORIGINS` 扩展必须与
    promote 写入同批落地，否则一上线就 fail-loud——review_p2_task23 P3-4）；
  - resolver 合并按 origin 排序：manual > source > experience——**尝试顺序**
    的语义（Locator Chain 链序），experience 策略排链尾而非丢弃；
  - 三源并存且 accessibility_id 冲突 → 记 warning（不可静默）；
  - 无 experience 策略时链序**完全不变**（P1 行为保留——排序只在
    experience 出现时介入，现有 manual/source 混排不被重排）。
"""
from __future__ import annotations

from repository.loader import LocatorStrategy
from repository.resolver import Repository


def _repo(tmp_path, yaml_text: str) -> Repository:
    d = tmp_path / "overrides" / "elements"
    d.mkdir(parents=True, exist_ok=True)
    (d / "HomeView.yaml").write_text(yaml_text, encoding="utf-8")
    return Repository.from_dirs(overrides_root=str(tmp_path / "overrides"))


def test_loader_accepts_experience_origin(tmp_path):
    _repo(tmp_path, """\
schema_version: "1.0"
kind: element
id: login_button
screen: HomeView
strategies:
  - {type: accessibility_id, value: v2, origin: experience}
""")
    eff = Repository.from_dirs(
        overrides_root=str(tmp_path / "overrides")).resolve(
        "HomeView.login_button", build="local")
    assert [s.origin for s in eff.strategies] == ["experience"]


def test_merge_orders_manual_source_experience(tmp_path):
    """三源并存：尝试顺序 manual > source > experience（链尾，不丢弃）。"""
    eff = _repo(tmp_path, """\
schema_version: "1.0"
kind: element
id: login_button
screen: HomeView
strategies:
  - {type: accessibility_id, value: exp_v3, origin: experience}
  - {type: accessibility_id, value: manual_v2, origin: manual}
  - {type: accessibility_id, value: src_v1, origin: source}
""").resolve("HomeView.login_button", build="local")
    assert [(s.origin, s.value) for s in eff.strategies] == [
        ("manual", "manual_v2"), ("source", "src_v1"),
        ("experience", "exp_v3")], "experience 排链尾而非丢弃"


def test_conflict_between_experience_and_manual_warns(tmp_path):
    eff = _repo(tmp_path, """\
schema_version: "1.0"
kind: element
id: login_button
screen: HomeView
strategies:
  - {type: accessibility_id, value: same_v, origin: experience}
  - {type: accessibility_id, value: same_v, origin: manual}
""").resolve("HomeView.login_button", build="local")
    assert any("experience_strategy_conflict" in w for w in eff.warnings), \
        "experience 与 manual 的同名策略冲突要可观测（不可静默）"


def test_no_experience_strategy_keeps_p1_order(tmp_path):
    """P1 行为保留：链里没有 experience 策略时**不做任何重排**——
    现有 manual/source 混排（source 在前）保持原顺序。"""
    eff = _repo(tmp_path, """\
schema_version: "1.0"
kind: element
id: login_button
screen: HomeView
strategies:
  - {type: accessibility_id, value: src_v1, origin: source}
  - {type: accessibility_id, value: manual_v2, origin: manual}
""").resolve("HomeView.login_button", build="local")
    assert [s.origin for s in eff.strategies] == ["source", "manual"]


def test_experience_strategy_survives_replace_mode(tmp_path):
    """merge 层的 replace 语义本身（P1 既有行为，不改）：experience
    override 用显式 replace（无 mode 键）时，generated 的 source 策略不进
    链——但 experience 自身不被丢。

    定档注记（review_p2_task41 P2-2）：promoter 的**真实产物**带
    `mode: append`（§9.2「排链尾不丢弃」由写入口兑现，见
    test_promoter.py::test_promoter_diff_uses_append_mode_to_keep_source_chain）；
    本测试只钉 merge 层对裸文档的既有语义，供 resolver 回归。"""
    d = tmp_path / "overrides" / "elements"
    d.mkdir(parents=True)
    (d / "HomeView.yaml").write_text("""\
schema_version: "1.0"
kind: element
id: login_button
screen: HomeView
strategies:
  - {type: accessibility_id, value: exp_v3, origin: experience}
""", encoding="utf-8")
    repo = Repository.from_dirs(
        generated_root=str(_gen(tmp_path)),
        overrides_root=str(tmp_path / "overrides"))
    eff = repo.resolve("HomeView.login_button", build="local")
    assert [(s.origin, s.value) for s in eff.strategies] == [
        ("experience", "exp_v3")]


def _gen(tmp_path):
    g = tmp_path / "generated"
    (g / "elements").mkdir(parents=True)
    (g / "elements" / "HomeView.yaml").write_text("""\
schema_version: "1.0"
kind: element
id: login_button
screen: HomeView
type: button
strategies:
  - {type: accessibility_id, value: src_v1, origin: source}
""", encoding="utf-8")
    return g
