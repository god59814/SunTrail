from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from typing import Any

import numpy as np
import requests

OLLAMA_BASE_URL = "http://localhost:11434/api"
EMBED_MODEL = "qwen3-embedding:0.6b"
CHAT_MODEL = "qwen3:4b"
EMBED_BATCH_SIZE = 128
EMBED_REQUEST_TIMEOUT = 300.0
OLLAMA_KEEP_ALIVE = "30m"
OLLAMA_READY_TIMEOUT = 90.0


class OllamaClassifier:
    def __init__(
        self,
        ollama_base_url: str = OLLAMA_BASE_URL,
        embed_model: str = EMBED_MODEL,
        chat_model: str = CHAT_MODEL,
        keep_alive: str = OLLAMA_KEEP_ALIVE,
    ) -> None:
        self.ollama_base_url = ollama_base_url.rstrip("/")
        self.embed_model = embed_model
        self.chat_model = chat_model
        self.keep_alive = keep_alive
        self.auto_start_ollama = (os.environ.get("AUTO_START_OLLAMA", "1").strip() != "0")
        self.ollama_ready_timeout = float(
            os.environ.get("OLLAMA_READY_TIMEOUT", str(OLLAMA_READY_TIMEOUT)).strip()
            or OLLAMA_READY_TIMEOUT
        )
        self._ollama_start_attempted = False

    def _tags_url(self) -> str:
        if self.ollama_base_url.endswith("/api"):
            return f"{self.ollama_base_url}/tags"
        return f"{self.ollama_base_url}/api/tags"

    def ensure_ollama_ready(self) -> None:
        tags_url = self._tags_url()

        def _is_ready() -> bool:
            try:
                r = requests.get(tags_url, timeout=2.0)
                return r.status_code == 200
            except requests.RequestException:
                return False

        if _is_ready():
            return

        if self.auto_start_ollama and not self._ollama_start_attempted:
            self._ollama_start_attempted = True
            ollama_bin = shutil.which("ollama")
            if ollama_bin:
                kwargs: dict[str, Any] = {
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                }
                if sys.platform.startswith("win"):
                    kwargs["creationflags"] = (
                        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                        | getattr(subprocess, "DETACHED_PROCESS", 0)
                    )
                subprocess.Popen([ollama_bin, "serve"], **kwargs)

        deadline = time.time() + max(1.0, self.ollama_ready_timeout)
        while time.time() < deadline:
            if _is_ready():
                return
            time.sleep(1.0)

        raise RuntimeError(
            "Ollama is not ready. Please start it manually with `ollama serve` "
            "or increase OLLAMA_READY_TIMEOUT."
        )

    def embed_texts(
        self,
        texts: list[str],
        *,
        batch_size: int = EMBED_BATCH_SIZE,
        timeout: float = EMBED_REQUEST_TIMEOUT,
    ) -> list[np.ndarray]:
        self.ensure_ollama_ready()
        if not texts:
            return []
        out: list[np.ndarray] = []
        for start in range(0, len(texts), batch_size):
            chunk = texts[start : start + batch_size]
            resp = requests.post(
                f"{self.ollama_base_url}/embed",
                json={
                    "model": self.embed_model,
                    "input": chunk,
                    "keep_alive": self.keep_alive,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            embeddings = data.get("embeddings", [])
            if len(embeddings) != len(chunk):
                raise RuntimeError(
                    f"embed count mismatch: expected {len(chunk)}, got {len(embeddings)}"
                )
            out.extend(np.array(vec, dtype=np.float32) for vec in embeddings)
        return out

    def chat_json(
        self, system_prompt: str, user_prompt: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.ensure_ollama_ready()
        del schema
        resp = requests.post(
            f"{self.ollama_base_url}/chat",
            json={
                "model": self.chat_model,
                "stream": False,
                "think": False,
                "keep_alive": self.keep_alive,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=180,
        )
        resp.raise_for_status()
        data = resp.json()
        return {"raw_output": data["message"]["content"]}

