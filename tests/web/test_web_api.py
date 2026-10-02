from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi.testclient import TestClient

from pywatermarkcleaner.web.server import COOKIE_NAME, create_app, event_stream
from pywatermarkcleaner.web.system import Settings, load_settings

from .conftest import BASE_URL, PORT, TOKEN, FakeExporter, make_video, wait_for

GOOD = {"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5}


def test_health_needs_no_cookie_but_checks_host(tmp_path: Path) -> None:
    app = create_app(token=TOKEN, port=PORT, settings_path=tmp_path / "s.json")
    assert TestClient(app, base_url=BASE_URL).get("/api/health").json()["ok"] is True
    evil = TestClient(app, base_url="http://evil.example:8765")
    assert evil.get("/api/health").status_code == 403


def test_api_requires_cookie_and_valid_host(client: TestClient) -> None:
    client.cookies.clear()
    assert client.get("/api/state").status_code == 401
    client.cookies.set(COOKIE_NAME, "wrong")
    assert client.get("/api/state").status_code == 401
    client.cookies.set(COOKIE_NAME, TOKEN)
    assert client.get("/api/state").status_code == 200
    rebinding = TestClient(client.app, base_url="http://attacker.test:8765")
    rebinding.cookies.set(COOKIE_NAME, TOKEN)
    assert rebinding.get("/api/state").status_code == 403
    localhost = TestClient(client.app, base_url=f"http://localhost:{PORT}")
    localhost.cookies.set(COOKIE_NAME, TOKEN)
    assert localhost.get("/api/state").status_code == 200


def test_token_is_swapped_for_httponly_strict_cookie(client: TestClient) -> None:
    client.cookies.clear()
    response = client.get(f"/?token={TOKEN}", follow_redirects=False)
    assert response.status_code == 303
    header = response.headers["set-cookie"].lower()
    assert f"{COOKIE_NAME}={TOKEN}".lower() in header
    assert "httponly" in header and "samesite=strict" in header
    bad = client.get("/?token=nope", follow_redirects=False)
    assert bad.status_code == 200 and "set-cookie" not in bad.headers
    assert "PyWatermarkCleaner" in client.get("/anything/deep").text  # SPA fallback/placeholder


def test_region_state_machine(client: TestClient, added: dict[str, Any]) -> None:
    assert added["state"] == "needs_region" and added["needs_proxy"] is False
    assert added["width"] == 64 and added["fps"] == 10.0
    url = f"/api/videos/{added['id']}/region"
    item = client.put(url, json=GOOD).json()
    assert item["state"] == "ready" and item["region"] == GOOD
    # 1x1 source pixels is below the 2x2 minimum.
    tiny = {"x": 0.0, "y": 0.0, "width": 1 / 64, "height": 1 / 48}
    item = client.put(url, json=tiny).json()
    assert item["state"] == "needs_region" and item["region"] is None
    client.put(url, json=GOOD)
    item = client.put(url, content="null", headers={"content-type": "application/json"}).json()
    assert item["state"] == "needs_region"
    assert client.put("/api/videos/nope/region", json=GOOD).status_code == 404


def test_apply_all_copies_region_and_counts_skipped(
    client: TestClient, tmp_path: Path, picked: list[str], added: dict[str, Any]
) -> None:
    other = make_video(tmp_path / "tiny.mp4", width=4, height=4)
    picked[:] = [str(other)]
    client.post("/api/videos/pick")
    client.put(f"/api/videos/{added['id']}/region", json=GOOD)
    body = client.post("/api/region/apply-all", json={"source_id": added["id"]}).json()
    states = {i["name"]: i["state"] for i in body["state"]["items"]}
    assert body["skipped"] == 0 and states == {"clip.mp4": "ready", "tiny.mp4": "ready"}
    # 10% of a 4x4 video is a single pixel: valid on the 64x48 source, skipped on the other.
    small = {"x": 0.25, "y": 0.25, "width": 0.1, "height": 0.1}
    client.put(f"/api/videos/{added['id']}/region", json=small)
    body = client.post("/api/region/apply-all", json={"source_id": added["id"]}).json()
    states = {i["name"]: i["state"] for i in body["state"]["items"]}
    assert body["skipped"] == 1 and states["tiny.mp4"] == "needs_region"


def test_duplicates_are_ignored_and_remove_works(client: TestClient, added: dict[str, Any]) -> None:
    assert len(client.post("/api/videos/pick").json()["items"]) == 1
    state = client.delete(f"/api/videos/{added['id']}").json()
    assert state["items"] == []
    assert client.delete(f"/api/videos/{added['id']}").status_code == 404


def test_settings_validation_and_persistence(client: TestClient, tmp_path: Path) -> None:
    ok = client.put("/api/settings", json={"radius": 5, "method": "navier-stokes"})
    assert ok.status_code == 200 and ok.json()["radius"] == 5
    assert client.put("/api/settings", json={"radius": 99}).status_code == 400
    assert client.put("/api/settings", json={"method": "magic"}).status_code == 422
    assert isinstance(client.put("/api/settings", json={"workers": 0}).json()["detail"], str)
    stored = load_settings(tmp_path / "config" / "settings.json")
    assert (stored.radius, stored.method) == (5, "navier-stokes")
    folder = client.post("/api/output-folder/pick").json()["output_folder"]
    assert folder == str((tmp_path / "out").resolve())


def test_settings_load_ignores_garbage(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    path.write_text('{"radius": "x", "workers": 999, "method": "telea"}')
    loaded = load_settings(path)
    assert loaded.radius == Settings().radius and loaded.workers == 1
    path.write_text("not json")
    assert load_settings(path).radius == 3


def test_source_supports_range(client: TestClient, added: dict[str, Any], video: Path) -> None:
    response = client.get(f"/api/videos/{added['id']}/source", headers={"Range": "bytes=0-9"})
    assert response.status_code == 206
    assert response.content == video.read_bytes()[:10]
    assert response.headers["content-range"].startswith("bytes 0-9/")
    assert client.get("/api/videos/missing/source").status_code == 404


def test_frame_returns_jpeg_and_cleaned_requires_region(
    client: TestClient, added: dict[str, Any]
) -> None:
    base = f"/api/videos/{added['id']}/frame"
    assert client.get(base, params={"t": 0, "cleaned": 1}).status_code == 409
    raw = client.get(base, params={"t": 1000})
    assert raw.status_code == 200 and raw.headers["content-type"] == "image/jpeg"
    image = cv2.imdecode(np.frombuffer(raw.content, np.uint8), cv2.IMREAD_COLOR)
    assert image.shape == (48, 64, 3)
    assert abs(int(image.mean()) - 80) < 12  # frame 10 -> brightness 80
    # Sequential, repeated and backwards reads all work through the single cached capture.
    for t in (1100, 1100, 1500, 200, 2900):
        assert client.get(base, params={"t": t}).status_code == 200
    client.put(f"/api/videos/{added['id']}/region", json=GOOD)
    cleaned = client.get(base, params={"t": 1000, "cleaned": 1})
    assert cleaned.status_code == 200 and cleaned.headers["content-type"] == "image/jpeg"


def test_preview_clip_renders_playable_mp4(client: TestClient, added: dict[str, Any]) -> None:
    url = f"/api/videos/{added['id']}/preview-clip"
    assert client.post(url, params={"t": 0}).status_code == 409
    client.put(f"/api/videos/{added['id']}/region", json=GOOD)
    reply = client.post(url, params={"t": 500})
    assert reply.status_code == 200, reply.text
    clip = client.get(reply.json()["url"])
    assert clip.status_code == 200 and clip.headers["content-type"] == "video/mp4"
    assert len(clip.content) > 100
    assert reply.json()["url"].endswith("?v=1")


def test_upload_streams_into_workspace_and_is_removed(
    client: TestClient, tmp_path: Path, video: Path
) -> None:
    with video.open("rb") as handle:
        state = client.post("/api/videos/upload", files=[("files", ("up.mp4", handle))]).json()
    assert [i["name"] for i in state["items"]] == ["up.mp4"]
    session = client.app.state.session  # type: ignore[attr-defined]
    stored = session.items[0].path
    assert stored.is_file()
    client.delete(f"/api/videos/{state['items'][0]['id']}")
    assert not stored.exists()


def test_unreadable_files_are_reported_not_added(
    client: TestClient, tmp_path: Path, picked: list[str]
) -> None:
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    picked[:] = [str(bad)]
    assert client.post("/api/videos/pick").json()["items"] == []


def test_start_cancel_retry_cycle(
    client: TestClient, added: dict[str, Any], exporter: FakeExporter, tmp_path: Path
) -> None:
    item_id = added["id"]
    assert client.post("/api/jobs/start", json={}).status_code == 400  # no region yet
    client.put(f"/api/videos/{item_id}/region", json=GOOD)
    client.put("/api/settings", json={"output_folder": str(tmp_path / "out")})

    exporter.gate.clear()
    state = client.post("/api/jobs/start", json={"format_policy": "mp4"}).json()
    assert state["running"] is True and state["settings"]["format_policy"] == "mp4"
    wait_for(lambda: len(exporter.requests) == 1)
    assert client.post(f"/api/jobs/{item_id}/retry").status_code == 409  # still active
    client.post(f"/api/jobs/{item_id}/cancel")
    wait_for(lambda: client.get("/api/state").json()["items"][0]["state"] == "canceled")
    assert client.get("/api/state").json()["running"] is False

    exporter.fail = True
    exporter.gate.set()
    assert client.post(f"/api/jobs/{item_id}/retry").status_code == 200
    wait_for(lambda: client.get("/api/state").json()["items"][0]["state"] == "failed")
    assert client.get("/api/state").json()["items"][0]["error"] == "disk full"

    exporter.fail = False
    client.post(f"/api/jobs/{item_id}/retry")
    wait_for(lambda: client.get("/api/state").json()["items"][0]["state"] == "completed")
    done = client.get("/api/state").json()["items"][0]
    assert done["progress"] == 100 and Path(done["output_path"]).is_file()
    assert done["output_path"].endswith(".mp4")
    assert client.get("/api/state").json()["batch_progress"] == 100
    assert client.post(f"/api/videos/{item_id}/reveal").json() == {"ok": True}


def test_scheduler_is_rebuilt_with_the_current_worker_count(
    client: TestClient, added: dict[str, Any], exporter: FakeExporter
) -> None:
    client.put(f"/api/videos/{added['id']}/region", json=GOOD)
    built: list[int] = []
    jobs = client.app.state.jobs  # type: ignore[attr-defined]
    real = jobs.scheduler_factory

    def factory(exp: Any, workers: int) -> Any:
        built.append(workers)
        return real(exp, workers)

    jobs.scheduler_factory = factory
    client.post("/api/jobs/start", json={})
    wait_for(lambda: client.get("/api/state").json()["items"][0]["state"] == "completed")
    client.put("/api/settings", json={"workers": 1})
    client.post(f"/api/jobs/{added['id']}/retry")
    wait_for(lambda: len(built) == 2)


def test_event_stream_sends_state_then_items_and_logs(client: TestClient) -> None:
    session = client.app.state.session  # type: ignore[attr-defined]

    async def collect() -> list[dict[str, Any]]:
        stream = event_stream(session)
        first = json.loads((await anext(stream))[len("data: ") :])
        assert session.client_count == 1
        session.log("hello")
        second = json.loads((await anext(stream))[len("data: ") :])
        await stream.aclose()
        return [first, second]

    first, second = asyncio.run(collect())
    assert first["type"] == "state" and second == {"type": "log", "message": "hello"}
    assert session.client_count == 0


def test_diagnostics_has_no_qt_line(client: TestClient, added: dict[str, Any]) -> None:
    text = client.get("/api/diagnostics").json()["text"]
    assert "clip.mp4: 64x48" in text and "Qt" not in text
