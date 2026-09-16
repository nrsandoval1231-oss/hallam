"""Deterministic identity and hashing.

Ids are sortable and human-readable so a ledger line identifies its subject
without a join. Hashes are the only basis for deduplication and approval scope:
both must be recomputable from content alone.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from typing import Any

_BASE32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _b32(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_BASE32[value & 31])
        value >>= 5
    return "".join(reversed(out))


def new_id(prefix: str) -> str:
    """Sortable, collision-resistant id: `<prefix>_<time><random>`.

    Time-ordered so an append-only ledger reads chronologically by id, and with
    enough randomness that two ids in the same millisecond never collide.
    """

    millis = int(time.time() * 1000)
    return f"{prefix}_{_b32(millis, 10)}{_b32(secrets.randbits(30), 6)}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_json(data: Any) -> str:
    """Canonical JSON: sorted keys, no incidental whitespace, UTF-8 safe."""

    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def content_hash(data: Any) -> str:
    """Hash of a structured artifact.

    Volatile metadata (timestamps, ids, run paths) must be excluded by the caller
    before hashing, never by this function, so that what is hashed is explicit.
    """

    return sha256_text(stable_json(data))


_WS = re.compile(r"\s+")
# Unicode-aware: `[^\w\s]` keeps letters and digits from every script. An
# ASCII-only class silently collapsed every CJK title to the empty string, which
# made all Japanese postings at one employer deduplicate into a single
# opportunity.
_NON_WORD = re.compile(r"[^\w\s]+|_", re.UNICODE)
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    return _WS.sub(" ", text).strip()


def slugify(text: str | None) -> str:
    normalized = normalize_text(text).lower()
    return _NON_WORD.sub("-", normalized).strip("-")


def normalize_title(title: str | None) -> str:
    """Normalize a title for comparison.

    Deliberately conservative: strips punctuation, parenthetical noise (location,
    remote markers, requisition ids) and whitespace, but keeps seniority and
    functional words, because two titles differing there are different roles.

    The class is Unicode-aware so a title in any script survives: an ASCII-only
    strip reduced every Japanese title to `""`, which made unrelated postings look
    identical and merged them into one opportunity.
    """

    if not title:
        return ""
    text = title.casefold()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = re.sub(r"[-–—|/]+", " ", text)
    text = re.sub(r"\b(?:remote|hybrid|onsite|on-site|usa|us|u\.s\.)\b", " ", text)
    text = re.sub(r"\breq(?:uisition)?\s*#?\s*\d+\b", " ", text)
    text = re.sub(r"\b\d{4,}\b", " ", text)
    normalized = normalize_text(_NON_WORD.sub(" ", text))
    if normalized:
        return normalized
    # A title made entirely of punctuation still needs a stable identity; falling
    # back to the raw text is better than returning "" and matching everything.
    return normalize_text(title).casefold()


def normalize_company(name: str | None) -> str:
    """Normalize a company name for comparison.

    Legal suffixes are dropped because the same employer publishes under
    "Acme", "Acme Inc." and "Acme, Inc." across boards. Unicode-aware for the
    same reason as `normalize_title`.
    """

    if not name:
        return ""
    text = name.casefold()
    text = text.replace("&", " and ")
    text = _NON_WORD.sub(" ", text)
    text = re.sub(
        r"\b(?:inc|llc|ltd|limited|corp|corporation|co|company|gmbh|plc|sa|nv|ag|"
        r"holdings|group|the)\b",
        " ",
        text,
    )
    normalized = normalize_text(text)
    if normalized:
        return normalized
    return _NON_WORD.sub(" ", name.casefold()).strip()


_STATE_ABBREV = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}


def normalize_location(location: str | None) -> str:
    """Collapse a location string to a comparable `city, ST`-ish token."""

    if not location:
        return ""
    text = normalize_text(location).lower()
    for full, abbrev in _STATE_ABBREV.items():
        text = re.sub(rf"\b{re.escape(full)}\b", abbrev.lower(), text)
    text = re.sub(r"\b(?:united states|usa|u\.s\.a\.|us)\b", " ", text)
    text = re.sub(r"\b(?:remote|hybrid|onsite|on-site)\b", " ", text)
    text = _NON_WORD.sub(" ", text)
    return normalize_text(text)


def canonical_key(*parts: str | None) -> str:
    """Deterministic deduplication key from normalized components.

    Parts are normalized by their producers; this only joins and hashes so the
    key is stable across runs and re-derivable from the persisted fields.
    """

    joined = "|".join((p or "").strip().lower() for p in parts)
    return sha256_text(joined)[:32]


def token_matches_phrase(haystack: str, phrase: str) -> bool:
    """Word-boundary phrase match that is punctuation-insensitive.

    `re.search(r"\\bintern\\b", ...)` matches inside "internal-sales", and a raw
    substring test matches "Intern" in "International". Both produce false
    rejections against a candidate's real search intent, so the comparison runs
    over tokenized text with the phrase's own tokens matched as a contiguous run.
    """

    if not haystack or not phrase:
        return False
    hay_tokens = [t for t in _TOKEN.findall(haystack.lower()) if t]
    phrase_tokens = [t for t in _TOKEN.findall(phrase.lower()) if t]
    if not phrase_tokens or len(phrase_tokens) > len(hay_tokens):
        return False
    width = len(phrase_tokens)
    return any(
        hay_tokens[start : start + width] == phrase_tokens
        for start in range(len(hay_tokens) - width + 1)
    )
