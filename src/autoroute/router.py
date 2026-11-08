import time
from uuid import uuid4

from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

from autoroute.catalog import discover_config, pick_spread
from autoroute.config import Config, ModelConfig, get_mode
from autoroute.observability import OutcomeTracker, log_decision


MAX_CANDIDATES = 3  # le choix principal + 2 replis au plus


class Decision(BaseModel):
    choices: list[str]  # modèles au format provider:nom, le meilleur en premier
    reason: str         # pourquoi le premier choix, en une ou deux phrases


class RouteResult(BaseModel):
    model: str             # "provider:name", utilisable par init_chat_model
    reason: str
    candidates: list[str]  # model en premier, puis les replis en cas d'échec
    invalid_choice: bool = False  # l'orchestrateur n'a donné aucun nom valide : repli sur lui
    routing_failed: bool = False  # aucun orchestrateur n'a répondu : repli sur un modèle découvert
    decision_id: str = Field(default_factory=lambda: uuid4().hex)

    def tracker(self) -> OutcomeTracker:
        """Callback à passer à invoke : enregistre le modèle qui a vraiment répondu."""
        return OutcomeTracker(self.decision_id, self.candidates)


def _find(config: Config, value: str) -> ModelConfig | None:
    value = value.strip()
    for m in config.models:
        if m.spec == value:
            return m
    by_name = [m for m in config.models if m.name == value]
    return by_name[0] if len(by_name) == 1 else None


def _decision_schema(config: Config) -> dict:
    """Schéma de sortie dont chaque élément de `choices` est limité aux modèles découverts.

    Un dict (pas un modèle Pydantic) : LangChain renvoie alors la réponse brute sans la
    valider. Si un fournisseur n'applique pas l'enum, _candidates et le repli prennent le
    relais au lieu d'une ValidationError.
    """
    return {
        "title": "Decision",
        "description": "Les modèles choisis pour la tâche, du meilleur au moins bon, et pourquoi.",
        "type": "object",
        "properties": {
            "choices": {
                "type": "array",
                "items": {"type": "string", "enum": [m.spec for m in config.models]},
                "description": (
                    f"De 1 à {MAX_CANDIDATES} modèles distincts au format fournisseur:nom, "
                    "le meilleur en premier, puis les alternatives en cas d'échec."
                ),
            },
            "reason": {
                "type": "string",
                "description": "Pourquoi le premier modèle, en une ou deux phrases.",
            },
        },
        "required": ["choices", "reason"],
        "additionalProperties": False,
    }


def _build_prompt(task: str, config: Config) -> str:
    lines = "\n".join(f"- {m.spec}" for m in config.models)
    return (
        "Tu es un routeur de modèles. Choisis le modèle le plus adapté à la tâche, "
        "uniquement parmi cette liste :\n"
        f"{lines}\n\n"
        "Tu ne disposes que des noms : appuie-toi sur ce que tu sais de ces modèles. "
        "Réserve les modèles les plus puissants aux tâches complexes "
        "et prends un modèle léger pour les tâches simples.\n\n"
        f"Tâche :\n{task}\n\n"
        "Réponds avec les noms exacts, tels qu'ils apparaissent dans la liste, au format "
        f"fournisseur:nom (champ choices) : le meilleur modèle en premier, puis au plus "
        f"{MAX_CANDIDATES - 1} modèle(s) de repli adapté(s) à la tâche, utilisé(s) si le premier échoue. "
        "Donne la raison du premier choix (champ reason)."
    )


def _candidates(config: Config, choices: list[str]) -> list[str]:
    """Garde les choix valides, sans doublon, dans l'ordre, et au plus MAX_CANDIDATES."""
    result: list[str] = []
    for value in choices:
        found = _find(config, value)
        if found and found.spec not in result:
            result.append(found.spec)
    return result[:MAX_CANDIDATES]


def _ask(orchestrator: str, task: str, config: Config) -> Decision:
    # strict=True : l'enum est garanti chez OpenAI, simple indication ailleurs.
    llm = init_chat_model(orchestrator).with_structured_output(
        _decision_schema(config), strict=True
    )
    return Decision.model_validate(llm.invoke(_build_prompt(task, config)))


def _last_resort(config: Config) -> list[str]:
    """Modèles renvoyés quand aucun orchestrateur n'a répondu.

    D'abord des modèles découverts qui ne sont pas des orchestrateurs en échec, répartis
    entre fournisseurs. S'il n'y en a pas, les orchestrateurs eux-mêmes.
    """
    failed = set(config.orchestrators)
    others = [m for m in config.models if m.spec not in failed]
    providers = list(dict.fromkeys(m.provider for m in config.models))
    return pick_spread(others, providers, MAX_CANDIDATES) or config.orchestrators[:MAX_CANDIDATES]


def _error_text(e: Exception) -> str:
    msg = " ".join(str(e).split())
    return f"{type(e).__name__}: {msg[:300]}"


def route_with_reason(task: str, config: Config | None = None) -> RouteResult:
    """Choisit le modèle. Ne lève une exception que si aucun modèle n'est disponible
    (pas de clé, découverte impossible). Une panne d'orchestrateur ne casse pas l'appel :
    les orchestrateurs de secours sont essayés, puis Autoroute se replie sur un modèle découvert.
    """
    start = time.perf_counter()
    decision_id = uuid4().hex
    mode = "custom" if config else "all"
    elapsed = lambda: int((time.perf_counter() - start) * 1000)

    if config is None:
        try:
            mode = get_mode()
            config = discover_config(mode=mode)
        except Exception as e:
            log_decision(
                decision_id=decision_id,
                task=task,
                orchestrator="",
                model="",
                reason="",
                invalid_choice=False,
                latency_ms=elapsed(),
                mode=mode,
                error=_error_text(e),
            )
            raise

    decision: Decision | None = None
    failures: list[str] = []
    for orchestrator in config.orchestrators:
        try:
            decision = _ask(orchestrator, task, config)
            break
        except Exception as e:
            failures.append(f"{orchestrator} : {_error_text(e)}")

    if decision is None:
        # Dernier recours : l'application continue avec un modèle découvert.
        candidates = _last_resort(config)
        result = RouteResult(
            model=candidates[0],
            reason="Aucun orchestrateur n'a répondu : repli sur un modèle découvert.",
            candidates=candidates,
            routing_failed=True,
            decision_id=decision_id,
        )
    else:
        candidates = _candidates(config, decision.choices)
        if not candidates:
            result = RouteResult(
                model=orchestrator,
                reason=f"Choix invalide de l'orchestrateur ({decision.choices!r}), repli sur l'orchestrateur.",
                invalid_choice=True,
                candidates=[orchestrator],
                decision_id=decision_id,
            )
        else:
            result = RouteResult(
                model=candidates[0], reason=decision.reason, candidates=candidates,
                decision_id=decision_id,
            )

    log_decision(
        decision_id=decision_id,
        task=task,
        orchestrator=orchestrator if decision else "",
        model=result.model,
        candidates=result.candidates,
        reason=result.reason,
        invalid_choice=result.invalid_choice,
        routing_failed=result.routing_failed,
        latency_ms=elapsed(),
        mode=mode,
        error=" ; ".join(failures) or None,
    )
    return result


def route(task: str, config: Config | None = None) -> str:
    return route_with_reason(task, config).model


def route_candidates(task: str, config: Config | None = None) -> list[str]:
    """Le meilleur modèle puis ses replis éventuels (MAX_CANDIDATES - 1 au plus), pour with_fallbacks ou ModelFallbackMiddleware."""
    return route_with_reason(task, config).candidates
