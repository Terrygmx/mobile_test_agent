"""LLMBudget — Circuit Breaker（Stage 8，设计文档 4.8.1 原样实现）。"""


class LLMBudget:
    def __init__(self, max_calls_per_run: int = 5):
        self.max_calls_per_run = max_calls_per_run
        self._used = 0

    def try_acquire(self) -> bool:
        if self._used >= self.max_calls_per_run:
            return False
        self._used += 1
        return True
