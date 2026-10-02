from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import uvicorn
from fastapi.testclient import TestClient

import pywatermarkcleaner.web as web
from pywatermarkcleaner.core.exceptions import MediaError
from pywatermarkcleaner.core.models import VideoMetadata
from pywatermarkcleaner.web import diagnostics, system
from pywatermarkcleaner.web.server import create_app

from .conftest import PORT, TOKEN


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("win32", "AppData"), ("darwin", "Application Support"), ("linux", ".config")],
)
def test_config_directory_per_platform(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, expected: str
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    directory = system.config_directory()
    assert expected in directory.parts and directory.name == "PyWatermarkCleaner"
    monkeypatch.setenv("APPDATA", str(tmp_path / "a"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "x"))
    assert system.config_directory().parent.name in {"a", "x", "Application Support"}


def test_save_settings_swallows_os_errors(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    system.save_settings(blocker / "settings.json", system.Settings())  # must not raise


def fake_run(stdout: str = "", error: Exception | None = None) -> Any:
    def run(command: list[str], **kwargs: Any) -> Any:
        run.command = command  # type: ignore[attr-defined]
        if error is not None:
            raise error
        return SimpleNamespace(stdout=stdout, returncode=0)

    return run


def test_pickers_parse_subprocess_json(monkeypatch: pytest.MonkeyPatch) -> None:
    run = fake_run(json.dumps(["a.mp4", "b.mp4"]))
    monkeypatch.setattr(subprocess, "run", run)
    assert system.pick_files() == ["a.mp4", "b.mp4"]
    assert run.command[-2:] == ["pywatermarkcleaner.web", "--pick-files"]
    assert system.pick_folder() == "a.mp4"

    monkeypatch.setattr(subprocess, "run", fake_run("[]"))
    assert system.pick_folder() is None
    monkeypatch.setattr(subprocess, "run", fake_run('{"not": "a list"}'))
    assert system.pick_files() == []
    monkeypatch.setattr(subprocess, "run", fake_run("garbage"))
    assert system.pick_files() == []
    monkeypatch.setattr(subprocess, "run", fake_run(error=OSError("no python")))
    assert system.pick_files() == []


def test_picker_command_when_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert system._picker_command("--pick-folder") == [sys.executable, "--pick-folder"]


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("win32", ["explorer", "/select,{p}"]),
        ("darwin", ["open", "-R", "{p}"]),
        ("linux", ["xdg-open", "{d}"]),
    ],
)
def test_reveal_in_folder_per_platform(
    monkeypatch: pytest.MonkeyPatch, platform: str, expected: list[str]
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "Popen", lambda command, **kw: calls.append(command))
    monkeypatch.setattr(sys, "platform", platform)
    target = Path("some") / "dir" / "out.mp4"
    system.reveal_in_folder(target)
    assert calls == [[part.format(p=target, d=target.parent) for part in expected]]


def test_build_proxy_reports_ffmpeg_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stderr=b"boom")
    )
    with pytest.raises(MediaError, match="boom"):
        system.build_proxy(tmp_path / "in.mkv", tmp_path / "out.mp4")


def test_proxy_endpoint_builds_and_serves_mp4(client: TestClient, added: dict[str, Any]) -> None:
    response = client.get(f"/api/videos/{added['id']}/proxy")
    assert response.status_code == 200 and response.headers["content-type"] == "video/mp4"


def test_proxy_endpoint_surfaces_failures(
    client: TestClient, added: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import pywatermarkcleaner.web.server as server

    def broken(source: Path, target: Path) -> None:
        raise MediaError("no ffmpeg")

    monkeypatch.setattr(server, "build_proxy", broken)
    response = client.get(f"/api/videos/{added['id']}/proxy")
    assert response.status_code == 500 and response.json()["detail"] == "no ffmpeg"


def test_diagnostics_lists_media_and_sanitizes_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meta = VideoMetadata(Path("a.mp4"), 10, 20, 30.0, 60, 2.0, "mp4")
    error = f"failed in {Path.home()}"
    text = diagnostics.build_diagnostics([meta], [error, ""])
    assert "a.mp4: 10x20, 2.00s, mp4" in text and "<home>" in text and str(Path.home()) not in text
    empty = diagnostics.build_diagnostics([], [])
    assert empty.count("- none") == 2

    def broken() -> str:
        raise RuntimeError("x")

    monkeypatch.setattr(diagnostics.imageio_ffmpeg, "get_ffmpeg_version", broken)
    assert "FFmpeg: unavailable" in diagnostics.build_diagnostics([], [])


def test_main_pick_modes_print_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(system, "run_tk_dialog", lambda mode: [mode])
    assert web.main(["--pick-folder"]) == 0
    assert json.loads(capsys.readouterr().out) == ["--pick-folder"]


def test_module_entry_point_exits_with_main_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(system, "run_tk_dialog", lambda mode: [])
    monkeypatch.setattr(sys, "argv", ["prog", "--pick-files"])
    with pytest.raises(SystemExit) as stop:
        runpy.run_module("pywatermarkcleaner.web", run_name="__main__")
    assert stop.value.code == 0


def test_main_smoke_test_fails_when_health_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise OSError("refused")

    monkeypatch.setattr(web.urllib.request, "urlopen", refuse)
    assert web.main(["--smoke-test"]) == 1


def test_main_serves_and_prints_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[bool] = []
    watched: list[dict[str, Any]] = []
    monkeypatch.setattr(uvicorn.Server, "run", lambda self, sockets=None: ran.append(True))
    monkeypatch.setattr(web, "_watch", lambda *a, **k: watched.append(k))
    assert web.main(["--no-browser"]) == 0
    assert ran and "?token=" in capsys.readouterr().out
    assert watched == [{"browser": False, "idle": web.IDLE_SECONDS}]


def watch_target(tmp_path: Path) -> tuple[uvicorn.Server, Any]:
    app = create_app(token=TOKEN, port=PORT, settings_path=tmp_path / "s.json")
    server = uvicorn.Server(uvicorn.Config(app))
    server.started = True
    return server, app


def test_watch_opens_browser_and_stops_when_idle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(web.webbrowser, "open", opened.append)
    server, app = watch_target(tmp_path)
    web._watch(server, app, "http://x/", browser=True, idle=0.0)
    assert opened == ["http://x/"] and server.should_exit


def test_watch_stays_alive_while_a_client_or_job_is_active(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    server, app = watch_target(tmp_path)
    app.state.jobs.has_active_jobs = lambda: True
    ticks = iter(range(3))

    def sleep(seconds: float) -> None:
        if next(ticks, None) is None:
            server.should_exit = True  # end the loop after a few busy ticks

    monkeypatch.setattr(web.time, "sleep", sleep)
    web._watch(server, app, "http://x/", browser=False, idle=0.0)
    assert server.should_exit


def test_watch_returns_if_server_exits_before_starting(tmp_path: Path) -> None:
    server, app = watch_target(tmp_path)
    server.started = False
    server.should_exit = True
    web._watch(server, app, "http://x/", browser=False, idle=0.0)
