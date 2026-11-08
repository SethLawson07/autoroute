from pathlib import Path

from typer.testing import CliRunner

from autoroute import catalog
from helpers import CONFIG, write_config
from autoroute.cli import app
from autoroute.config import MODES, load_orchestrator

runner = CliRunner()


def test_init_creates_files():
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert "orchestrators:" in CONFIG.read_text(encoding="utf-8")
    assert "MISTRAL_API_KEY=" in Path(".env.example").read_text(encoding="utf-8")
    assert "*.jsonl" in Path(".autoroute/.gitignore").read_text(encoding="utf-8")
    assert "Créé : .autoroute/config.yaml" in result.output


def test_init_output_leaves_choice_automatic():
    runner.invoke(app, ["init"])
    for mode in MODES:
        assert load_orchestrator(mode=mode) is None


def test_init_orchestrator_sets_active_mode_only(monkeypatch):
    monkeypatch.setenv("AUTOROUTE_MODE", "anthropic")
    runner.invoke(app, ["init", "-o", "anthropic:claude-sonnet-5-5"])
    assert load_orchestrator(mode="anthropic") == "anthropic:claude-sonnet-5-5"
    assert load_orchestrator(mode="all") is None


def test_init_orchestrator_with_backslash():
    result = runner.invoke(app, ["init", "-o", r"openai:a\x"])
    assert result.exit_code == 0
    assert load_orchestrator() == r"openai:a\x"


def test_init_never_overwrites():
    custom = "orchestrators:\n  all: custom\n"
    write_config(custom)
    runner.invoke(app, ["init"])
    assert CONFIG.read_text(encoding="utf-8") == custom


def test_models_without_config_uses_default_orchestrator(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setattr(catalog, "list_chat_models", lambda p: ["claude-haiku-5-5"])
    result = runner.invoke(app, ["models"])
    assert result.exit_code == 0
    assert "Orchestrateur : anthropic:claude-haiku-5-5" in result.output


def test_models_shows_key_sources_without_values(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-system")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    Path(".env").write_text(
        "OPENAI_API_KEY=sk-secret-file\nANTHROPIC_API_KEY=sk-ant-secret\n", encoding="utf-8"
    )

    def refused(provider):
        raise ValueError("clé refusée ou API indisponible (401)")

    monkeypatch.setattr(catalog, "list_chat_models", refused)
    result = runner.invoke(app, ["models"])  # toutes les clés refusées : la découverte échoue
    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.output
    assert "system (.env ignored)" in result.output
    assert "GOOGLE_API_KEY     missing" in result.output
    assert "secret" not in result.output


def assert_clean(result):
    """La CLI a terminé normalement : pas d'exception Python remontée à l'utilisateur."""
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.output


def test_models_with_unreadable_config_warns_in_one_line(monkeypatch):
    write_config("orchestrators:\n  all: : :\n")
    monkeypatch.setattr(catalog, "list_chat_models", lambda p: [f"{p}-mini"])
    result = runner.invoke(app, ["models"])
    assert_clean(result)
    assert result.exit_code == 0
    assert "Attention : .autoroute/config.yaml illisible (YAML invalide, ligne 2)" in result.output
    assert "UserWarning" not in result.output


def test_models_unexpected_error_is_clean(monkeypatch):
    def boom(provider):
        raise RuntimeError("imprévu")

    monkeypatch.setattr(catalog, "list_chat_models", boom)
    result = runner.invoke(app, ["models"])
    assert_clean(result)
    assert result.exit_code == 1
    assert "Erreur inattendue (RuntimeError) : imprévu" in result.output


def test_models_invalid_mode_is_clean(monkeypatch):
    monkeypatch.setenv("AUTOROUTE_MODE", "inconnu")
    result = runner.invoke(app, ["models"])
    assert_clean(result)
    assert result.exit_code == 1
    assert "Erreur : AUTOROUTE_MODE doit valoir" in result.output


def test_init_rejects_orchestrator_without_provider():
    result = runner.invoke(app, ["init", "-o", "gpt-4o-mini"])
    assert_clean(result)
    assert result.exit_code == 1
    assert "fournisseur:nom" in result.output
    assert not CONFIG.exists()


def test_route_shows_orchestrator_failure_once(monkeypatch):
    monkeypatch.setattr(catalog, "list_chat_models", lambda p: [f"{p}-mini", f"{p}-large"])

    def boom(spec, **kw):
        raise RuntimeError("panne")

    monkeypatch.setattr("autoroute.router.init_chat_model", boom)
    result = runner.invoke(app, ["route", "Dis bonjour"])
    assert result.exit_code == 0
    assert result.output.count("Attention : Aucun orchestrateur n'a répondu") == 1
    assert "Modèle : " in result.output
