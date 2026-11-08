import json

import pytest

from autoroute import observability
from autoroute.config import ModelConfig
from autoroute.router import MAX_CANDIDATES, Decision, route, route_candidates, route_with_reason


def read_log():
    lines = observability.LOG_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_route_returns_provider_and_name(config, fake_llm):
    fake_llm(Decision(choices=["anthropic:claude-sonnet-4-6"], reason="Tâche de code"))
    assert route("Écris du code", config) == "anthropic:claude-sonnet-4-6"


def test_choice_without_provider_is_resolved(config, fake_llm):
    fake_llm(Decision(choices=["claude-sonnet-4-6"], reason="Tâche de code"))
    assert route("Écris du code", config) == "anthropic:claude-sonnet-4-6"


def test_prompt_contains_models_and_task(config, fake_llm):
    fake = fake_llm(Decision(choices=["openai:gpt-4o-mini"], reason="Simple"))
    route("Dis bonjour", config)
    assert "openai:gpt-4o-mini" in fake.prompt
    assert "anthropic:claude-sonnet-4-6" in fake.prompt
    assert "Dis bonjour" in fake.prompt


def test_invalid_choice_falls_back_to_orchestrator(config, fake_llm):
    fake_llm(Decision(choices=["modele-invente"], reason="?"))
    result = route_with_reason("Une tâche", config)
    assert result.invalid_choice is True
    assert result.model == "openai:gpt-4o-mini"


def test_decision_is_logged(config, fake_llm):
    fake_llm(Decision(choices=["anthropic:claude-sonnet-4-6"], reason="Tâche de code"))
    route("Écris du code", config)
    event = read_log()[0]
    assert event["model"] == "anthropic:claude-sonnet-4-6"
    assert event["reason"] == "Tâche de code"
    assert event["orchestrator"] == "openai:gpt-4o-mini"
    assert event["invalid_choice"] is False
    assert event["event"] == "decision"
    assert event["decision_id"]
    assert event["error"] is None
    assert event["routing_failed"] is False


def test_no_warning_when_orchestrator_answers(config, fake_llm, recwarn):
    fake_llm(Decision(choices=["openai:gpt-4o-mini"], reason="Simple"))
    route_with_reason("Dis bonjour", config)
    assert not [w for w in recwarn if "orchestrateur" in str(w.message).lower()]


def test_orchestrator_failure_does_not_raise(config, monkeypatch):
    def boom(spec, **kw):
        raise RuntimeError("panne")

    monkeypatch.setattr("autoroute.router.init_chat_model", boom)
    with pytest.warns(UserWarning, match="Aucun orchestrateur n'a répondu .*openai:gpt-4o-mini.*repli sur anthropic:claude-sonnet-4-6"):
        result = route_with_reason("Une tâche", config)
    assert result.routing_failed is True
    # Un modèle découvert qui n'est pas l'orchestrateur en échec.
    assert result.model == "anthropic:claude-sonnet-4-6"
    assert config.orchestrator not in result.candidates
    event = read_log()[0]
    assert event["routing_failed"] is True
    assert "panne" in event["error"]


def test_last_resort_spreads_across_providers(config, monkeypatch):
    config.models += [
        ModelConfig(name="gpt-4o", provider="openai"),
        ModelConfig(name="claude-haiku-4-5", provider="anthropic"),
    ]
    monkeypatch.setattr("autoroute.router.init_chat_model", lambda spec, **kw: 1 / 0)
    with pytest.warns(UserWarning, match="Aucun orchestrateur"):
        result = route_with_reason("Une tâche", config)
    # Petit modèle d'abord, un fournisseur après l'autre, sans l'orchestrateur en échec.
    assert result.candidates == ["openai:gpt-4o", "anthropic:claude-haiku-4-5", "anthropic:claude-sonnet-4-6"]


def test_last_resort_falls_back_to_orchestrators_when_nothing_else(config, monkeypatch):
    config.backup_orchestrators = ["anthropic:claude-sonnet-4-6"]  # tous les modèles ont échoué
    monkeypatch.setattr("autoroute.router.init_chat_model", lambda spec, **kw: 1 / 0)
    with pytest.warns(UserWarning, match="Aucun orchestrateur"):
        result = route_with_reason("Une tâche", config)
    assert result.routing_failed is True
    assert result.candidates == ["openai:gpt-4o-mini", "anthropic:claude-sonnet-4-6"]


def test_backup_orchestrator_used_when_first_fails(config, fake_llm, monkeypatch):
    config.backup_orchestrators = ["anthropic:claude-sonnet-4-6"]
    good = fake_llm(Decision(choices=["openai:gpt-4o-mini"], reason="Simple"))

    def init(spec, **kw):
        if spec == "openai:gpt-4o-mini":
            raise RuntimeError("quota dépassé")
        return good

    monkeypatch.setattr("autoroute.router.init_chat_model", init)
    with pytest.warns(UserWarning, match="en échec : openai:gpt-4o-mini. Secours utilisé : anthropic:claude-sonnet-4-6"):
        result = route_with_reason("Dis bonjour", config)
    assert result.routing_failed is False
    assert result.model == "openai:gpt-4o-mini"
    event = read_log()[0]
    assert event["orchestrator"] == "anthropic:claude-sonnet-4-6"
    assert "quota" in event["error"]


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

    assert route("Écris du code") == "anthropic:claude-sonnet-4-6"
    assert seen["mode"] == "anthropic"
    assert read_log()[0]["mode"] == "anthropic"


def test_schema_restricts_choice_to_discovered_models(config, fake_llm):
    fake = fake_llm(Decision(choices=["openai:gpt-4o-mini"], reason="Simple"))
    route("Dis bonjour", config)
    items = fake.schema["properties"]["choices"]["items"]
    assert items["enum"] == ["openai:gpt-4o-mini", "anthropic:claude-sonnet-4-6"]
    assert fake.schema["required"] == ["choices", "reason"]
    assert fake.kwargs.get("strict") is True


def test_choice_outside_enum_still_falls_back(config, fake_llm):
    # Un fournisseur qui n'applique pas l'enum ne doit pas faire planter le routage.
    fake_llm(Decision(choices=["gpt-99-ultra"], reason="?"))
    result = route_with_reason("Une tâche", config)
    assert result.invalid_choice is True
    assert result.model == config.orchestrator


@pytest.fixture
def config3(config):
    config.models.append(ModelConfig(name="claude-haiku-5-5", provider="anthropic"))
    config.models.append(ModelConfig(name="gpt-5-5", provider="openai"))
    return config


def test_candidates_keep_order(config3, fake_llm):
    fake_llm(Decision(choices=["anthropic:claude-sonnet-4-6", "openai:gpt-5-5"], reason="Code"))
    result = route_with_reason("Écris du code", config3)
    assert result.model == "anthropic:claude-sonnet-4-6"
    assert result.candidates == ["anthropic:claude-sonnet-4-6", "openai:gpt-5-5"]


def test_candidates_drop_invalid_and_duplicates(config3, fake_llm):
    fake_llm(Decision(
        choices=["inventé", "openai:gpt-5-5", "gpt-5-5", "anthropic:claude-haiku-5-5"],
        reason="Code",
    ))
    result = route_with_reason("Écris du code", config3)
    assert result.candidates == ["openai:gpt-5-5", "anthropic:claude-haiku-5-5"]
    assert result.invalid_choice is False


def test_candidates_capped_at_max(config, fake_llm):
    config.models += [ModelConfig(name=f"m{i}", provider="openai") for i in range(MAX_CANDIDATES)]
    fake_llm(Decision(choices=[m.spec for m in config.models], reason="Code"))
    assert len(config.models) > MAX_CANDIDATES
    assert len(route_candidates("Écris du code", config)) == MAX_CANDIDATES == 3


def test_invalid_choice_candidates_is_orchestrator(config, fake_llm):
    fake_llm(Decision(choices=[], reason="?"))
    result = route_with_reason("Une tâche", config)
    assert result.invalid_choice is True
    assert result.candidates == [config.orchestrator]


def test_route_still_returns_first_candidate(config3, fake_llm):
    fake_llm(Decision(choices=["openai:gpt-5-5", "anthropic:claude-haiku-5-5"], reason="Code"))
    assert route("Écris du code", config3) == "openai:gpt-5-5"


def test_candidates_are_logged(config3, fake_llm):
    fake_llm(Decision(choices=["openai:gpt-5-5", "anthropic:claude-haiku-5-5"], reason="Code"))
    route("Écris du code", config3)
    assert read_log()[0]["candidates"] == ["openai:gpt-5-5", "anthropic:claude-haiku-5-5"]


def test_decision_id_links_result_and_log(config, fake_llm):
    fake_llm(Decision(choices=["openai:gpt-4o-mini"], reason="Simple"))
    result = route_with_reason("Dis bonjour", config)
    assert len(result.decision_id) == 32
    assert read_log()[0]["decision_id"] == result.decision_id
