"""Entry point.

    python -m transcriber                       # start the web app and open the browser
    python -m transcriber serve --port 8000     # same, with options
    python -m transcriber transcribe video.mp4 [more files...] -f srt -f txt
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__, engine, formats


def _already_running(url: str) -> bool:
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(f"{url}/api/info", timeout=1) as res:
            return "default_model" in json.load(res)
    except Exception:
        return False


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .server import create_app

    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    if _already_running(url):
        # Launched again (e.g. desktop shortcut double-clicked twice): just show the open app.
        print(f"Video Transcriber is already running at {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    app = create_app(data_dir=args.data_dir, device=args.device)
    print(f"\n  Video Transcriber v{__version__} running at {url}\n  Press Ctrl+C to stop.\n")
    if not args.no_browser:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def _transcribe(args: argparse.Namespace) -> int:
    fmts = args.format or ["txt"]
    failures = 0
    for file in args.files:
        src = Path(file)
        out_dir = Path(args.output_dir) if args.output_dir else src.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n> {src.name}")

        def on_progress(frac: float, seg: dict | None) -> None:
            if seg and args.verbose:
                print(f"  [{formats.format_clock(seg['start'])}] {seg['text'].strip()}")
            elif not args.verbose:
                print(f"\r  {frac * 100:5.1f}%", end="", flush=True)

        try:
            result = engine.transcribe(
                src,
                model=args.model,
                device=args.device,
                language=args.language,
                task="translate" if args.translate else "transcribe",
                vad=not args.no_vad,
                on_progress=on_progress,
                on_status=lambda m: print(f"  {m}..."),
            )
        except (engine.TranscriptionError, OSError) as exc:
            print(f"\n  ERROR: {exc}", file=sys.stderr)
            failures += 1
            continue
        except KeyboardInterrupt:
            print("\n  Cancelled.")
            return 130

        meta = {"source": src.name, "language": result.language,
                "duration": round(result.duration, 3), "model": result.model}
        print(f"\n  Language: {result.language}  |  {result.device.upper()}  |  {result.elapsed}s")
        for fmt in fmts:
            ext = formats.FORMATS[fmt][0]
            suffix = "_timestamped" if fmt == "timestamped" else ""
            dest = out_dir / f"{src.stem}{suffix}.{ext}"
            dest.write_text(formats.render(fmt, result.segments, meta), encoding="utf-8")
            print(f"  Saved {dest}")
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="transcriber", description="Turn videos into text transcripts, locally.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto",
                        help="Run on GPU (cuda), CPU, or detect automatically (default)")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", parents=[common], help="Start the web app (default)")
    serve.add_argument("--host", default="127.0.0.1", help="Use 0.0.0.0 to allow other devices on your network")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--data-dir", help="Where uploads and transcript history are stored")
    serve.add_argument("--no-browser", action="store_true", help="Don't open a browser tab")

    tr = sub.add_parser("transcribe", parents=[common], help="Transcribe files from the command line")
    tr.add_argument("files", nargs="+", help="Video or audio files")
    tr.add_argument("-m", "--model", choices=list(engine.MODELS), help="Whisper model (default depends on device)")
    tr.add_argument("-l", "--language", help="Spoken language code, e.g. en (default: auto-detect)")
    tr.add_argument("-f", "--format", action="append", choices=list(formats.FORMATS),
                    help="Output format; repeat for several (default: txt)")
    tr.add_argument("-o", "--output-dir", help="Output folder (default: next to each input file)")
    tr.add_argument("--translate", action="store_true", help="Translate speech to English")
    tr.add_argument("--no-vad", action="store_true", help="Disable silence filtering")
    tr.add_argument("-v", "--verbose", action="store_true", help="Print segments as they are transcribed")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    argv = list(sys.argv[1:] if argv is None else argv)
    # No sub-command given: default to `serve` (e.g. `python -m transcriber --port 9000`).
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version")):
        argv.insert(0, "serve")
    args = build_parser().parse_args(argv)
    if args.command == "transcribe":
        return _transcribe(args)
    return _serve(args)


if __name__ == "__main__":
    sys.exit(main())
