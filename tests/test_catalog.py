import pytest

from helpers import write_config

from autoroute import catalog
from autoroute.config import ModelConfig


def models():
    return [
        ModelConfig(name="gpt-4o-mini", provider="openai"),
        ModelConfig(name="claude-sonnet-4-6", provider="anthropic"),
    ]


def test_dedupe_keeps_alias_else_latest_dated():
    ids = [
        "gpt-4o",
        "gpt-4o-2024-08-06",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-5-20250101",
        "claude-opus-4-5-20251101",
    ]
    assert catalog._dedupe_dated(ids) == [
        "claude-haiku-4-5-20251001",
        "claude-opus-4-5-20251101",
        "gpt-4o",
    ]


def test_openai_filter_keeps_only_chat_models(monkeypatch):
    monkeypatch.setattr(
        catalog,
        "list_models",
        lambda provider: ["gpt-4o", "text-embedding-3-small", "gpt-4o-audio-preview", "whisper-1", "o3"],
    )
    assert catalog.list_chat_models("openai") == ["gpt-4o", "o3"]


def test_gemini_filter_drops_latest_aliases(monkeypatch):
    # Les alias « -latest » de Gemini répondent 404 via LangChain : ni orchestrateur ni choix final.
    monkeypatch.setattr(
        catalog,
        "list_models",
        lambda provider: ["gemini-3.8-flash", "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-pro-latest"],
    )
    assert catalog.list_chat_models("google_genai") == ["gemini-3.8-flash"]


def test_resolve_by_name():
    assert catalog._resolve_orchestrator("gpt-4o-mini", models()) == "openai:gpt-4o-mini"


def test_resolve_qualified():
    assert catalog._resolve_orchestrator("anthropic:claude-sonnet-4-6", models()) == "anthropic:claude-sonnet-4-6"


def test_resolve_unknown_raises():
    with pytest.raises(ValueError, match="introuvable"):
        catalog._resolve_orchestrator("inconnu", models())


def test_resolve_ambiguous_raises():
    both = [ModelConfig(name="x", provider="openai"), ModelConfig(name="x", provider="anthropic")]
    with pytest.raises(ValueError, match="plusieurs"):
        catalog._resolve_orchestrator("x", both)


def test_discover_config(monkeypatch):
    write_config("orchestrators:\n  all: gpt-4o-mini\n")
    monkeypatch.setattr(
        catalog,
        "list_chat_models",
        lambda provider: {"openai": ["gpt-4o-mini"], "anthropic": ["claude-sonnet-4-6"]}[provider],
    )
    config = catalog.discover_config()
    assert config.orchestrator == "openai:gpt-4o-mini"
    assert [m.spec for m in config.models] == ["openai:gpt-4o-mini", "anthropic:claude-sonnet-4-6"]


def test_discover_without_keys_raises(monkeypatch):
    write_config("orchestrators:\n  all: gpt-4o-mini\n")
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(ValueError, match="Aucun modèle"):
        catalog.discover_config()

def fake_models(monkeypatch, by_provider):
    """Clés présentes pour les fournisseurs de by_provider, absentes pour les autres."""
    monkeypatch.setattr(catalog, "get_api_key", lambda p: "fake-key" if p in by_provider else None)
    monkeypatch.setattr(catalog, "list_chat_models", lambda p: by_provider[p])


def pick(names, provider="anthropic"):
    return catalog._pick_orchestrators(
        "all", [ModelConfig(name=n, provider=provider) for n in names]
    )


def test_orchestrator_is_newest_light_model():
    assert pick(["claude-opus-5-5", "claude-haiku-4-5-20251001", "claude-haiku-5-5"])[0]         == "anthropic:claude-haiku-5-5"


def test_orchestrator_follows_new_versions():
    # Pas de nom figé : une nouvelle version est prise sans changer le code.
    names = ["gpt-4o-mini", "gpt-4.1-mini", "gpt-5", "gpt-7-mini"]
    assert pick(names, "openai")[0] == "openai:gpt-7-mini"


def test_orchestrator_family_is_a_whole_word():
    # « gemini » contient « mini » mais n'est pas un petit modèle : flash l'emporte.
    names = ["gemini-3-pro", "gemini-2.5-flash"]
    assert pick(names, "google_genai")[0] == "google_genai:gemini-2.5-flash"


def test_orchestrator_prefers_stable_and_latest():
    assert pick(["mistral-small-2506", "mistral-small-latest"], "mistralai")[0]         == "mistralai:mistral-small-latest"
    assert pick(["gemini-3-flash-preview", "gemini-2.5-flash"], "google_genai")[0]         == "google_genai:gemini-2.5-flash"


def test_orchestrator_without_light_family_still_picked():
    assert pick(["claude-opus-5-5"]) == ["anthropic:claude-opus-5-5"]


def test_backup_orchestrators_spread_over_providers(monkeypatch):
    fake_models(monkeypatch, {
        "openai": ["gpt-5", "gpt-5-mini"],
        "anthropic": ["claude-haiku-5-5", "claude-opus-5-5"],
    })
    config = catalog.discover_config()
    assert config.orchestrators == [
        "openai:gpt-5-mini", "anthropic:claude-haiku-5-5", "openai:gpt-5",
    ]


def test_orchestrator_with_only_anthropic_key(monkeypatch):
    fake_models(monkeypatch, {"anthropic": ["claude-haiku-5-5", "claude-sonnet-4-6"]})
    assert catalog.discover_config().orchestrator == "anthropic:claude-haiku-5-5"


def test_orchestrator_in_sovereign_mode(monkeypatch):
    fake_models(monkeypatch, {"openai": ["gpt-4o-mini"], "mistralai": ["mistral-small-latest"]})
    config = catalog.discover_config(mode="sovereign")
    assert all(o.startswith("mistralai:") for o in config.orchestrators)


def test_user_orchestrator_overrides_automatic(monkeypatch):
    write_config("orchestrators:\n  all: anthropic:claude-sonnet-4-6\n")
    fake_models(monkeypatch, {
        "openai": ["gpt-4o-mini"],
        "anthropic": ["claude-sonnet-4-6"],
    })
    config = catalog.discover_config()
    assert config.orchestrator == "anthropic:claude-sonnet-4-6"
    assert config.backup_orchestrators == ["openai:gpt-4o-mini"]


def test_unreachable_user_orchestrator_warns_and_uses_automatic(monkeypatch):
    write_config("orchestrators:\n  all: openai:modele-retire\n")
    fake_models(monkeypatch, {"openai": ["gpt-5-mini"]})
    with pytest.warns(UserWarning, match="modele-retire"):
        config = catalog.discover_config()
    assert config.orchestrator == "openai:gpt-5-mini"


def test_unexpected_api_response_is_a_clean_error(monkeypatch):
    def changed_format(provider):
        return {}["data"]  # KeyError : le fournisseur a changé sa réponse

    monkeypatch.setattr(catalog, "list_models", changed_format)
    with pytest.raises(ValueError, match="réponse inattendue"):
        catalog.list_chat_models("openai")


def test_unexpected_api_response_skips_only_that_provider(monkeypatch):
    def list_models(provider):
        if provider == "openai":
            raise KeyError("data")
        return ["claude-haiku-5-5"]

    monkeypatch.setattr(catalog, "list_models", list_models)
    with pytest.warns(UserWarning, match="openai : réponse inattendue"):
        config = catalog.discover_config()
    assert [m.spec for m in config.models] == ["anthropic:claude-haiku-5-5"]
