from langchain.agents import create_agent
from langchain.agents.middleware import ModelFallbackMiddleware
from langchain.tools import tool

from autoroute import route


@tool
def get_weather(city: str) -> str:
    """Donne la météo d'une ville."""
    return f"Il fait grand soleil à {city}."


task = "Quel temps fait-il à Paris ?"

r = route(task)

agent = create_agent(
    model=r.model,
    tools=[get_weather],
    system_prompt="Tu es un assistant utile.",
    # If the primary model fails, the agent retries with the fallback models, in order.
    middleware=[ModelFallbackMiddleware(*r.fallbacks)] if r.fallbacks else [],
)

result = agent.invoke({"messages": [{"role": "user", "content": task}]})
print(result["messages"][-1].content)
