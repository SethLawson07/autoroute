from autoroute import catalog


def fake_discovery(monkeypatch, orchestrator):
    monkeypatch.setattr(catalog, "get_api_key", lambda p: "fake-key")
    monkeypatch.setattr(catalog, "list_chat_models", lambda p: [f"{p}-model"])
    monkeypatch.setattr(catalog, "load_orchestrator", lambda path=None, mode="all": orchestrator)


def test_sovereign_mode_only_mistral(monkeypatch):
    fake_discovery(monkeypatch, "mistralai:mistralai-model")
    config = catalog.discover_config(mode="sovereign")
    assert {m.provider for m in config.models} == {"mistralai"}


def test_all_mode_all_providers(monkeypatch):
    fake_discovery(monkeypatch, "openai:openai-model")
    config = catalog.discover_config(mode="all")
    assert {m.provider for m in config.models} == set(catalog.MODES["all"])
