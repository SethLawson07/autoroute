from pathlib import Path

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


def test_existing_gitignore_is_kept(monkeypatch):
    Path(".autoroute").mkdir()
    Path(".autoroute/.gitignore").write_text("*\n", encoding="utf-8")
    monkeypatch.setattr(observability, "LOG_PATH", Path(".autoroute/log.jsonl"))
    log_once()
    assert Path(".autoroute/.gitignore").read_text(encoding="utf-8") == "*\n"
