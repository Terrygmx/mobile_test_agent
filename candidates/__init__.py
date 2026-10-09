"""candidates/ — 候选资产（设计 4 节的 Candidate Tier；Task 3.3 起）。

P3 的「候选资产」是 Agent 可以**创建**、但必须经人工 Accept 才能成为正式资产
（F6）的一类中间物：Candidate Test（Task 3.4/3.5）、Discovery Event（M4）、
Bug Candidate（M5）。

本包当前只有 `fingerprint.py`（Test Fingerprint，F8）。表读写（`test_case.py` /
`discovery.py` / `bug.py`）与统一审核（`review.py`）随各自任务落地——**不在本
文件预建空壳**（P2 Task 1.2「表结构随实现定型」的教训）。
"""
