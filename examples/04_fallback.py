"""Shows a real fallback: the primary model is replaced by one that does not exist.

LangChain gets an error from the primary model (model not found) and moves on:
the answer comes from the first fallback.
"""

from langchain.chat_models import init_chat_model

from autoroute import route

task = "Dis bonjour"

r = route(task)
if not r.fallbacks:
    raise SystemExit("L'orchestrateur n'a proposé aucun repli : relance le script.")

# Simulate a failing chosen model (instead of r.model). Same provider as r.model:
# its API key exists, so LangChain can build the model and the call itself fails.
provider = r.model.split(":", 1)[0]
primary = f"{provider}:modele-inexistant"
print(f"Principal (forcé en échec) : {primary}  replis : {r.fallbacks}")

model = init_chat_model(primary).with_fallbacks([init_chat_model(m) for m in r.fallbacks])
print(model.invoke(task).content)
