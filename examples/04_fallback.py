"""Shows a real fallback: the primary model is replaced by one that does not exist.

LangChain gets a NotFoundError from the primary model and moves on:
the answer comes from the first fallback.
"""

from langchain.chat_models import init_chat_model

from autoroute import route_with_reason

task = "Dis bonjour"

r = route_with_reason(task)
if len(r.candidates) < 2:
    raise SystemExit("L'orchestrateur n'a proposé aucun repli : relance le script.")

r.candidates[0] = "openai:gpt-inexistant"  # simulate a failing primary model
primary, *backups = r.candidates
print(f"Principal (forcé en échec) : {primary}  replis : {backups}")

model = init_chat_model(primary).with_fallbacks([init_chat_model(m) for m in backups])
print(model.invoke(task).content)
