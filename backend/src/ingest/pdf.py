"""
PDF ingestion using PyMuPDF (fitz).

Extracts text page-by-page, preserving section structure where possible.
Handles multi-column layouts, tables, and embedded images (text only).
"""

import hashlib
from pathlib import Path

try:
    import fitz  # PyMuPDF
    _HAS_PYMUPDF = True
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    fitz = None
    _HAS_PYMUPDF = False

from src.models.source import Source


class PDFIngestor:
    """
    Reads a PDF file and extracts structured text content.

    Returns a Source model with metadata and the raw extracted text,
    ready for chunking and embedding.

    Usage:
        ingestor = PDFIngestor()
        source, pages = await ingestor.ingest("paper.pdf", agent_id="agent-1")
    """

    async def ingest(
        self,
        file_path: str,
        agent_id: str,
    ) -> tuple[Source, list[dict[str, str]]]:
        """
        Extract text from a PDF file.

        Args:
            file_path: Path to the PDF file.
            agent_id: ID of the agent performing ingestion.

        Returns:
            A tuple of (Source model, list of page dicts with
            'page_number', 'text', and 'section_title' keys).
        """
        if not _HAS_PYMUPDF:
            raise RuntimeError(
                "The optional 'PyMuPDF' package is not installed. "
                "Install backend ingestion dependencies to enable PDF ingest."
            )
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {file_path}")

        doc = fitz.open(str(path))

        # Extract text per page
        pages: list[dict[str, str]] = []
        full_text_parts: list[str] = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("text")

            # Extract tables as markdown (preserves structure)
            table_md = _extract_tables_as_markdown(page)
            if table_md:
                text = text.strip() + "\n\n" + table_md if text.strip() else table_md

            if text.strip():
                pages.append({
                    "page_number": str(page_num + 1),
                    "text": text.strip(),
                    "section_title": "",
                })
                full_text_parts.append(text.strip())

        full_text = "\n\n".join(full_text_parts)

        # Extract title from metadata or first line
        metadata = doc.metadata or {}
        title = metadata.get("title", "") or _extract_title(pages)

        # Compute content hash for deduplication
        raw_bytes = path.read_bytes()
        content_hash = hashlib.sha256(raw_bytes).hexdigest()

        source = Source(
            uri=str(path.absolute()),
            source_type="pdf",
            title=title,
            raw_hash=content_hash,
            file_path=str(path),
            created_by=agent_id,
            confidence=1.0,  # Source material has max confidence
        )

        doc.close()
        return source, pages


def _extract_tables_as_markdown(page: "fitz.Page") -> str:
    """Extract tables from a PDF page and render as markdown.

    Uses PyMuPDF's find_tables() when available. Tables are wrapped
    with [TABLE] markers so the chunker can keep them atomic.
    """
    if not _HAS_PYMUPDF:
        return ""
    try:
        tables = page.find_tables()
    except (AttributeError, Exception):
        # find_tables() not available in older PyMuPDF versions
        return ""

    if not tables or not tables.tables:
        return ""

    parts = []
    for table in tables.tables:
        try:
            data = table.extract()
            if not data or len(data) < 2:
                continue

            # Build markdown table
            headers = [str(cell or "").strip() for cell in data[0]]
            if not any(headers):
                continue

            md_lines = ["[TABLE]"]
            md_lines.append("| " + " | ".join(headers) + " |")
            md_lines.append("| " + " | ".join("---" for _ in headers) + " |")
            for row in data[1:]:
                cells = [str(cell or "").strip() for cell in row]
                md_lines.append("| " + " | ".join(cells) + " |")
            md_lines.append("[/TABLE]")
            parts.append("\n".join(md_lines))
        except Exception:
            continue

    return "\n\n".join(parts)


def _extract_title(pages: list[dict[str, str]]) -> str:
    """Best-effort title extraction from the first page text."""
    if not pages:
        return "Untitled"
    first_page = pages[0]["text"]
    # Take the first non-empty line as a rough title
    for line in first_page.split("\n"):
        stripped = line.strip()
        if stripped and len(stripped) > 5:
            return stripped[:200]
    return "Untitled"
