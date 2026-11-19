"""`autoroute ui` : lecture du log dans une page web locale.

Lecture seule, sur 127.0.0.1 uniquement, sans ressource externe ni dépendance : le log
contient les prompts, rien ne doit sortir de la machine.
"""
import json
import socket
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path

from autoroute.observability import _log_path

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_DECISIONS = 1000  # les plus récentes : un gros log reste rapide à afficher

# Inline uniquement, aucune requête vers l'extérieur.
_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def log_file(path: str | None = None) -> Path:
    """Le log à lire : --log, sinon AUTOROUTE_LOG, sinon .autoroute/log.jsonl."""
    return Path(path) if path else _log_path()


def status(event: dict) -> str:
    """ok, backup (un orchestrateur de secours a répondu), fallback (routed=False), error (aucun modèle)."""
    if not event.get("model"):
        return "error"
    if event.get("routing_failed"):
        return "fallback"
    if event.get("error"):
        return "backup"
    return "ok"


def read_decisions(path: Path, limit: int = MAX_DECISIONS) -> dict:
    """Les `limit` dernières décisions du log, les plus récentes en premier.

    Les lignes illisibles et les autres événements (ex. anciens « outcome ») sont ignorés.
    """
    if not path.is_file():
        raise ValueError(
            f"Aucun log trouvé : {path.as_posix()}. Lance d'abord un routage "
            "(autoroute route \"…\"), ou indique un fichier avec --log."
        )
    decisions: list[dict] = []
    skipped = 0
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(event, dict):
                skipped += 1
                continue
            if event.get("event") == "decision":
                event["status"] = status(event)
                decisions.append(event)
    total = len(decisions)
    recent = decisions[-limit:][::-1]
    return {
        "path": path.as_posix(),
        "total": total,
        "shown": len(recent),
        "skipped": skipped,
        "summary": summarize(recent),
        "decisions": recent,
    }


def summarize(decisions: list[dict]) -> dict:
    n = len(decisions)
    routed = [d for d in decisions if d["status"] in ("ok", "backup")]
    latencies = [d["latency_ms"] for d in decisions if isinstance(d.get("latency_ms"), (int, float))]
    rate = lambda k: round(100 * k / n, 1) if n else 0.0
    return {
        "count": n,
        "routing_failed_pct": rate(sum(d["status"] == "fallback" for d in decisions)),
        "invalid_choice_pct": rate(sum(bool(d.get("invalid_choice")) for d in decisions)),
        "error_pct": rate(sum(d["status"] == "error" for d in decisions)),
        "mean_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "models": Counter(d["model"] for d in routed).most_common(),
        "orchestrators": Counter(d["orchestrator"] for d in routed if d.get("orchestrator")).most_common(),
    }


def _page() -> bytes:
    return files("autoroute.templates").joinpath("ui.html").read_bytes()


def make_handler(path: Path):
    page = _page()

    class Handler(BaseHTTPRequestHandler):
        server_version = "autoroute-ui"
        sys_version = ""

        def log_message(self, format, *args) -> None:  # pas de bruit dans le terminal
            pass

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", _CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            # Contre le « DNS rebinding » : une page d'un autre site ne doit pas lire le log.
            port = self.server.server_address[1]
            if self.headers.get("Host") not in (f"{HOST}:{port}", f"localhost:{port}"):
                self._send(403, b"Forbidden", "text/plain; charset=utf-8")
                return
            route = self.path.split("?", 1)[0]
            if route == "/":
                self._send(200, page, "text/html; charset=utf-8")
            elif route == "/api/log":
                try:
                    data = read_decisions(path)
                except ValueError as e:
                    data = {"path": path.as_posix(), "error": str(e)}
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
            else:
                self._send(404, b"Not found", "text/plain; charset=utf-8")

    return Handler


class _Server(ThreadingHTTPServer):
    # Sous Windows, SO_REUSEADDR (actif par défaut dans HTTPServer) laisse deux serveurs
    # écouter le même port : on veut une erreur claire à la place.
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(path: Path, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    try:
        server = _Server((HOST, port), make_handler(path))
    except OSError as e:
        raise ValueError(
            f"Impossible d'ouvrir le port {port} ({e.strerror or e}) : il est peut-être déjà utilisé. "
            "Essaie un autre port avec --port."
        ) from None
    return server
