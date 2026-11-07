"""Tests sur les vraies API : lancés seulement avec « pytest -m live ».

Les clés viennent des variables d'environnement (secrets GitHub en CI) ou, en local,
du .env à la racine du dépôt. Un fournisseur sans clé est ignoré (skip), pas en échec.
"""
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Remplace la fixture de tests/conftest.py : vraies clés, mais dossier et log temporaires."""
    load_dotenv(ROOT / ".env", override=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("autoroute.observability.LOG_PATH", tmp_path / "log.jsonl")
    for var in ("AUTOROUTE_LOG", "AUTOROUTE_CONFIG", "AUTOROUTE_MODE"):
        monkeypatch.delenv(var, raising=False)
