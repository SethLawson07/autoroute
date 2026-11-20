import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from autoroute.config import AUTOROUTE_DIR, ensure_parent, load_env

LOG_PATH = AUTOROUTE_DIR / "log.jsonl"
SCHEMA_VERSION = "3"

logger = logging.getLogger(__name__)

# Logs déjà signalés comme impossibles à écrire : un seul WARNING par chemin, pas un par routage.
_unwritable: set[str] = set()


# Valeurs spéciales de AUTOROUTE_LOG (sinon : un chemin de fichier).
STDOUT = "stdout"  # une ligne JSON par décision sur la sortie standard (logs du conteneur)
OFF = "off"        # aucune écriture


def _log_target() -> Path | str:
    """Où écrire les décisions : un fichier (Path), STDOUT ou OFF."""
    load_env()
    value = (os.getenv("AUTOROUTE_LOG") or "").strip()
    if value.lower() in (STDOUT, OFF):
        return value.lower()
    return Path(value or LOG_PATH)


def _log_path() -> Path:
    """Le fichier de log. Lève ValueError si AUTOROUTE_LOG vaut stdout ou off (pas de fichier)."""
    target = _log_target()
    if isinstance(target, str):
        raise ValueError(f"AUTOROUTE_LOG={target} : aucun fichier de log. Indique un fichier avec --log.")
    return target


def _write(event: dict) -> None:
    target = _log_target()
    if target == OFF:
        return
    event = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **event,
    }
    line = json.dumps(event, ensure_ascii=False) + "\n"
    if target == STDOUT:
        try:
            sys.stdout.write(line)
            sys.stdout.flush()  # visible tout de suite dans les logs du conteneur
        except (OSError, ValueError, UnicodeEncodeError):
            pass  # sortie fermée ou mal encodée : principe 8, on n'interrompt pas route()
        return
    path = target
    try:
        ensure_parent(path)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
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
