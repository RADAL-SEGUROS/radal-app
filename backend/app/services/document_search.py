"""Search a document's own text and quote the paragraphs that match.

Fast reading (docs/v12/11-fast-reading.md, decision 4) keeps only the TITLES of
exclusions and particular clauses in the structured read; the full wording
stays in the PDF. The agent's READ tool ``search_document_text`` uses this to
quote the exact paragraphs on demand — in the two corpus cases that decided
coverage the wording WAS the coverage, so it is quoted verbatim, never
paraphrased here.

Deterministic, no AI: paragraphs are split from the extracted text, terms are
matched accent- and case-insensitively, and paragraphs are ranked by how many
distinct query terms they hold (then by hits, a phrase bonus and position).
"""
from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from app.models.document import Document

__all__ = [
    "Paragraph",
    "document_paragraphs",
    "split_paragraphs",
    "search_paragraphs",
    "query_terms",
    "MAX_SEARCH_CHARS",
]

#: The search reads further than the AI read's 45 k budget (a wordings PDF is
#: long and the clause asked about may be near the end) but stays bounded.
MAX_SEARCH_CHARS = 600_000
#: Paragraph target size when the extracted text has no blank lines (pypdf).
_CHUNK_CHARS = 900

_STOPWORDS = frozenset(
    """a al ante bajo cabe con contra de del desde durante e el en entre hacia hasta la las le
    les lo los mas mediante o para pero por que se segun sin so sobre su sus tras u un una unas
    uno unos y ya cual cuales donde como cuando es son esta este esto estos estas ser fue ha han
    hay muy tambien si no ni dice decir clausula poliza""".split()
)


def fold(text: str) -> str:
    """Lower-case, accent-free (``"Exclusión"`` -> ``"exclusion"``)."""
    norm = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in norm if not unicodedata.combining(c)).lower()


_WORD_RE = re.compile(r"[a-z0-9ñ]+(?:[.,/-][a-z0-9]+)*")


def query_terms(query: str) -> list[str]:
    """The meaningful terms of a query: folded, no stopwords, deduplicated."""
    terms: list[str] = []
    for word in _WORD_RE.findall(fold(query)):
        if word in _STOPWORDS or (len(word) < 3 and not word.isdigit()):
            continue
        if word not in terms:
            terms.append(word)
    return terms


@dataclass
class Paragraph:
    index: int
    text: str
    page: int | None = None


def _chunk(block: str) -> list[str]:
    """Split an over-long block on line ends, preferring sentence ends."""
    lines = [line.strip() for line in block.split("\n") if line.strip()]
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current} {line}".strip() if current else line
        if len(candidate) > _CHUNK_CHARS and current:
            chunks.append(current)
            current = line
            continue
        current = candidate
        if len(current) > _CHUNK_CHARS * 0.6 and current.endswith((".", ":", ";")):
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return chunks


def split_paragraphs(text: str, *, page: int | None = None, start: int = 0) -> list[Paragraph]:
    """Paragraphs of ``text``: blank-line blocks, long blocks re-chunked."""
    out: list[Paragraph] = []
    for block in re.split(r"\n\s*\n", text or ""):
        block = block.strip()
        if not block:
            continue
        pieces = _chunk(block) if len(block) > _CHUNK_CHARS else [" ".join(block.split())]
        for piece in pieces:
            out.append(Paragraph(index=start + len(out), text=piece, page=page))
    return out


def _pdf_pages(data: bytes) -> list[str] | None:
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - dependency is pinned
        return None
    try:
        reader = PdfReader(io.BytesIO(data))
        pages: list[str] = []
        total = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            pages.append(text)
            total += len(text)
            if total > MAX_SEARCH_CHARS:
                break
        return pages
    except Exception:  # noqa: BLE001 - a corrupt PDF falls back to the generic loader
        return None


def document_paragraphs(document: Document) -> list[Paragraph]:
    """The document's paragraphs, with page numbers when the file is a PDF.

    Raises :class:`app.services.ai.DocumentUnavailable` when there is no
    readable text (a scan) — the caller folds it for the model.
    """
    from app.services import ai as ai_service

    name = (document.original_name or "").lower()
    if (document.mime_type or "").lower() == "application/pdf" or name.endswith(".pdf"):
        pages = _pdf_pages(ai_service._document_bytes(document))
        if pages is not None:
            paragraphs: list[Paragraph] = []
            for number, page_text in enumerate(pages, start=1):
                page_text = re.sub(r"[^\S\n\t]+", " ", page_text)
                paragraphs.extend(
                    split_paragraphs(page_text, page=number, start=len(paragraphs))
                )
            if sum(len(p.text) for p in paragraphs) >= 40:
                return paragraphs
            raise ai_service.DocumentUnavailable(
                f"No readable text in document {document.id} — a scanned file needs OCR first"
            )
    return split_paragraphs(ai_service.load_document_text(document))


def search_paragraphs(
    paragraphs: list[Paragraph],
    query: str,
    *,
    max_hits: int = 5,
    max_chars: int = 900,
    neighbour_chars: int = 300,
) -> dict[str, Any]:
    """Rank ``paragraphs`` against ``query``; return the best with ±1 neighbour.

    The matched paragraph is cut at ``max_chars`` and each neighbour at
    ``neighbour_chars`` so five hits stay inside the agent's fold budget.
    """
    terms = query_terms(query)
    phrase = " ".join(_WORD_RE.findall(fold(query)))
    folded = [fold(p.text) for p in paragraphs]
    scored: list[tuple[tuple[int, int, int, int], int]] = []
    for position, text in enumerate(folded):
        counts = [len(re.findall(rf"(?<![a-z0-9]){re.escape(term)}", text)) for term in terms]
        distinct = sum(1 for count in counts if count)
        if not distinct:
            continue
        bonus = 1 if phrase and len(terms) > 1 and phrase in " ".join(_WORD_RE.findall(text)) else 0
        scored.append(((distinct, bonus, sum(counts), -position), position))
    scored.sort(reverse=True)

    def _cut(text: str, limit: int = max_chars) -> str:
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

    hits: list[dict[str, Any]] = []
    for score, position in scored[: max(1, max_hits)]:
        paragraph = paragraphs[position]
        before = paragraphs[position - 1] if position > 0 else None
        after = paragraphs[position + 1] if position + 1 < len(paragraphs) else None
        hits.append(
            {
                "paragraph": paragraph.index,
                "page": paragraph.page,
                "matched_terms": [t for t in terms if re.search(rf"(?<![a-z0-9]){re.escape(t)}", folded[position])],
                "text": _cut(paragraph.text),
                "before": _cut(before.text, neighbour_chars) if before is not None else None,
                "after": _cut(after.text, neighbour_chars) if after is not None else None,
            }
        )
    return {
        "terms": terms,
        "total_matches": len(scored),
        "paragraphs_searched": len(paragraphs),
        "hits": hits,
    }
