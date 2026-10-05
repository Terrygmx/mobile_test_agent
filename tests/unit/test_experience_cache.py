"""Task 3.3 / P2-08：进程内 Recovery Cache（设计 7.3）。

plan step 1 的失败测试清单逐条对应：
  - cache_key = sha256(`app_id|screen_id|target_id|failure_type|screen_fingerprint`)；
  - 命中返回候选列表但 **Guard 仍执行**（E1 延伸：缓存不允许绕过安全校验）；
  - `app_build` 变化不整体清空（无失效风暴，交给 Guard/validated_builds 自然裁决）；
  - LRU 容量边界。

定位（设计 7.3 原文）：缓存是「运行时加速层，**不是权威数据**」——它只
缓存「该失败签名下有哪些可用候选」（ranker 的输入集），跳过的是 Store
的磁盘 lookup；每条候选被尝试时的 Guard 判定、样本记账（4.7）、状态推进
全部照旧。缓存里**不存在**「这条已验证/可直接执行」的标记——API 形状上
就没有这个东西（get 返回裸 Experience 列表，无 verdict 字段），让「绕过
Guard」在类型上不可表达，而不是靠调用方自觉。
"""
from __future__ import annotations

import hashlib

import pytest

from experience.cache import RecoveryCache, cache_key
from experience.models import Experience, ExperienceStatus
from experience.runtime_guard import experience_runtime_guard, RuntimeContext
from repository.loader import LocatorStrategy


class _FakeFinder:
    """Guard 的 finder 端：当前屏找不到元素（0 匹配 → NOT_FOUND）。"""

    def __init__(self, count: int = 0) -> None:
        self.count = count

    def find_all(self, locator):  # noqa: ANN001, ANN202
        self.last_count = self.count
        return []


def _exp(exp_id: str = "exp_1", *,
         status: ExperienceStatus = ExperienceStatus.CANDIDATE) -> Experience:
    return Experience(
        experience_id=exp_id, app_id="com.x", screen_id="HomeView",
        target_id="login_button",
        strategy=LocatorStrategy(type="accessibility_id", value="v2",
                                 origin="experience"),
        origin="LLM_ACCEPTED_RECOVERY", status=status,
        seed_run_id="run_seed", seed_step_id=1, seed_recovery_review_id=1)


# --- cache_key：设计 7.3 公式 + 两个登记的细化 -------------------------------


def test_cache_key_matches_design_formula():
    """cache_key 是设计 7.3 公式的 sha256（确定性、可跨进程复算）。"""
    expected = hashlib.sha256(
        "com.x|HomeView|login_button|NOT_FOUND|fp_abc".encode()).hexdigest()
    assert cache_key("com.x", "HomeView", "login_button", "NOT_FOUND",
                     "fp_abc") == expected


def test_cache_key_is_order_sensitive():
    a = cache_key("app1", "screenB", "t", "NOT_FOUND", "fp")
    b = cache_key("screenB", "app1", "t", "NOT_FOUND", "fp")
    assert a != b, "五元组顺序换位必须换 key"


def test_cache_key_none_component_maps_to_empty():
    """None 段落映射为空串（登记的细化）——f-string 的字面 'None' 会与
    真值为 'None' 的段撞车；失败签名的缺段（如屏指纹读不到）用 '' 表达。"""
    with_fp = cache_key("com.x", "HomeView", "t", "NOT_FOUND", "fp")
    without_fp = cache_key("com.x", "HomeView", "t", "NOT_FOUND", None)
    assert without_fp != with_fp
    assert without_fp == hashlib.sha256(
        "com.x|HomeView|t|NOT_FOUND|".encode()).hexdigest()


@pytest.mark.parametrize("bad", ["a|b"])
def test_cache_key_rejects_pipe_in_component(bad):
    """含 '|' 的段直接拒绝（登记的细化）：管道是键分隔符，混进段里会让
    两个不同签名静默撞出同一个 key——宁可 fail-loud。"""
    with pytest.raises(ValueError, match="撞 key"):
        cache_key(bad, "s", "t", "NOT_FOUND", "fp")


# --- 命中/未命中：返回候选列表，缓存不改判 -----------------------------------


def test_put_then_get_returns_candidates():
    cache = RecoveryCache()
    exps = [_exp("exp_a"), _exp("exp_b")]
    key = cache_key("com.x", "HomeView", "login_button", "NOT_FOUND", "fp")
    cache.put(key, exps)
    assert cache.get(key) == exps


def test_miss_returns_none():
    assert RecoveryCache().get("nope") is None


def test_put_same_key_overwrites():
    cache = RecoveryCache()
    key = cache_key("com.x", "HomeView", "t", "NOT_FOUND", "fp")
    cache.put(key, [_exp("old")])
    cache.put(key, [_exp("new")])
    assert [e.experience_id for e in cache.get(key)] == ["new"]


def test_cached_hit_still_governed_by_guard():
    """E1 延伸（设计 7.3 第一条，plan 的「Guard 仍执行」）。

    缓存命中只省 Store 的磁盘 lookup；把命中返回的候选喂给真 Guard，
    判定照旧由 Guard 做——本例当前屏与候选屏不符 → `MISS/SCREEN_MISMATCH`
    （4.7：不计样本）。缓存层在 API 形状上就没有「已验证/可跳过」的标记
    （get 返回裸 Experience 列表），「绕过 Guard」不可表达。
    """
    cache = RecoveryCache()
    key = cache_key("com.x", "HomeView", "login_button", "NOT_FOUND", "fp")
    cache.put(key, [_exp("exp_cached")])
    cached = cache.get(key)

    gres = experience_runtime_guard(
        cached[0],
        RuntimeContext(current_screen="OtherView",        # 与候选屏不符
                       expected_type="XCUIElementTypeButton",
                       effective_risk=None,
                       action="tap", element_id="login_button",
                       screen_fingerprint="fp"),
        _FakeFinder(count=0))
    assert gres.outcome == "MISS"
    assert gres.reason == "SCREEN_MISMATCH", "缓存命中不改判——判定仍归 Guard"
    assert gres.record_as_sample is False


def test_cache_returns_copies_not_live_references():
    """缓存完整性：put/get 都是深拷贝——调用方拿到手改（后续引擎记账等）
    不会污染缓存里的正本（「不是权威数据」反过来说：权威在 Store，缓存
    不能被运行时突变悄悄改写）。"""
    cache = RecoveryCache()
    key = cache_key("com.x", "HomeView", "t", "NOT_FOUND", "fp")
    cache.put(key, [_exp("exp_a")])

    returned = cache.get(key)[0]
    returned.sample_count += 99

    assert cache.get(key)[0].sample_count == 0
    assert cache.get(key)[0] is not returned


def test_put_side_copy_isolates_cache_from_source_mutation():
    """P3-3（review_p2_task33）：put 侧对称隔离——调用方 put 之后继续改
    源列表，缓存正本不受影响（防止有人「优化掉」put 侧拷贝）。"""
    cache = RecoveryCache()
    key = cache_key("com.x", "HomeView", "t", "NOT_FOUND", "fp")
    source = [_exp("exp_a")]
    cache.put(key, source)

    source[0].sample_count += 99
    source.append(_exp("intruder"))

    cached = cache.get(key)
    assert len(cached) == 1
    assert cached[0].sample_count == 0
    assert cached[0].experience_id == "exp_a"


def test_put_empty_list_is_not_cached():
    """P3-1（review_p2_task33）：空列表不进缓存——负缓存黑洞的钉子。

    「miss 也缓存」是最常见的缓存陷阱：Store 侧新增候选后，本进程会
    永远看不见且无任何报错。这条行为最容易被后人「顺手修好」（把空
    列表也缓存上），故用测试钉住。
    """
    cache = RecoveryCache()
    key = cache_key("com.x", "HomeView", "t", "NOT_FOUND", "fp")
    cache.put(key, [])
    assert cache.get(key) is None
    assert len(cache) == 0


# --- app_build 不在键里：无失效风暴 ------------------------------------------


def test_app_build_not_in_cache_key_no_wholesale_invalidation():
    """设计 7.3：app_build 变化**不整体清空**——键根本不含 build 段，
    Guard 的 validated_builds 检查自然裁决要不要重新执行验证。"""
    assert cache_key("com.x", "s", "t", "NOT_FOUND", "fp") == \
        cache_key("com.x", "s", "t", "NOT_FOUND", "fp")
    # 语义上：不存在「按 build 失效」的入口；clear() 是显式运维动作
    cache = RecoveryCache()
    key = cache_key("com.x", "s", "t", "NOT_FOUND", "fp")
    cache.put(key, [_exp()])
    cache.clear()          # 只有显式 clear，没有 build-change 钩子
    assert cache.get(key) is None


def test_cache_tracks_hit_miss_counters():
    """加速层要有账可查（Gate M3 的速度梯度验证要用）：命中/未命中计数。"""
    cache = RecoveryCache()
    key = cache_key("com.x", "s", "t", "NOT_FOUND", "fp")
    cache.put(key, [_exp()])
    cache.get(key)
    cache.get(key)
    cache.get("miss")
    assert (cache.hits, cache.misses) == (2, 1)


# --- LRU 容量边界 -------------------------------------------------------------


def test_lru_evicts_oldest_beyond_capacity():
    cache = RecoveryCache(capacity=2)
    k1 = cache_key("a", "s", "t1", "NOT_FOUND", "fp")
    k2 = cache_key("a", "s", "t2", "NOT_FOUND", "fp")
    k3 = cache_key("a", "s", "t3", "NOT_FOUND", "fp")
    cache.put(k1, [_exp("e1")])
    cache.put(k2, [_exp("e2")])
    cache.put(k3, [_exp("e3")])          # 容量 2 → k1 被逐出
    assert cache.get(k1) is None
    assert cache.get(k2) is not None
    assert cache.get(k3) is not None


def test_lru_get_refreshes_recency():
    """LRU 的 U：get 会刷新新近度——最近被用过的不被逐出。"""
    cache = RecoveryCache(capacity=2)
    k1 = cache_key("a", "s", "t1", "NOT_FOUND", "fp")
    k2 = cache_key("a", "s", "t2", "NOT_FOUND", "fp")
    k3 = cache_key("a", "s", "t3", "NOT_FOUND", "fp")
    cache.put(k1, [_exp("e1")])
    cache.put(k2, [_exp("e2")])
    cache.get(k1)                         # k1 变「最近使用」
    cache.put(k3, [_exp("e3")])          # 逐出的是 k2，不是 k1
    assert cache.get(k1) is not None
    assert cache.get(k2) is None


def test_capacity_must_be_positive():
    with pytest.raises(ValueError, match="capacity"):
        RecoveryCache(capacity=0)
