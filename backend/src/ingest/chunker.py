"""
Semantic text chunking for RAG retrieval.

Splits extracted text into chunks sized for embedding (512–1024 tokens).
Uses sentence-aware splitting to avoid cutting mid-thought.
Respects [TABLE]...[/TABLE] blocks as atomic units.
"""

import re

from src.models.source import SourceChunk

# Approximate characters per token (conservative estimate)
CHARS_PER_TOKEN = 4
DEFAULT_CHUNK_SIZE = 800  # tokens
DEFAULT_CHUNK_OVERLAP = 100  # tokens


class TextChunker:
    """
    Splits text into overlapping semantic chunks.

    Respects sentence boundaries to avoid cutting mid-thought.
    Each chunk overlaps with its neighbors for context continuity
    during RAG retrieval.

    Usage:
        chunker = TextChunker(chunk_size=800, overlap=100)
        chunks = chunker.chunk(sections, source_id="abc", agent_id="agent-1")
    """

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> None:
        self._chunk_size_chars = chunk_size * CHARS_PER_TOKEN
        self._overlap_chars = chunk_overlap * CHARS_PER_TOKEN

    def chunk(
        self,
        sections: list[dict[str, str]],
        source_id: str,
        agent_id: str,
    ) -> list[SourceChunk]:
        """
        Split sections into SourceChunk models.

        Args:
            sections: List of dicts with 'text' and 'section_title' keys.
            source_id: ID of the parent Source.
            agent_id: ID of the agent performing chunking.

        Returns:
            List of SourceChunk models with sequential chunk_index values.
        """
        chunks: list[SourceChunk] = []
        chunk_index = 0

        for section in sections:
            text = section.get("text", "")
            title = section.get("section_title", "")

            if not text.strip():
                continue

            # Split into sentences first
            sentences = _split_sentences(text)
            current_chunk: list[str] = []
            current_length = 0

            for sentence in sentences:
                sentence_len = len(sentence)

                if current_length + sentence_len > self._chunk_size_chars and current_chunk:
                    # Emit current chunk
                    chunk_text = " ".join(current_chunk)
                    chunks.append(SourceChunk(
                        source_id=source_id,
                        text=chunk_text,
                        chunk_index=chunk_index,
                        section_title=title,
                        created_by=agent_id,
                        confidence=1.0,
                    ))
                    chunk_index += 1

                    # Keep overlap: take last few sentences that fit in overlap window
                    overlap_sentences: list[str] = []
                    overlap_len = 0
                    for s in reversed(current_chunk):
                        if overlap_len + len(s) > self._overlap_chars:
                            break
                        overlap_sentences.insert(0, s)
                        overlap_len += len(s)

                    current_chunk = overlap_sentences
                    current_length = overlap_len

                current_chunk.append(sentence)
                current_length += sentence_len

            # Emit remaining text as final chunk
            if current_chunk:
                chunk_text = " ".join(current_chunk)
                chunks.append(SourceChunk(
                    source_id=source_id,
                    text=chunk_text,
                    chunk_index=chunk_index,
                    section_title=title,
                    created_by=agent_id,
                    confidence=1.0,
                ))
                chunk_index += 1

        return chunks


def _split_sentences(text: str) -> list[str]:
    """
    Split text into sentences using simple heuristics.

    Handles common sentence terminators (.!?) while avoiding
    false splits on abbreviations and decimal numbers.
    Preserves [TABLE]...[/TABLE] blocks as single atomic units.
    """
    # First, extract [TABLE] blocks and replace with placeholders
    table_blocks: list[str] = []
    def _replace_table(match: re.Match) -> str:
        table_blocks.append(match.group(0))
        return f"__TABLE_PLACEHOLDER_{len(table_blocks) - 1}__"

    text_with_placeholders = re.sub(
        r'\[TABLE\].*?\[/TABLE\]',
        _replace_table,
        text,
        flags=re.DOTALL,
    )

    # Split on sentence-ending punctuation followed by whitespace and uppercase
    # This avoids splitting on "Dr." or "3.14"
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text_with_placeholders)

    # Also split on double newlines (paragraph boundaries)
    result: list[str] = []
    for sentence in sentences:
        parts = sentence.split("\n\n")
        for part in parts:
            cleaned = part.strip()
            if cleaned:
                # Restore table placeholders
                for i, table in enumerate(table_blocks):
                    placeholder = f"__TABLE_PLACEHOLDER_{i}__"
                    cleaned = cleaned.replace(placeholder, table)
                result.append(cleaned)

    return result
