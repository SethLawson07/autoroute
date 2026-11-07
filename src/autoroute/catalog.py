import json
import re
import warnings

import httpx

from autoroute.config import (
    ENV_KEYS, LIGHT_FAMILIES, MODES, Config, ModelConfig, get_api_key, load_orchestrator,
)

SUPPORTED = MODES["all"]

_DATED = re.compile(r"^(.*?)-(\d{8}|\d{4}-\d{2}-\d{2}|\d{4})$")
_OPENAI_CHAT = ("gpt-", "chatgpt-", "o1", "o3", "o4")
_OPENAI_EXCLUDE = (
    "audio", "realtime", "image", "tts", "transcribe", "embedding",
    "moderation", "search", "instruct", "whisper", "dall-e", "codex",
)

_GEMINI_EXCLUDE = (
    "embedding", "imagen", "tts", "image", "live", "audio",
    "aqa", "veo", "learnlm", "robotics",
)

def list_models(provider: str) -> list[str]:
    key = get_api_key(provider)
    if key is None:
        raise ValueError(f"Clé manquante pour {provider}")

    if provider == "openai":
        r = httpx.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=30,
        )
        r.raise_for_status()
        return sorted(m["id"] for m in r.json()["data"])

    if provider == "anthropic":
        ids: list[str] = []
        params: dict = {"limit": 1000}
        while True:
            r = httpx.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                params=params,
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            ids += [m["id"] for m in data["data"]]
            if not data.get("has_more"):
                break
            params["after_id"] = data["last_id"]
        return sorted(ids)

    if provider == "google_genai":
        names: list[str] = []
        params: dict = {"pageSize": 1000}
        while True:
            r = httpx.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                headers={"x-goog-api-key": key},
                params=params,
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            names += [
                m["name"].removeprefix("models/")
                for m in data.get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])
            ]
            token = data.get("nextPageToken")
            if not token:
                break
            params["pageToken"] = token
        return sorted(names)

    if provider == "mistralai":
        r = httpx.get(
            "https://api.mistral.ai/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=30,
        )
        r.raise_for_status()
        return sorted(
            m["id"]
            for m in r.json()["data"]
            if m.get("capabilities", {}).get("completion_chat", False)
        )

    raise ValueError(f"Fournisseur non géré : {provider}")


def _dedupe_dated(ids: list[str]) -> list[str]:
    """Garde l'alias s'il existe, sinon la version datée la plus récente."""
    groups: dict[str, list[str]] = {}
    for i in ids:
        m = _DATED.match(i)
        groups.setdefault(m.group(1) if m else i, []).append(i)
    return sorted(base if base in group else max(group) for base, group in groups.items())


def list_chat_models(provider: str) -> list[str]:
    try:
        ids = list_models(provider)
    except httpx.HTTPStatusError as e:
        raise ValueError(f"clé refusée ou API indisponible ({e.response.status_code})")
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as e:
        # Format de réponse changé par le fournisseur : on ignore ce fournisseur, pas tout.
        raise ValueError(f"réponse inattendue de l'API ({type(e).__name__})") from e
    if provider == "openai":
        ids = [
            i for i in ids
            if i.startswith(_OPENAI_CHAT) and not any(x in i for x in _OPENAI_EXCLUDE)
        ]

    if provider == "google_genai":
        ids = [
            i for i in ids
            if i.startswith("gemini") and not any(x in i for x in _GEMINI_EXCLUDE)
        ]
    return _dedupe_dated(ids)


def _resolve_orchestrator(wanted: str, models: list[ModelConfig]) -> str:
    if ":" in wanted:
        provider, name = wanted.split(":", 1)
        matches = [m for m in models if m.provider == provider and m.name == name]
    else:
        matches = [m for m in models if m.name == wanted]

    if not matches:
        raise ValueError(
            f"Orchestrateur « {wanted} » introuvable parmi les modèles disponibles "
            "(clé API manquante ou modèle indisponible). Vérifie avec « autoroute models »."
        )
    if len(matches) > 1:
        raise ValueError(f"« {wanted} » existe chez plusieurs fournisseurs : écris provider:nom")
    return matches[0].spec


MAX_ORCHESTRATORS = 3  # l'orchestrateur + 2 de secours


def _orchestrator_rank(m: ModelConfig) -> tuple:
    """Clé de tri : petit modèle d'abord, stable avant preview, puis le plus récent."""
    name = m.name.lower()
    tokens = re.split(r"[-_.:/]", name)
    light = any(family in tokens for family in LIGHT_FAMILIES)
    unstable = any(t in tokens for t in ("preview", "exp", "experimental"))
    numbers = [int(n) for n in re.findall(r"\d+", name)]
    return (
        not light,
        unstable,
        "latest" not in tokens,               # un alias « latest » suit les nouvelles versions
        not numbers,                          # sans numéro : on ne sait pas dater
        tuple(-n for n in numbers),           # numéros les plus grands d'abord
        name,
    )


def _pick_orchestrators(mode: str, models: list[ModelConfig]) -> list[str]:
    """Jusqu'à MAX_ORCHESTRATORS orchestrateurs, choisis parmi les modèles découverts.

    Un fournisseur après l'autre (ordre du mode), pour qu'une panne chez l'un laisse un
    orchestrateur chez un autre. Aucun nom figé : la liste suit l'évolution des fournisseurs.
    """
    ranked = {
        p: sorted((m for m in models if m.provider == p), key=_orchestrator_rank)
        for p in MODES[mode]
    }
    picked: list[str] = []
    while len(picked) < MAX_ORCHESTRATORS and any(ranked.values()):
        for p in MODES[mode]:
            if ranked[p] and len(picked) < MAX_ORCHESTRATORS:
                picked.append(ranked[p].pop(0).spec)
    return picked


def discover_config(path: str | None = None, mode: str = "all") -> Config:
    wanted = load_orchestrator(path, mode)
    models: list[ModelConfig] = []
    errors: list[str] = []

    for provider in MODES[mode]:
        if get_api_key(provider) is None:
            continue
        try:
            models += [
                ModelConfig(name=name, provider=provider)
                for name in list_chat_models(provider)
            ]
        except (ValueError, httpx.HTTPError) as e:
            errors.append(f"{provider} : {e}")

    if errors and models:
        warnings.warn("Fournisseurs ignorés : " + " ; ".join(errors))
    if not models:
        keys = " ou ".join(ENV_KEYS[p] for p in MODES[mode])
        detail = f" Erreurs : {' ; '.join(errors)}" if errors else ""
        raise ValueError(f"Aucun modèle trouvé en mode « {mode} » : vérifie {keys} dans .env.{detail}")
    picked = _pick_orchestrators(mode, models)
    if wanted:
        try:
            forced = _resolve_orchestrator(wanted, models)
            picked = [forced] + [o for o in picked if o != forced][: MAX_ORCHESTRATORS - 1]
        except ValueError as e:
            # Ne pas casser l'application : on prévient et on garde le choix automatique.
            warnings.warn(f"{e} Choix automatique utilisé : {picked[0]}.")
    return Config(orchestrator=picked[0], backup_orchestrators=picked[1:], models=models)