"""P3 intake: text / PDF / PNG / JPG / WebP -> validated TaskInput -> content blocks.

Every fixture is generated in the test (pypdf for PDFs, zlib / struct or
Pillow for images, hand-built headers for the dimension parser); nothing is
read from the network. The request-shape test sends the blocks through
``make_chat_model(role="intake")`` into the ``wire`` mock transport.
"""

from __future__ import annotations

import base64
import io
import json
import mimetypes
import struct
import zlib
from pathlib import Path

import pytest

from tfl_deep_agent.intake import files
from tfl_deep_agent.intake.files import (
    API_IMAGE_MAX_BYTES,
    API_PDF_MAX_PAGES,
    IntakeError,
    IntakeLimits,
    from_base64,
    from_bytes,
    from_path,
    from_text,
    image_size,
    sniff_mime,
)

# ---------------------------------------------------------------------------
# Fixtures (generated, no network)
# ---------------------------------------------------------------------------


def make_pdf(pages: int = 1, *, password: str | None = None, owner: str | None = None) -> bytes:
    pypdf = pytest.importorskip("pypdf")
    w = pypdf.PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    if password is not None:
        w.encrypt(password, owner)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def make_png(width: int = 3, height: int = 2) -> bytes:
    """A valid RGB PNG, built by hand (no Pillow)."""
    raw = b"".join(b"\x00" + b"\xff\xff\xff" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + _png_chunk(b"IDAT", zlib.compress(raw))
            + _png_chunk(b"IEND", b""))


def png_header(width: int, height: int) -> bytes:
    """Only the signature and IHDR: enough for the header parser."""
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))


def make_pillow_image(fmt: str, size=(37, 21), **kw) -> bytes:
    pil = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    pil.new("RGB", size, "white").save(buf, fmt, **kw)
    return buf.getvalue()


def jpeg_header(width: int, height: int, sof: int = 0xC0) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof_seg = (bytes([0xFF, sof]) + struct.pack(">HBHHB", 11, 8, height, width, 1)
               + b"\x01\x11\x00")
    return b"\xff\xd8" + app0 + sof_seg + b"\xff\xd9"


def webp_vp8x(width: int, height: int) -> bytes:
    payload = b"\x00\x00\x00\x00" + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    chunk = b"VP8X" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


def webp_vp8l(width: int, height: int) -> bytes:
    bits = (width - 1) | ((height - 1) << 14)
    payload = b"\x2f" + struct.pack("<I", bits) + b"\x00" * 4
    chunk = b"VP8L" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


def webp_vp8(width: int, height: int) -> bytes:
    payload = b"\x00\x00\x00" + b"\x9d\x01\x2a" + struct.pack("<HH", width, height) + b"\x00" * 4
    chunk = b"VP8 " + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


def code_of(fn, *a, **kw) -> tuple[str, str]:
    with pytest.raises(IntakeError) as ei:
        fn(*a, **kw)
    return ei.value.code, str(ei.value)


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


def test_limits_default_to_settings_defaults():
    from tfl_deep_agent.settings import DEFAULTS, default_settings

    lim = IntakeLimits.from_settings(None)
    assert (lim.max_file_mb, lim.max_pdf_pages) == (DEFAULTS["intake"]["max_file_mb"],
                                                     DEFAULTS["intake"]["max_pdf_pages"])
    assert IntakeLimits.from_settings(default_settings()) == lim
    assert IntakeLimits.from_settings(default_settings().intake) == lim
    assert IntakeLimits() == lim
    assert lim.max_file_bytes == 10 * 1024 * 1024


def test_limits_from_mapping_and_validation():
    assert IntakeLimits.from_settings({"intake": {"max_file_mb": 2, "max_pdf_pages": 3}}) == IntakeLimits(2, 3)
    assert IntakeLimits.from_settings({"max_file_mb": 0.5, "max_pdf_pages": 1}) == IntakeLimits(0.5, 1)
    with pytest.raises(ValueError):
        IntakeLimits(max_file_mb=0)
    with pytest.raises(ValueError):
        IntakeLimits(max_pdf_pages=0)


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def test_text_is_normalised_and_keeps_unicode_math():
    t = from_text("  Докажите, что L = {aⁿbⁿ | n ≥ 0} \r\nне регулярен.\r\n  ")
    assert t.kind == "text" and t.mime_type == "text/plain"
    assert t.text == "Докажите, что L = {aⁿbⁿ | n ≥ 0} \nне регулярен."
    assert "aⁿbⁿ" in t.text  # NFC, not NFKC: superscripts survive
    assert not t.needs_transcription
    assert t.content_blocks() == [{"type": "text", "text": t.text}]
    assert t.content_blocks("Задача:") == [{"type": "text", "text": "Задача:"},
                                          {"type": "text", "text": t.text}]
    assert t.input_path == "/input/task.txt"


def test_text_nfc_composes_decomposed_letters():
    decomposed = "й"  # и + combining breve
    assert from_text(f"й-{decomposed}").text == "й-й"


def test_empty_text_is_refused():
    assert code_of(from_text, "   \n\t ")[0] == "empty"


def test_text_over_size_limit():
    code, msg = code_of(from_text, "a" * 2000, limits=IntakeLimits(max_file_mb=0.001))
    assert code == "too_large" and "intake.max_file_mb" in msg


def test_text_file_utf8_with_bom_and_markdown(tmp_path):
    p = tmp_path / "задача.txt"
    p.write_bytes("﻿L = {w ∈ {a,b}⁺ | |w|ₐ = 2|w|_b}".encode("utf-8"))
    t = from_path(p)
    assert t.kind == "text" and t.text.startswith("L = {w")
    assert t.name == "задача.txt" and t.warnings == ()
    md = tmp_path / "t.md"
    md.write_text("# Task\nS → aSb | ε\n", encoding="utf-8")
    assert from_path(md).text == "# Task\nS → aSb | ε"


def test_text_not_utf8_is_readable_error(tmp_path):
    p = tmp_path / "cp1251.txt"
    p.write_bytes("Докажите".encode("cp1251"))
    code, msg = code_of(from_path, p)
    assert code == "not_utf8" and "UTF-8" in msg and "cp1251.txt" in msg


def test_unknown_binary_and_unknown_extension():
    assert code_of(from_bytes, b"\x01\x02\x00\x03" * 10, name="blob.bin")[0] == "unsupported_type"
    t = from_bytes("S → ε".encode(), name="task.grammar")
    assert t.kind == "text" and any("unknown extension" in w for w in t.warnings)


# ---------------------------------------------------------------------------
# Sniffing and unsupported formats
# ---------------------------------------------------------------------------


def test_sniff_by_magic_bytes():
    assert sniff_mime(make_png()) == "image/png"
    assert sniff_mime(jpeg_header(4, 4)) == "image/jpeg"
    assert sniff_mime(webp_vp8x(4, 4)) == "image/webp"
    assert sniff_mime(b"%PDF-1.7\n...") == "application/pdf"
    assert sniff_mime(b"\n\xef\xbb\xbf%PDF-1.4") == "application/pdf"
    assert sniff_mime(b"RIFF\x00\x00\x00\x00WAVEfmt ") is None
    assert sniff_mime("текст".encode()) is None


@pytest.mark.parametrize("data,label", [
    (b"GIF89a" + b"\x00" * 20, "GIF"),
    (b"PK\x03\x04" + b"\x00" * 20, "DOCX"),
    (b"\x00\x00\x00\x18ftypheic" + b"\x00" * 20, "HEIC"),
    (b"BM" + b"\x00" * 30, "BMP"),
])
def test_unsupported_formats_are_named(data, label):
    code, msg = code_of(from_bytes, data, name="scan.gif")
    assert code == "unsupported_type"
    assert label in msg and "PDF, PNG, JPG, WebP" in msg


def test_binary_extension_with_text_content_is_type_mismatch():
    code, msg = code_of(from_bytes, b"just some text", name="task.pdf")
    assert code == "type_mismatch" and "PDF" in msg
    code, _ = code_of(from_bytes, b"just some text", name="photo.JPG")
    assert code == "type_mismatch"
    code, _ = code_of(from_bytes, b"just some text", declared_mime="image/png")
    assert code == "type_mismatch"


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def test_pdf_pages_blocks_and_metadata(tmp_path):
    data = make_pdf(3)
    p = tmp_path / "Билет 50.pdf"
    p.write_bytes(data)
    t = from_path(p)
    assert t.kind == "pdf" and t.mime_type == "application/pdf"
    assert t.pages == 3 and t.needs_transcription
    assert t.input_path == "/input/task.pdf"
    assert t.content_blocks() == [{"type": "file", "base64": base64.b64encode(data).decode(),
                                   "mime_type": "application/pdf"}]
    assert base64.b64decode(t.base64) == data
    assert "3 pages" in t.describe() and "Билет 50.pdf" in t.describe()
    meta = t.metadata()
    json.dumps(meta)
    assert meta["pages"] == 3 and meta["size_bytes"] == len(data) and "data" not in meta
    assert len(meta["sha256"]) == 64


def test_pdf_page_limit_from_settings():
    code, msg = code_of(from_bytes, make_pdf(3), name="sheet.pdf",
                        limits=IntakeLimits(max_pdf_pages=2))
    assert code == "too_many_pages"
    assert "3 pages" in msg and "2-page" in msg and "intake.max_pdf_pages" in msg
    assert from_bytes(make_pdf(2), limits=IntakeLimits(max_pdf_pages=2)).pages == 2


def test_pdf_page_limit_capped_by_api():
    code, msg = code_of(from_bytes, make_pdf(API_PDF_MAX_PAGES + 1),
                        limits=IntakeLimits(max_pdf_pages=500))
    assert code == "too_many_pages" and "Anthropic API" in msg


@pytest.mark.parametrize("password,owner", [("secret", None), ("", "owner-only")])
def test_encrypted_pdf_is_refused(password, owner):
    code, msg = code_of(from_bytes, make_pdf(1, password=password, owner=owner), name="locked.pdf")
    assert code == "encrypted" and "locked.pdf" in msg


def test_truncated_pdf_is_corrupt():
    data = make_pdf(2)
    code, msg = code_of(from_bytes, data[:60], name="broken.pdf")
    assert code == "corrupt" and "broken.pdf" in msg


def test_pdf_over_size_limit_checked_before_reading(tmp_path, monkeypatch):
    p = tmp_path / "big.pdf"
    p.write_bytes(make_pdf(1) + b"%" + b"0" * 5000)

    def _no_read(self):
        raise AssertionError("the size limit must be checked before reading the file")

    monkeypatch.setattr(Path, "read_bytes", _no_read)
    code, msg = code_of(from_path, p, limits=IntakeLimits(max_file_mb=0.001))
    assert code == "too_large" and "big.pdf" in msg and "0.001 MB limit" in msg


def test_missing_and_empty_files(tmp_path):
    assert code_of(from_path, tmp_path / "nope.pdf")[0] == "not_found"
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    assert code_of(from_path, empty)[0] == "empty"


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def test_png_built_by_hand():
    data = make_png(5, 3)
    t = from_bytes(data, name="task.png")
    assert (t.kind, t.mime_type, t.width, t.height) == ("image", "image/png", 5, 3)
    assert t.warnings == ()
    assert t.content_blocks("Перепиши условие.") == [
        {"type": "text", "text": "Перепиши условие."},
        {"type": "image", "base64": base64.b64encode(data).decode(), "mime_type": "image/png"},
    ]
    assert t.input_path == "/input/task.png"


@pytest.mark.parametrize("fmt,kw,mime", [
    ("JPEG", {}, "image/jpeg"),
    ("JPEG", {"progressive": True}, "image/jpeg"),
    ("WEBP", {}, "image/webp"),
    ("WEBP", {"lossless": True}, "image/webp"),
    ("PNG", {}, "image/png"),
])
def test_pillow_generated_images(fmt, kw, mime):
    data = make_pillow_image(fmt, (37, 21), **kw)
    t = from_bytes(data, name=f"scan.{fmt.lower()}")
    assert (t.mime_type, t.width, t.height) == (mime, 37, 21)
    assert t.content_blocks()[0]["mime_type"] == mime


@pytest.mark.parametrize("data,mime,size", [
    (jpeg_header(640, 480), "image/jpeg", (640, 480)),
    (jpeg_header(17, 9, sof=0xC2), "image/jpeg", (17, 9)),
    (webp_vp8x(1024, 768), "image/webp", (1024, 768)),
    (webp_vp8l(300, 200), "image/webp", (300, 200)),
    (webp_vp8(123, 45), "image/webp", (123, 45)),
    (png_header(7000, 10), "image/png", (7000, 10)),
])
def test_header_parser(data, mime, size):
    assert image_size(data, mime) == size


def test_extension_and_declared_type_mismatch_is_a_warning_not_an_error():
    data = make_png()
    t = from_bytes(data, name="photo.jpg", declared_mime="image/webp")
    assert t.mime_type == "image/png" and t.input_path == "/input/task.png"
    assert len(t.warnings) == 2
    assert all("treated as PNG" in w for w in t.warnings)
    assert "warnings" in t.metadata()
    # text/markdown and image/jpg are normalised, so no spurious warning
    assert from_bytes(jpeg_header(4, 4), name="a.jpeg", declared_mime="image/jpg").warnings == ()


def test_truncated_image_header_is_corrupt():
    code, msg = code_of(from_bytes, b"\x89PNG\r\n\x1a\n" + b"\x00" * 4, name="cut.png")
    assert code == "corrupt" and "PNG" in msg
    assert code_of(from_bytes, b"\xff\xd8\xff\xe0\x00\x10JFIF", name="cut.jpg")[0] == "corrupt"
    assert code_of(from_bytes, b"RIFF\x10\x00\x00\x00WEBPVP8 \x00", name="cut.webp")[0] == "corrupt"


def test_image_over_api_side_limit():
    code, msg = code_of(from_bytes, png_header(9000, 10), name="wide.png")
    assert code == "image_too_large" and "9000x10" in msg and "8000" in msg


def test_image_over_api_byte_limit_even_when_settings_allow_it():
    data = png_header(10, 10) + b"\x00" * API_IMAGE_MAX_BYTES
    code, msg = code_of(from_bytes, data, name="huge.png", limits=IntakeLimits(max_file_mb=10))
    assert code == "image_too_large" and "5.0 MB" in msg


def test_image_over_settings_limit():
    code, msg = code_of(from_bytes, make_png(), limits=IntakeLimits(max_file_mb=0.00005))
    assert code == "too_large" and "max_file_mb" in msg


# ---------------------------------------------------------------------------
# Base64 upload (TFL Lab)
# ---------------------------------------------------------------------------


def test_base64_plain_and_data_url():
    data = make_png(2, 2)
    b64 = base64.b64encode(data).decode()
    assert from_base64(b64, name="a.png").data == data
    # line-wrapped base64 is accepted
    wrapped = "\n".join(b64[i:i + 10] for i in range(0, len(b64), 10))
    assert from_base64(wrapped).data == data
    # a data URL's MIME type acts as the declared type
    t = from_base64(f"data:image/jpeg;base64,{b64}", name="x")
    assert t.mime_type == "image/png" and any("declared type" in w for w in t.warnings)


def test_base64_text_and_pdf():
    t = from_base64(base64.b64encode("L = {aⁿbᵐ | n ≠ m}".encode()).decode(), name="t.txt")
    assert t.text == "L = {aⁿbᵐ | n ≠ m}"
    pdf = make_pdf(1)
    assert from_base64("data:application/pdf;base64," + base64.b64encode(pdf).decode()).pages == 1


def test_bad_base64():
    code, msg = code_of(from_base64, "not*base64!!", name="up.pdf")
    assert code == "bad_base64" and "up.pdf" in msg


def test_base64_size_checked_before_decoding(monkeypatch):
    def _no_decode(*a, **kw):
        raise AssertionError("must not decode an oversized upload")

    monkeypatch.setattr(files.base64, "b64decode", _no_decode)
    payload = "A" * 4000
    code, _ = code_of(from_base64, payload, limits=IntakeLimits(max_file_mb=0.001))
    assert code == "too_large"


# ---------------------------------------------------------------------------
# /input file data for deepagents
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("make,mime", [
    (lambda: make_pdf(1), "application/pdf"),
    (make_png, "image/png"),
    (lambda: jpeg_header(3, 3), "image/jpeg"),
    (lambda: webp_vp8l(3, 3), "image/webp"),
])
def test_input_path_extension_gives_the_right_mime_for_read_file(make, mime):
    t = from_bytes(make())
    # deepagents' read_file derives the block MIME type from the path suffix
    assert mimetypes.guess_type("file" + Path(t.input_path).suffix)[0] == mime
    fd = t.to_file_data()
    assert fd["encoding"] == "base64" and base64.b64decode(fd["content"]) == t.data


def test_text_file_data_is_utf8():
    fd = from_text("S → aSb | ε").to_file_data()
    assert fd["encoding"] == "utf-8" and fd["content"] == "S → aSb | ε"


@pytest.mark.parametrize("make", [lambda: make_pdf(1), lambda: webp_vp8l(3, 3)],
                         ids=["pdf", "webp"])
def test_read_file_from_deepagents_state_returns_the_standard_block(make):
    """The intake subagent reads /input with deepagents' own read_file; the
    block it gets must equal what content_blocks() builds (same type, base64
    and MIME type, WebP included)."""
    pytest.importorskip("deepagents")
    from deepagents import create_deep_agent
    from langchain_core.messages import HumanMessage, ToolMessage

    from tfl_deep_agent.agent import scripted_model_class

    t = from_bytes(make(), name="Фото задачи")
    model = scripted_model_class()(
        model="claude-sonnet-5-5", role="intake",
        replies=[{"content": "", "tool_calls": [
                     {"name": "read_file", "args": {"file_path": t.input_path}, "id": "r1"}]},
                 {"content": "## Tasks\n1. x\n\n## Uncertain readings\n- none"}],
        defs={})
    agent = create_deep_agent(model=model, tools=[])
    out = agent.invoke({"messages": [HumanMessage("transcribe")],
                        "files": {t.input_path: t.to_file_data()}})
    reads = [m for m in out["messages"] if isinstance(m, ToolMessage) and m.name == "read_file"]
    assert len(reads) == 1 and reads[0].status == "success"
    blocks = [b for b in reads[0].content_blocks if b.get("type") in ("image", "file")]
    want = t.content_blocks()[0]
    assert [(b["type"], b["mime_type"], b["base64"]) for b in blocks] == [
        (want["type"], want["mime_type"], want["base64"])]


# ---------------------------------------------------------------------------
# Wire: the blocks reach the API as document / image blocks
# ---------------------------------------------------------------------------


def test_intake_blocks_on_the_wire(wire):
    pytest.importorskip("langchain_anthropic")
    from langchain_core.messages import HumanMessage

    from tfl_deep_agent.models import make_chat_model

    pdf = from_bytes(make_pdf(2), name="ticket.pdf")
    png = from_bytes(make_png(3, 3), name="photo.png")
    llm = make_chat_model(role="intake", api_key="test")

    llm.invoke([HumanMessage(content=pdf.content_blocks("Transcribe the task."))])
    blocks = wire.last.body["messages"][0]["content"]
    assert blocks[0] == {"type": "text", "text": "Transcribe the task."}
    assert blocks[1]["type"] == "document"
    assert blocks[1]["source"] == {"type": "base64", "media_type": "application/pdf",
                                   "data": pdf.base64}

    llm.invoke([HumanMessage(content=png.content_blocks())])
    blocks = wire.last.body["messages"][0]["content"]
    assert blocks == [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                   "data": png.base64}}]


# ---------------------------------------------------------------------------
# The intake role prompt
# ---------------------------------------------------------------------------


def test_intake_prompt_covers_the_contract():
    from tfl_deep_agent.roles import ROLES, load_prompt

    text = load_prompt(ROLES["intake"].prompt)
    assert "stub" not in text.lower()
    for needle in ("/input", "Unicode", "aⁿbⁿ", "## Tasks", "## Uncertain readings",
                   "numbered list", "do not solve", "Never guess silently", "1 или l?"):
        assert needle in text, needle
