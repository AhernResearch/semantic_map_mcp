"""Shared result types and the only replaceable model dependency."""
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class MediaPayload:
    metadata: Mapping[str, Any]
    content: bytes


@dataclass(frozen=True)
class ToolResult:
    envelope: Mapping[str, Any]
    media: tuple[MediaPayload, ...]


class TextEncoder(Protocol):
    space_id: str
    fingerprint: str

    def encode(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...
