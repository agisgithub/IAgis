"""Detecção normalizada de menções, sem interpretar o conteúdo."""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass

_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Mention:
    content_hash: str
    normalized_content: str


def normalize_content(content: str) -> str:
    return _WS.sub(" ", _TAGS.sub(" ", html.unescape(content))).strip()


def detect_mention(content: str, mention: str, *, author_id: int | None = None,
                   own_user_id: int | None = None) -> Mention | None:
    if own_user_id is not None and author_id == own_user_id:
        return None
    normalized = normalize_content(content)
    pattern = re.compile(rf"(?<![\w@]){re.escape(mention)}(?!\w)", re.IGNORECASE)
    if not pattern.search(normalized):
        return None
    digest = hashlib.sha256(normalized.casefold().encode("utf-8")).hexdigest()
    return Mention(digest, normalized)
