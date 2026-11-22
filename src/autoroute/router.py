import hashlib
import logging
import time
from uuid import uuid4

from langchain.chat_models import init_chat_model
from pydantic import BaseModel, ConfigDict, Field

from autoroute.catalog import discover_config, pick_spread
from autoroute.config import MODES, Config, ModelConfig, config_path, get_api_key, get_mode
from autoroute.observability import log_decision


MAX_CANDIDATES = 3  # le choix principal + 2 replis au plus

# Limite de temps par orchestrateur (principe 8 : ne jamais bloquer l'application).
# Sans elle, les SDK attendent jusqu'à 10 min par requête, et Gemini relance 6 fois
# après une erreur 429 (246 s mesurées avec une clé gratuite). Au-delà, on passe à
# l'orchestrateur de secours, puis au dernier recours.
ORCHESTRATOR_TIMEOUT = 20  # secondes par requête
ORCHESTRATOR_RETRIES = 1   # relance au plus

# Durée de vie du cache de la découverte (route() sans config) : évite de rappeler l'API
# de liste de chaque fournisseur à chaque routage.
DISCOVERY_TTL = 300  # secondes

# clé (mode, fichier de config, clés API) → (instant de la découverte, Config)
_discovery_cache: dict[tuple, tuple[float, Config]] = {}

# Pas de NullHandler : sans configuration, Python affiche quand même les WARNING sur stderr.
logger = logging.getLogger(__name__)


class Decision(BaseModel):
    choices: list[str]  # modèles au format provider:nom, le modèle choisi en premier
    language: str = ""  # langue de la tâche, écrite avant reason pour que la raison la suive
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

    model: str                                           # modèle choisi, au format demandé (to=)
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
    # Prompt et schéma en anglais : en français, Magistral rédigeait la raison en français même
    # pour une tâche en anglais (qualification du 2026-11-22). En anglais, les 4 orchestrateurs
    # suivent la langue de la tâche (essai : anglais, français, espagnol, 24/24).
    return {
        "title": "Decision",
        "description": "The sufficient model for the task, its fallbacks, and why.",
        "type": "object",
        "properties": {
            "choices": {
                "type": "array",
                "items": {"type": "string", "enum": [m.spec for m in config.models]},
                "description": (
                    f"1 to {MAX_CANDIDATES} distinct models as provider:name, "
                    "the chosen model first, then the fallbacks."
                ),
            },
            # Avant reason : l'orchestrateur nomme la langue de la tâche, puis rédige la raison.
            "language": {
                "type": "string",
                "description": "The language the task is written in (e.g. English, français, español).",
            },
            "reason": {
                "type": "string",
                "description": (
                    "Why the first model, in one or two sentences, written in the language "
                    "given in language (not necessarily English)."
                ),
            },
        },
        "required": ["choices", "language", "reason"],
        "additionalProperties": False,
    }


def _build_prompt(task: str, config: Config) -> str:
    lines = "\n".join(f"- {m.spec}" for m in config.models)
    return (
        "You are a model router. Pick the sufficient model for the task: the lightest one able "
        "to do it without losing quality, only from this list:\n"
        f"{lines}\n\n"
        "You only have the names: rely on what you know about these models. "
        "Only pick a powerful model if the task requires it; "
        "for a simple task, a lightweight model is enough.\n\n"
        f"Task:\n{task}\n\n"
        "Answer with the exact names, as they appear in the list, as provider:name "
        "(field choices): the chosen model first, then at most "
        f"{MAX_CANDIDATES - 1} fallback model(s) able to do the task, used if the first one fails. "
        "Then give the language the task is written in (field language), "
        "and the reason for the first choice written in that language (field reason).\n\n"
        "Language of the reason: the task's language, not the language of these instructions. "
        "These instructions are in English, but if the task is in French, the reason is in French; "
        "in Spanish, in Spanish; and so on. Write in English only if the task is in English."
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
    llm = init_chat_model(
        orchestrator, timeout=ORCHESTRATOR_TIMEOUT, max_retries=ORCHESTRATOR_RETRIES
    ).with_structured_output(
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


def _cache_key(mode: str) -> tuple:
    """Ce dont dépend la découverte : un changement de mode, de config.yaml ou de clé API
    invalide le cache. Les clés sont hachées : jamais gardées en clair hors de l'environnement."""
    path = config_path().resolve()
    mtime = path.stat().st_mtime if path.exists() else None
    keys = hashlib.sha256(
        "\n".join(get_api_key(p) or "" for p in MODES[mode]).encode()
    ).hexdigest()
    return (mode, str(path), mtime, keys)


def _cached_discovery(mode: str) -> Config:
    """discover_config(mode), gardé DISCOVERY_TTL secondes. Les échecs ne sont pas gardés."""
    key = _cache_key(mode)
    hit = _discovery_cache.get(key)
    if hit and time.monotonic() - hit[0] < DISCOVERY_TTL:
        return hit[1]
    config = discover_config(mode=mode)
    _discovery_cache.clear()  # une seule entrée : l'ancienne clé ne servira plus
    _discovery_cache[key] = (time.monotonic(), config)
    return config


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
        raise ValueError(f"Unknown to={to!r}. Accepted values: {', '.join(FORMATS)}.")
    start = time.perf_counter()
    decision_id = uuid4().hex
    mode = config.mode if config else "all"  # le log garde le mode de la découverte
    elapsed = lambda: int((time.perf_counter() - start) * 1000)

    if config is None:
        try:
            mode = get_mode()
            config = _cached_discovery(mode)
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
            failures.append(f"{orchestrator}: {_error_text(e)}")
            continue
        candidates = _candidates(config, decision.choices)
        if candidates:
            answered, reason = orchestrator, decision.reason
            break
        # Choix invalide : traité comme un échec, l'orchestrateur suivant a sa chance.
        invalid_choice = True
        failed.append(orchestrator)
        failures.append(f"{orchestrator}: invalid choice {decision.choices!r}")

    if answered is None:
        # Dernier recours : l'application continue avec un modèle découvert.
        candidates = _last_resort(config)
        # Texte fixe, en anglais : sans orchestrateur, aucun modèle ne peut le rédiger dans la
        # langue de la tâche (documenté dans le README, « Limites connues »).
        reason = "No orchestrator answered: falling back to a discovered model."

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
        error="; ".join(failures) or None,
    )
    if not result.routed:
        logger.warning(
            f"No orchestrator answered ({', '.join(failed)}): "
            f"falling back to {candidates[0]}. Error details in the log."
        )
    elif failed:
        logger.warning(
            f"Orchestrator failed: {', '.join(failed)}. Backup used: {answered}. "
            "Error details in the log."
        )
    return result
