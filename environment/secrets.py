"""SecretProvider（Stage 5）。ponytail: 接口 + Env 实现，Vault 等留到 V1。"""

import os


class SecretProvider:
    def get(self, key: str) -> str:
        raise NotImplementedError


class EnvSecretProvider(SecretProvider):
    def get(self, key: str) -> str:
        val = os.environ.get(key)
        if val is None:
            raise KeyError(f"secret {key} not found")
        return val
