import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from autoroute.config import AUTOROUTE_DIR, ensure_parent, load_env

LOG_PATH = AUTOROUTE_DIR / "log.jsonl"
SCHEMA_VERSION = "3"

logger = logging.getLogger(__name__)

# Logs déjà signalés comme impossibles à écrire : un seul WARNING par chemin, pas un par routage.
_unwritable: set[str] = set()


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
    try:
        ensure_parent(path)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as e:
        # Principe 8 : un log impossible à écrire (conteneur en lecture seule, droits)
        # ne doit pas faire planter route(). La décision est perdue, pas l'application.
        if str(path) not in _unwritable:
            _unwritable.add(str(path))
            logger.warning(
                f"Log {path.as_posix()} impossible à écrire ({type(e).__name__}) : "
                "décisions non enregistrées. Change AUTOROUTE_LOG vers un dossier accessible en écriture."
            )


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
