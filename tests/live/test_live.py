"""Qualification avec les vraies API : découverte et routage, sans appeler le modèle final.

Coût : un appel d'orchestrateur par test de routage (petit modèle, quelques centimes au plus).
"""
import json

import pytest

from autoroute import observability, route_with_reason
from autoroute.catalog import discover_config, list_chat_models
from autoroute.config import ENV_KEYS, MODES, get_api_key

pytestmark = pytest.mark.live

# Le mode qui n'active que ce fournisseur.
PROVIDER_MODE = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google_genai": "gemini",
    "mistralai": "sovereign",
}


def need_key(provider: str) -> None:
    if get_api_key(provider) is None:
        pytest.skip(f"{ENV_KEYS[provider]} absente")


def last_decision() -> dict:
    lines = observability.LOG_PATH.read_text(encoding="utf-8").splitlines()
    return [e for e in map(json.loads, lines) if e["event"] == "decision"][-1]


@pytest.mark.parametrize("provider", list(PROVIDER_MODE))
def test_discovery_finds_chat_models(provider):
    need_key(provider)
    models = list_chat_models(provider)
    print(f"\n{provider} : {len(models)} modèles")
    assert models


@pytest.mark.parametrize("provider", list(PROVIDER_MODE))
def test_routing_in_single_provider_mode(provider):
    need_key(provider)
    mode = PROVIDER_MODE[provider]
    config = discover_config(mode=mode)
    result = route_with_reason("Dis bonjour en une phrase.", config)
    event = last_decision()
    print(
        f"\n{mode} : orchestrateur {event['orchestrator']} -> {result.model} "
        f"({event['latency_ms']} ms, invalid_choice={result.invalid_choice})"
    )
    assert not result.routing_failed, event["error"]
    # Le mode est respecté : aucun modèle d'un autre fournisseur (souveraineté).
    assert all(c.startswith(f"{provider}:") for c in result.candidates)


def test_routing_in_all_mode_is_logged():
    if not any(get_api_key(p) for p in MODES["all"]):
        pytest.skip("aucune clé API")
    result = route_with_reason("Écris une fonction Python qui trie une liste de dictionnaires.")
    event = last_decision()
    print(f"\nall : {event['orchestrator']} -> {result.candidates} ({event['latency_ms']} ms)")
    assert not result.routing_failed, event["error"]
    assert event["decision_id"] == result.decision_id
    assert event["mode"] == "all"
    assert event["latency_ms"] < 60_000
