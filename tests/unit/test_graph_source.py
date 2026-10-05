"""Task 5.2 / P2-12：Source Graph 构建（设计 12.1 的另一半 / E12）。

plan step 1 的失败测试清单：
  - metadata 的 screens/导航信息 → `screen_nodes(source_of='source')`；
  - **metadata 无导航声明时如实为空，不推测**（E12）。

实测前提（2026-10-05，全仓 165 个 `source_metadata.json` 逐关键词扫过）：当前
metadata 格式**不含任何导航声明**（无 nav/transition/goto/navigate/action/tap/
push/segue 字段）→ 源图的转移如实为空，节点取顶层 `screens`。
"""
from __future__ import annotations

import json

import pytest

from graph import (
    GRAPH_SCHEMA_VERSION,
    RUNTIME,
    SOURCE,
    GraphStore,
    build_source_graph,
    declared_screens,
    read_source_metadata,
)

APP = "com.phaset0.logindemo"


def _metadata(**kw) -> dict:
    d = {
        "build": "1026",
        "generated_at": "2026-10-05T10:00:00Z",
        "git_commit": "abc1234",
        "parser_version": "0.2.0",
        "screens": ["HomeView", "LoginView", "ProfileView"],
        "screen_elements": [
            {"name": "HomeView", "elements": [{"id": "go_profile"}]},
            {"name": "LoginView", "elements": [{"id": "login_button"}]},
            {"name": "ProfileView", "elements": []},
        ],
    }
    d.update(kw)
    return d


# --- declared_screens：如实为空 vs 格式坏了 ---------------------------------


def test_declared_screens_sorted_and_deduped():
    assert declared_screens(_metadata()) == ["HomeView", "LoginView",
                                             "ProfileView"]
    assert declared_screens(_metadata(screens=["B", "A", "B"])) == ["A", "B"]


def test_missing_screens_key_is_empty_not_an_error():
    """**没有 `screens` 键** → 空（该 metadata 对屏一无所知，如实为空）。

    这与「格式坏了」是两件事：老的/最小的 metadata（如只有 build+git_commit）
    不该让建图失败，但也不该被推测出一堆屏。
    """
    assert declared_screens({"build": "local", "git_commit": "x"}) == []
    g = build_source_graph({"build": "local"})
    assert g.nodes == () and g.transitions == ()
    assert g.source_of == SOURCE, "空图也要带对来源（否则替换范围会错）"


@pytest.mark.parametrize("bad", [
    {"screens": "HomeView"},          # 不是 list
    {"screens": [1, 2]},              # 元素不是字符串
    {"screens": [""]},                # 空串屏名
    {"screens": [None]},
])
def test_malformed_screens_fails_loud(bad):
    """格式坏了要炸——「没声明」与「声明读不出来」不能长得一样。"""
    with pytest.raises(ValueError):
        declared_screens(bad)


# --- build_source_graph：节点来自 screens，转移如实为空 ----------------------


def test_nodes_come_from_screens_with_source_evidence():
    g = build_source_graph(_metadata(), app_id=APP)

    assert g.source_of == SOURCE
    assert g.screens == ("HomeView", "LoginView", "ProfileView")
    node = g.node("HomeView")
    assert node.source_of == SOURCE
    assert node.evidence == ("source_declared",), "声明不是观测，证据名要区分"
    assert node.visit_count == 0, "声明面没有「访问」这回事，不编一个别的含义"
    assert node.first_seen == node.last_seen == "2026-10-05T10:00:00Z", \
        "取 metadata 的 generated_at（声明是什么时候生成的）"


def test_transitions_are_empty_when_metadata_declares_no_navigation():
    """**无导航声明 → 转移如实为空**（plan step 1 的核心要求，E12）。

    当前 metadata 格式里根本没有导航字段（实测），所以源图的转移是空的——
    不推测、不用「屏的字母序」编一条链。
    """
    g = build_source_graph(_metadata(), app_id=APP)
    assert g.transitions == ()


def test_screen_elements_names_are_not_used_as_nodes():
    """**`screen_elements[].name` 不当节点**（真机实测它与屏 id 不同名）。

    真机 metadata：`screens` 含 `SpikeSheet`/`SpikeTab`，而
    `screen_elements[].name` 含 `SpikeScreenRoot`/`SpikeTabScreen`——后者在
    `generated/<build>/elements/` 下，是**元素组**不是屏。拿它当节点会让源图
    与运行时图不同名，diff 全变 ADDED/NOT_OBSERVED。
    """
    meta = _metadata(screens=["SpikeSheet", "SpikeTab"],
                     screen_elements=[{"name": "SpikeScreenRoot", "elements": []},
                                      {"name": "SpikeTabScreen", "elements": []}])
    g = build_source_graph(meta, app_id=APP)

    assert g.screens == ("SpikeSheet", "SpikeTab")
    assert "SpikeScreenRoot" not in g.screens
    assert "SpikeTabScreen" not in g.screens


def test_app_build_defaults_to_metadata_build_and_can_be_overridden():
    """范围与运行时图对齐：build 缺省走**与运行时侧同一个解析入口**。

    `build_identity.resolve_app_build` 是唯一规则——所以「metadata 没有 build」
    时两面都退到 `DEFAULT_APP_BUILD`（"local"），不会分叉成 `""` vs `"local"`
    （review_p2_task52 P3-1 实测的分叉：源图 `""`、运行时 `"local"` → diff 找
    不到同一 scope）。
    """
    from source.build_identity import DEFAULT_APP_BUILD, resolve_app_build

    assert build_source_graph(_metadata()).app_build == "1026"
    assert build_source_graph(_metadata(), app_build="9999").app_build == "9999"
    # 没有 build 键 / build 为 None / 空串 / 纯空白 → 与运行时侧同值
    for meta in ({"screens": ["A"]}, {"screens": ["A"], "build": None},
                 {"screens": ["A"], "build": ""},
                 {"screens": ["A"], "build": "   "}):
        assert build_source_graph(meta).app_build == DEFAULT_APP_BUILD
        assert resolve_app_build(meta) == DEFAULT_APP_BUILD, "两面同源"


def test_app_build_resolution_is_shared_with_the_run_side():
    """同一个解析入口被两侧调用（不是两份兜底）：直接断言函数是同一个。"""
    from cli.main import _resolve_app_build
    from source.build_identity import resolve_app_build
    import inspect
    src = inspect.getsource(_resolve_app_build)
    assert "resolve_app_build" in src, \
        "运行时侧必须调共用入口，而不是自己 `or DEFAULT_APP_BUILD`"
    assert resolve_app_build({"build": "1026"}) == "1026"


def test_source_graph_is_a_runtime_graph_value():
    """源图与运行时图是**同结构值**（diff 直接对比，不需要第二套模型）。"""
    from graph.models import RuntimeGraph
    assert isinstance(build_source_graph(_metadata()), RuntimeGraph)


# --- read_source_metadata：坏输入不吞 ---------------------------------------


def test_read_source_metadata_roundtrip(tmp_path):
    p = tmp_path / "source_metadata.json"
    p.write_text(json.dumps(_metadata()), encoding="utf-8")
    assert read_source_metadata(p)["screens"] == ["HomeView", "LoginView",
                                                  "ProfileView"]


def test_read_source_metadata_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="不存在"):
        read_source_metadata(tmp_path / "nope.json")


def test_read_source_metadata_bad_json(tmp_path):
    """坏 JSON → ValueError（解析由 `build_identity.read_metadata` 负责，
    图侧只翻异常类型——解析逻辑不重复实现）。"""
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="非 JSON"):
        read_source_metadata(p)


def test_read_source_metadata_non_object(tmp_path):
    p = tmp_path / "list.json"
    p.write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(ValueError, match="顶层必须是对象"):
        read_source_metadata(p)


def test_read_source_metadata_shares_one_parser(tmp_path):
    """图侧与运行时侧共用同一个解析点（`build_identity.read_metadata`）。"""
    from source.build_identity import read_metadata
    p = tmp_path / "source_metadata.json"
    p.write_text(json.dumps(_metadata()), encoding="utf-8")
    assert read_source_metadata(p) == read_metadata(p)


# --- 落库：两面互不干扰（Task 5.1 P2-1 的实测场景，现在用真 builder） --------


def test_store_keeps_source_and_runtime_scopes_separate(tmp_path):
    """先写运行时图、再写源图 → **运行时面原样保留**（P2-1 的钉子）。

    这正是 Task 5.1 评审要求「开 5.2 之前修」的那条：5.2 的交付物就是源图，
    而 `upsert_graph` 的替换范围若用常量 RUNTIME，写声明面会清空观察面。
    """
    gdb = tmp_path / "graph.db"
    store = GraphStore(gdb)

    from graph.models import RUNTIME as R, ScreenNode, RuntimeGraph
    store.upsert_graph(RuntimeGraph(
        app_id=APP, app_build="1026", source_of=R,
        nodes=(ScreenNode("LoginView", R, 2), ScreenNode("HomeView", R, 1))))

    store.upsert_graph(build_source_graph(_metadata(), app_id=APP))

    assert store.load_graph(APP, "1026", RUNTIME).screens == (
        "HomeView", "LoginView")
    assert store.load_graph(APP, "1026", SOURCE).screens == (
        "HomeView", "LoginView", "ProfileView")
    assert (APP, "1026", SOURCE) in store.list_scopes()
    assert GRAPH_SCHEMA_VERSION == "001_graph_schema"


def test_source_upsert_is_replace_not_accumulate(tmp_path):
    """重跑 `graph build --from-source` 幂等（声明的屏被删掉时图跟着收敛）。"""
    gdb = tmp_path / "graph.db"
    store = GraphStore(gdb)
    store.upsert_graph(build_source_graph(_metadata(), app_id=APP))
    store.upsert_graph(build_source_graph(
        _metadata(screens=["LoginView"]), app_id=APP))

    assert store.load_graph(APP, "1026", SOURCE).screens == ("LoginView",)


def test_source_and_runtime_differ_on_the_same_scope(tmp_path):
    """同一 `(app_id, build)` 下两面可以不同——这正是 diff（Task 5.3）的输入。"""
    gdb = tmp_path / "graph.db"
    store = GraphStore(gdb)
    from graph.models import RUNTIME as R, ScreenNode, RuntimeGraph
    store.upsert_graph(RuntimeGraph(
        app_id=APP, app_build="1026", source_of=R,
        nodes=(ScreenNode("LoginView", R, 1), ScreenNode("SearchView", R, 1))))
    store.upsert_graph(build_source_graph(_metadata(), app_id=APP))

    runtime = set(store.load_graph(APP, "1026", RUNTIME).screens)
    source = set(store.load_graph(APP, "1026", SOURCE).screens)
    assert source - runtime == {"HomeView", "ProfileView"}, \
        "声明有、运行时没到 → Task 5.3 的 NOT_OBSERVED（矩阵 #16）"
    assert runtime - source == {"SearchView"}, "运行时到了、声明没有 → ADDED"


# --- review_p2_task52 的修订钉子（P3-2 / P3-4） -----------------------------


# --- review_p2_task52 的修订钉子（P3-2 / P3-4） -----------------------------


def test_whitespace_only_screen_name_is_rejected():
    """P3-4：纯空白屏名不是「有名字的屏」（判据与「非空字符串」措辞一致）。

    早先只判 `not item`，于是 `"  "` 被接受、成为一个名叫两个空格的节点。
    """
    for bad in (["  "], ["\t"], ["\n"], ["A", "   "]):
        with pytest.raises(ValueError, match="非空字符串"):
            declared_screens({"screens": bad})


def test_generated_at_type_is_validated_at_build_time():
    """P3-2：`generated_at` 的类型闸门在 **build 现场**，不是等到 upsert。

    不校验的后果（评审探针实测）：`123` 被 SQLite 的 TEXT 亲和性**静默落库为
    `'123'`**；`['a']` / `{'x': 1}` 直到 upsert 才炸
    `ProgrammingError: Error binding parameter`——离现场很远。同一个函数里对
    `screens` 严、对 `generated_at` 放任，是两套标准。
    """
    for bad in (123, ["a"], {"x": 1}, True, "   "):
        with pytest.raises(ValueError, match="generated_at"):
            build_source_graph({"screens": ["A"], "generated_at": bad})


def test_generated_at_none_and_iso_string_pass_through():
    """`None`（无时间）与 ISO 串（正常）都要放行。"""
    assert build_source_graph({"screens": ["A"]}).node("A").first_seen is None
    g = build_source_graph({"screens": ["A"],
                            "generated_at": "2026-10-05T10:00:00Z"})
    assert g.node("A").first_seen == "2026-10-05T10:00:00Z"


def test_generated_at_failure_happens_before_any_db_write(tmp_path):
    """错误在 build 就抛，落库根本不会发生（坏数据进不了库）。"""
    gdb = tmp_path / "graph.db"
    store = GraphStore(gdb)
    with pytest.raises(ValueError, match="generated_at"):
        store.upsert_graph(build_source_graph(
            {"screens": ["A"], "generated_at": ["a"]}))
    assert store.load_graph(APP, "local", SOURCE).is_empty, "库里什么都没写"
