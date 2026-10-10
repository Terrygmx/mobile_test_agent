"""candidates/ — 候选资产（设计 4 节的 Candidate Tier；Task 3.3 起）。

P3 的「候选资产」是 Agent 可以**创建**、但必须经人工 Accept 才能成为正式资产
（F6）的一类中间物：Candidate Test（Task 3.4/3.5）、Discovery Event（M4）、
Bug Candidate（M5）。

本包现有三块：

- `fingerprint.py`（Test Fingerprint，F8；Task 3.3）；
- `test_case.py`（`test_candidates` / `test_candidate_rejections` 的行模型，
  Task 3.4）——**SQL 不在本包**（单写者 = `agents/storage.py`）；
- `review.py`（统一审核 `decide_candidate` + ACCEPT 落盘，F6 首个消费者，
  Task 3.5）——M4 的 Discovery Event / M5 的 Bug Candidate 复用同一入口。

`discovery.py` / `bug.py` 随各自任务落地——**不在本文件预建空壳**
（P2 Task 1.2「表结构随实现定型」的教训）。
"""
