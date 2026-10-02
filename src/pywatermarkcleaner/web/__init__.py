"""Local web application boundary."""

from __future__ import annotations

import json
import secrets
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from collections.abc import Sequence

IDLE_SECONDS = 30.0


def _free_socket() -> socket.socket:
    """Bind a loopback socket on a free port; uvicorn serves on this very socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    return sock


def _watch(server: object, app: object, url: str, *, browser: bool, idle: float) -> None:
    """Open the browser once up, then stop the server after ``idle`` s with no client or job."""
    import uvicorn

    assert isinstance(server, uvicorn.Server)
    while not server.started:
        if server.should_exit:
            return
        time.sleep(0.05)
    if browser:
        webbrowser.open(url)
    session = app.state.session  # type: ignore[attr-defined]
    jobs = app.state.jobs  # type: ignore[attr-defined]
    quiet_since = time.monotonic()
    while not server.should_exit:
        time.sleep(0.5)
        if session.client_count or jobs.has_active_jobs():
            quiet_since = time.monotonic()
        elif time.monotonic() - quiet_since > idle:
            server.should_exit = True


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    # Hidden picker modes run inside a child process (see system.py).
    for mode in ("--pick-files", "--pick-folder"):
        if mode in args:
            from .system import run_tk_dialog

            print(json.dumps(run_tk_dialog(mode)))
            return 0

    import uvicorn

    from .server import create_app

    sock = _free_socket()
    port = int(sock.getsockname()[1])
    token = secrets.token_urlsafe(32)
    app = create_app(token=token, port=port)
    server = uvicorn.Server(
        uvicorn.Config(app, log_config=None, log_level="warning", timeout_graceful_shutdown=1)
    )

    if "--smoke-test" in args:
        thread = threading.Thread(target=server.run, args=([sock],), daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=10) as r:
                ok = bool(json.load(r).get("ok"))
        except (OSError, ValueError):
            ok = False
        finally:
            server.should_exit = True
            thread.join(timeout=5)
        return 0 if ok else 1

    url = f"http://127.0.0.1:{port}/?token={token}"
    if sys.stdout is not None:
        print(f"PyWatermarkCleaner running at {url}", flush=True)
    watcher = threading.Thread(
        target=_watch,
        args=(server, app, url),
        kwargs={"browser": "--no-browser" not in args, "idle": IDLE_SECONDS},
        daemon=True,
    )
    watcher.start()
    server.run([sock])
    return 0


__all__ = ["main"]
