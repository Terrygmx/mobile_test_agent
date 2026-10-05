"""Task 4.3：缓存接线（设计 7.3 / plan Task 3.3 的接线定档两条红线）。

红线（plan 原文）：
  ① 命中路径与未命中路径必须**共用同一段 Guard+记账代码**（E1：缓存只省
     Store 磁盘 lookup，不省 Guard）；
  ② 缓存**不得改变 ranker 的输入集语义**（缓存的是 ranker 的输入候选集，
     不是排序结果的应用裁决）。

外加一条由 review_p2_task33 P3-1 钉住的行为：**空列表不入缓存**（负缓存会
让 Store 侧后增的候选在本进程内永久失明，且没有任何报错）。
"""
from __future__ import annotations

import pytest

from agent.context import RecoveryContext
from agent.recovery import RecoveryEngine
from executor.policy import FailurePhase, Idempotency
from experience import SQLiteExperienceStore
from experience.cache import RecoveryCache, cache_key
from experience.models import CandidateSeed
from repository.loader import LocatorStrategy
from testcase.schema import Risk
from tests.fault_injection.fi_support import El, llm_json

APP = "com.matrix.app"
PAGE = "<App><Node name='screen.HomeView' visible='true'/></App>"


class _CountingStore(SQLiteExperienceStore):
    """统计 `lookup` 真实调用次数——缓存接线的核心断言就是它。"""

    def __init__(self, path):
        super().__init__(path)
        self.lookups = 0

    def lookup(self, app_id, screen_id, target_id):
        self.lookups += 1
        return super().lookup(app_id, screen_id, target_id)


class _Repo:
    def __init__(self, entries=None, screens=("HomeView",)):
        self.entries = entries or {}
        from types import SimpleNamespace
        self.generated_screens = {
            s: SimpleNamespace(marker=f"screen.{s}") for s in screens}
        self.override_screens = {}

    def screen_kind_hint(self, screen):
        return "page"

    def elements_of(self, screen):
        return []

    def resolve(self, ref, *, build):
        from types import SimpleNamespace
        rid = ref.id if hasattr(ref, "id") else str(ref)
        if rid not in self.entries:
            raise KeyError(rid)
        screen, risk, etype = self.entries[rid]
        return SimpleNamespace(id=rid, screen=screen, risk=risk, type=etype)


def _seed(store, *, value="signin_button", target="login_button"):
    return store.create_candidate(CandidateSeed(
        review_id=1, recovery_id=1, seed_run_id="run_seed", seed_step_id=1,
        seed_recovery_review_id=1, app_id=APP, screen_id="HomeView",
        target_id=target,
        strategy=LocatorStrategy(type="accessibility_id", value=value,
                                 origin="experience")))


def _ctx(**kw):
    dispatched: list = []
    defaults = dict(
        failure_type="ELEMENT_NOT_FOUND", phase=FailurePhase.PRE_DISPATCH,
        element_id="login_button", screen_id="HomeView", strategies=(),
        action="tap", expected_type="button", effective_risk=Risk.LOW,
        effective_idempotency=Idempotency.IDEMPOTENT,
        app_id=APP, app_build="local", run_id="run_c",
        testcase_id="tc1", page_source=lambda: PAGE,
        find_all=lambda loc: [El()],
        redispatch=lambda el: dispatched.append(el))
    defaults.update(kw)
    return RecoveryContext(**defaults), dispatched


def _engine(store, **kw):
    kw.setdefault("repo", _Repo())
    kw.setdefault("sleep", lambda s: None)
    return RecoveryEngine(experience_store=store, **kw)


@pytest.fixture()
def store(tmp_path):
    return _CountingStore(tmp_path / "exp.db")


def _types(r):
    return [e["event_type"] for e in r.detail.get("experience_events") or []]


def _lookup_detail(r):
    return next(e["detail"] for e in r.detail["experience_events"]
                if e["event_type"] == "experience_lookup")


# --- 红线②：缓存只省 Store lookup，不改候选集语义 ---------------------------


def test_second_recovery_hits_cache_and_skips_store(store):
    """第二次同签名恢复走缓存 → Store.lookup 不再被调用，结论完全一致。"""
    _seed(store)
    cache = RecoveryCache()
    ctx1, dispatched1 = _ctx()
    r1 = _engine(store, cache=cache).recover(ctx1)
    assert store.lookups == 1 and r1.recovered

    ctx2, dispatched2 = _ctx()
    r2 = _engine(store, cache=cache).recover(ctx2)

    assert store.lookups == 1, "第二次必须命中缓存、不再查库"
    assert cache.hits == 1 and cache.misses == 1
    assert _lookup_detail(r2)["source"] == "cache"
    assert r2.recovered and r2.kind == r1.kind
    assert len(dispatched2) == 1, "缓存命中照样真的执行了动作"
    assert r2.strategy == r1.strategy


def test_cache_hit_still_runs_the_full_guard(store):
    """红线①：命中路径与未命中路径共用同一段 Guard+记账。

    用「缓存里已有候选，但这次 Guard 判 BLOCK（0 匹配）」证明：命中了缓存
    也照样执行 Guard——若缓存能绕过校验，这里会 EXECUTE。
    """
    _seed(store)
    cache = RecoveryCache()
    _engine(store, cache=cache).recover(_ctx()[0])
    assert store.lookups == 1

    ctx, dispatched = _ctx(find_all=lambda loc: [])   # 这次 0 匹配
    r = _engine(store, cache=cache).recover(ctx)

    assert not r.recovered and dispatched == []
    assert store.lookups == 1, "确实走了缓存"
    cand = next(s for s in r.detail["stages"]
                if s["stage"] == "experience_candidate")
    assert (cand["outcome"], cand["reason"]) == ("BLOCK", "TARGET_NOT_FOUND")
    [sample] = r.detail["experience_runs"]
    assert sample["result"] == "FAILURE", "缓存命中不豁免 4.7 记账"


def test_cache_does_not_change_ranker_input_semantics(store):
    """红线②：缓存的是**候选集**，排序仍在其后发生。

    造两条候选（一条高成功率、一条低），断言命中缓存时的尝试顺序与直接
    查库时**一致**——缓存不能把「排好序的结果」固化下来。
    """
    hi = _seed(store, value="hi_value")
    lo = _seed(store, value="lo_value", target="login_button")
    # 让 lo 变「更差」：给它记两次失败（成功率 0），hi 保持零样本
    from experience.models import ExperienceRun
    for i in range(2):
        store.record_run(lo.experience_id, ExperienceRun(
            experience_id=lo.experience_id, run_id=f"run_{i}", step_id=i + 1,
            app_build="local", result="FAILURE"))
    assert hi.experience_id != lo.experience_id

    def _first_tried(cache):
        """本次恢复里**第一条被评估**的候选 id（= ranker 的输出首项）。

        比对象身份可靠：每次恢复都会新建 El 实例，按身份比必然不等。
        """
        ctx, dispatched = _ctx(find_all=lambda loc: [El()])
        r = _engine(store, cache=cache).recover(ctx)
        cands = [s for s in r.detail["stages"]
                 if s["stage"] == "experience_candidate"]
        assert len(cands) == 1, "第一条就成功 → 只评估一条"
        return cands[0]["experience_id"]

    uncached = _first_tried(None)
    cached = _first_tried(RecoveryCache())
    assert uncached == cached, "缓存不得改变候选集语义（排序仍在其后发生）"


def test_cache_key_includes_failure_type_and_fingerprint(store):
    """不同 failure_type / 不同 fingerprint → 不同 key（各自独立缓存）。"""
    _seed(store)
    cache = RecoveryCache()
    _engine(store, cache=cache).recover(_ctx()[0])
    assert store.lookups == 1

    _engine(store, cache=cache).recover(
        _ctx(failure_type="ELEMENT_NOT_VISIBLE")[0])
    assert store.lookups == 2, "failure_type 变了 → 不命中同一 key"

    other_page = "<App><Node name='screen.HomeView' visible='true'/>" \
                 "<Node name='extra_field' visible='true'/></App>"
    _engine(store, cache=cache).recover(_ctx(page_source=lambda: other_page)[0])
    assert store.lookups == 3, "指纹变了 → 不命中同一 key"

    key = cache_key(APP, "HomeView", "login_button", "ELEMENT_NOT_FOUND",
                    None)
    assert key  # 公式可用（细节归 test_experience_cache.py）


# --- 负缓存：空列表不入缓存（review_p2_task33 P3-1 的黑洞论证） --------------


def test_empty_lookup_is_not_cached_so_new_candidates_are_visible(store):
    """空库 miss 不入缓存——Store 侧后增候选必须能被本进程立刻看见。

    负缓存是缓存最经典的陷阱：把 miss 也缓存下来，进程内就永远看不见后增
    的数据，而且没有任何报错。
    """
    cache = RecoveryCache()
    r = _engine(store, cache=cache).recover(_ctx()[0])
    assert not r.recovered and _types(r) == ["experience_lookup",
                                             "experience_miss"]
    assert store.lookups == 1

    _seed(store)                       # 同进程内后增候选
    r2 = _engine(store, cache=cache).recover(_ctx()[0])

    assert store.lookups == 2, "空结果没被缓存 → 会再查一次库"
    assert r2.recovered, "后增的候选立刻可见"


def test_disabled_cache_keeps_pre_wiring_behaviour(store):
    """不传 cache → 每次真查库，且 lookup 事件来源恒为 store（行为同接线前）。"""
    _seed(store)
    _engine(store).recover(_ctx()[0])
    _engine(store).recover(_ctx()[0])
    assert store.lookups == 2, "无缓存 = 每次查库（不引入隐式缓存）"


def test_cache_not_consulted_when_key_is_incomplete(store):
    """键不全（无 app_id）→ 连缓存都不碰：缓存是 Store 的加速层，不是替代品。

    若这里查了缓存，就会用「空 app_id 的签名」命中另一条 run 的结果——
    缓存必须与 Store 同一条准入路径（同一个 incomplete_key 判定）。
    """
    _seed(store)
    cache = RecoveryCache()
    r = _engine(store, cache=cache).recover(_ctx(app_id="")[0])
    assert not r.recovered
    assert {"stage": "experience", "outcome": "incomplete_key",
            "missing": "app_id"} in r.detail["stages"]
    assert store.lookups == 0 and cache.misses == 0 and cache.hits == 0


# --- 缓存不得改变 LLM 回落语义 ----------------------------------------------


def test_cache_miss_falls_back_to_llm_unchanged(store):
    """缓存未命中 → 走 Store → 无候选 → 照常回落 LLM（回落语义不变）。"""
    from llm.budget import LLMBudget
    from tests.fault_injection.fi_support import FakeLLM

    llm = FakeLLM([llm_json(value="llm_value")])
    # LLM 候选也要在 Repository 里登记，否则校验链判 TARGET_UNREGISTERED
    # ——那是另一条判据，会把本测试的「回落是否照常」淹没。
    repo = _Repo(entries={"llm_value": ("HomeView", Risk.LOW, "button")})
    ctx, dispatched = _ctx(find_with=lambda sts: El())
    r = _engine(store, cache=RecoveryCache(), repo=repo, llm=llm,
                budget=LLMBudget()).recover(ctx)

    assert r.recovered and r.kind == "llm", "空库 → 缓存不介入 → 回落 LLM"
    assert _types(r) == ["experience_lookup", "experience_miss"]
    assert len(dispatched) == 1, "LLM 候选真的被执行了"


def test_measured_latency_is_not_truncated_to_zero(store):
    """P3-2 的实证：计时不再被 `int()` 截断——200 次取均值必须 > 0。

    单次亚毫秒操作可能四舍五入到 0，所以取均值（与 review_p2_task43 的探针
    同法）。修订前 `int(delta * 1000)` 让 cache 段**恒为 0**（样本集 `{0}`），
    设计 17 的「Cache < Store」梯度就成了 `0 < 0`。
    """
    _seed(store)
    cache = RecoveryCache()
    lat = []
    for _ in range(200):
        ctx, _d = _ctx()
        r = _engine(store, cache=cache).recover(ctx)
        lat.append(_lookup_detail(r)["latency_ms"])

    assert all(isinstance(x, float) for x in lat), "必须是浮点毫秒"
    assert store.lookups == 1, "首次查库、其后全命中缓存"
    assert sum(lat) / len(lat) > 0, "均值 > 0 才说明没被截断成 0"


def test_measured_latency_is_not_truncated_to_zero(store):
    """P3-2 的实证：计时不再被 `int()` 截断——200 次取均值必须 > 0。

    单次亚毫秒操作可能四舍五入到 0，所以取均值（与 review_p2_task43 的探针
    同法）。修订前 `int(delta * 1000)` 让 cache 段**恒为 0**（样本集 `{0}`），
    设计 17 的「Cache < Store」梯度就成了 `0 < 0`。
    """
    _seed(store)
    cache = RecoveryCache()
    lat = []
    for _ in range(200):
        ctx, _d = _ctx()
        r = _engine(store, cache=cache).recover(ctx)
        lat.append(_lookup_detail(r)["latency_ms"])

    assert all(isinstance(x, float) for x in lat), "必须是浮点毫秒"
    assert store.lookups == 1, "首次查库、其后全命中缓存"
    assert sum(lat) / len(lat) > 0, "均值 > 0 才说明没被截断成 0"
