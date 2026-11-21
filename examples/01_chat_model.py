from langchain.chat_models import init_chat_model

from autoroute import route

task = "Écris une fonction Python qui parse un CSV"

# The chosen model, then up to 2 fallbacks chosen by the orchestrator.
r = route(task)
print(f"Modèle choisi : {r.model}  (replis : {r.fallbacks})")

# If the chosen model fails (rate limit, outage, no access...), LangChain tries the next one.
model = init_chat_model(r.model).with_fallbacks([init_chat_model(m) for m in r.fallbacks])
print(model.invoke(task).content)
