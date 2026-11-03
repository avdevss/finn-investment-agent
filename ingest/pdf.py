# Fetches, caches and text-extracts PDFs. Every PDF in the project comes through
# here, so a document is downloaded once and extracted once across runs and
# restarts: bytes keyed on the URL, text keyed on the sha256 of the bytes.
#
# A URL that does not answer with a PDF gets a marker file, so an interrupted run
# does not retry the same dead attachment on every resume.

from __future__ import annotations

import hashlib
import io
import logging
import pathlib
from dataclasses import dataclass
from typing import Final

import pdfplumber
import requests

from ingest.http import BSE_PDF_HEADERS, get

log = logging.getLogger(__name__)

# pdfminer narrates every malformed CropBox at warning level.
logging.getLogger("pdfminer").setLevel(logging.ERROR)
logging.getLogger("pdfplumber").setLevel(logging.ERROR)

CACHE: Final = pathlib.Path(__file__).parent / "cache"
PDF_DIR: Final = CACHE / "pdf"
TEXT_DIR: Final = CACHE / "text"

PAGE_BREAK: Final = "\f"
FETCH_TIMEOUT: Final = 90.0


@dataclass(frozen=True)
class PdfText:
    text: str
    pages: int

    @property
    def char_count(self) -> int:
        return len(self.text.strip())

    @property
    def is_scanned(self) -> bool:
        """No extractable text means a scanned image. Skipped, never OCRed."""
        return self.char_count == 0

    def first_pages(self, n: int) -> str:
        return PAGE_BREAK.join(self.text.split(PAGE_BREAK)[:n]).strip()

    def page_list(self) -> list[str]:
        return self.text.split(PAGE_BREAK)


def sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def _write_atomic(path: pathlib.Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if isinstance(data, bytes):
        tmp.write_bytes(data)
    else:
        tmp.write_text(data)
    tmp.replace(path)


def fetch(url: str) -> bytes | None:
    """PDF bytes, cached on the URL. None when the source will not give us a PDF."""
    key = sha256(url)
    path = PDF_DIR / f"{key}.pdf"
    miss = PDF_DIR / f"{key}.miss"

    if path.exists():
        return path.read_bytes()
    if miss.exists():
        return None

    try:
        data = get(url, headers=BSE_PDF_HEADERS, timeout=FETCH_TIMEOUT).content
    except requests.RequestException as e:
        log.warning("pdf fetch failed, %s: %s", type(e).__name__, url[:110])
        _write_atomic(miss, f"{type(e).__name__}: {e}"[:400])
        return None

    if not data.startswith(b"%PDF"):
        log.warning("not a PDF (%d bytes, starts %r): %s", len(data), data[:12], url[:110])
        _write_atomic(miss, f"not a pdf, starts {data[:12]!r}")
        return None

    _write_atomic(path, data)
    return data


def text(data: bytes) -> PdfText:
    """Extracted text, cached on the content hash. Pages joined on a form feed."""
    path = TEXT_DIR / f"{sha256(data)}.txt"
    if path.exists():
        body = path.read_text()
        return PdfText(body, body.count(PAGE_BREAK) + 1)

    try:
        with pdfplumber.open(io.BytesIO(data)) as doc:
            pages = [p.extract_text() or "" for p in doc.pages]
    except Exception as e:
        # pdfminer raises a wide and undocumented family on malformed files, and a
        # single bad attachment must not end the run.
        log.warning("pdf text extraction failed, %s: %s", type(e).__name__, e)
        return PdfText("", 0)

    body = PAGE_BREAK.join(pages)
    _write_atomic(path, body)
    return PdfText(body, len(pages))


def fetch_text(url: str) -> tuple[str, PdfText] | None:
    """(content hash, text) for a URL. None when it is unfetchable or a scan."""
    data = fetch(url)
    if data is None:
        return None
    extracted = text(data)
    if extracted.is_scanned:
        log.info("no extractable text, skipping: %s", url[:110])
        return None
    return sha256(data), extracted
