import json

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from autoroute import observability
from autoroute.router import RouteResult


class FakeChat(BaseChatModel):
    """Modèle de chat sans réseau : échoue ou répond avec un usage de tokens."""

    model: str
    fail: bool = False

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.fail:
            raise RuntimeError(f"{self.model} en panne")
        msg = AIMessage(
            content="ok",
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, **kwargs):
        return self


def outcomes():
    lines = observability.LOG_PATH.read_text(encoding="utf-8").splitlines()
    return [e for e in map(json.loads, lines) if e["event"] == "outcome"]


@pytest.fixture
def result():
    return RouteResult(
        model="openai:primary",
        reason="test",
        candidates=["openai:primary", "anthropic:backup"],
    )


def test_single_model_success(result):
    FakeChat(model="primary").invoke("hi", config={"callbacks": [result.tracker()]})
    [out] = outcomes()
    assert out["decision_id"] == result.decision_id
    assert out["served_by"] == "openai:primary"
    assert out["fallbacks_used"] == 0
    assert out["failures"] == []
    assert out["usage"] == {"input_tokens": 10, "output_tokens": 5}
    assert out["error"] is None


def test_fallback_used_is_recorded(result):
    model = FakeChat(model="primary", fail=True).with_fallbacks([FakeChat(model="backup")])
    model.invoke("hi", config={"callbacks": [result.tracker()]})
    [out] = outcomes()
    assert out["primary"] == "openai:primary"
    assert out["served_by"] == "anthropic:backup"
    assert out["fallbacks_used"] == 1
    assert out["failures"] == [
        {"model": "openai:primary", "error": "RuntimeError: primary en panne"}
    ]
    assert out["usage"]["output_tokens"] == 5


def test_all_models_fail(result):
    model = FakeChat(model="primary", fail=True).with_fallbacks([FakeChat(model="backup", fail=True)])
    with pytest.raises(RuntimeError):
        model.invoke("hi", config={"callbacks": [result.tracker()]})
    [out] = outcomes()
    assert out["served_by"] is None
    assert len(out["failures"]) == 2
    # with_fallbacks relance la première erreur : c'est celle que reçoit l'utilisateur.
    assert out["error"] == "RuntimeError: primary en panne"


def test_tracker_writes_one_line_per_invoke(result):
    tracker = result.tracker()
    model = FakeChat(model="primary")
    model.invoke("un", config={"callbacks": [tracker]})
    model.invoke("deux", config={"callbacks": [tracker]})
    assert len(outcomes()) == 2


def test_agent_with_fallback_middleware(result):
    from langchain.agents import create_agent
    from langchain.agents.middleware import ModelFallbackMiddleware

    agent = create_agent(
        model=FakeChat(model="primary", fail=True),
        tools=[],
        middleware=[ModelFallbackMiddleware(FakeChat(model="backup"))],
    )
    agent.invoke(
        {"messages": [{"role": "user", "content": "hi"}]},
        config={"callbacks": [result.tracker()]},
    )
    [out] = outcomes()
    assert out["served_by"] == "anthropic:backup"
    assert out["fallbacks_used"] == 1
    assert out["failures"][0]["model"] == "openai:primary"
