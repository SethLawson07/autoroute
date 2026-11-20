import logging
import re
import webbrowser
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Iterator, NoReturn

import typer

from autoroute.catalog import discover_config
from autoroute.config import ENV_KEYS, MODES, config_path, ensure_parent, get_mode, key_source
from autoroute.router import route
from autoroute.ui import DEFAULT_PORT, HOST, log_file, make_server, read_decisions

app = typer.Typer(help="Autoroute : choisit le bon modèle LLM pour une tâche.")


def _short(e: Exception) -> str:
    msg = " ".join(str(e).split())
    return msg[:300] + "…" if len(msg) > 300 else msg


def _fail(e: Exception) -> NoReturn:
    # Les ValueError portent nos messages destinés à l'utilisateur : le type n'apporte rien.
    prefix = "Erreur" if isinstance(e, ValueError) else f"Erreur inattendue ({type(e).__name__})"
    typer.echo(f"{prefix} : {_short(e)}", err=True)
    raise typer.Exit(1)


class _Attention(logging.Handler):
    """Affiche les avertissements d'Autoroute en une ligne, sans nom de logger."""

    def emit(self, record: logging.LogRecord) -> None:
        typer.echo(f"Attention : {record.getMessage()}", err=True)


@contextmanager
def _cli() -> Iterator[None]:
    """Cadre commun des commandes : jamais de trace Python, avertissements en une ligne."""
    logger = logging.getLogger("autoroute")
    handler = _Attention(logging.WARNING)
    logger.addHandler(handler)
    propagate, logger.propagate = logger.propagate, False  # pas de second affichage
    try:
        yield
    except (typer.Exit, typer.Abort):
        raise
    except Exception as e:
        _fail(e)
    finally:
        logger.removeHandler(handler)
        logger.propagate = propagate


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
        r = route(task)  # les échecs d'orchestrateur sont affichés en avertissement
        typer.echo(f"Modèle : {r.model}")
        typer.echo(f"Replis : {', '.join(r.fallbacks) or 'aucun'}")
        typer.echo(f"Raison : {r.reason}")
        typer.echo(f"Routé  : {'oui' if r.routed else 'non (modèle par défaut)'}")
        typer.echo(f"Id     : {r.id}")


@app.command()
def ui(
    log: str | None = typer.Option(
        None, "--log", help="Fichier de log à lire (défaut : AUTOROUTE_LOG, sinon .autoroute/log.jsonl ; requis si AUTOROUTE_LOG vaut stdout ou off)",
    ),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="Port local du serveur"),
):
    """Ouvre le log dans le navigateur (serveur local, lecture seule)."""
    with _cli():
        path = log_file(log)
        read_decisions(path, limit=1)  # log absent ou illisible : erreur propre avant de démarrer
        server = make_server(path, port)
        url = f"http://{HOST}:{server.server_address[1]}"
        typer.echo(f"Log : {path.as_posix()}")
        typer.echo(f"Interface : {url}  (Ctrl+C pour arrêter)")
        typer.echo("Accessible depuis cette machine uniquement : le log contient tes prompts.")
        webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            typer.echo("Arrêté.")
        finally:
            server.server_close()


if __name__ == "__main__":
    app()
