"""Discover immutable local transcript files for the Phase 1 source library."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Transcript:
    path: Path
    relative_path: str
    filename: str
    year: int
    modified_at: float


def discover_transcripts(root: Path) -> list[Transcript]:
    """Return raw `.txt` transcripts without modifying their contents."""
    if not root.is_dir():
        raise ValueError(f"Transcript directory does not exist: {root}")

    transcripts: list[Transcript] = []
    for path in sorted(root.rglob("*.txt")):
        relative_path = path.relative_to(root).as_posix()
        year = 2025 if "2025" in path.relative_to(root).parts else 2026
        transcripts.append(
            Transcript(
                path=path,
                relative_path=relative_path,
                filename=path.name,
                year=year,
                modified_at=path.stat().st_mtime,
            )
        )
    return transcripts
