import json
from pathlib import Path

import pytest

from autoroute import observability
from autoroute.observability import log_decision


def log_once():
    log_decision(
        task="tâche", orchestrator="orch", model="modèle",
        reason="raison", invalid_choice=False, latency_ms=1,
    )


def test_log_path_from_env(monkeypatch, tmp_path):
    custom = tmp_path / "sub" / "custom.jsonl"
    monkeypatch.setenv("AUTOROUTE_LOG", str(custom))
    log_once()
    assert custom.exists()
    assert not (custom.parent / ".gitignore").exists()  # dossier choisi : on n'y touche pas


def test_default_log_goes_to_autoroute_dir_with_gitignore(monkeypatch):
    monkeypatch.setattr(observability, "LOG_PATH", Path(".autoroute/log.jsonl"))
    log_once()
    assert Path(".autoroute/log.jsonl").exists()
    gitignore = Path(".autoroute/.gitignore").read_text(encoding="utf-8")
    assert "*.jsonl" in gitignore.splitlines()


@pytest.mark.parametrize("value", ["stdout", " STDOUT "])
def test_log_to_stdout(monkeypatch, capsys, value):
    monkeypatch.setenv("AUTOROUTE_LOG", value)
    log_once()
    event = json.loads(capsys.readouterr().out)
    assert event["event"] == "decision"
    assert event["task"] == "tâche"
    assert not Path(value.strip()).exists()  # pas de fichier nommé « stdout »
    assert not observability.LOG_PATH.exists()


@pytest.mark.parametrize("value", ["off", "OFF"])
def test_log_off_writes_nothing(monkeypatch, capsys, caplog, value):
    monkeypatch.setenv("AUTOROUTE_LOG", value)
    log_once()
    assert capsys.readouterr().out == ""
    assert not Path(value).exists()
    assert not observability.LOG_PATH.exists()
    assert not caplog.records  # off est un choix, pas un incident : aucun WARNING


def test_route_with_log_off(monkeypatch, config, fake_llm):
    from autoroute import route
    from autoroute.router import Decision

    monkeypatch.setenv("AUTOROUTE_LOG", "off")
    fake_llm(Decision(choices=["openai:gpt-4o-mini"], reason="Simple"))
    assert route("Dis bonjour", config).routed is True


def unwritable_log(monkeypatch, tmp_path) -> Path:
    """Un chemin de log impossible à créer : son « dossier » est un fichier."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("", encoding="utf-8")
    path = blocker / "log.jsonl"
    monkeypatch.setenv("AUTOROUTE_LOG", str(path))
    return path


def test_unwritable_log_warns_once_and_does_not_raise(monkeypatch, tmp_path, caplog):
    path = unwritable_log(monkeypatch, tmp_path)
    with caplog.at_level("WARNING", logger="autoroute"):
        log_once()
        log_once()
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "Cannot write log" in warnings[0]
    assert "tâche" not in warnings[0]  # le texte de la tâche ne fuit pas dans le WARNING
    assert not path.exists()


def test_route_survives_unwritable_log(monkeypatch, tmp_path, config, fake_llm):
    from autoroute import route
    from autoroute.router import Decision

    unwritable_log(monkeypatch, tmp_path)
    fake_llm(Decision(choices=["openai:gpt-4o-mini"], reason="Simple"))
    r = route("Dis bonjour", config)
    assert r.model == "openai:gpt-4o-mini"
    assert r.routed is True


def test_existing_gitignore_is_kept(monkeypatch):
    Path(".autoroute").mkdir()
    Path(".autoroute/.gitignore").write_text("*\n", encoding="utf-8")
    monkeypatch.setattr(observability, "LOG_PATH", Path(".autoroute/log.jsonl"))
    log_once()
    assert Path(".autoroute/.gitignore").read_text(encoding="utf-8") == "*\n"
