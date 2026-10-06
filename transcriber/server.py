"""FastAPI web server: REST API + the single-page web UI."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, engine, formats
from .jobs import JobManager

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def create_app(data_dir: Path | None = None, device: str | None = None) -> FastAPI:
    data_dir = Path(data_dir or os.environ.get("VT_DATA_DIR") or DEFAULT_DATA_DIR)
    device = device or os.environ.get("VT_DEVICE", "auto")
    manager = JobManager(data_dir, device=device)
    resolved_device = device if device != "auto" else ("cuda" if engine.cuda_available() else "cpu")

    app = FastAPI(title="Video Transcriber", version=__version__)
    app.state.manager = manager

    @app.middleware("http")
    async def revalidate_ui(request, call_next):
        # Always revalidate UI files so an updated app never runs with stale CSS/JS.
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    def get_job_or_404(job_id: str):
        job = manager.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return job

    @app.get("/api/info")
    def info():
        return {
            "version": __version__,
            "device": resolved_device,
            "default_model": engine.default_model(resolved_device),
            "models": [{"id": k, "description": v} for k, v in engine.MODELS.items()],
            "languages": [{"code": k, "name": v} for k, v in engine.LANGUAGES.items()],
            "formats": list(formats.FORMATS),
            "extensions": sorted(engine.MEDIA_EXTENSIONS),
        }

    @app.post("/api/jobs", status_code=201)
    def create_job(
        file: UploadFile = File(...),
        model: str = Form(""),
        language: str = Form(""),
        task: str = Form("transcribe"),
        vad: bool = Form(True),
    ):
        name = file.filename or "upload"
        if Path(name).suffix.lower() not in engine.MEDIA_EXTENSIONS:
            raise HTTPException(400, f"Unsupported file type '{Path(name).suffix}'. "
                                     f"Supported: {', '.join(sorted(engine.MEDIA_EXTENSIONS))}")
        if model and model not in engine.MODELS:
            raise HTTPException(400, f"Unknown model '{model}'")
        if task not in ("transcribe", "translate"):
            raise HTTPException(400, "task must be 'transcribe' or 'translate'")
        job = manager.submit(file.file, name, model=model, language=language, task=task, vad=vad)
        return job.summary()

    @app.get("/api/jobs")
    def list_jobs():
        return [j.summary() for j in manager.list()]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, since: int = 0):
        """Job status. `since` returns only segments from that index (for incremental polling)."""
        job = get_job_or_404(job_id)
        data = job.summary()
        data["queue_position"] = manager.queue_position(job)
        data["segments_offset"] = since
        data["segments"] = job.segments[since:]
        return data

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        get_job_or_404(job_id)
        if not manager.cancel(job_id):
            raise HTTPException(409, "Job is not running")
        return {"ok": True}

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str):
        if not manager.delete(job_id):
            raise HTTPException(404, "Job not found")
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/download/{fmt}")
    def download(job_id: str, fmt: str, inline: bool = False):
        job = get_job_or_404(job_id)
        if job.status != "done":
            raise HTTPException(409, "Transcript is not ready yet")
        if fmt not in formats.FORMATS:
            raise HTTPException(400, f"Unknown format '{fmt}'")
        ext, media_type = formats.FORMATS[fmt]
        body = formats.render(fmt, job.segments, job.meta())
        stem = Path(job.filename).stem + ("_timestamped" if fmt == "timestamped" else "")
        filename = f"{stem}.{ext}"
        disposition = "inline" if inline else "attachment"
        headers = {"Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(filename)}"}
        return Response(body, media_type=media_type, headers=headers)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
