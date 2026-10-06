"""Render transcript segments into downloadable text formats."""

from __future__ import annotations

import json
from typing import Iterable, TypedDict


class Segment(TypedDict):
    start: float
    end: float
    text: str


FORMATS: dict[str, tuple[str, str]] = {
    # key: (file extension, media type)
    "txt": ("txt", "text/plain; charset=utf-8"),
    "timestamped": ("txt", "text/plain; charset=utf-8"),
    "srt": ("srt", "application/x-subrip; charset=utf-8"),
    "vtt": ("vtt", "text/vtt; charset=utf-8"),
    "json": ("json", "application/json; charset=utf-8"),
}


def _split_seconds(seconds: float) -> tuple[int, int, int, int]:
    total_ms = max(0, round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return hours, minutes, secs, ms


def format_timestamp(seconds: float, sep: str = ",") -> str:
    """HH:MM:SS,mmm (SRT) or HH:MM:SS.mmm (VTT)."""
    h, m, s, ms = _split_seconds(seconds)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def format_clock(seconds: float) -> str:
    """Compact clock for human-readable transcripts: M:SS or H:MM:SS."""
    h, m, s, _ = _split_seconds(seconds)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def to_txt(segments: Iterable[Segment]) -> str:
    """Plain text, one paragraph-ish line per segment."""
    return "\n".join(seg["text"].strip() for seg in segments if seg["text"].strip()) + "\n"


def to_timestamped(segments: Iterable[Segment]) -> str:
    lines = [
        f"[{format_clock(seg['start'])}] {seg['text'].strip()}"
        for seg in segments
        if seg["text"].strip()
    ]
    return "\n".join(lines) + "\n"


def to_srt(segments: Iterable[Segment]) -> str:
    blocks = []
    for i, seg in enumerate((s for s in segments if s["text"].strip()), start=1):
        blocks.append(
            f"{i}\n"
            f"{format_timestamp(seg['start'])} --> {format_timestamp(seg['end'])}\n"
            f"{seg['text'].strip()}\n"
        )
    return "\n".join(blocks)


def to_vtt(segments: Iterable[Segment]) -> str:
    blocks = ["WEBVTT\n"]
    for seg in segments:
        if not seg["text"].strip():
            continue
        blocks.append(
            f"{format_timestamp(seg['start'], '.')} --> {format_timestamp(seg['end'], '.')}\n"
            f"{seg['text'].strip()}\n"
        )
    return "\n".join(blocks)


def to_json(segments: Iterable[Segment], meta: dict | None = None) -> str:
    payload = dict(meta or {})
    payload["segments"] = [
        {"start": round(s["start"], 3), "end": round(s["end"], 3), "text": s["text"].strip()}
        for s in segments
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def render(fmt: str, segments: list[Segment], meta: dict | None = None) -> str:
    if fmt == "txt":
        return to_txt(segments)
    if fmt == "timestamped":
        return to_timestamped(segments)
    if fmt == "srt":
        return to_srt(segments)
    if fmt == "vtt":
        return to_vtt(segments)
    if fmt == "json":
        return to_json(segments, meta)
    raise ValueError(f"Unknown format: {fmt!r}. Choose from {', '.join(FORMATS)}")
