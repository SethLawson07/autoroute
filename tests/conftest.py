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
    # Le cache de la découverte ne doit pas passer d'un test à l'autre.
    monkeypatch.setattr("autoroute.router._discovery_cache", {})
    monkeypatch.setattr("autoroute.observability._unwritable", set())


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
        if isinstance(self.decision, dict):
            return self.decision  # réponse brute, ex. un champ manquant
        return self.decision.model_dump()


@pytest.fixture
def fake_llm(monkeypatch):
    def install(decision):
        fake = FakeLLM(decision)
        monkeypatch.setattr("autoroute.router.init_chat_model", lambda spec, **kw: fake)
        return fake

    return install

@pytest.fixture
def fake_orchestrators(monkeypatch):
    """Une réponse par orchestrateur : une Decision, ou une exception à lever."""
    def install(answers):
        def init(spec, **kw):
            answer = answers[spec]
            if isinstance(answer, Exception):
                raise answer
            return FakeLLM(answer)

        monkeypatch.setattr("autoroute.router.init_chat_model", init)

    return install
