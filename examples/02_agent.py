from langchain.agents import create_agent
from langchain.agents.middleware import ModelFallbackMiddleware
from langchain.tools import tool

from autoroute import route_with_reason


@tool
def get_weather(city: str) -> str:
    """Donne la météo d'une ville."""
    return f"Il fait grand soleil à {city}."


task = "Quel temps fait-il à Vannes ?"

r = route_with_reason(task)
primary, *backups = r.candidates

agent = create_agent(
    model=primary,
    tools=[get_weather],
    system_prompt="Tu es un assistant utile.",
    # If the primary model fails, the agent retries with the fallback models, in order.
    middleware=[ModelFallbackMiddleware(*backups)] if backups else [],
)

# The tracker logs which model actually answered, the fallbacks used and the tokens.
result = agent.invoke(
    {"messages": [{"role": "user", "content": task}]},
    config={"callbacks": [r.tracker()]},
)
print(result["messages"][-1].content)
