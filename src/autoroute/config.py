import os
import warnings
from pathlib import Path

import yaml
from dotenv import dotenv_values, load_dotenv
from pydantic import BaseModel

# Dossier d'Autoroute dans le projet de l'utilisateur (le point évite un conflit de nom
# avec le paquet « autoroute » lors d'un import).
AUTOROUTE_DIR = Path(".autoroute")
DEFAULT_CONFIG = AUTOROUTE_DIR / "config.yaml"

# Écrit dans .autoroute/ : le log (texte des tâches) ne doit jamais partir dans git.
_DIR_GITIGNORE = "# Créé par Autoroute : le log contient le texte de tes tâches.\n*.jsonl\n"

ENV_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
    "mistralai": "MISTRAL_API_KEY",
}

MODES = {
    "all": ["openai", "anthropic", "google_genai", "mistralai"],
    "sovereign": ["mistralai"],
    "openai": ["openai"],
    "anthropic": ["anthropic"],
    "gemini": ["google_genai"],
}

# Familles de petits modèles rapides : l'orchestrateur est choisi parmi les modèles
# découverts dont le nom contient un de ces mots (le plus récent d'abord). Aucun nom de
# modèle exact n'est figé : si aucune famille n'est trouvée, un autre modèle découvert sert.
LIGHT_FAMILIES = ("haiku", "mini", "flash", "small")

class ModelConfig(BaseModel):
    name: str
    provider: str

    @property
    def spec(self) -> str:
        return f"{self.provider}:{self.name}"


class Config(BaseModel):
    orchestrator: str          # "provider:name", premier orchestrateur essayé
    models: list[ModelConfig]  # modèles découverts chez les fournisseurs
    backup_orchestrators: list[str] = []  # essayés dans l'ordre si l'orchestrateur échoue

    @property
    def orchestrators(self) -> list[str]:
        return [self.orchestrator, *self.backup_orchestrators]


def load_env() -> None:
    """Charge le .env du dossier courant. Les variables système gardent la priorité."""
    load_dotenv(Path.cwd() / ".env", override=False)


def ensure_parent(path: Path) -> None:
    """Crée le dossier de path. Pour .autoroute/, ajoute un .gitignore qui exclut le log."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.name == AUTOROUTE_DIR.name:
        gitignore = path.parent / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(_DIR_GITIGNORE, encoding="utf-8")


def config_path(path: str | None = None) -> Path:
    load_env()
    return Path(path or os.getenv("AUTOROUTE_CONFIG") or DEFAULT_CONFIG)


def load_orchestrator(path: str | None = None, mode: str = "all") -> str | None:
    """Orchestrateur imposé par l'utilisateur pour ce mode, ou None (choix automatique).

    Le fichier de config (.autoroute/config.yaml) est facultatif : sans fichier, ou sans entrée pour le mode, renvoie None.
    Un fichier illisible ne casse rien : avertissement, puis choix automatique (None).
    """
    p = config_path(path)
    if not p.exists():
        return None

    def unreadable(why: str) -> None:
        warnings.warn(f"{p.as_posix()} illisible ({why}) : choix automatique de l'orchestrateur.")

    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        unreadable(f"YAML invalide, ligne {mark.line + 1}" if mark else "YAML invalide")
        return None
    except (OSError, UnicodeDecodeError) as e:
        unreadable(type(e).__name__)
        return None

    orchestrators = data.get("orchestrators") if isinstance(data, dict) else None
    if orchestrators is None:
        if not isinstance(data, dict):
            unreadable("le fichier doit contenir « orchestrators: »")
        return None
    if not isinstance(orchestrators, dict):
        unreadable("« orchestrators » doit associer un mode à un modèle, ex. « all: provider:nom »")
        return None

    value = orchestrators.get(mode)
    if value is None:
        return None
    if not isinstance(value, str):
        unreadable(f"orchestrators.{mode} doit être un texte au format provider:nom")
        return None
    return value.strip() or None


def get_mode() -> str:
    load_env()
    value = (os.getenv("AUTOROUTE_MODE") or "all").strip().lower()
    if value not in MODES:
        raise ValueError(f"AUTOROUTE_MODE doit valoir : {', '.join(MODES)}")
    return value


def get_api_key(provider: str) -> str | None:
    load_env()
    env_name = ENV_KEYS.get(provider, f"{provider.upper()}_API_KEY")
    key = (os.getenv(env_name) or "").strip()
    return key or None



def key_source(provider: str) -> str:
    """D'où vient la clé du fournisseur : "system", ".env", "system (.env ignored)" ou "missing".

    Ne renvoie jamais la valeur de la clé.
    """
    load_env()
    name = ENV_KEYS[provider]
    current = (os.getenv(name) or "").strip()
    if not current:
        return "missing"
    in_file = (dotenv_values(Path.cwd() / ".env").get(name) or "").strip()
    if not in_file:
        return "system"
    return ".env" if in_file == current else "system (.env ignored)"
