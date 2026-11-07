from pathlib import Path

CONFIG = Path(".autoroute/config.yaml")


def write_config(text: str) -> None:
    """Écrit .autoroute/config.yaml dans le dossier courant (temporaire pendant les tests)."""
    CONFIG.parent.mkdir(exist_ok=True)
    CONFIG.write_text(text, encoding="utf-8")
