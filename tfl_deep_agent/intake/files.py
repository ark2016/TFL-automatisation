"""Turn a task file (text, PDF, PNG / JPG / WebP) into langchain content blocks (P3).

Entry points (each returns a :class:`TaskInput` or raises :class:`IntakeError`):

* :func:`from_text`   -- a statement typed or pasted as text;
* :func:`from_path`   -- a file on disk (CLI ``--file``);
* :func:`from_bytes`  -- raw bytes with an optional name / declared MIME type;
* :func:`from_base64` -- the TFL Lab upload (base64 JSON, a ``data:`` URL is accepted).

The file type is decided by **magic bytes**, not by the extension or the
browser's MIME type: ``%PDF-`` -> PDF, the PNG signature, ``FF D8 FF`` -> JPEG,
``RIFF....WEBP`` -> WebP. A file without a known signature is accepted as text
only if it is valid UTF-8 without NUL bytes. A known extension that disagrees
with the content is not an error (a ``.jpg`` that is really a PNG is common);
the content wins and the mismatch is recorded in ``TaskInput.warnings``.
A ``.pdf`` / image extension on content with no recognised signature *is* an
error: the file is damaged or not what it claims to be.

Limits (``settings.intake``): ``max_file_mb`` applies to every input (1 MB =
1024 * 1024 bytes), ``max_pdf_pages`` to PDFs (page count read with pypdf).
On top of them the Anthropic API caps are enforced, so a file the API would
reject fails here with a readable message instead of a 400 mid-run: an image
at most :data:`API_IMAGE_MAX_BYTES` and :data:`API_IMAGE_MAX_SIDE` px per side,
a PDF at most :data:`API_PDF_MAX_PAGES` pages, and no encrypted PDFs.

Content blocks use the langchain standard format that ``models.py`` pins
(``tests/test_models_request_shape.py``): ``{"type": "file", "base64",
"mime_type": "application/pdf"}`` and ``{"type": "image", "base64",
"mime_type"}``. langchain-anthropic turns them into Anthropic ``document`` /
``image`` blocks with a ``base64`` source. deepagents' ``read_file`` returns the
same block shape for a binary file, so :meth:`TaskInput.to_file_data` stores
the file for ``/input`` and the ``intake`` subagent can read it there.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import mimetypes
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MB = 1024 * 1024

PDF_MIME = "application/pdf"
PNG_MIME = "image/png"
JPEG_MIME = "image/jpeg"
WEBP_MIME = "image/webp"
TEXT_MIME = "text/plain"

IMAGE_MIMES = (PNG_MIME, JPEG_MIME, WEBP_MIME)
SUPPORTED_MIMES = (PDF_MIME, *IMAGE_MIMES, TEXT_MIME)
SUPPORTED_LABEL = "text (.txt / .md), PDF, PNG, JPG, WebP"

# Anthropic API caps (vision and PDF support docs). Settings may only lower them.
API_IMAGE_MAX_BYTES = 5 * MB
API_IMAGE_MAX_SIDE = 8000
API_PDF_MAX_PAGES = 100

# Path of the task file inside the agent's virtual filesystem.
INPUT_DIR = "/input"

_EXT_FOR_MIME = {
    PDF_MIME: ".pdf", PNG_MIME: ".png", JPEG_MIME: ".jpg", WEBP_MIME: ".webp", TEXT_MIME: ".txt",
}
_MIME_FOR_EXT = {
    ".pdf": PDF_MIME, ".png": PNG_MIME, ".jpg": JPEG_MIME, ".jpeg": JPEG_MIME,
    ".webp": WEBP_MIME, ".txt": TEXT_MIME, ".md": TEXT_MIME,
}
_BINARY_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
_LABEL = {PDF_MIME: "PDF", PNG_MIME: "PNG", JPEG_MIME: "JPEG", WEBP_MIME: "WebP", TEXT_MIME: "text"}

# Recognised but unsupported formats: a precise message beats "not UTF-8".
_UNSUPPORTED_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"GIF87a", "GIF"), (b"GIF89a", "GIF"),
    (b"BM", "BMP"),
    (b"II*\x00", "TIFF"), (b"MM\x00*", "TIFF"),
    (b"PK\x03\x04", "ZIP / DOCX / ODT"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "MS Office (DOC / XLS)"),
    (b"{\\rtf", "RTF"),
    (b"\x7fELF", "executable"), (b"MZ", "executable"),
)

# deepagents' read_file picks the block MIME type with mimetypes.guess_type on
# the /input path. Python 3.12 on Windows does not know .webp (it reads the
# registry), so a WebP would go out as application/octet-stream. Register it.
if mimetypes.guess_type("task.webp")[0] != WEBP_MIME:
    mimetypes.add_type(WEBP_MIME, ".webp")


# ---------------------------------------------------------------------------
# Errors and limits
# ---------------------------------------------------------------------------

class IntakeError(ValueError):
    """A task input that cannot be used, with a message meant for a human.

    ``code`` is a stable machine-readable reason (``empty``, ``too_large``,
    ``too_many_pages``, ``unsupported_type``, ``type_mismatch``, ``corrupt``,
    ``encrypted``, ``not_utf8``, ``bad_base64``, ``not_found``,
    ``image_too_large``) for the UI and tests.
    """

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class IntakeLimits:
    """``settings.intake`` caps. Defaults equal ``settings.DEFAULTS["intake"]``."""

    max_file_mb: float = 10
    max_pdf_pages: int = 20

    def __post_init__(self) -> None:
        if not (isinstance(self.max_file_mb, (int, float)) and self.max_file_mb > 0):
            raise ValueError(f"max_file_mb must be > 0, got {self.max_file_mb!r}")
        if not (isinstance(self.max_pdf_pages, int) and self.max_pdf_pages >= 1):
            raise ValueError(f"max_pdf_pages must be an int >= 1, got {self.max_pdf_pages!r}")

    @property
    def max_file_bytes(self) -> int:
        return int(self.max_file_mb * MB)

    @classmethod
    def from_settings(cls, settings: Any = None) -> IntakeLimits:
        """From a ``settings.Settings``, its ``intake`` part, a mapping with
        ``max_file_mb`` / ``max_pdf_pages`` (or one holding ``"intake"``), or
        ``None`` (the defaults)."""
        if settings is None:
            from ..settings import DEFAULTS

            src: Any = DEFAULTS["intake"]
        elif isinstance(settings, IntakeLimits):
            return settings
        elif isinstance(settings, Mapping):
            src = settings.get("intake", settings)
        else:
            src = getattr(settings, "intake", settings)
        get = src.get if isinstance(src, Mapping) else (lambda k: getattr(src, k))
        return cls(max_file_mb=get("max_file_mb"), max_pdf_pages=get("max_pdf_pages"))


def _limits(limits: IntakeLimits | Any | None) -> IntakeLimits:
    return limits if isinstance(limits, IntakeLimits) else IntakeLimits.from_settings(limits)


def _fmt_size(n: int | float) -> str:
    if n < 1024:
        return f"{int(n)} B"
    if n < MB:
        return f"{n / 1024:.1f} KB"
    return f"{n / MB:.1f} MB"


def _fmt_mb(mb: float) -> str:
    return f"{mb:g} MB"


# ---------------------------------------------------------------------------
# The result
# ---------------------------------------------------------------------------

Kind = Literal["text", "pdf", "image"]


@dataclass(frozen=True)
class TaskInput:
    """One validated task source. ``data`` is the raw file (UTF-8 for text)."""

    kind: Kind
    mime_type: str
    name: str
    data: bytes = field(repr=False)
    text: str | None = None
    pages: int | None = None
    width: int | None = None
    height: int | None = None
    warnings: tuple[str, ...] = ()

    @property
    def size_bytes(self) -> int:
        return len(self.data)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def base64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")

    @property
    def needs_transcription(self) -> bool:
        """A PDF or an image goes through the ``intake`` subagent; text does not."""
        return self.kind != "text"

    @property
    def input_path(self) -> str:
        """Where the file lives in the agent's filesystem. A fixed ASCII name
        (the original may hold Cyrillic or spaces); the extension follows the
        sniffed type, because deepagents' ``read_file`` derives the block's
        MIME type from it."""
        return f"{INPUT_DIR}/task{_EXT_FOR_MIME[self.mime_type]}"

    def describe(self) -> str:
        """One line for logs, the CLI and HITL cards."""
        parts = [_LABEL[self.mime_type]]
        if self.pages is not None:
            parts.append(f"{self.pages} page{'s' if self.pages != 1 else ''}")
        if self.width is not None and self.height is not None:
            parts.append(f"{self.width}x{self.height} px")
        parts.append(_fmt_size(self.size_bytes))
        return f"{self.name} ({', '.join(parts)})"

    def content_blocks(self, instruction: str | None = None) -> list[dict[str, Any]]:
        """Content for a ``HumanMessage``: an optional leading text block with
        ``instruction``, then the task itself (a text, ``file`` or ``image``
        block in the langchain standard format)."""
        blocks: list[dict[str, Any]] = []
        if instruction:
            blocks.append({"type": "text", "text": instruction})
        if self.kind == "text":
            blocks.append({"type": "text", "text": self.text or ""})
        elif self.kind == "pdf":
            blocks.append({"type": "file", "base64": self.base64, "mime_type": PDF_MIME})
        else:
            blocks.append({"type": "image", "base64": self.base64, "mime_type": self.mime_type})
        return blocks

    def to_file_data(self) -> dict[str, Any]:
        """A deepagents ``FileData`` for :attr:`input_path` (base64 for a binary
        file, UTF-8 text otherwise)."""
        try:
            from deepagents.backends.utils import create_file_data
        except ImportError:  # [deep] extra not installed: same shape by hand
            def create_file_data(content: str, encoding: str = "utf-8") -> dict[str, Any]:
                return {"content": content, "encoding": encoding}
        if self.kind == "text":
            return create_file_data(self.text or "", encoding="utf-8")
        return create_file_data(self.base64, encoding="base64")

    def metadata(self) -> dict[str, Any]:
        """JSON-safe description for ``result.json`` (no file content)."""
        meta: dict[str, Any] = {
            "kind": self.kind, "mime_type": self.mime_type, "name": self.name,
            "size_bytes": self.size_bytes, "sha256": self.sha256, "input_path": self.input_path,
        }
        if self.pages is not None:
            meta["pages"] = self.pages
        if self.width is not None:
            meta["width"], meta["height"] = self.width, self.height
        if self.warnings:
            meta["warnings"] = list(self.warnings)
        return meta


# ---------------------------------------------------------------------------
# Sniffing
# ---------------------------------------------------------------------------

def sniff_mime(data: bytes) -> str | None:
    """The supported binary type of ``data`` by its signature, or ``None``.
    Text is not sniffed here (see :func:`from_bytes`)."""
    head = data[:16]
    # Leading whitespace or a BOM before the PDF header is tolerated (pypdf
    # reads such files); arbitrary junk is not.
    if data[:64].lstrip(b" \t\r\n\x00").removeprefix(b"\xef\xbb\xbf").startswith(b"%PDF-"):
        return PDF_MIME
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG_MIME
    if head.startswith(b"\xff\xd8\xff"):
        return JPEG_MIME
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return WEBP_MIME
    return None


def _unsupported_format(data: bytes) -> str | None:
    for sig, label in _UNSUPPORTED_SIGNATURES:
        if data.startswith(sig):
            return label
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypheif"):
        return "HEIC / HEIF"
    if data[4:12] == b"ftypavif":
        return "AVIF"
    return None


# ---------------------------------------------------------------------------
# Image headers (dimensions without Pillow)
# ---------------------------------------------------------------------------

_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    i, n = 2, len(data)
    while i + 3 < n:
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker == 0xD8 or marker == 0x01 or 0xD0 <= marker <= 0xD7:  # standalone markers
            i += 2
            continue
        if marker in (0xD9, 0xDA):  # EOI / start of scan before any frame header
            return None
        seg_len = int.from_bytes(data[i + 2:i + 4], "big")
        if seg_len < 2:
            return None
        if marker in _JPEG_SOF:
            if i + 9 > n:
                return None
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return w, h
        i += 2 + seg_len
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    chunk = data[12:16]
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        w = int.from_bytes(data[26:28], "little") & 0x3FFF
        h = int.from_bytes(data[28:30], "little") & 0x3FFF
        return w, h
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X" and len(data) >= 30:
        return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
    return None


def image_size(data: bytes, mime_type: str) -> tuple[int, int] | None:
    """``(width, height)`` from the image header, or ``None`` if the header is
    missing or truncated."""
    reader = {PNG_MIME: _png_size, JPEG_MIME: _jpeg_size, WEBP_MIME: _webp_size}[mime_type]
    size = reader(data)
    if size is None or size[0] <= 0 or size[1] <= 0:
        return None
    return size


# ---------------------------------------------------------------------------
# Validation per kind
# ---------------------------------------------------------------------------

def pdf_page_count(data: bytes, name: str = "the PDF") -> int:
    """Number of pages, via pypdf. Raises :class:`IntakeError` for a damaged
    or encrypted file."""
    try:
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError
    except ImportError as exc:  # pragma: no cover - pypdf is in the [deep] extra
        raise IntakeError(
            "PDF intake needs pypdf: pip install -e \".[deep]\"", code="corrupt",
        ) from exc
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise IntakeError(
                f"{name} is encrypted or password-protected; the Anthropic API accepts only "
                "unencrypted PDFs. Remove the protection (for example, print it to a new PDF) "
                "and upload again.", code="encrypted")
        pages = len(reader.pages)
    except IntakeError:
        raise
    except (PyPdfError, ValueError, KeyError, TypeError, AttributeError, IndexError, OSError) as exc:
        raise IntakeError(
            f"{name} could not be read as a PDF ({type(exc).__name__}: {exc}); "
            "the file may be damaged or truncated.", code="corrupt") from exc
    if pages < 1:
        raise IntakeError(f"{name} has no pages.", code="corrupt")
    return pages


def _check_size(size: int, limits: IntakeLimits, name: str) -> None:
    if size == 0:
        raise IntakeError(f"{name} is empty.", code="empty")
    if size > limits.max_file_bytes:
        raise IntakeError(
            f"{name} is {_fmt_size(size)}, over the {_fmt_mb(limits.max_file_mb)} limit "
            "(settings intake.max_file_mb).", code="too_large")


def _build_pdf(data: bytes, name: str, limits: IntakeLimits, warnings: list[str]) -> TaskInput:
    pages = pdf_page_count(data, name)
    cap = min(limits.max_pdf_pages, API_PDF_MAX_PAGES)
    if pages > cap:
        where = ("settings intake.max_pdf_pages" if cap == limits.max_pdf_pages
                 else "the Anthropic API limit")
        raise IntakeError(
            f"{name} has {pages} pages, over the {cap}-page limit ({where}). "
            "Upload only the pages with the task.", code="too_many_pages")
    return TaskInput(kind="pdf", mime_type=PDF_MIME, name=name, data=data, pages=pages,
                     warnings=tuple(warnings))


def _build_image(data: bytes, mime: str, name: str, warnings: list[str]) -> TaskInput:
    label = _LABEL[mime]
    if len(data) > API_IMAGE_MAX_BYTES:
        raise IntakeError(
            f"{name} is {_fmt_size(len(data))}; the Anthropic API accepts images up to "
            f"{_fmt_size(API_IMAGE_MAX_BYTES)}. Save it smaller (for example as JPEG) or crop "
            "it to the task.", code="image_too_large")
    size = image_size(data, mime)
    if size is None:
        raise IntakeError(
            f"{name} starts like a {label} image but its header could not be read; "
            "the file may be damaged or truncated.", code="corrupt")
    w, h = size
    if max(w, h) > API_IMAGE_MAX_SIDE:
        raise IntakeError(
            f"{name} is {w}x{h} px; the Anthropic API accepts at most "
            f"{API_IMAGE_MAX_SIDE} px per side. Scale it down.", code="image_too_large")
    return TaskInput(kind="image", mime_type=mime, name=name, data=data, width=w, height=h,
                     warnings=tuple(warnings))


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # NFC only: NFKC would fold superscripts and subscripts (aⁿ -> an).
    return unicodedata.normalize("NFC", text).strip()


def _build_text(text: str, name: str, warnings: list[str] | tuple[str, ...] = ()) -> TaskInput:
    norm = _normalize_text(text)
    if not norm:
        raise IntakeError(f"{name} contains no text.", code="empty")
    return TaskInput(kind="text", mime_type=TEXT_MIME, name=name, data=norm.encode("utf-8"),
                     text=norm, warnings=tuple(warnings))


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def from_text(text: str, *, name: str = "task.txt", limits: IntakeLimits | Any | None = None) -> TaskInput:
    """A task statement given as text (normalised newlines, NFC, stripped)."""
    if not isinstance(text, str):
        raise TypeError(f"from_text expects str, got {type(text).__name__}")
    lim = _limits(limits)
    raw = text.encode("utf-8")
    _check_size(len(raw) if text.strip() else 0, lim, "The task text")
    return _build_text(text, name)


def _claimed_mime(name: str | None, declared_mime: str | None) -> tuple[str | None, str | None]:
    """(MIME implied by the extension, normalised declared MIME)."""
    ext = Path(name).suffix.lower() if name else ""
    by_ext = _MIME_FOR_EXT.get(ext)
    declared = declared_mime.split(";", 1)[0].strip().lower() if declared_mime else None
    if declared == "image/jpg":
        declared = JPEG_MIME
    if declared in ("text/markdown", "text/x-markdown"):
        declared = TEXT_MIME
    return by_ext, declared


def from_bytes(
    data: bytes,
    *,
    name: str | None = None,
    declared_mime: str | None = None,
    limits: IntakeLimits | Any | None = None,
) -> TaskInput:
    """Validate raw file bytes. ``name`` (for the extension and messages) and
    ``declared_mime`` (e.g. the browser's ``File.type``) are hints only; the
    content decides the type."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError(f"from_bytes expects bytes, got {type(data).__name__}")
    data = bytes(data)
    lim = _limits(limits)
    label_name = name or "The uploaded file"
    _check_size(len(data), lim, label_name)

    by_ext, declared = _claimed_mime(name, declared_mime)
    sniffed = sniff_mime(data)
    warnings: list[str] = []

    if sniffed is not None:
        for source, claimed in (("extension", by_ext), ("declared type", declared)):
            if claimed and claimed != sniffed and claimed in SUPPORTED_MIMES:
                warnings.append(
                    f"{label_name}: the {source} says {_LABEL.get(claimed, claimed)}, "
                    f"the content is {_LABEL[sniffed]}; treated as {_LABEL[sniffed]}.")
        file_name = name or f"upload{_EXT_FOR_MIME[sniffed]}"
        if sniffed == PDF_MIME:
            return _build_pdf(data, file_name, lim, warnings)
        return _build_image(data, sniffed, file_name, warnings)

    other = _unsupported_format(data)
    if other is not None:
        raise IntakeError(
            f"{label_name} is a {other} file, which is not supported. "
            f"Supported: {SUPPORTED_LABEL}.", code="unsupported_type")

    ext = Path(name).suffix.lower() if name else ""
    if ext in _BINARY_EXTS or (declared in SUPPORTED_MIMES and declared != TEXT_MIME):
        claimed_label = _LABEL[by_ext] if ext in _BINARY_EXTS else _LABEL[declared]  # type: ignore[index]
        raise IntakeError(
            f"{label_name} is named or typed as {claimed_label}, but its content is not a "
            f"{claimed_label} file (it starts with {data[:8]!r}). The file may be damaged, "
            "or it has the wrong extension.", code="type_mismatch")

    if b"\x00" in data[:8192]:
        raise IntakeError(
            f"{label_name} is a binary file of an unknown type. Supported: {SUPPORTED_LABEL}.",
            code="unsupported_type")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IntakeError(
            f"{label_name} is not valid UTF-8 text (byte {exc.start}). Save it as UTF-8, "
            f"or upload a PDF / image. Supported: {SUPPORTED_LABEL}.", code="not_utf8") from exc
    if ext and ext not in _MIME_FOR_EXT:
        warnings.append(f"{label_name}: unknown extension {ext}; read as UTF-8 text.")
    return _build_text(text, name or "task.txt", warnings)


_DATA_URL = re.compile(r"^data:(?P<mime>[^;,]*)(?:;[^,]*)?;base64,", re.IGNORECASE)


def from_base64(
    payload: str,
    *,
    name: str | None = None,
    declared_mime: str | None = None,
    limits: IntakeLimits | Any | None = None,
) -> TaskInput:
    """Decode an upload sent as base64 (plain or a ``data:<mime>;base64,`` URL;
    the URL's MIME type is used as ``declared_mime`` when none is given). The
    size limit is checked on the encoded length before decoding."""
    if not isinstance(payload, str):
        raise TypeError(f"from_base64 expects str, got {type(payload).__name__}")
    lim = _limits(limits)
    label_name = name or "The uploaded file"
    s = payload.strip()
    m = _DATA_URL.match(s)
    if m:
        declared_mime = declared_mime or (m.group("mime") or None)
        s = s[m.end():]
    s = re.sub(r"\s+", "", s)
    if len(s) * 3 // 4 > lim.max_file_bytes + 2:
        raise IntakeError(
            f"{label_name} is about {_fmt_size(len(s) * 3 // 4)}, over the "
            f"{_fmt_mb(lim.max_file_mb)} limit (settings intake.max_file_mb).", code="too_large")
    try:
        data = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise IntakeError(f"{label_name}: the upload is not valid base64 ({exc}).",
                          code="bad_base64") from exc
    return from_bytes(data, name=name, declared_mime=declared_mime, limits=lim)


def from_path(path: str | Path, *, limits: IntakeLimits | Any | None = None) -> TaskInput:
    """Read and validate a task file from disk. The size is checked before
    the file is read."""
    lim = _limits(limits)
    p = Path(path)
    if not p.is_file():
        raise IntakeError(f"Task file not found: {path}", code="not_found")
    size = p.stat().st_size
    _check_size(size, lim, p.name)
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise IntakeError(f"Cannot read {p.name}: {exc}", code="not_found") from exc
    return from_bytes(data, name=p.name, limits=lim)


__all__ = [
    "API_IMAGE_MAX_BYTES", "API_IMAGE_MAX_SIDE", "API_PDF_MAX_PAGES", "INPUT_DIR",
    "IMAGE_MIMES", "IntakeError", "IntakeLimits", "SUPPORTED_MIMES", "TaskInput",
    "from_base64", "from_bytes", "from_path", "from_text", "image_size",
    "pdf_page_count", "sniff_mime",
]
