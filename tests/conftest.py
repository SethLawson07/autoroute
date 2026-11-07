import pytest

from autoroute.config import Config, ModelConfig


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Chaque test travaille dans un dossier vide : ni vrai .env, ni vrai log."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("autoroute.observability.LOG_PATH", tmp_path / "log.jsonl")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    for var in ("GOOGLE_API_KEY", "MISTRAL_API_KEY", "AUTOROUTE_MODE", "AUTOROUTE_CONFIG"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def config():
    return Config(
        orchestrator="openai:gpt-4o-mini",
        models=[
            ModelConfig(name="gpt-4o-mini", provider="openai"),
            ModelConfig(name="claude-sonnet-4-6", provider="anthropic"),
        ],
    )


class FakeLLM:
    """Imite init_chat_model(...).with_structured_output(...)."""

    def __init__(self, decision):
        self.decision = decision
        self.prompt = None
        self.schema = None

    def with_structured_output(self, schema, **kwargs):
        self.schema = schema
        self.kwargs = kwargs
        return self

    def invoke(self, prompt):
        self.prompt = prompt
        # Avec un schéma dict, LangChain renvoie un dict, pas un objet Pydantic.
        return self.decision.model_dump()


@pytest.fixture
def fake_llm(monkeypatch):
    def install(decision):
        fake = FakeLLM(decision)
        monkeypatch.setattr("autoroute.router.init_chat_model", lambda spec, **kw: fake)
        return fake

    return install