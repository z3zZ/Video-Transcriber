"""Background job queue: one worker thread transcribes uploaded files in order."""

from __future__ import annotations

import json
import logging
import queue
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import engine

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")


@dataclass
class Job:
    id: str
    filename: str
    model: str | None = None
    language: str | None = None
    task: str = "transcribe"
    vad: bool = True
    status: str = "queued"  # queued | running | done | error | cancelled
    progress: float = 0.0
    message: str = "Waiting in queue"
    segments: list[dict] = field(default_factory=list)
    detected_language: str | None = None
    duration: float = 0.0
    device: str | None = None
    used_model: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    elapsed: float | None = None

    def summary(self) -> dict:
        data = asdict(self)
        data.pop("segments")
        data["segment_count"] = len(self.segments)
        return data

    def meta(self) -> dict:
        return {
            "source": self.filename,
            "language": self.detected_language,
            "duration": round(self.duration, 3),
            "model": self.used_model,
        }


class JobManager:
    def __init__(self, data_dir: Path, device: str = "auto"):
        self.data_dir = Path(data_dir)
        self.upload_dir = self.data_dir / "uploads"
        self.jobs_dir = self.data_dir / "jobs"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.device = device

        self._jobs: dict[str, Job] = {}
        self._cancel: set[str] = set()
        self._lock = threading.Lock()
        self._queue: queue.Queue[str] = queue.Queue()
        self._load_history()
        self._clear_stale_uploads()
        self._worker = threading.Thread(target=self._work, name="transcriber-worker", daemon=True)
        self._worker.start()

    # ---- persistence -----------------------------------------------------------------

    def _load_history(self) -> None:
        for path in self.jobs_dir.glob("*.json"):
            try:
                job = Job(**json.loads(path.read_text(encoding="utf-8")))
            except Exception as exc:
                log.warning("Skipping unreadable job file %s: %s", path.name, exc)
                continue
            if job.status in ACTIVE:  # interrupted by a restart
                job.status, job.error, job.message = "error", "Interrupted by app restart", "Interrupted"
            self._jobs[job.id] = job

    def _clear_stale_uploads(self) -> None:
        for path in self.upload_dir.iterdir():
            path.unlink(missing_ok=True)

    def _save(self, job: Job) -> None:
        tmp = self.jobs_dir / f"{job.id}.json.tmp"
        tmp.write_text(json.dumps(asdict(job), ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.jobs_dir / f"{job.id}.json")

    # ---- public API ------------------------------------------------------------------

    def submit(self, fileobj, filename: str, model=None, language=None, task="transcribe", vad=True) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:12],
            filename=Path(filename).name or "upload",
            model=model or None,
            language=language or None,
            task=task,
            vad=vad,
        )
        dest = self._upload_path(job)
        with open(dest, "wb") as out:
            shutil.copyfileobj(fileobj, out, length=4 * 1024 * 1024)
        with self._lock:
            self._jobs[job.id] = job
        self._queue.put(job.id)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.status not in ACTIVE:
            return False
        self._cancel.add(job_id)
        if job.status == "queued":
            self._finish(job, "cancelled", message="Cancelled")
        return True

    def delete(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        if job.status in ACTIVE:
            self.cancel(job_id)
        with self._lock:
            self._jobs.pop(job_id, None)
        (self.jobs_dir / f"{job_id}.json").unlink(missing_ok=True)
        return True

    def queue_position(self, job: Job) -> int:
        queued = [j for j in sorted(self._jobs.values(), key=lambda j: j.created_at) if j.status == "queued"]
        return queued.index(job) + 1 if job in queued else 0

    # ---- worker ----------------------------------------------------------------------

    def _upload_path(self, job: Job) -> Path:
        return self.upload_dir / f"{job.id}{Path(job.filename).suffix.lower()}"

    def _finish(self, job: Job, status: str, message: str, error: str | None = None) -> None:
        job.status, job.message, job.error = status, message, error
        job.finished_at = time.time()
        self._cancel.discard(job.id)
        self._upload_path(job).unlink(missing_ok=True)
        if job.id in self._jobs:  # not deleted while it was running
            self._save(job)

    def _work(self) -> None:
        while True:
            job_id = self._queue.get()
            job = self._jobs.get(job_id)
            if not job or job.status != "queued":
                continue
            self._run(job)

    def _run(self, job: Job) -> None:
        job.status, job.message = "running", "Starting"

        def on_status(msg: str) -> None:
            job.message = msg

        def on_progress(frac: float, seg: dict | None) -> None:
            job.progress = round(frac, 4)
            if seg is not None:
                job.segments.append(seg)

        try:
            result = engine.transcribe(
                self._upload_path(job),
                model=job.model,
                device=self.device,
                language=job.language,
                task=job.task,
                vad=job.vad,
                on_progress=on_progress,
                should_cancel=lambda: job.id in self._cancel,
                on_status=on_status,
            )
        except engine.TranscriptionCancelled:
            self._finish(job, "cancelled", "Cancelled")
            return
        except Exception as exc:
            log.exception("Job %s failed", job.id)
            self._finish(job, "error", "Failed", error=str(exc) or exc.__class__.__name__)
            return

        job.segments = result.segments
        job.detected_language = result.language
        job.duration = result.duration
        job.device = result.device
        job.used_model = result.model
        job.elapsed = result.elapsed
        job.progress = 1.0
        self._finish(job, "done", "Done")
