import http.client
import json
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autoroute import ui
from autoroute.cli import app

runner = CliRunner()


def decision(**kw):
    event = {
        "schema_version": "3", "timestamp": "2026-11-19T10:00:00+00:00", "event": "decision",
        "decision_id": "id", "task": "Dis bonjour", "orchestrator": "openai:gpt-5-mini",
        "model": "openai:gpt-5-mini", "candidates": ["openai:gpt-5-mini"], "reason": "Simple",
        "invalid_choice": False, "routing_failed": False, "latency_ms": 1000, "mode": "all", "error": None,
    }
    event.update(kw)
    return event


def write_log(path: Path, events, extra_lines=()):
    lines = [json.dumps(e, ensure_ascii=False) for e in events] + list(extra_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- lecture du log ---

def test_status_of_each_decision():
    assert ui.status(decision()) == "ok"
    assert ui.status(decision(error="openai : quota")) == "backup"
    assert ui.status(decision(routing_failed=True, orchestrator="", error="panne")) == "fallback"
    assert ui.status(decision(model="", orchestrator="", error="Aucun modèle")) == "error"


def test_read_decisions_newest_first_and_skips_bad_lines(tmp_path):
    path = write_log(
        tmp_path / "log.jsonl",
        [decision(decision_id="a"), {"event": "outcome", "decision_id": "a"}, decision(decision_id="b")],
        extra_lines=["{pas du json", "[1, 2]", ""],
    )
    data = ui.read_decisions(path)
    assert [d["decision_id"] for d in data["decisions"]] == ["b", "a"]  # « outcome » ignoré
    assert data["total"] == 2
    assert data["skipped"] == 2
    assert data["decisions"][0]["status"] == "ok"


def test_read_decisions_keeps_only_the_most_recent(tmp_path):
    path = write_log(tmp_path / "log.jsonl", [decision(decision_id=str(i)) for i in range(5)])
    data = ui.read_decisions(path, limit=2)
    assert [d["decision_id"] for d in data["decisions"]] == ["4", "3"]
    assert (data["total"], data["shown"]) == (5, 2)


def test_missing_log_is_a_clean_error(tmp_path):
    with pytest.raises(ValueError, match="No log found"):
        ui.read_decisions(tmp_path / "absent.jsonl")


def test_summary(tmp_path):
    path = write_log(tmp_path / "log.jsonl", [
        decision(model="openai:gpt-5", latency_ms=1000),
        decision(model="openai:gpt-5", latency_ms=3000, invalid_choice=True, error="choix invalide",
                 orchestrator="anthropic:claude-haiku-5-5"),
        decision(model="anthropic:claude-sonnet-5-5", routing_failed=True, orchestrator="", latency_ms=5000),
        decision(model="", orchestrator="", latency_ms=1000, error="Aucun modèle"),
    ])
    s = ui.read_decisions(path)["summary"]
    assert s["count"] == 4
    assert s["routing_failed_pct"] == 25.0
    assert s["invalid_choice_pct"] == 25.0
    assert s["error_pct"] == 25.0
    assert s["mean_latency_ms"] == 2500
    # Seules les décisions routées comptent dans les répartitions.
    assert dict(s["models"]) == {"openai:gpt-5": 2}
    assert dict(s["orchestrators"]) == {"openai:gpt-5-mini": 1, "anthropic:claude-haiku-5-5": 1}


@pytest.mark.parametrize("value", ["off", "stdout"])
def test_ui_without_log_file_fails_cleanly(monkeypatch, value):
    monkeypatch.setenv("AUTOROUTE_LOG", value)
    result = runner.invoke(app, ["ui"])
    assert result.exit_code != 0
    assert "--log" in result.output
    assert "Traceback" not in result.output


def test_ui_log_option_works_with_log_off(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOROUTE_LOG", "off")
    path = write_log(tmp_path / "prod.jsonl", [decision()])
    assert ui.log_file(str(path)) == path


def test_log_file_defaults_to_autoroute_log(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOROUTE_LOG", str(tmp_path / "custom.jsonl"))
    assert ui.log_file() == tmp_path / "custom.jsonl"
    assert ui.log_file("autre.jsonl") == Path("autre.jsonl")


# --- serveur ---

@pytest.fixture
def server(tmp_path):
    path = write_log(tmp_path / "log.jsonl", [decision(task="<script>alert(1)</script>")])
    srv = ui.make_server(path, port=0)  # port libre choisi par le système
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def get(srv, path, host=None):
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers={"Host": host or f"127.0.0.1:{port}"})
    res = conn.getresponse()
    return res, res.read()


def test_server_listens_on_localhost_only(server):
    assert server.server_address[0] == "127.0.0.1"


def test_server_serves_page_with_strict_csp(server):
    res, body = get(server, "/")
    assert res.status == 200
    assert b"Autoroute" in body
    csp = res.getheader("Content-Security-Policy")
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp
    # Aucune ressource externe.
    assert b"http://" not in body.replace(b"http://127.0.0.1", b"") and b"https://" not in body


def test_server_api_returns_decisions(server):
    res, body = get(server, "/api/log")
    assert res.status == 200
    data = json.loads(body)
    assert data["decisions"][0]["task"] == "<script>alert(1)</script>"  # donnée brute, affichée en textContent


def test_server_rejects_foreign_host(server):
    # Protection contre le « DNS rebinding ».
    res, _ = get(server, "/api/log", host="evil.example:80")
    assert res.status == 403


def test_server_unknown_path(server):
    res, _ = get(server, "/../.env")
    assert res.status == 404


def test_page_never_uses_innerhtml():
    page = ui._page().decode("utf-8")
    assert "innerHTML" not in page and "insertAdjacentHTML" not in page


def test_busy_port_is_a_clean_error(server):
    with pytest.raises(ValueError, match="--port"):
        ui.make_server(Path("log.jsonl"), port=server.server_address[1])


# --- commande ---

def test_cli_ui_without_log_is_clean(tmp_path):
    result = runner.invoke(app, ["ui", "--log", str(tmp_path / "absent.jsonl")])
    assert result.exit_code == 1
    assert "No log found" in result.output
    assert "Traceback" not in result.output


def test_cli_ui_starts_server_and_opens_browser(tmp_path, monkeypatch):
    path = write_log(tmp_path / "log.jsonl", [decision()])
    opened = []

    class FakeServer:
        server_address = ("127.0.0.1", 8765)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            opened.append("closed")

    monkeypatch.setattr("autoroute.cli.make_server", lambda p, port: FakeServer())
    monkeypatch.setattr("autoroute.cli.webbrowser.open", lambda url: opened.append(url))
    result = runner.invoke(app, ["ui", "--log", str(path)])
    assert result.exit_code == 0, result.output
    assert "http://127.0.0.1:8765" in result.output
    assert opened == ["http://127.0.0.1:8765", "closed"]
