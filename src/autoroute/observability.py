import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from autoroute.config import AUTOROUTE_DIR, ensure_parent, load_env

LOG_PATH = AUTOROUTE_DIR / "log.jsonl"
SCHEMA_VERSION = "2"


def _log_path() -> Path:
    load_env()
    return Path(os.getenv("AUTOROUTE_LOG") or LOG_PATH)


def _write(event: dict) -> None:
    event = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **event,
    }
    path = _log_path()
    ensure_parent(path)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _error_text(error: BaseException) -> str:
    msg = " ".join(str(error).split())
    return f"{type(error).__name__}: {msg[:300]}"


def log_decision(
    *,
    decision_id: str = "",
    task: str,
    orchestrator: str,
    model: str,
    candidates: list[str] | None = None,
    reason: str,
    invalid_choice: bool,
    routing_failed: bool = False,
    latency_ms: int,
    mode: str = "all",
    error: str | None = None,
) -> None:
    """Une ligne "decision" : ce que l'orchestrateur a choisi, et pourquoi.

    Paramètres nommés uniquement, dans l'ordre des champs du log.
    """
    _write({
        "event": "decision",
        "decision_id": decision_id,
        "task": task,
        "orchestrator": orchestrator,
        "model": model,
        "candidates": candidates or [],
        "reason": reason,
        "invalid_choice": invalid_choice,
        "routing_failed": routing_failed,
        "latency_ms": latency_ms,
        "mode": mode,
        "error": error,
    })


class OutcomeTracker(BaseCallbackHandler):
    """Callback LangChain : observe l'appel au modèle final, sans le faire.

    À chaque fin d'appel (invoke de l'utilisateur), écrit une ligne "outcome" liée à la
    décision : quel modèle a vraiment répondu, combien de replis ont servi, quelles
    tentatives ont échoué, et les tokens consommés.
    """

    raise_error = False  # un bug d'observabilité ne doit jamais casser l'appel

    def __init__(self, decision_id: str, candidates: list[str]):
        self.decision_id = decision_id
        self.candidates = candidates
        self._reset()

    def _reset(self) -> None:
        self._root: UUID | None = None
        self._start = 0.0
        self._models: dict[UUID, str] = {}
        self._attempts: list[dict] = []

    # --- suivi de l'appel racine (with_fallbacks, agent, ou modèle seul) ---

    def _enter(self, run_id: UUID, parent_run_id: UUID | None) -> None:
        if parent_run_id is None and self._root is None:
            self._root = run_id
            self._start = time.perf_counter()

    def _exit(self, run_id: UUID, error: BaseException | None = None) -> None:
        if run_id == self._root:
            self._flush(error)

    def _spec(self, model_name: str | None) -> str:
        """Retrouve "provider:name" parmi les candidats à partir du nom vu par LangChain."""
        if not model_name:
            return "unknown"
        for spec in self.candidates:
            if spec.split(":", 1)[-1] == model_name or spec == model_name:
                return spec
        return model_name

    # --- callbacks LangChain ---

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs) -> None:
        self._enter(run_id, parent_run_id)

    def on_chain_end(self, outputs, *, run_id, parent_run_id=None, **kwargs) -> None:
        self._exit(run_id)

    def on_chain_error(self, error, *, run_id, parent_run_id=None, **kwargs) -> None:
        self._exit(run_id, error)

    def on_chat_model_start(
        self, serialized, messages, *, run_id, parent_run_id=None, metadata=None, **kwargs
    ) -> None:
        self._enter(run_id, parent_run_id)
        self._models[run_id] = self._spec((metadata or {}).get("ls_model_name"))

    def on_llm_end(self, response, *, run_id, parent_run_id=None, **kwargs) -> None:
        self._attempts.append({
            "model": self._models.get(run_id, "unknown"),
            "ok": True,
            "usage": _usage(response),
        })
        self._exit(run_id)

    def on_llm_error(self, error, *, run_id, parent_run_id=None, **kwargs) -> None:
        self._attempts.append({
            "model": self._models.get(run_id, "unknown"),
            "ok": False,
            "error": _error_text(error),
        })
        self._exit(run_id, error)

    # --- écriture ---

    def _flush(self, error: BaseException | None) -> None:
        primary = self.candidates[0] if self.candidates else None
        served = [a for a in self._attempts if a["ok"]]
        _write({
            "event": "outcome",
            "decision_id": self.decision_id,
            "primary": primary,
            "served_by": served[-1]["model"] if served else None,
            "calls": len(served),
            "fallbacks_used": sum(1 for a in served if a["model"] != primary),
            "failures": [
                {"model": a["model"], "error": a["error"]} for a in self._attempts if not a["ok"]
            ],
            "usage": {
                "input_tokens": sum(a["usage"]["input_tokens"] for a in served),
                "output_tokens": sum(a["usage"]["output_tokens"] for a in served),
            },
            "latency_ms": int((time.perf_counter() - self._start) * 1000),
            "error": _error_text(error) if error else None,
        })
        self._reset()


def _usage(response: Any) -> dict:
    """Tokens d'une réponse LangChain (usage_metadata standard), 0 si absents."""
    total = {"input_tokens": 0, "output_tokens": 0}
    for generations in getattr(response, "generations", []) or []:
        for gen in generations:
            meta = getattr(getattr(gen, "message", None), "usage_metadata", None) or {}
            total["input_tokens"] += int(meta.get("input_tokens") or 0)
            total["output_tokens"] += int(meta.get("output_tokens") or 0)
    return total
