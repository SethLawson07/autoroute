import json
import logging
import warnings

import httpx
import pytest

from autoroute import observability
from autoroute.config import ModelConfig
from autoroute.router import (
    MAX_CANDIDATES, ORCHESTRATOR_RETRIES, ORCHESTRATOR_TIMEOUT, Decision, Route, route,
)


def read_log():
    lines = observability.LOG_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def warnings_of(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


@pytest.fixture
def config3(config):
    config.models.append(ModelConfig(name="claude-haiku-5-5", provider="anthropic"))
    config.models.append(ModelConfig(name="gpt-5-5", provider="openai"))
    return config


# --- l'objet Route ---

def test_route_returns_route_object(config3, fake_llm):
    fake_llm(Decision(choices=["anthropic:claude-sonnet-4-6", "openai:gpt-5-5"], reason="Code"))
    r = route("Écris du code", config3)
    assert isinstance(r, Route)
    assert r.model == "anthropic:claude-sonnet-4-6"
    assert r.fallbacks == ["openai:gpt-5-5"]
    assert r.reason == "Code"
    assert r.routed is True
    assert len(r.id) == 32


def test_route_is_immutable(config, fake_llm):
    fake_llm(Decision(choices=["openai:gpt-4o-mini"], reason="Simple"))
    r = route("Dis bonjour", config)
    with pytest.raises(Exception):
        r.model = "autre"


def test_print_shows_full_route(config, fake_llm):
    fake_llm(Decision(choices=["openai:gpt-4o-mini"], reason="Simple"))
    text = str(route("Dis bonjour", config))
    assert text.startswith("Route(model='openai:gpt-4o-mini', fallbacks=[]")
    assert "routed=True" in text


def test_single_choice_has_no_fallbacks(config, fake_llm):
    fake_llm(Decision(choices=["openai:gpt-4o-mini"], reason="Simple"))
    assert route("Dis bonjour", config).fallbacks == []


def test_public_api_is_route_only():
    import autoroute

    assert autoroute.__all__ == ["Route", "route"]
    assert not hasattr(autoroute, "route_with_reason")
    assert not hasattr(autoroute, "route_candidates")


# --- choix de l'orchestrateur ---

def test_choice_without_provider_is_resolved(config, fake_llm):
    fake_llm(Decision(choices=["claude-sonnet-4-6"], reason="Tâche de code"))
    assert route("Écris du code", config).model == "anthropic:claude-sonnet-4-6"


def test_prompt_contains_models_and_task(config, fake_llm):
    fake = fake_llm(Decision(choices=["openai:gpt-4o-mini"], reason="Simple"))
    route("Dis bonjour", config)
    assert "openai:gpt-4o-mini" in fake.prompt
    assert "anthropic:claude-sonnet-4-6" in fake.prompt
    assert "Dis bonjour" in fake.prompt


def test_prompt_asks_reason_in_task_language(config, fake_llm):
    fake = fake_llm(Decision(choices=["openai:gpt-4o-mini"], reason="Simple task"))
    route("Say hello", config)
    assert "langue de la tâche" in fake.prompt


def test_schema_restricts_choice_to_discovered_models(config, fake_llm):
    fake = fake_llm(Decision(choices=["openai:gpt-4o-mini"], reason="Simple"))
    route("Dis bonjour", config)
    items = fake.schema["properties"]["choices"]["items"]
    assert items["enum"] == ["openai:gpt-4o-mini", "anthropic:claude-sonnet-4-6"]
    assert fake.schema["required"] == ["choices", "reason"]
    assert fake.kwargs.get("strict") is True


def test_fallbacks_keep_order(config3, fake_llm):
    fake_llm(Decision(choices=["openai:gpt-5-5", "anthropic:claude-haiku-5-5"], reason="Code"))
    r = route("Écris du code", config3)
    assert r.model == "openai:gpt-5-5"
    assert r.fallbacks == ["anthropic:claude-haiku-5-5"]


def test_invalid_and_duplicate_choices_are_dropped(config3, fake_llm):
    fake_llm(Decision(
        choices=["inventé", "openai:gpt-5-5", "gpt-5-5", "anthropic:claude-haiku-5-5"],
        reason="Code",
    ))
    r = route("Écris du code", config3)
    assert r.model == "openai:gpt-5-5"
    assert r.fallbacks == ["anthropic:claude-haiku-5-5"]
    assert r.routed is True
    assert read_log()[0]["invalid_choice"] is False


def test_fallbacks_capped(config, fake_llm):
    config.models += [ModelConfig(name=f"m{i}", provider="openai") for i in range(MAX_CANDIDATES)]
    fake_llm(Decision(choices=[m.spec for m in config.models], reason="Code"))
    assert len(config.models) > MAX_CANDIDATES
    assert len(route("Écris du code", config).fallbacks) == MAX_CANDIDATES - 1 == 2


# --- choix invalide : traité comme un orchestrateur en échec ---

@pytest.mark.parametrize("choices", [["modele-invente"], []])
def test_invalid_choice_without_backup_is_last_resort(config, fake_llm, choices, caplog):
    fake_llm(Decision(choices=choices, reason="?"))
    r = route("Une tâche", config)
    assert r.routed is False
    # Le dernier recours écarte l'orchestrateur qui a mal répondu.
    assert r.model == "anthropic:claude-sonnet-4-6"
    event = read_log()[0]
    assert event["invalid_choice"] is True
    assert event["routing_failed"] is True
    assert "invalid choice" in event["error"]
    assert any("No orchestrator answered" in w for w in warnings_of(caplog))


def test_invalid_choice_tries_backup_orchestrator(config, fake_orchestrators, caplog):
    config.backup_orchestrators = ["anthropic:claude-sonnet-4-6"]
    fake_orchestrators({
        "openai:gpt-4o-mini": Decision(choices=["gpt-99-ultra"], reason="?"),
        "anthropic:claude-sonnet-4-6": Decision(choices=["openai:gpt-4o-mini"], reason="Simple"),
    })
    r = route("Dis bonjour", config)
    assert r.routed is True
    assert r.model == "openai:gpt-4o-mini"
    assert r.reason == "Simple"
    event = read_log()[0]
    assert event["orchestrator"] == "anthropic:claude-sonnet-4-6"
    assert event["invalid_choice"] is True
    assert event["routing_failed"] is False
    assert warnings_of(caplog) == [
        "Orchestrator failed: openai:gpt-4o-mini. Backup used: anthropic:claude-sonnet-4-6. "
        "Error details in the log."
    ]


# --- panne des orchestrateurs ---

def test_orchestrator_failure_does_not_raise(config, monkeypatch, caplog):
    def boom(spec, **kw):
        raise RuntimeError("panne")

    monkeypatch.setattr("autoroute.router.init_chat_model", boom)
    r = route("Une tâche", config)
    assert r.routed is False
    # Un modèle découvert qui n'est pas l'orchestrateur en échec.
    assert r.model == "anthropic:claude-sonnet-4-6"
    assert config.orchestrator not in [r.model, *r.fallbacks]
    assert "No orchestrator answered" in r.reason
    event = read_log()[0]
    assert event["routing_failed"] is True
    assert "panne" in event["error"]
    [warning] = warnings_of(caplog)
    assert "No orchestrator answered (openai:gpt-4o-mini): falling back to anthropic:claude-sonnet-4-6" in warning


def test_failure_is_a_log_record_not_a_python_warning(config, monkeypatch, recwarn):
    monkeypatch.setattr("autoroute.router.init_chat_model", lambda spec, **kw: 1 / 0)
    route("Une tâche", config)
    assert not [w for w in recwarn if "orchestrateur" in str(w.message).lower()]


def test_failure_does_not_raise_when_warnings_are_errors(config, monkeypatch):
    # Principe 8 : même avec « -W error », le routage ne fait pas planter l'application.
    monkeypatch.setattr("autoroute.router.init_chat_model", lambda spec, **kw: 1 / 0)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert route("Une tâche", config).routed is False


def test_no_warning_when_orchestrator_answers(config, fake_llm, caplog):
    fake_llm(Decision(choices=["openai:gpt-4o-mini"], reason="Simple"))
    route("Dis bonjour", config)
    assert warnings_of(caplog) == []


def test_last_resort_spreads_across_providers(config, monkeypatch):
    config.models += [
        ModelConfig(name="gpt-4o", provider="openai"),
        ModelConfig(name="claude-haiku-4-5", provider="anthropic"),
    ]
    monkeypatch.setattr("autoroute.router.init_chat_model", lambda spec, **kw: 1 / 0)
    r = route("Une tâche", config)
    # Petit modèle d'abord, un fournisseur après l'autre, sans l'orchestrateur en échec.
    assert [r.model, *r.fallbacks] == [
        "openai:gpt-4o", "anthropic:claude-haiku-4-5", "anthropic:claude-sonnet-4-6",
    ]


def test_last_resort_falls_back_to_orchestrators_when_nothing_else(config, monkeypatch):
    config.backup_orchestrators = ["anthropic:claude-sonnet-4-6"]  # tous les modèles ont échoué
    monkeypatch.setattr("autoroute.router.init_chat_model", lambda spec, **kw: 1 / 0)
    r = route("Une tâche", config)
    assert r.routed is False
    assert [r.model, *r.fallbacks] == ["openai:gpt-4o-mini", "anthropic:claude-sonnet-4-6"]


def test_backup_orchestrator_used_when_first_fails(config, fake_orchestrators, caplog):
    config.backup_orchestrators = ["anthropic:claude-sonnet-4-6"]
    fake_orchestrators({
        "openai:gpt-4o-mini": RuntimeError("quota dépassé"),
        "anthropic:claude-sonnet-4-6": Decision(choices=["openai:gpt-4o-mini"], reason="Simple"),
    })
    r = route("Dis bonjour", config)
    assert r.routed is True
    assert r.model == "openai:gpt-4o-mini"
    event = read_log()[0]
    assert event["orchestrator"] == "anthropic:claude-sonnet-4-6"
    assert "quota" in event["error"]
    assert any("Backup used: anthropic:claude-sonnet-4-6" in w for w in warnings_of(caplog))


# --- limite de temps de l'orchestrateur ---

def test_orchestrator_gets_timeout_and_retry_limit(config, fake_llm, monkeypatch):
    seen = {}
    fake = fake_llm(Decision(choices=["openai:gpt-4o-mini"], reason="Simple"))

    def init(spec, **kw):
        seen.update(kw)
        return fake

    monkeypatch.setattr("autoroute.router.init_chat_model", init)
    route("Dis bonjour", config)
    assert seen == {"timeout": ORCHESTRATOR_TIMEOUT, "max_retries": ORCHESTRATOR_RETRIES}
    assert ORCHESTRATOR_TIMEOUT <= 20 and ORCHESTRATOR_RETRIES <= 1


class TimedOutLLM:
    """Orchestrateur qui dépasse la limite de temps : l'appel lève une erreur de timeout."""

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, prompt):
        raise httpx.ReadTimeout("The read operation timed out")


def test_timed_out_orchestrator_hands_over_to_backup(config, fake_llm, monkeypatch):
    config.backup_orchestrators = ["anthropic:claude-sonnet-4-6"]
    backup = fake_llm(Decision(choices=["openai:gpt-4o-mini"], reason="Simple"))
    monkeypatch.setattr(
        "autoroute.router.init_chat_model",
        lambda spec, **kw: TimedOutLLM() if spec == "openai:gpt-4o-mini" else backup,
    )
    r = route("Dis bonjour", config)
    assert r.routed is True
    event = read_log()[0]
    assert event["orchestrator"] == "anthropic:claude-sonnet-4-6"
    assert "ReadTimeout" in event["error"]


def test_all_orchestrators_timed_out_is_last_resort(config, monkeypatch):
    monkeypatch.setattr("autoroute.router.init_chat_model", lambda spec, **kw: TimedOutLLM())
    r = route("Dis bonjour", config)
    assert r.routed is False
    assert "ReadTimeout" in read_log()[0]["error"]


# --- découverte et log ---

def test_discovery_failure_is_logged_and_reraised(monkeypatch):
    def no_models(mode="all", **kw):
        raise ValueError("Aucun modèle trouvé")

    monkeypatch.setattr("autoroute.router.discover_config", no_models)
    with pytest.raises(ValueError):
        route("Une tâche")
    assert "Aucun modèle" in read_log()[0]["error"]


def test_route_without_config_discovers_with_env_mode(config, fake_llm, monkeypatch):
    seen = {}

    def fake_discover(mode="all", **kw):
        seen["mode"] = mode
        return config

    monkeypatch.setenv("AUTOROUTE_MODE", "anthropic")
    monkeypatch.setattr("autoroute.router.discover_config", fake_discover)
    fake_llm(Decision(choices=["anthropic:claude-sonnet-4-6"], reason="Tâche de code"))

    assert route("Écris du code").model == "anthropic:claude-sonnet-4-6"
    assert seen["mode"] == "anthropic"
    assert read_log()[0]["mode"] == "anthropic"


# --- cache de la découverte ---

@pytest.fixture
def counted_discovery(config, fake_llm, monkeypatch):
    """discover_config simulé qui compte ses appels."""
    calls = []

    def fake_discover(mode="all", **kw):
        calls.append(mode)
        return config

    monkeypatch.setattr("autoroute.router.discover_config", fake_discover)
    fake_llm(Decision(choices=["openai:gpt-4o-mini"], reason="Simple"))
    return calls


def test_discovery_is_cached_between_routes(counted_discovery):
    route("Tâche 1")
    route("Tâche 2")
    assert len(counted_discovery) == 1


def test_discovery_cache_expires(counted_discovery, monkeypatch):
    import autoroute.router as router

    now = [1000.0]
    monkeypatch.setattr(router.time, "monotonic", lambda: now[0])
    route("Tâche 1")
    now[0] += router.DISCOVERY_TTL + 1
    route("Tâche 2")
    assert len(counted_discovery) == 2


def test_discovery_cache_depends_on_mode(counted_discovery, monkeypatch):
    route("Tâche 1")
    monkeypatch.setenv("AUTOROUTE_MODE", "anthropic")
    route("Tâche 2")
    assert counted_discovery == ["all", "anthropic"]


def test_discovery_cache_depends_on_api_keys(counted_discovery, monkeypatch):
    route("Tâche 1")
    monkeypatch.setenv("OPENAI_API_KEY", "other-key")
    route("Tâche 2")
    assert len(counted_discovery) == 2


def test_discovery_cache_depends_on_config_file(counted_discovery, tmp_path):
    route("Tâche 1")
    path = tmp_path / ".autoroute" / "config.yaml"
    path.parent.mkdir()
    path.write_text("orchestrators:\n  all: openai:gpt-4o-mini\n", encoding="utf-8")
    route("Tâche 2")
    assert len(counted_discovery) == 2


def test_discovery_failure_is_not_cached(config, fake_llm, monkeypatch):
    calls = []

    def flaky(mode="all", **kw):
        calls.append(mode)
        if len(calls) == 1:
            raise ValueError("Aucun modèle trouvé")
        return config

    monkeypatch.setattr("autoroute.router.discover_config", flaky)
    fake_llm(Decision(choices=["openai:gpt-4o-mini"], reason="Simple"))
    with pytest.raises(ValueError):
        route("Tâche 1")
    assert route("Tâche 2").routed is True
    assert len(calls) == 2


def test_explicit_config_skips_discovery(counted_discovery, config):
    route("Tâche", config)
    assert counted_discovery == []


def test_decision_is_logged(config3, fake_llm):
    fake_llm(Decision(choices=["anthropic:claude-sonnet-4-6", "openai:gpt-5-5"], reason="Tâche de code"))
    r = route("Écris du code", config3)
    event = read_log()[0]
    assert event["event"] == "decision"
    assert event["decision_id"] == r.id
    assert event["model"] == "anthropic:claude-sonnet-4-6"
    assert event["candidates"] == ["anthropic:claude-sonnet-4-6", "openai:gpt-5-5"]
    assert event["reason"] == "Tâche de code"
    assert event["orchestrator"] == "openai:gpt-4o-mini"
    assert event["invalid_choice"] is False
    assert event["routing_failed"] is False
    assert event["error"] is None


# --- format de sortie (to=) et fournisseurs ---

@pytest.fixture
def config_google(config3):
    config3.models.append(ModelConfig(name="gemini-3.8-flash", provider="google_genai"))
    config3.models.append(ModelConfig(name="mistral-small-latest", provider="mistralai"))
    return config3


def test_default_format_is_langchain(config_google, fake_llm):
    fake_llm(Decision(choices=["google_genai:gemini-3.8-flash", "anthropic:claude-haiku-5-5"], reason="Code"))
    r = route("Écris du code", config_google)
    assert r.model == "google_genai:gemini-3.8-flash"
    assert r.fallbacks == ["anthropic:claude-haiku-5-5"]


def test_native_format_gives_official_names(config_google, fake_llm):
    fake_llm(Decision(
        choices=["google_genai:gemini-3.8-flash", "anthropic:claude-haiku-5-5", "mistralai:mistral-small-latest"],
        reason="Code",
    ))
    r = route("Écris du code", config_google, to="native")
    assert r.model == "gemini-3.8-flash"
    assert r.fallbacks == ["claude-haiku-5-5", "mistral-small-latest"]


@pytest.mark.parametrize("to", ["langchain", "native"])
def test_providers_are_neutral_and_aligned(config_google, fake_llm, to):
    fake_llm(Decision(
        choices=["google_genai:gemini-3.8-flash", "openai:gpt-5-5", "mistralai:mistral-small-latest"],
        reason="Code",
    ))
    r = route("Écris du code", config_google, to=to)
    assert r.providers == ["google", "openai", "mistral"]
    assert r.provider == "google"
    assert len(r.providers) == 1 + len(r.fallbacks)


def test_native_format_when_not_routed(config, monkeypatch):
    monkeypatch.setattr("autoroute.router.init_chat_model", lambda spec, **kw: 1 / 0)
    r = route("Une tâche", config, to="native")
    assert r.routed is False
    assert r.model == "claude-sonnet-4-6"
    assert r.provider == "anthropic"


def test_log_keeps_provider_name_format_whatever_to(config_google, fake_llm):
    fake_llm(Decision(choices=["google_genai:gemini-3.8-flash", "anthropic:claude-haiku-5-5"], reason="Code"))
    route("Écris du code", config_google, to="native")
    event = read_log()[0]
    assert event["model"] == "google_genai:gemini-3.8-flash"
    assert event["candidates"] == ["google_genai:gemini-3.8-flash", "anthropic:claude-haiku-5-5"]


def test_unknown_format_raises_before_any_call(config, monkeypatch):
    called = []
    monkeypatch.setattr("autoroute.router.init_chat_model", lambda spec, **kw: called.append(spec))
    with pytest.raises(ValueError, match="Unknown to='litelm'. Accepted values: langchain, native"):
        route("Une tâche", config, to="litelm")
    assert called == []
