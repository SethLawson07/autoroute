import logging
import time
from uuid import uuid4

from langchain.chat_models import init_chat_model
from pydantic import BaseModel, ConfigDict, Field

from autoroute.catalog import discover_config, pick_spread
from autoroute.config import Config, ModelConfig, get_mode
from autoroute.observability import log_decision


MAX_CANDIDATES = 3  # le choix principal + 2 replis au plus

# Pas de NullHandler : sans configuration, Python affiche quand même les WARNING sur stderr.
logger = logging.getLogger(__name__)


class Decision(BaseModel):
    choices: list[str]  # modèles au format provider:nom, le meilleur en premier
    reason: str         # pourquoi le premier choix, en une ou deux phrases


# Nom neutre de chaque fournisseur (identifiant LangChain → nom courant).
PROVIDERS = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google_genai": "google",
    "mistralai": "mistral",
}

# Formats de sortie (paramètre `to` de route()) : spec LangChain "provider:name" → chaîne.
FORMATS = {
    "langchain": lambda provider, name: f"{provider}:{name}",  # LangChain, LangGraph, Deep Agents
    "native": lambda provider, name: name,  # SDK officiels, LlamaIndex, Haystack
}


def _format(spec: str, to: str) -> str:
    provider, name = spec.split(":", 1)
    return FORMATS[to](provider, name)


def _provider(spec: str) -> str:
    provider = spec.split(":", 1)[0]
    return PROVIDERS.get(provider, provider)


class Route(BaseModel):
    """Résultat de route() : le modèle à utiliser, ses replis, et pourquoi."""

    model_config = ConfigDict(frozen=True)

    model: str                                           # meilleur modèle, au format demandé (to=)
    fallbacks: list[str] = Field(default_factory=list)   # 0 à 2 replis, même format, sans model
    provider: str = ""                                   # fournisseur de model : openai, anthropic, google, mistral
    providers: list[str] = Field(default_factory=list)   # fournisseurs de [model, *fallbacks], même ordre
    reason: str                                          # pourquoi ce modèle
    routed: bool = True  # False : aucun orchestrateur n'a répondu, model n'a pas été choisi pour la tâche
    id: str = Field(default_factory=lambda: uuid4().hex)  # decision_id dans le log

    def __str__(self) -> str:
        return repr(self)  # print(r) affiche Route(model=..., ...), plus lisible pour déboguer


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
                "description": "Pourquoi le premier modèle, en une ou deux phrases, dans la langue de la tâche.",
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
        "Donne la raison du premier choix (champ reason), "
        "rédigée dans la langue de la tâche (ex. en anglais si la tâche est en anglais)."
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


def route(task: str, config: Config | None = None, to: str = "langchain") -> Route:
    """Choisit le modèle. Ne lève une exception que si aucun modèle n'est disponible
    (pas de clé, découverte impossible) ou si `to` est inconnu. Un orchestrateur en panne,
    ou qui cite un modèle inexistant, ne casse pas l'appel : les orchestrateurs de secours
    sont essayés, puis Autoroute se replie sur un modèle découvert (routed=False).

    `to` choisit le format de model et fallbacks : "langchain" (défaut, "google_genai:gemini-…")
    ou "native" (nom officiel seul, "gemini-…", pour les SDK, LlamaIndex, Haystack).
    """
    if to not in FORMATS:
        # Erreur de code, pas une panne de routage : visible tout de suite, avant tout appel.
        raise ValueError(f"to={to!r} inconnu. Valeurs acceptées : {', '.join(FORMATS)}.")
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

    answered: str | None = None   # l'orchestrateur dont le choix a été retenu
    candidates: list[str] = []
    reason = ""
    invalid_choice = False
    failures: list[str] = []
    failed: list[str] = []
    for orchestrator in config.orchestrators:
        try:
            decision = _ask(orchestrator, task, config)
        except Exception as e:
            failed.append(orchestrator)
            failures.append(f"{orchestrator} : {_error_text(e)}")
            continue
        candidates = _candidates(config, decision.choices)
        if candidates:
            answered, reason = orchestrator, decision.reason
            break
        # Choix invalide : traité comme un échec, l'orchestrateur suivant a sa chance.
        invalid_choice = True
        failed.append(orchestrator)
        failures.append(f"{orchestrator} : choix invalide {decision.choices!r}")

    if answered is None:
        # Dernier recours : l'application continue avec un modèle découvert.
        candidates = _last_resort(config)
        reason = "Aucun orchestrateur n'a répondu : repli sur un modèle découvert."

    formatted = [_format(spec, to) for spec in candidates]
    providers = [_provider(spec) for spec in candidates]
    result = Route(
        model=formatted[0],
        fallbacks=formatted[1:],
        provider=providers[0],
        providers=providers,
        reason=reason,
        routed=answered is not None,
        id=decision_id,
    )

    log_decision(
        decision_id=decision_id,
        task=task,
        orchestrator=answered or "",
        model=candidates[0],  # le log garde le format "provider:name", quel que soit to
        candidates=candidates,
        reason=result.reason,
        invalid_choice=invalid_choice,
        routing_failed=not result.routed,
        latency_ms=elapsed(),
        mode=mode,
        error=" ; ".join(failures) or None,
    )
    if not result.routed:
        logger.warning(
            f"Aucun orchestrateur n'a répondu ({', '.join(failed)}) : "
            f"repli sur {candidates[0]}. Détail de l'erreur dans le log."
        )
    elif failed:
        logger.warning(
            f"Orchestrateur en échec : {', '.join(failed)}. Secours utilisé : {answered}. "
            "Détail de l'erreur dans le log."
        )
    return result
