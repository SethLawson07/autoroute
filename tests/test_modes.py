import pytest

from autoroute import catalog
from autoroute.config import MODES


@pytest.mark.parametrize("mode", MODES)
def test_mode_restricts_providers(monkeypatch, mode):
    first = MODES[mode][0]
    monkeypatch.setattr(catalog, "get_api_key", lambda p: "fake-key")
    monkeypatch.setattr(catalog, "list_chat_models", lambda p: [f"{p}-model"])
    monkeypatch.setattr(
        catalog, "load_orchestrator",
        lambda path=None, mode="all": f"{first}:{first}-model",
    )
    config = catalog.discover_config(mode=mode)
    assert {m.provider for m in config.models} == set(MODES[mode])