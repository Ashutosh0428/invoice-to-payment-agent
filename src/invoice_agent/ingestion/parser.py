from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from loguru import logger

from invoice_agent.core.errors import DocumentParseError

PDF_SUFFIXES = {".pdf"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"}
OFFICE_SUFFIXES = {".docx", ".xlsx", ".pptx"}
MARKUP_SUFFIXES = {".html", ".htm"}
TEXT_SUFFIXES = {".txt", ".md", ".eml"}

SUPPORTED_SUFFIXES = (
    PDF_SUFFIXES | IMAGE_SUFFIXES | OFFICE_SUFFIXES | MARKUP_SUFFIXES | TEXT_SUFFIXES
)


@dataclass(slots=True)
class ParsedDocument:
    path: Path
    markdown: str
    sha256: str
    page_count: int = 0
    table_count: int = 0
    parser: str = "docling"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return len(self.markdown.strip()) < 20


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache
def _converter() -> Any:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = True
    options.do_table_structure = True
    options.table_structure_options.do_cell_matching = True  # type: ignore[attr-defined]

    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


def parse_document(path: str | Path) -> ParsedDocument:
    """Docling for anything with layout; plain read for text. OCR is on so scanned
    invoices — the majority of real AP volume — go down the same path as native PDFs."""
    path = Path(path)
    if not path.exists():
        raise DocumentParseError(f"document not found: {path}")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise DocumentParseError(
            f"unsupported document type '{suffix}'",
            details={"supported": sorted(SUPPORTED_SUFFIXES)},
        )

    digest = sha256_file(path)

    if suffix in TEXT_SUFFIXES:
        text = path.read_text(encoding="utf-8", errors="replace")
        return ParsedDocument(
            path=path, markdown=text, sha256=digest, page_count=1, parser="plaintext"
        )

    try:
        result = _converter().convert(str(path))
        document = result.document
        markdown = document.export_to_markdown()
        parsed = ParsedDocument(
            path=path,
            markdown=markdown,
            sha256=digest,
            page_count=len(getattr(document, "pages", []) or []),
            table_count=len(getattr(document, "tables", []) or []),
            parser="docling",
            metadata={"filename": path.name, "suffix": suffix},
        )
    except Exception as exc:
        logger.warning("Docling failed on {}: {} - falling back to text layer", path.name, exc)
        parsed = _fallback_parse(path, digest, suffix)

    if parsed.is_empty:
        raise DocumentParseError(
            f"no readable content extracted from {path.name}",
            details={"parser": parsed.parser, "sha256": digest},
        )
    return parsed


def _fallback_parse(path: Path, digest: str, suffix: str) -> ParsedDocument:
    if suffix in PDF_SUFFIXES:
        try:
            import fitz

            with fitz.open(path) as doc:
                text = "\n\n".join(page.get_text() for page in doc)
                return ParsedDocument(
                    path=path,
                    markdown=text,
                    sha256=digest,
                    page_count=doc.page_count,
                    parser="pymupdf-fallback",
                )
        except Exception as exc:
            raise DocumentParseError(f"could not parse {path.name}: {exc}") from exc

    if suffix in MARKUP_SUFFIXES:
        import re

        raw = path.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"<[^>]+>", " ", raw)
        return ParsedDocument(
            path=path, markdown=text, sha256=digest, page_count=1, parser="html-fallback"
        )

    raise DocumentParseError(f"no fallback parser available for {suffix}")
