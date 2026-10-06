import json

import pytest

from transcriber import formats

SEGMENTS = [
    {"start": 0.0, "end": 2.5, "text": " Hello there."},
    {"start": 2.5, "end": 3661.042, "text": " General Kenobi! "},
    {"start": 3661.042, "end": 3662.0, "text": "   "},
]


def test_format_timestamp():
    assert formats.format_timestamp(0) == "00:00:00,000"
    assert formats.format_timestamp(3661.042) == "01:01:01,042"
    assert formats.format_timestamp(1.9996, ".") == "00:00:02.000"
    assert formats.format_timestamp(-1) == "00:00:00,000"


def test_format_clock():
    assert formats.format_clock(5) == "0:05"
    assert formats.format_clock(125) == "2:05"
    assert formats.format_clock(3725) == "1:02:05"


def test_txt_skips_blank_segments():
    assert formats.to_txt(SEGMENTS) == "Hello there.\nGeneral Kenobi!\n"


def test_timestamped():
    assert formats.to_timestamped(SEGMENTS) == "[0:00] Hello there.\n[0:02] General Kenobi!\n"


def test_srt():
    assert formats.to_srt(SEGMENTS) == (
        "1\n00:00:00,000 --> 00:00:02,500\nHello there.\n\n"
        "2\n00:00:02,500 --> 01:01:01,042\nGeneral Kenobi!\n"
    )


def test_vtt():
    out = formats.to_vtt(SEGMENTS)
    assert out.startswith("WEBVTT\n")
    assert "00:00:02.500 --> 01:01:01.042\nGeneral Kenobi!" in out


def test_json_includes_meta():
    data = json.loads(formats.to_json(SEGMENTS, {"language": "en"}))
    assert data["language"] == "en"
    assert data["segments"][1] == {"start": 2.5, "end": 3661.042, "text": "General Kenobi!"}


def test_render_dispatch_and_unknown():
    for fmt in formats.FORMATS:
        assert formats.render(fmt, SEGMENTS)
    with pytest.raises(ValueError):
        formats.render("docx", SEGMENTS)
