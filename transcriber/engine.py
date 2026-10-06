"""Speech-to-text engine: wraps faster-whisper with device fallback and model caching."""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

MODELS: dict[str, str] = {
    "tiny": "Fastest, lowest accuracy (~75 MB)",
    "base": "Very fast, basic accuracy (~145 MB)",
    "small": "Good balance for CPU (~490 MB)",
    "medium": "High accuracy, slow on CPU (~1.5 GB)",
    "large-v3-turbo": "Near-best accuracy, fast on GPU (~1.6 GB)",
    "large-v3": "Best accuracy, slowest (~3 GB)",
    "distil-large-v3": "English-only, fast & accurate (~1.5 GB)",
}

LANGUAGES: dict[str, str] = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
    "pt": "Portuguese", "nl": "Dutch", "ru": "Russian", "uk": "Ukrainian", "pl": "Polish",
    "tr": "Turkish", "ar": "Arabic", "hi": "Hindi", "zh": "Chinese", "ja": "Japanese",
    "ko": "Korean", "vi": "Vietnamese", "id": "Indonesian", "th": "Thai", "sv": "Swedish",
    "no": "Norwegian", "da": "Danish", "fi": "Finnish", "el": "Greek", "cs": "Czech",
    "ro": "Romanian", "hu": "Hungarian", "he": "Hebrew", "fa": "Persian", "ms": "Malay",
    "tl": "Tagalog", "ta": "Tamil", "ur": "Urdu", "bn": "Bengali",
}

MEDIA_EXTENSIONS = {
    # video
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v", ".mpg", ".mpeg", ".ts", ".3gp",
    # audio
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
}


class TranscriptionCancelled(Exception):
    pass


class TranscriptionError(Exception):
    pass


# --------------------------------------------------------------------------------------
# GPU support detection
# --------------------------------------------------------------------------------------

_dll_dirs_registered = False


def _register_nvidia_dll_dirs() -> None:
    """On Windows, make DLLs from the optional `nvidia-*-cu12` pip packages discoverable."""
    global _dll_dirs_registered
    if _dll_dirs_registered or sys.platform != "win32":
        return
    _dll_dirs_registered = True
    for base in map(Path, sys.path):
        nvidia = base / "nvidia"
        if not nvidia.is_dir():
            continue
        for bin_dir in nvidia.glob("*/bin"):
            try:
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            except OSError:
                pass


def cuda_available() -> bool:
    """True if a CUDA GPU is present *and* the cuBLAS/cuDNN runtime libraries can be loaded."""
    _register_nvidia_dll_dirs()
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() < 1:
            return False
    except Exception:
        return False
    if sys.platform == "win32":
        libs = ["cublas64_12.dll", "cudnn64_9.dll"]
        loader = ctypes.WinDLL
    else:
        libs = ["libcublas.so.12", "libcudnn.so.9"]
        loader = ctypes.CDLL
    for lib in libs:
        try:
            loader(lib)
        except OSError:
            log.info("CUDA GPU found but %s is missing; using CPU", lib)
            return False
    return True


def default_model(device: str) -> str:
    return "large-v3-turbo" if device == "cuda" else "small"


# --------------------------------------------------------------------------------------
# Model cache
# --------------------------------------------------------------------------------------

_models: dict[tuple[str, str, str], object] = {}
_models_lock = threading.Lock()


def _compute_type(device: str) -> str:
    return "float16" if device == "cuda" else "int8"


def load_model(name: str, device: str):
    from faster_whisper import WhisperModel

    key = (name, device, _compute_type(device))
    with _models_lock:
        if key not in _models:
            # Keep at most one model in memory: they are large.
            _models.clear()
            log.info("Loading model %s on %s (%s)", *key)
            _models[key] = WhisperModel(
                name,
                device=device,
                compute_type=key[2],
                cpu_threads=max(1, (os.cpu_count() or 4) - 1),
            )
        return _models[key]


# --------------------------------------------------------------------------------------
# Transcription
# --------------------------------------------------------------------------------------


@dataclass
class TranscriptionResult:
    segments: list[dict] = field(default_factory=list)
    language: str | None = None
    language_probability: float | None = None
    duration: float = 0.0
    model: str = ""
    device: str = ""
    elapsed: float = 0.0


ProgressCallback = Callable[[float, dict | None], None]


def _is_gpu_runtime_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(s in msg for s in ("cublas", "cudnn", "cuda", "cudart"))


def transcribe(
    path: str | Path,
    model: str | None = None,
    device: str = "auto",
    language: str | None = None,
    task: str = "transcribe",
    vad: bool = True,
    on_progress: ProgressCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_status: Callable[[str], None] | None = None,
) -> TranscriptionResult:
    """Transcribe the audio track of a video/audio file.

    `on_progress(fraction, new_segment)` is called as segments are produced.
    `should_cancel()` is polled between segments; return True to abort.
    """
    path = Path(path)
    if not path.is_file():
        raise TranscriptionError(f"File not found: {path}")

    if device == "auto":
        device = "cuda" if cuda_available() else "cpu"
    elif device == "cuda":
        _register_nvidia_dll_dirs()
    model = model or default_model(device)
    if model not in MODELS:
        raise TranscriptionError(f"Unknown model {model!r}. Choose from: {', '.join(MODELS)}")
    if task not in ("transcribe", "translate"):
        raise TranscriptionError("task must be 'transcribe' or 'translate'")

    try:
        return _run(path, model, device, language, task, vad, on_progress, should_cancel, on_status)
    except (TranscriptionCancelled, TranscriptionError):
        raise
    except Exception as exc:
        if device == "cuda" and _is_gpu_runtime_error(exc):
            log.warning("GPU transcription failed (%s); retrying on CPU", exc)
            if on_status:
                on_status("GPU unavailable, falling back to CPU")
            return _run(path, model, "cpu", language, task, vad, on_progress, should_cancel, on_status)
        raise


def _run(path, model, device, language, task, vad, on_progress, should_cancel, on_status):
    started = time.perf_counter()
    if on_status:
        on_status(f"Loading model '{model}' on {device.upper()} (first run downloads it)")
    whisper = load_model(model, device)

    if on_status:
        on_status("Extracting audio and detecting language")
    try:
        segments_iter, info = whisper.transcribe(
            str(path),
            language=language or None,
            task=task,
            vad_filter=vad,
            beam_size=5,
        )
    except Exception as exc:
        if _is_gpu_runtime_error(exc):
            raise
        raise TranscriptionError(_friendly_decode_error(exc)) from exc

    result = TranscriptionResult(
        language=info.language,
        language_probability=round(info.language_probability, 3),
        duration=info.duration,
        model=model,
        device=device,
    )
    if on_status:
        on_status("Transcribing")

    for seg in segments_iter:
        if should_cancel and should_cancel():
            raise TranscriptionCancelled()
        item = {"start": seg.start, "end": seg.end, "text": seg.text}
        result.segments.append(item)
        if on_progress:
            frac = min(seg.end / info.duration, 1.0) if info.duration else 0.0
            on_progress(frac, item)

    if on_progress:
        on_progress(1.0, None)
    result.elapsed = round(time.perf_counter() - started, 2)
    return result


def _friendly_decode_error(exc: Exception) -> str:
    msg = str(exc)
    low = msg.lower()
    if "invalid data" in low or "could not find" in low or "no audio" in low or "stream" in low:
        return "No readable audio track was found in this file."
    return f"Could not decode media: {msg}"
