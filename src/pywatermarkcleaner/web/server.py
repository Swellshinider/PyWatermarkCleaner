"""FastAPI application: authenticated, localhost-only HTTP API plus the static frontend."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import secrets
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

import cv2
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from pywatermarkcleaner import __version__
from pywatermarkcleaner.core.cancellation import CancellationToken
from pywatermarkcleaner.core.exceptions import MediaError, PreviewError
from pywatermarkcleaner.core.geometry import NormalizedRegion
from pywatermarkcleaner.core.media import OpenCVMediaReader
from pywatermarkcleaner.core.models import (
    InpaintMethod,
    JobState,
    PreviewRequest,
    ProcessingOptions,
)
from pywatermarkcleaner.core.preview import (
    PreviewService,
    render_preview_clip,
    scale_to_long_edge,
)
from pywatermarkcleaner.core.scheduler import JobScheduler

from .diagnostics import build_diagnostics
from .jobs import JobController
from .session import Item, Session
from .system import (
    build_proxy,
    config_directory,
    load_settings,
    pick_files,
    pick_folder,
    reveal_in_folder,
    save_settings,
)

COOKIE_NAME = "pwc_session"
STATIC_DIR = Path(__file__).parent / "static"
CLIP_SECONDS = 5.0
_ACTIVE = {JobState.QUEUED, JobState.PROCESSING}
_PLACEHOLDER = (
    "<!doctype html><title>PyWatermarkCleaner</title><body style='font:16px sans-serif;"
    "margin:3rem'><h1>PyWatermarkCleaner</h1><p>The web interface has not been built. "
    "Run <code>pnpm --dir frontend install</code> and <code>pnpm --dir frontend run "
    "build</code>, then reload.</p></body>"
)


class RegionBody(BaseModel):
    x: float
    y: float
    width: float
    height: float


class SettingsPatch(BaseModel):
    method: Literal["telea", "navier-stokes"] | None = None
    radius: int | None = None
    performance: Literal["fast", "balanced", "quality"] | None = None
    workers: int | None = None
    output_folder: str | None = None
    format_policy: Literal["original", "mp4"] | None = None


class StartBody(BaseModel):
    format_policy: Literal["original", "mp4"] | None = None


class ApplyAllBody(BaseModel):
    source_id: str


def _fail(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


def create_app(
    *,
    token: str,
    port: int,
    settings_path: Path | None = None,
    exporter: Any | None = None,
    scheduler_factory: Callable[..., Any] = JobScheduler,
    file_picker: Callable[[], list[str]] = pick_files,
    folder_picker: Callable[[], str | None] = pick_folder,
    reveal: Callable[[Path], None] = reveal_in_folder,
) -> FastAPI:
    """Build the app. ``token``/``port`` define the auth cookie and the accepted Host values."""
    settings_file = settings_path or config_directory() / "settings.json"
    session = Session(load_settings(settings_file))
    jobs = JobController(session, exporter=exporter, scheduler_factory=scheduler_factory)
    reader = OpenCVMediaReader()
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        jobs.close()
        session.close()

    app = FastAPI(title="PyWatermarkCleaner", lifespan=lifespan)
    app.state.session = session
    app.state.jobs = jobs

    # -- security ----------------------------------------------------------
    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Rejecting unknown Host headers blocks DNS-rebinding attacks.
        if request.headers.get("host", "") not in allowed_hosts:
            return JSONResponse({"detail": "Forbidden host."}, status_code=403)
        path = request.url.path
        if path.startswith("/api") and path != "/api/health":
            cookie = request.cookies.get(COOKIE_NAME, "")
            if not secrets.compare_digest(cookie.encode(), token.encode()):
                return JSONResponse({"detail": "Not authenticated."}, status_code=401)
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, error: RequestValidationError) -> JSONResponse:
        first = error.errors()[0] if error.errors() else {}
        where = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
        return JSONResponse(
            {"detail": f"Invalid request {where}: {first.get('msg', 'bad value')}".strip()},
            status_code=422,
        )

    # -- helpers -----------------------------------------------------------
    def item_or_404(item_id: str) -> Item:
        item = session.get(item_id)
        if item is None:
            raise _fail(404, "Video not found.")
        return item

    def add_paths(paths: list[Path], *, uploaded: bool = False) -> None:
        for path in paths:
            try:
                metadata = reader.probe(path)
            except Exception as error:
                session.log(f"Could not add {path.name}: {error}")
                if uploaded:
                    shutil.rmtree(path.parent, ignore_errors=True)
                continue
            if session.add(metadata, uploaded=uploaded) is None:
                session.log(f"{path.name} is already in the queue.")
        session.publish_state()

    def options() -> ProcessingOptions:
        return ProcessingOptions(InpaintMethod(session.settings.method), session.settings.radius)

    def to_region(body: RegionBody | None) -> NormalizedRegion | None:
        if body is None:
            return None
        try:
            return NormalizedRegion(body.x, body.y, body.width, body.height)
        except ValueError:
            return None

    def media_file(path: Path, media_type: str | None = None) -> FileResponse:
        if not path.is_file():
            raise _fail(404, "Media file is missing.")
        guessed = media_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=guessed)  # Starlette handles Range requests.

    # -- public ------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    # -- state and events --------------------------------------------------
    @app.get("/api/state")
    def get_state() -> dict[str, Any]:
        return session.state()

    @app.get("/api/events")
    async def events() -> StreamingResponse:
        return StreamingResponse(
            event_stream(session),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # -- queue -------------------------------------------------------------
    @app.post("/api/videos/pick")
    def pick_videos() -> dict[str, Any]:
        add_paths([Path(p) for p in file_picker()])
        return session.state()

    @app.post("/api/videos/upload")
    async def upload_video(request: Request, name: str = "video") -> dict[str, Any]:
        """Store one raw request body as a video; the client sends one request per file."""
        folder = session.workspace / "uploads" / secrets.token_hex(6)
        folder.mkdir(parents=True)
        target = folder / (Path(name).name or "video")
        try:
            with target.open("wb") as handle:
                async for chunk in request.stream():
                    handle.write(chunk)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        await run_in_threadpool(add_paths, [target], uploaded=True)
        return session.state()

    @app.delete("/api/videos/{item_id}")
    def remove_video(item_id: str) -> dict[str, Any]:
        item = item_or_404(item_id)
        jobs.cancel_item(item)
        session.remove(item_id)
        session.publish_state()
        return session.state()

    # -- media -------------------------------------------------------------
    @app.get("/api/videos/{item_id}/source")
    def source(item_id: str) -> FileResponse:
        return media_file(item_or_404(item_id).path)

    @app.get("/api/videos/{item_id}/proxy")
    def proxy(item_id: str) -> FileResponse:
        item = item_or_404(item_id)
        target = session.workspace / f"{item.id}-proxy.mp4"
        with item.proxy_lock:
            if not target.is_file():
                try:
                    build_proxy(item.path, target)
                except MediaError as error:
                    raise _fail(500, str(error)) from error
        return media_file(target, "video/mp4")

    @app.get("/api/videos/{item_id}/frame")
    def frame(item_id: str, t: float = 0, cleaned: int = 0) -> Response:
        item = item_or_404(item_id)
        region = item.region
        if cleaned and region is None:
            raise _fail(409, "Draw a region before previewing the cleaned frame.")
        timestamp = max(0, int(t))
        try:
            cache = session.frames(item)
            if cleaned and region is not None:
                result = PreviewService(cache).render(
                    PreviewRequest(item.path, timestamp, region, options(), 0)
                )
                image = result.cleaned_frame
            else:
                image = scale_to_long_edge(cache.read_frame(item.path, timestamp))
        except (MediaError, PreviewError) as error:
            raise _fail(404, str(error)) from error
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 88])
        if not ok:
            raise _fail(500, "Could not encode the frame.")
        return Response(encoded.tobytes(), media_type="image/jpeg")

    @app.post("/api/videos/{item_id}/preview-clip")
    def preview_clip(item_id: str, t: float = 0) -> dict[str, str]:
        item = item_or_404(item_id)
        if item.region is None:
            raise _fail(409, "Draw a region before rendering a preview clip.")
        with item.proxy_lock:
            item.clip_version += 1
            version = item.clip_version
            for old in session.workspace.glob(f"{item.id}-clip-*.mp4"):
                old.unlink(missing_ok=True)
            target = session.workspace / f"{item.id}-clip-{version}.mp4"
            try:
                render_preview_clip(
                    item.path,
                    max(0, int(t)),
                    CLIP_SECONDS,
                    item.region,
                    options(),
                    target,
                    CancellationToken(),
                )
            except (MediaError, PreviewError) as error:
                raise _fail(500, str(error)) from error
        return {"url": f"/api/videos/{item.id}/preview-clip.mp4?v={version}"}

    @app.get("/api/videos/{item_id}/preview-clip.mp4")
    def preview_clip_file(item_id: str, v: int = 0) -> FileResponse:
        item = item_or_404(item_id)
        return media_file(
            session.workspace / f"{item.id}-clip-{item.clip_version}.mp4", "video/mp4"
        )

    @app.post("/api/videos/{item_id}/reveal")
    def reveal_video(item_id: str) -> dict[str, bool]:
        item = item_or_404(item_id)
        target = item.output_path if item.output_path and item.output_path.exists() else item.path
        reveal(target)
        return {"ok": True}

    # -- regions -----------------------------------------------------------
    @app.put("/api/videos/{item_id}/region")
    def put_region(
        item_id: str, body: Annotated[RegionBody | None, Body()] = None
    ) -> dict[str, Any]:
        item = item_or_404(item_id)
        session.set_region(item, to_region(body))
        session.publish_item(item)
        return item.to_dict()

    @app.post("/api/region/apply-all")
    def apply_all(body: ApplyAllBody) -> dict[str, Any]:
        source_item = item_or_404(body.source_id)
        skipped = 0
        for item in list(session.items):
            if item is not source_item and not session.set_region(item, source_item.region):
                skipped += 1
        session.publish_state()
        return {"skipped": skipped, "state": session.state()}

    # -- settings ----------------------------------------------------------
    @app.put("/api/settings")
    def put_settings(patch: SettingsPatch) -> dict[str, Any]:
        try:
            session.settings.apply(patch.model_dump(exclude_none=True))
        except ValueError as error:
            raise _fail(400, str(error)) from error
        save_settings(settings_file, session.settings)
        session.publish_state()
        return session.state()["settings"]  # type: ignore[no-any-return]

    @app.post("/api/output-folder/pick")
    def pick_output_folder() -> dict[str, Any]:
        chosen = folder_picker()
        if chosen:
            try:
                session.settings.apply({"output_folder": chosen})
            except ValueError as error:
                raise _fail(400, str(error)) from error
            save_settings(settings_file, session.settings)
            session.publish_state()
        return session.state()["settings"]  # type: ignore[no-any-return]

    # -- jobs --------------------------------------------------------------
    @app.post("/api/jobs/start")
    def start_jobs(body: Annotated[StartBody | None, Body()] = None) -> dict[str, Any]:
        if not session.all_ready():
            raise _fail(400, "Every video needs a region before cleaning can start.")
        if body is not None and body.format_policy is not None:
            session.settings.apply({"format_policy": body.format_policy})
            save_settings(settings_file, session.settings)
        jobs.start()
        return session.state()

    @app.post("/api/jobs/cancel-all")
    def cancel_all() -> dict[str, Any]:
        jobs.cancel_all()
        return session.state()

    @app.post("/api/jobs/{item_id}/cancel")
    def cancel_job(item_id: str) -> dict[str, Any]:
        item = item_or_404(item_id)
        jobs.cancel_item(item)
        return item.to_dict()

    @app.post("/api/jobs/{item_id}/retry")
    def retry_job(item_id: str) -> dict[str, Any]:
        item = item_or_404(item_id)
        if item.state in _ACTIVE:
            raise _fail(409, "This video is already being processed.")
        if item.region is None:
            raise _fail(400, "Draw a region before retrying.")
        jobs.retry(item)
        return item.to_dict()

    @app.get("/api/diagnostics")
    def diagnostics() -> dict[str, str]:
        with session.lock:
            metadata = [item.metadata for item in session.items]
            errors = [item.error for item in session.items]
        return {"text": build_diagnostics(metadata, errors)}

    # -- frontend ----------------------------------------------------------
    def index_response() -> Response:
        index = STATIC_DIR / "index.html"
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse(_PLACEHOLDER)

    @app.get("/")
    def root(candidate: str | None = Query(default=None, alias="token")) -> Response:
        if candidate is not None and secrets.compare_digest(candidate.encode(), token.encode()):
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(COOKIE_NAME, candidate, httponly=True, samesite="strict")
            return response
        return index_response()

    @app.get("/{path:path}")
    def spa(path: str) -> Response:
        if path.startswith("api/"):
            raise _fail(404, "Not found.")
        candidate = (STATIC_DIR / path).resolve()
        if candidate.is_file() and candidate.is_relative_to(STATIC_DIR.resolve()):
            return FileResponse(candidate)
        return index_response()

    return app


async def event_stream(session: Session) -> AsyncIterator[str]:
    """Yield SSE messages: the full state first, then item/log events and keep-alives."""
    queue = session.subscribe()
    try:
        yield f"data: {json.dumps({'type': 'state', 'state': session.state()})}\n\n"
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                yield ": keep-alive\n\n"
            else:
                yield f"data: {message}\n\n"
    finally:
        session.unsubscribe(queue)
