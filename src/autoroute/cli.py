import re
import warnings
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Iterator, NoReturn

import typer

from autoroute.catalog import discover_config
from autoroute.config import ENV_KEYS, MODES, config_path, ensure_parent, get_mode, key_source
from autoroute.router import route_with_reason

app = typer.Typer(help="Autoroute : choisit le bon modèle LLM pour une tâche.")


def _short(e: Exception) -> str:
    msg = " ".join(str(e).split())
    return msg[:300] + "…" if len(msg) > 300 else msg


def _fail(e: Exception) -> NoReturn:
    # Les ValueError portent nos messages destinés à l'utilisateur : le type n'apporte rien.
    prefix = "Erreur" if isinstance(e, ValueError) else f"Erreur inattendue ({type(e).__name__})"
    typer.echo(f"{prefix} : {_short(e)}", err=True)
    raise typer.Exit(1)


def _show_warning(message, category, filename, lineno, file=None, line=None) -> None:
    typer.echo(f"Attention : {message}", err=True)


@contextmanager
def _cli() -> Iterator[None]:
    """Cadre commun des commandes : jamais de trace Python, avertissements en une ligne."""
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        warnings.showwarning = _show_warning
        try:
            yield
        except (typer.Exit, typer.Abort):
            raise
        except Exception as e:
            _fail(e)


def _template(name: str) -> str:
    return files("autoroute.templates").joinpath(name).read_text(encoding="utf-8")


@app.command()
def init(
    orchestrator: str | None = typer.Option(
        None, "--orchestrator", "-o",
        help="Orchestrateur du mode actif (AUTOROUTE_MODE), au format fournisseur:nom",
    ),
):
    """Crée .env.example et .autoroute/config.yaml (facultatif) s'ils n'existent pas."""
    with _cli():
        config_yaml = _template("config.yaml")
        if orchestrator:
            if ":" not in orchestrator:
                raise ValueError(f"« {orchestrator} » : écris l'orchestrateur au format fournisseur:nom")
            mode = get_mode()
            # Décommente la ligne du mode actif. Remplacement par fonction : la valeur est
            # insérée telle quelle, sans interprétation des « \ ».
            config_yaml = re.sub(
                rf"^\s*#?\s*{re.escape(mode)}:.*$",
                lambda _: f"  {mode}: {orchestrator}",
                config_yaml, flags=re.M,
            )
        files_to_create = {
            config_path(): config_yaml,
            Path(".env.example"): _template("env.example"),
        }
        for path, content in files_to_create.items():
            if path.exists():
                typer.echo(f"Déjà présent, non modifié : {path.as_posix()}")
            else:
                ensure_parent(path)
                path.write_text(content, encoding="utf-8")
                typer.echo(f"Créé : {path.as_posix()}")
        typer.echo("Étape suivante : copie .env.example en .env et renseigne tes clés API.")


@app.command()
def models():
    """Liste les modèles disponibles dans le mode actif."""
    with _cli():
        mode = get_mode()

        # Affiché avant la découverte : c'est quand elle échoue qu'on en a le plus besoin.
        typer.echo(f"Mode : {mode}")
        typer.echo("Clés :")
        for provider in MODES[mode]:
            typer.echo(f"  {ENV_KEYS[provider]:<18} {key_source(provider)}")

        config = discover_config(mode=mode)

        typer.echo(f"Orchestrateur : {config.orchestrator}")
        if config.backup_orchestrators:
            typer.echo(f"Orchestrateurs de secours : {', '.join(config.backup_orchestrators)}")
        for m in config.models:
            typer.echo(f"- {m.spec}")


@app.command("route")
def route_cmd(task: str):
    """Affiche le modèle choisi pour une tâche, et pourquoi."""
    with _cli():
        result = route_with_reason(task)
        if result.routing_failed:
            typer.echo("Attention : routage impossible, détail de l'erreur dans le log.", err=True)
        typer.echo(f"Modèle : {result.model}")
        typer.echo(f"Raison : {result.reason}")
        if len(result.candidates) > 1:
            typer.echo(f"Replis : {', '.join(result.candidates[1:])}")


if __name__ == "__main__":
    app()
