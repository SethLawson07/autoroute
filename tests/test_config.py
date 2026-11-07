import pytest

from helpers import write_config

from autoroute.config import get_api_key, key_source, load_orchestrator


def test_load_orchestrator():
    write_config("orchestrators:\n  all: gpt-4o-mini\n")
    assert load_orchestrator() == "gpt-4o-mini"


def test_missing_file_means_automatic():
    assert load_orchestrator() is None


def test_missing_mode_means_automatic():
    write_config("orchestrators:\n  sovereign: mistralai:mistral-small-latest\n")
    assert load_orchestrator(mode="all") is None


def test_commented_template_means_automatic():
    write_config("orchestrators:\n  # all: openai:gpt-4o-mini\n")
    assert load_orchestrator() is None


def test_empty_api_key_is_missing(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    assert get_api_key("openai") is None


def test_system_env_wins_over_dotenv(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "from-system")
    with open(".env", "w", encoding="utf-8") as f:
        f.write("OPENAI_API_KEY=from-file\n")
    assert get_api_key("openai") == "from-system"


def test_dotenv_used_when_system_env_absent(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    with open(".env", "w", encoding="utf-8") as f:
        f.write("OPENAI_API_KEY=from-file\n")
    assert get_api_key("openai") == "from-file"

def test_config_path_from_env(monkeypatch):
    with open("custom.yaml", "w", encoding="utf-8") as f:
        f.write("orchestrators:\n  all: gpt-4o-mini\n")
    monkeypatch.setenv("AUTOROUTE_CONFIG", "custom.yaml")
    assert load_orchestrator() == "gpt-4o-mini"


def test_orchestrator_depends_on_mode():
    write_config("orchestrators:\n  all: gpt-4o-mini\n  sovereign: mistralai:mistral-small-latest\n")
    assert load_orchestrator(mode="sovereign") == "mistralai:mistral-small-latest"


def write_dotenv(content):
    with open(".env", "w", encoding="utf-8") as f:
        f.write(content)


def test_key_source_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    assert key_source("openai") == "missing"


def test_key_source_system(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "from-system")
    assert key_source("openai") == "system"


def test_key_source_dotenv(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    write_dotenv("OPENAI_API_KEY=from-file\n")
    assert key_source("openai") == ".env"


def test_key_source_dotenv_ignored(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "from-system")
    write_dotenv("OPENAI_API_KEY=from-file\n")
    assert key_source("openai") == "system (.env ignored)"


@pytest.mark.parametrize("content, why", [
    ("orchestrators:\n  all: : :\n", "ligne 2"),
    ("- a\n- b\n", "orchestrators:"),
    ("orchestrators: [a, b]\n", "associer un mode"),
    ("orchestrators:\n  all: [a, b]\n", "provider:nom"),
])
def test_unreadable_config_warns_and_means_automatic(content, why):
    write_config(content)
    with pytest.warns(UserWarning, match=why):
        assert load_orchestrator() is None
