"""Build a small MP4 (colour-bar video + an audio track) from a WAV file, for testing.

Usage: python scripts/make_sample_video.py speech.wav out.mp4

Uses PyAV (installed with faster-whisper), so no separate ffmpeg install is needed.
"""

import sys
from fractions import Fraction

import av
import numpy as np


def main(wav_path: str, out_path: str) -> None:
    with av.open(wav_path) as src:
        in_audio = src.streams.audio[0]
        frames = [f for f in src.decode(in_audio)]
        rate = in_audio.rate
    duration = sum(f.samples for f in frames) / rate

    fps = 10
    with av.open(out_path, "w") as out:
        v = out.add_stream("libx264" if "libx264" in av.codecs_available else "mpeg4", rate=fps)
        v.width, v.height, v.pix_fmt = 320, 180, "yuv420p"
        a = out.add_stream("aac", rate=rate)
        a.layout = "mono"

        for i in range(int(duration * fps) + 1):
            img = np.zeros((180, 320, 3), dtype=np.uint8)
            img[:, :, i % 3] = 200
            frame = av.VideoFrame.from_ndarray(img, format="rgb24")
            frame.pts = i
            frame.time_base = Fraction(1, fps)
            for packet in v.encode(frame):
                out.mux(packet)
        for packet in v.encode():
            out.mux(packet)

        resampler = av.AudioResampler(format="fltp", layout="mono", rate=rate)
        for f in frames:
            f.pts = None
            for rf in resampler.resample(f):
                for packet in a.encode(rf):
                    out.mux(packet)
        for packet in a.encode():
            out.mux(packet)
    print(f"Wrote {out_path} ({duration:.1f}s)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
