"""Common parser contract and structured ingestion failures."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from .models import RawDocument


class IngestionError(ValueError):
    """A source cannot be represented safely by the selected parser."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str | Path | None = None,
    ) -> None:
        self.code = code
        self.path = Path(path) if path is not None else None
        super().__init__(message)


class DocumentParser(ABC):
    """Interface implemented by every format-specific parser."""

    source_type: str
    suffixes: tuple[str, ...]

    @abstractmethod
    def parse(
        self,
        path: str | Path,
        *,
        source_id: str | None = None,
    ) -> RawDocument:
        """Parse one file into a format-neutral raw document."""

    def _source_path(self, path: str | Path) -> Path:
        source_path = Path(path)
        if not source_path.is_file():
            raise IngestionError(
                "SOURCE_NOT_FOUND",
                f"Source file does not exist: {source_path}",
                path=source_path,
            )
        if source_path.suffix.casefold() not in self.suffixes:
            raise IngestionError(
                "UNSUPPORTED_SOURCE_SUFFIX",
                (
                    f"{type(self).__name__} does not support source suffix "
                    f"{source_path.suffix!r}"
                ),
                path=source_path,
            )
        return source_path

    @staticmethod
    def _source_id(path: Path, source_id: str | None) -> str:
        candidate = source_id if source_id is not None else path.name
        if not isinstance(candidate, str) or not candidate.strip():
            raise IngestionError(
                "INVALID_SOURCE_ID",
                "source_id must be a non-empty string",
                path=path,
            )
        return candidate.strip()
