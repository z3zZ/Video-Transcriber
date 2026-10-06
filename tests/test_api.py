"""API tests. The real speech model is replaced by a fake so tests run in milliseconds."""

import time

import pytest
from fastapi.testclient import TestClient

from transcriber import engine, server

FAKE_SEGMENTS = [
    {"start": 0.0, "end": 1.5, "text": " First line."},
    {"start": 1.5, "end": 3.0, "text": " Second line."},
]


def fake_transcribe(path, model=None, device="auto", language=None, task="transcribe", vad=True,
                    on_progress=None, should_cancel=None, on_status=None):
    if b"broken" in open(path, "rb").read():
        raise engine.TranscriptionError("Could not read an audio track from this file.")
    if b"slow" in open(path, "rb").read():
        for _ in range(200):
            if should_cancel():
                raise engine.TranscriptionCancelled()
            time.sleep(0.01)
    for i, seg in enumerate(FAKE_SEGMENTS, 1):
        on_progress(i / len(FAKE_SEGMENTS), seg)
    return engine.TranscriptionResult(
        segments=list(FAKE_SEGMENTS), language=language or "en", language_probability=0.99,
        duration=3.0, model=model or "small", device="cpu", elapsed=0.1,
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "transcribe", fake_transcribe)
    monkeypatch.setattr(engine, "cuda_available", lambda: False)
    app = server.create_app(data_dir=tmp_path, device="auto")
    with TestClient(app) as c:
        c.data_dir = tmp_path
        yield c


def upload(client, name="clip.mp4", content=b"fake video", **form):
    return client.post("/api/jobs", files={"file": (name, content)}, data=form)


def wait_done(client, job_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_info(client):
    info = client.get("/api/info").json()
    assert info["device"] == "cpu"
    assert info["default_model"] == "small"
    assert ".mp4" in info["extensions"]
    assert "srt" in info["formats"]


def test_index_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Video Transcriber" in res.text


def test_full_flow(client):
    res = upload(client, language="en", model="tiny")
    assert res.status_code == 201
    job = wait_done(client, res.json()["id"])
    assert job["status"] == "done"
    assert job["detected_language"] == "en"
    assert job["used_model"] == "tiny"
    assert [s["text"] for s in job["segments"]] == [" First line.", " Second line."]

    # Incremental polling
    assert len(client.get(f"/api/jobs/{job['id']}?since=1").json()["segments"]) == 1

    srt = client.get(f"/api/jobs/{job['id']}/download/srt")
    assert srt.status_code == 200
    assert "00:00:01,500 --> 00:00:03,000\nSecond line." in srt.text
    assert "clip.srt" in srt.headers["content-disposition"]

    txt = client.get(f"/api/jobs/{job['id']}/download/txt")
    assert txt.text == "First line.\nSecond line.\n"

    # Upload is removed once processed
    assert list((client.data_dir / "uploads").iterdir()) == []


def test_rejects_unsupported_file(client):
    res = upload(client, name="notes.pdf")
    assert res.status_code == 400
    assert "Unsupported" in res.json()["detail"]


def test_rejects_bad_options(client):
    assert upload(client, model="huge").status_code == 400
    assert upload(client, task="summarize").status_code == 400


def test_decode_error_reported(client):
    job = wait_done(client, upload(client, content=b"broken").json()["id"])
    assert job["status"] == "error"
    assert "audio track" in job["error"]
    assert client.get(f"/api/jobs/{job['id']}/download/txt").status_code == 409


def test_cancel_running_job(client):
    job_id = upload(client, content=b"slow").json()["id"]
    for _ in range(100):
        if client.get(f"/api/jobs/{job_id}").json()["status"] == "running":
            break
        time.sleep(0.01)
    assert client.post(f"/api/jobs/{job_id}/cancel").json() == {"ok": True}
    assert wait_done(client, job_id)["status"] == "cancelled"
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 409


def test_unknown_format_and_job(client):
    job = wait_done(client, upload(client).json()["id"])
    assert client.get(f"/api/jobs/{job['id']}/download/docx").status_code == 400
    assert client.get("/api/jobs/nope").status_code == 404


def test_history_persists_and_delete(client, tmp_path, monkeypatch):
    job = wait_done(client, upload(client).json()["id"])

    # A fresh app on the same data dir sees the saved job.
    app2 = server.create_app(data_dir=tmp_path)
    with TestClient(app2) as c2:
        ids = [j["id"] for j in c2.get("/api/jobs").json()]
        assert job["id"] in ids
        assert c2.delete(f"/api/jobs/{job['id']}").json() == {"ok": True}
        assert c2.get(f"/api/jobs/{job['id']}").status_code == 404
    assert not (tmp_path / "jobs" / f"{job['id']}.json").exists()


def test_ui_assets_revalidate_and_font_bundled(client):
    for path in ("/", "/static/style.css", "/static/fonts/JetBrainsMono-latin.woff2"):
        res = client.get(path)
        assert res.status_code == 200, path
        assert res.headers["cache-control"] == "no-cache"
    assert "cache-control" not in client.get("/api/info").headers
