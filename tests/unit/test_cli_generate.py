"""Task 3.5（P3-13）：`mta generate` CLI（设计 6.1 主流程的出口）。

判据（plan Task 3.5 Steps）：参数校验 / 候选入库 / F7 环境闸 / 空 gap 不报错；
以及 handover §9 前置 2（AE）在 CLI 层的复核。

fixture 用**真 git 仓库 + 真 sqlite + 真 trace**（替身会让「列名」「ref 写法」
这类错误静默通过——Task 2.2/2.3 的教训）；LLM 用一个**本地 HTTP 桩**（M2 Gate
同款），因为 `LLMProvider` 是 stdlib urllib 直连、没有可注入的接缝。
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from agents.storage import SQLiteAgentStore
from cli.main import main
from graph.builder import build_runtime_graph, read_trace_steps
from tracer.storage import TraceStore

APP_FILE = "Demo.swift"
_ELEMENT = """schema_version: '1.0'
kind: element
id: {elem}
screen: {screen}
type: button
strategies:
- type: accessibility_id
  value: {elem}
  origin: source
  source_file: {file}
  source_line: 1
metadata:
  origin: source
  risk: LOW
"""

_SCREEN = """schema_version: '1.0'
kind: screen
id: {screen}
marker: screen.{screen}
kind_hint: page
metadata:
  risk: LOW
  origin: source
"""

# 图上的转移：HomeView --tap:HomeView.go_search--> SearchView
_SEED_STEPS = [("launch_app", None), ("wait_for", "screen:HomeView"),
               ("tap", "HomeView.go_search"), ("wait_for", "screen:SearchView")]


def _git(home, cwd, *args) -> str:
    env = dict(os.environ, HOME=str(home))
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                       text=True, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _metadata(git_commit: str) -> dict:
    return {"app_version": "debug", "build": "local", "git_commit": git_commit,
            "screens": ["HomeView", "SearchView"],
            "screen_elements": [
                {"name": "HomeView", "elements": [
                    {"id": "go_search", "accessibility_id": "go_search",
                     "type": "button", "resolution_type": "literal",
                     "source": {"file": APP_FILE, "line": 1}}]},
                {"name": "SearchView", "elements": []}]}


@pytest.fixture()
def proj(tmp_path):
    """自洽的 generate 工程：git 仓库 + generated/local + suites + 预置 trace。"""
    home = tmp_path / "home"
    home.mkdir()
    gen = tmp_path / "repository" / "generated" / "local"
    (gen / "elements").mkdir(parents=True)
    (gen / "screens").mkdir(parents=True)
    (gen / "elements" / "HomeView.yaml").write_text(
        _ELEMENT.format(elem="go_search", screen="HomeView", file=APP_FILE))
    (gen / "screens" / "HomeView.yaml").write_text(
        _SCREEN.format(screen="HomeView"))
    (gen / "screens" / "SearchView.yaml").write_text(
        _SCREEN.format(screen="SearchView"))

    git_root = tmp_path / "git"
    subprocess.run(["git", "init", "-q", str(git_root)], check=True,
                   capture_output=True)
    _git(home, git_root, "config", "user.email", "t@t")
    _git(home, git_root, "config", "user.name", "t")
    (git_root / APP_FILE).write_text("v1\n")
    _git(home, git_root, "add", "-A")
    _git(home, git_root, "commit", "-qm", "base")
    head = _git(home, git_root, "rev-parse", "HEAD").strip()

    (gen / "source_metadata.json").write_text(
        json.dumps(_metadata(head), ensure_ascii=False), encoding="utf-8")

    # 正式用例：只声明 HomeView 到达，**不**声明 HomeView→SearchView（否则无 gap）
    # ⚠️ `suites/` 必须在 **git 根之内**：ACCEPT 落盘要 `git add` 那个文件
    # （promoter 的 E10 前提），仓库外的路径提交不可能成功。
    suites_root = git_root / "suites"
    suites = suites_root / "smoke"
    suites.mkdir(parents=True)
    (suites / "home_001.yaml").write_text(
        'schema_version: "0.2"\nid: home_001\nname: home\nsuite: smoke\n'
        'steps:\n  - action: launch_app\n'
        '  - wait_for:\n      target: screen:HomeView\n'
        '      condition: active\n      timeout: 10\n', encoding="utf-8")
    _git(home, git_root, "add", "-A")
    _git(home, git_root, "commit", "-qm", "suites")

    out = tmp_path / "out"
    out.mkdir()
    trace_db = out / "trace.db"
    store = TraceStore(str(trace_db))
    store.start_run("run_seed", suite="smoke", device_type="simulator",
                    app_build="local", app_bundle_id="com.demo")
    tc_run = store.start_testcase("run_seed", "seed_001")
    for idx, (step_type, target) in enumerate(_SEED_STEPS):
        store.record_step(tc_run, idx, step_type, target_id=target,
                          status="SUCCESS")
    store.end_testcase(tc_run, "PASS")
    store.end_run("run_seed", "PASS", 0)

    agent_db = out / "agent.db"
    SQLiteAgentStore(agent_db).save_plan("plan_test", app_build="local",
                                         git_commit=head, tasks=[])
    return {"tmp": tmp_path, "home": home, "git": git_root, "gen": gen,
            "suites_root": suites_root, "head": head,
            "trace_db": trace_db, "exp_db": out / "experience.db",
            "agent_db": agent_db}


def _argv(proj, *extra) -> list[str]:
    return ["generate", "--gap-from", "plan_test",
            "--repo-root", str(proj["git"]),
            "--generated", str(proj["gen"]),
            "--suites-root", str(proj["suites_root"]),
            "--db", str(proj["trace_db"]),
            "--exp-db", str(proj["exp_db"]),
            "--agent-db", str(proj["agent_db"]),
            "--bundle-id", "com.demo",
            "--suite", "smoke", "--fake-driver", *extra]


# --- 本地 LLM 桩（`LLMProvider` 无注入接缝，M2 Gate 同款做法） ---------------

class _StubLLM(BaseHTTPRequestHandler):
    CONTENT = "{}"

    def do_POST(self):                       # noqa: N802 — http.server 约定
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.dumps({"choices": [
            {"message": {"content": type(self).CONTENT}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture()
def stub_llm(monkeypatch):
    """把 LLM 指到本地桩（`LLM_BASE_URL`/`LLM_API_KEY` 是 provider 的唯一配置面）。"""
    servers = []

    def _serve(content: str) -> None:
        cls = type("_S", (_StubLLM,), {"CONTENT": content})
        srv = HTTPServer(("127.0.0.1", 0), cls)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        host, port = srv.server_address
        monkeypatch.setenv("LLM_BASE_URL", f"http://{host}:{port}/v1")
        monkeypatch.setenv("LLM_API_KEY", "stub")
        monkeypatch.setenv("LLM_MODEL", "stub")
        # provider 走 urllib：环回必须绕开代理（本机 HTTP_PROXY 的老问题）
        monkeypatch.setenv("no_proxy", "127.0.0.1,localhost,::1")
        monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")

    yield _serve
    for srv in servers:
        srv.shutdown()
        srv.server_close()


_CANDIDATE_STEPS = json.dumps({
    "steps": [{"action": "tap", "target": "HomeView.go_search"},
              {"wait_for": {"target": "screen:SearchView",
                            "condition": "active", "timeout": 10}}]})


# --- 图前提自检 ---------------------------------------------------------------


def test_seeded_trace_yields_the_expected_transition(proj):
    """夹具自检：预置 trace 真的建出 HomeView→SearchView 这条转移。

    没有它，下面「生成了候选」的断言可能因为**图是空的**而假绿（空 gap →
    零候选，与「生成失败」长得一样）。
    """
    steps, app_id, build = read_trace_steps(proj["trace_db"])
    graph = build_runtime_graph(steps, app_id=app_id, app_build=build)
    assert [(t.from_screen, t.to_screen, t.trigger)
            for t in graph.transitions] == [
        ("HomeView", "SearchView", "tap:HomeView.go_search")]


# --- 矩阵 #1 / F7 -------------------------------------------------------------


def test_production_is_refused_before_anything_is_written(proj):
    # 用一个**尚不存在**的 agent-db 路径：fixture 自己建过 agent.db（存 plan），
    # 拿它断言「没写东西」会测错对象。F7 前置必须在该文件被创建之前退出。
    fresh = proj["tmp"] / "untouched.db"
    with pytest.raises(SystemExit) as e:
        main(_argv(proj, "--env-kind", "production", "--agent-db", str(fresh)))
    assert "F7" in str(e.value)
    assert not fresh.exists(), "前置在写任何东西之前（连库文件都不该建）"


def test_non_sandbox_is_refused_before_anything_is_written(proj, capsys):
    """矩阵 #9 的**前置**版：staging 下 generate 在写库/装配之前就退出。

    `run_candidate` 的闸门在生成循环**内部**抛（`generate` 不接它）——不前置
    拦，staging 会先入库一部分候选再炸（部分写入）。故这里断言 exit 3 且
    **没有任何候选入库**。
    """
    assert main(_argv(proj, "--env-kind", "staging")) == 3
    out = capsys.readouterr().out
    assert out.startswith("PREFLIGHT ERROR:")
    assert "sandbox" in out
    assert SQLiteAgentStore(proj["agent_db"]).list_candidates() == []


def test_generate_without_bundle_id_takes_it_from_the_trace(proj, stub_llm, capsys):
    """P1-1：不给 `--bundle-id` 时从 trace 归一化的 app_id 取，**不再 traceback**。

    早先 `app_id = args.bundle_id or ""` → trace 是 `com.demo` 时 `graph_query`
    的 fail-loud `ValueError` 直接穿透（一段 traceback），而 `--fake-driver`
    正是最常用的本地路径——工具契约要求前置错误以 exit 3 收场。
    """
    stub_llm(_CANDIDATE_STEPS)
    argv = [a for a in _argv(proj) if a not in ("--bundle-id", "com.demo")]
    assert "--bundle-id" not in argv, "夹具自检：本用例必须真的不给 bundle-id"
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "gaps=1（现场重算）" in out, out
    assert "Traceback" not in out
    assert len(SQLiteAgentStore(proj["agent_db"]).list_candidates()) == 1


def test_generate_with_a_wrong_bundle_id_is_a_preflight_error(proj, stub_llm,
                                                              capsys):
    """与 trace 不符的 `--bundle-id` → 前置错误 exit 3（不是 traceback）。"""
    stub_llm(_CANDIDATE_STEPS)
    argv = ["WRONGAPP" if a == "com.demo" else a for a in _argv(proj)]
    assert main(argv) == 3
    out = capsys.readouterr().out
    assert out.startswith("PREFLIGHT ERROR:") and "app_id" in out, out
    assert SQLiteAgentStore(proj["agent_db"]).list_candidates() == []


def test_empty_trace_is_not_an_error(proj, stub_llm, capsys):
    """trace 为空（新 `--db`）→ 空图 → 零 gap → exit 0（矩阵 #6 语义）。

    「没有历史观测」不等于「工具用错了」——不该因为没给 `--bundle-id` 把它
    变成前置失败（P1-1 的修法不能把这条既有语义改坏）。`--db` 里没有 `runs`
    表时 `graph_query` 会 fail-loud（那是给「库被指错」的诊断，不是给「还没
    跑过」的）——两者由只读探测分开。

    ⚠️ 本命令**自己**会往 `--db` 写它的 Dry Run run 行（口径登记：图源与
    Dry Run 记录同库），故结束时该文件**应当**存在——在这里断言「没建文件」
    是测错对象。
    """
    stub_llm(_CANDIDATE_STEPS)
    fresh = proj["tmp"] / "empty_trace.db"
    argv = list(_argv(proj))
    argv[argv.index("--db") + 1] = str(fresh)
    argv = [a for a in argv if a not in ("--bundle-id", "com.demo")]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "gaps=0" in out and "Traceback" not in out, out
    assert SQLiteAgentStore(proj["agent_db"]).list_candidates() == []


def test_unknown_plan_is_a_preflight_error(proj, capsys):
    assert main(["generate", "--gap-from", "nope",
                 "--agent-db", str(proj["agent_db"]),
                 "--db", str(proj["trace_db"])]) == 3
    assert "PREFLIGHT ERROR" in capsys.readouterr().out


# --- 主路径 -------------------------------------------------------------------


def test_generate_lands_a_candidate(proj, stub_llm, capsys):
    """端到端：gap → LLM → 校验 → Dry Run → `test_candidates` PENDING。"""
    stub_llm(_CANDIDATE_STEPS)
    assert main(_argv(proj)) == 0
    out = capsys.readouterr().out
    assert "gaps=1（现场重算）" in out, out
    rows = SQLiteAgentStore(proj["agent_db"]).list_candidates()
    assert len(rows) == 1, out
    row = rows[0]
    assert row["review_status"] == "PENDING"
    assert row["dry_run_status"] == "PASS", f"Dry Run 应通过（fake driver）：{out}"
    assert row["fingerprint"]
    # 候选声明的套件来自 --suite（ACCEPT 落盘目录靠它）
    import yaml

    from testcase.schema import parse_testcase_dict
    tc = parse_testcase_dict(yaml.safe_load(row["testcase_yaml"]))
    assert tc.suite == "smoke" and tc.status == "CANDIDATE"
    assert tc.generation_evidence.coverage_gap == ["HomeView -> SearchView"]


def test_generate_without_llm_generates_nothing(proj, capsys):
    """`--no-llm` → 不生成（生成必须有 LLM；与 `planner.plan` 的门槛同款）。"""
    assert main(_argv(proj, "--no-llm")) == 0
    assert SQLiteAgentStore(proj["agent_db"]).list_candidates() == []
    assert "未提供 llm+budget" in capsys.readouterr().out


def test_generate_is_idempotent_on_a_pending_candidate(proj, stub_llm, capsys):
    """重跑同 gap → 不重复入库（未决候选进指纹基准，矩阵 #8 的 CLI 级复核）。"""
    stub_llm(_CANDIDATE_STEPS)
    assert main(_argv(proj)) == 0
    capsys.readouterr()
    assert main(_argv(proj)) == 0
    out = capsys.readouterr().out
    store = SQLiteAgentStore(proj["agent_db"])
    assert len(store.list_candidates()) == 1, out
    rejs = store.list_rejections(reason="DUPLICATE")
    assert len(rejs) == 1 and rejs[0]["duplicate_of"], out


# --- handover §9 前置 2（AE）在 CLI 层的复核 ----------------------------------


def test_decided_candidate_is_not_overwritten_by_a_rerun(proj, stub_llm, capsys):
    """AE：ACCEPT 之后重跑 generate → 库里那条候选**逐字节不变**。"""
    stub_llm(_CANDIDATE_STEPS)
    assert main(_argv(proj)) == 0
    capsys.readouterr()
    store = SQLiteAgentStore(proj["agent_db"])
    cid = store.list_candidates()[0]["candidate_id"]
    before = store.get_candidate(cid)["testcase_yaml"]
    store.decide_candidate(cid, review_status="ACCEPT", reviewer="op")

    assert main(_argv(proj)) == 0
    out = capsys.readouterr().out
    assert store.get_candidate(cid)["testcase_yaml"] == before, out
    assert store.get_candidate(cid)["review_status"] == "ACCEPT"
    assert "已人工决策" in out


# --- mta candidate review（F6 的 CLI 面） ------------------------------------


def _cand_argv(proj, *extra) -> list[str]:
    return ["candidate", "review", *extra,
            "--db", str(proj["agent_db"])]


def test_candidate_list_shows_pending(proj, stub_llm, capsys):
    stub_llm(_CANDIDATE_STEPS)
    assert main(_argv(proj)) == 0
    capsys.readouterr()
    assert main(_cand_argv(proj, "list")) == 0
    out = capsys.readouterr().out
    assert "[PENDING]" in out and "dry=PASS" in out and "suite=smoke" in out
    assert "candidate review: 1 条" in out


def test_candidate_accept_lands_the_file(proj, stub_llm, capsys):
    """CLI 层端到端：accept → suites/<suite>/<id>.yaml + commit + 库 ACCEPT。"""
    stub_llm(_CANDIDATE_STEPS)
    assert main(_argv(proj)) == 0
    capsys.readouterr()
    store = SQLiteAgentStore(proj["agent_db"])
    cid = store.list_candidates()[0]["candidate_id"]
    assert main(["candidate", "review", "accept", cid,
                 "--db", str(proj["agent_db"]),
                 "--suites-root", str(proj["suites_root"]),
                 "--generated", str(proj["gen"]),
                 "--repo-root", str(proj["git"]),
                 "--reviewer", "op"]) == 0
    out = capsys.readouterr().out
    assert "ACCEPT" in out and "git revert" in out
    target = proj["suites_root"] / "smoke" / f"{cid}.yaml"
    assert target.exists()
    assert store.get_candidate(cid)["review_status"] == "ACCEPT"
    # 落盘的用例能被 P1 发现（设计 6.1「不是新路径」）
    from cli.pipeline import SessionPipeline
    ids = {c.id for c in SessionPipeline(
        suites_root=proj["suites_root"]).discover()}
    assert cid in ids


def test_candidate_reject_requires_note_at_the_parser(proj, stub_llm, capsys):
    """`--note` 是 parser 级 required（P2 `rev_rej` 同款）→ SystemExit(2)。"""
    stub_llm(_CANDIDATE_STEPS)
    assert main(_argv(proj)) == 0
    capsys.readouterr()
    cid = SQLiteAgentStore(proj["agent_db"]).list_candidates()[0]["candidate_id"]
    with pytest.raises(SystemExit):
        main(["candidate", "review", "reject", cid,
              "--db", str(proj["agent_db"])])


def test_candidate_reject_lands_the_note(proj, stub_llm, capsys):
    stub_llm(_CANDIDATE_STEPS)
    assert main(_argv(proj)) == 0
    capsys.readouterr()
    store = SQLiteAgentStore(proj["agent_db"])
    cid = store.list_candidates()[0]["candidate_id"]
    assert main(["candidate", "review", "reject", cid, "--note", "重复",
                 "--db", str(proj["agent_db"]), "--reviewer", "op"]) == 0
    assert store.get_candidate(cid)["review_note"] == "重复"
    assert not (proj["suites_root"] / "smoke" / f"{cid}.yaml").exists()


def test_candidate_accept_with_a_second_decision_is_refused(proj, stub_llm,
                                                            capsys):
    stub_llm(_CANDIDATE_STEPS)
    assert main(_argv(proj)) == 0
    capsys.readouterr()
    store = SQLiteAgentStore(proj["agent_db"])
    cid = store.list_candidates()[0]["candidate_id"]
    store.decide_candidate(cid, review_status="REJECT", reviewer="op",
                           note="先拒了")
    assert main(["candidate", "review", "accept", cid,
                 "--db", str(proj["agent_db"]),
                 "--suites-root", str(proj["suites_root"]),
                 "--generated", str(proj["gen"]),
                 "--repo-root", str(proj["git"])]) == 3
    assert "不可二次决策" in capsys.readouterr().out


def test_candidate_list_rejections_shows_skip_trace(proj, stub_llm, capsys):
    """矩阵 #8 的 CLI 面：`--rejections` 能看到 SKIP 的 `duplicate_of`。"""
    stub_llm(_CANDIDATE_STEPS)
    assert main(_argv(proj)) == 0
    assert main(_argv(proj)) == 0          # 第二次 → DUPLICATE 留痕
    capsys.readouterr()
    assert main(_cand_argv(proj, "list", "--rejections")) == 0
    out = capsys.readouterr().out
    assert "reason=DUPLICATE" in out and "duplicate_of=" in out


def test_candidate_unknown_verb_returns_2(proj, capsys):
    """未知审核动词 → exit 2，但**由 argparse 挡**，不是 handler 的分支。

    ⚠️ 早先 handler 里有两个「未知动词 → return 2」的分支，实测**不可达**
    （子解析器 `required=True` 先抛 `SystemExit(2)`；探针把两处改成 `0` 得
    0 red）。故本测试断言的是 SystemExit(2) 这一**真实**的拦截点
    （review_p3_task35 P3-3）。
    """
    with pytest.raises(SystemExit) as e:
        main(["candidate", "review", "nope", "--db", str(proj["agent_db"])])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e2:
        main(["candidate", "nope"])
    assert e2.value.code == 2


def test_candidate_reject_missing_candidate_is_exit_3(proj, capsys):
    """P3-2：reject 的**错误映射**也要有 CLI 级钉子（先前零覆盖）。

    accept 侧有「不可二次决策」钉住映射，reject 侧一条都没有——把
    `return 3` 改成别的值曾经 0 red。这里用「候选不存在」这条最易达的错误面。
    """
    assert main(["candidate", "review", "reject", "nope", "--note", "理由",
                 "--db", str(proj["agent_db"])]) == 3
    assert "不存在" in capsys.readouterr().out


def test_candidate_reject_of_a_decided_candidate_is_exit_3(proj, stub_llm,
                                                           capsys):
    """reject 面的第二条映射：已决候选（与 accept 侧同款断言）。"""
    stub_llm(_CANDIDATE_STEPS)
    assert main(_argv(proj)) == 0
    capsys.readouterr()
    store = SQLiteAgentStore(proj["agent_db"])
    cid = store.list_candidates()[0]["candidate_id"]
    store.decide_candidate(cid, review_status="ACCEPT", reviewer="op")
    assert main(["candidate", "review", "reject", cid, "--note", "改主意",
                 "--db", str(proj["agent_db"])]) == 3
    assert "不可二次决策" in capsys.readouterr().out


def test_candidate_list_on_an_unreadable_db_is_exit_3(proj, capsys):
    """P3-4：`sqlite3.Error` **不是** `RuntimeError`——库读不了要 exit 3。

    早先 `--db` 指向一个目录时裸 `OperationalError` 直接漏成 traceback。
    """
    a_dir = proj["tmp"] / "a_directory"
    a_dir.mkdir()
    assert main(["candidate", "review", "list", "--db", str(a_dir)]) == 3
    out = capsys.readouterr().out
    assert out.startswith("CANDIDATE ERROR:"), out


def test_candidate_export_patch_with_broken_yaml_is_exit_3(proj, stub_llm,
                                                           capsys):
    """P3-5：`--export-patch` 遇坏 YAML 与 ACCEPT 路径同一处置（exit 3，不是 traceback）。"""
    stub_llm(_CANDIDATE_STEPS)
    assert main(_argv(proj)) == 0
    capsys.readouterr()
    store = SQLiteAgentStore(proj["agent_db"])
    cid = store.list_candidates()[0]["candidate_id"]
    with store._write_tx() as conn:            # 人工/外部把文本写坏
        conn.execute("UPDATE test_candidates SET testcase_yaml=? WHERE"
                     " candidate_id=?", ("{ not: yaml: [", cid))
    assert main(["candidate", "review", "accept", cid, "--export-patch",
                 "--db", str(proj["agent_db"]),
                 "--suites-root", str(proj["suites_root"])]) == 3
    out = capsys.readouterr().out
    assert out.startswith("CANDIDATE ERROR:") and "解析失败" in out, out


def test_generate_failure_ends_the_run_row_as_aborted(proj, stub_llm, capsys,
                                                      monkeypatch):
    """P3-8：生成器抛异常 → run 行必须收口为 ABORTED（不是留 `RUNNING`）。

    早先 `end_run` 在 try/finally 的 finally **之外**——异常穿透时 trace 里
    留下 `status='RUNNING'` / `end_time=NULL` 的持久化「跑了一半」痕迹。
    """
    import generator.generator as gen_mod

    def boom(*a, **kw):
        raise RuntimeError("生成器炸了")

    monkeypatch.setattr(gen_mod, "generate", boom)
    stub_llm(_CANDIDATE_STEPS)
    assert main(_argv(proj)) == 3
    out = capsys.readouterr().out
    assert "生成失败" in out and "Traceback" not in out, out
    from tracer.storage import TraceStore

    conn = TraceStore(str(proj["trace_db"])).conn
    rows = conn.execute("SELECT status, end_time FROM runs WHERE"
                        " run_id LIKE 'gen_%'").fetchall()
    assert rows, "应留下一条 gen_* 的 run 行"
    assert all(r["status"] == "ABORTED" and r["end_time"] for r in rows), \
        [dict(r) for r in rows]


def test_generator_run_ids_are_recorded(proj, stub_llm, capsys):
    """口径登记：Dry Run 的步骤**写进**作为图源的同一 trace 库（P3-7）。

    图来自 `--db` 的 trace，而 `generate` 自己又往它写 Dry Run 的观测——
    这条关系是**刻意的**（Dry Run 是真实执行，观测理应进 trace），但审计
    必须显式登记它，否则「图源被自己改写」是个看不见的口径。本测试把现状
    **钉住**（改成就地沉默才是回归）。
    """
    stub_llm(_CANDIDATE_STEPS)
    assert main(_argv(proj)) == 0
    capsys.readouterr()
    from tracer.storage import TraceStore

    conn = TraceStore(str(proj["trace_db"])).conn
    n = conn.execute("SELECT COUNT(*) FROM runs WHERE run_id LIKE 'gen_%'"
                     ).fetchone()[0]
    assert n == 1, f"generate 应在图源库里留下自己的 run 行，实得 {n}"
