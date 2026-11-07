from langchain.chat_models import init_chat_model

from autoroute import route_with_reason

task = "Écris une fonction Python qui parse un CSV"

# The best model first, then up to 2 fallbacks chosen by the orchestrator.
r = route_with_reason(task)
primary, *backups = r.candidates
print(f"Modèle choisi : {primary}  (replis : {backups})")

# If the primary model fails (rate limit, outage, no access...), LangChain tries the next one.
model = init_chat_model(primary).with_fallbacks([init_chat_model(m) for m in backups])

# The tracker logs which model actually answered, the fallbacks used and the tokens.
print(model.invoke(task, config={"callbacks": [r.tracker()]}).content)
