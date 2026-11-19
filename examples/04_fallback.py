"""Shows a real fallback: the primary model is replaced by one that does not exist.

LangChain gets a NotFoundError from the primary model and moves on:
the answer comes from the first fallback.
"""

from langchain.chat_models import init_chat_model

from autoroute import route

task = "Dis bonjour"

r = route(task)
if not r.fallbacks:
    raise SystemExit("L'orchestrateur n'a proposé aucun repli : relance le script.")

primary = "openai:gpt-inexistant"  # simulate a failing chosen model (instead of r.model)
print(f"Principal (forcé en échec) : {primary}  replis : {r.fallbacks}")

model = init_chat_model(primary).with_fallbacks([init_chat_model(m) for m in r.fallbacks])
print(model.invoke(task).content)
