"""Comment-preserving edits to the source registry file (`config/sources.yaml`).

The registry is a hand-maintained file full of comments that explain why each board is
there. Loading it into a YAML dumper and writing it back drops every one of them, so
edits here are line-level: an entry is appended as text under `sources:`, and `enabled`
is flipped in place. Every edit is re-parsed and checked by `load_registry` before the
file is touched, and the write is atomic, so a bad edit leaves the original file alone.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from sabra_domain.errors import ConfigurationError, ValidationFailure

_SAFE_SCALAR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._&'/()+,-]*$")
_RESERVED = {"true", "false", "yes", "no", "on", "off", "null", "none", "~", "y", "n"}
_ENTRY_KEYS = (
    "id",
    "kind",
    "token",
    "display_name",
    "industry",
    "domain",
    "enabled",
    "max_items",
    "verified_jobs",
    "verified_at",
    "notes",
)


def _reads_back(rendered: str, value: str) -> bool:
    """Whether YAML reads `rendered` as exactly the string `value`."""

    try:
        loaded = yaml.safe_load("k: " + rendered)
    except yaml.YAMLError:
        return False
    return isinstance(loaded, dict) and loaded.get("k") == value and type(loaded["k"]) is str


def _refuse_unprintable(text: str) -> None:
    """Lone surrogates, control characters and line/paragraph separators are not saved."""

    for char in text:
        if char not in "\n\t" and not char.isprintable():
            raise ValidationFailure(
                f"the value contains a character that cannot be saved safely ({char!r}); "
                "remove it and try again"
            )


def yaml_scalar(value: Any) -> str:
    """Render one value as a YAML scalar that reads back as the same Python value.

    A plain scalar is used only when YAML itself reads it back as the identical string
    (YAML 1.1 reads `0x1F`, `0b101` and `1_000` as numbers, `yes` as a boolean); anything
    else is double-quoted. Characters that cannot be saved safely are refused.
    """

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    # Ordinary whitespace that Python calls unprintable is normalised, not refused: a
    # board name fetched from Greenhouse may carry a non-breaking space, and notes
    # pasted on Windows carry CRLF line endings.
    text = text.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    _refuse_unprintable(text)
    if (
        _SAFE_SCALAR.match(text)
        and text.lower() not in _RESERVED
        and not text.endswith((" ", ":"))
        and ": " not in text
        and " #" not in text
        and _reads_back(text, text)
    ):
        return text
    # A JSON string is a valid YAML double-quoted scalar.
    quoted = json.dumps(text, ensure_ascii=False)
    if not _reads_back(quoted, text):
        raise ValidationFailure(f"the value {text!r} cannot be saved to the registry file")
    return quoted


def format_entry(entry: dict[str, Any], *, indent: str = "  ") -> list[str]:
    """The lines of one registry entry, formatted like the file's existing ones."""

    keys = [key for key in _ENTRY_KEYS if entry.get(key) is not None]
    keys += [key for key in entry if key not in _ENTRY_KEYS and entry[key] is not None]
    lines: list[str] = []
    for index, key in enumerate(keys):
        prefix = f"{indent}- " if index == 0 else f"{indent}  "
        lines.append(f"{prefix}{key}: {yaml_scalar(entry[key])}")
    return lines


def _newline_of(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _top_level_key(line: str) -> bool:
    """A non-comment line at column 0 that starts a new top-level mapping key."""

    return bool(line) and not line[0].isspace() and not line.startswith(("#", "-"))


def append_entry_text(text: str, entry: dict[str, Any]) -> str:
    """Return `text` with `entry` appended to the end of its `sources:` list."""

    newline = _newline_of(text)
    lines = text.split(newline) if text else []
    start = next((i for i, line in enumerate(lines) if re.match(r"^sources:\s*(#.*)?$", line)), None)
    flow_empty = next(
        (i for i, line in enumerate(lines) if re.match(r"^sources:\s*\[\s*\]\s*(#.*)?$", line)),
        None,
    )
    if flow_empty is not None:
        lines[flow_empty] = "sources:"
        start = flow_empty
    if start is None:
        raise ConfigurationError("the source registry has no block-style `sources:` list to edit")

    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _top_level_key(lines[index]):
            end = index
            break
    last_content = start
    for index in range(start + 1, end):
        if lines[index].strip():
            last_content = index
    new_lines = format_entry(entry)
    insertion = ["", *new_lines] if last_content != start else new_lines
    lines[last_content + 1 : last_content + 1] = insertion
    result = newline.join(lines)
    if not result.endswith(newline):
        result += newline
    return result


def _entry_span(lines: list[str], source_id: str) -> tuple[int, int, int] | None:
    """`(first line, one past the last line, dash indent)` of the entry with this id."""

    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)-\s+id:\s*(.*?)\s*(?:#.*)?$", line)
        if not match:
            continue
        value = match.group(2).strip().strip("'\"")
        if value != source_id:
            continue
        indent = len(match.group(1))
        end = len(lines)
        for follow in range(index + 1, len(lines)):
            candidate = lines[follow]
            if candidate.strip() and (len(candidate) - len(candidate.lstrip())) <= indent:
                end = follow
                break
        return index, end, indent
    return None


def set_enabled_text(text: str, source_id: str, enabled: bool) -> str:
    """Return `text` with the entry's `enabled` flag set, leaving everything else alone."""

    newline = _newline_of(text)
    lines = text.split(newline)
    span = _entry_span(lines, source_id)
    if span is None:
        raise ConfigurationError(f"source {source_id!r} was not found in the registry file")
    start, end, indent = span
    value = "true" if enabled else "false"
    key_indent = " " * (indent + 2)
    pattern = re.compile(rf"^({re.escape(key_indent)}enabled:\s*)([^\s#]+)(\s*#.*)?\s*$")
    for index in range(start, end):
        match = pattern.match(lines[index])
        if match:
            lines[index] = f"{match.group(1)}{value}{match.group(3) or ''}"
            return newline.join(lines)
    if enabled:
        return text  # no `enabled` key means enabled; nothing to write
    last = start
    for index in range(start, end):
        if lines[index].strip():
            last = index
    lines.insert(last + 1, f"{key_indent}enabled: false")
    return newline.join(lines)


# One lock per process serialises read-modify-write of the registry file (two quick clicks
# in the dashboard run on different threads). Another process (a CLI command during a
# dashboard edit) is handled by re-reading the file just before the replace and starting
# over when it changed.
_EDIT_LOCK = threading.Lock()
_EDIT_ATTEMPTS = 6
_REPLACE_ATTEMPTS = 20
_REPLACE_PAUSE = 0.05


def _write_atomically(path: Path, data: bytes) -> None:
    """Write a temp file next to `path`, flush it to disk, and swap it in.

    On Windows `os.replace` fails with PermissionError while another process (an editor,
    an antivirus scan) has the file open, so it is retried briefly.
    """

    descriptor, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".sources-", suffix=".tmp")
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(temp, path)
                return
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(_REPLACE_PAUSE)
    finally:
        temp.unlink(missing_ok=True)


def _sources_of(text: str) -> list[dict[str, Any]]:
    loaded = yaml.safe_load(text) or {}
    sources = loaded.get("sources") if isinstance(loaded, dict) else None
    if not isinstance(sources, list):
        raise ConfigurationError("the source registry has no `sources` list")
    return sources


def _validated(new_text: str, expected: list[dict[str, Any]], settings: Any) -> None:
    """Parse `new_text` exactly as `load_registry` would, and check it says what was meant.

    The registry must load, and its entries must equal `expected` field for field, so a
    value YAML would quietly read as something else is caught before the file is touched.
    """

    from .registry import load_registry

    with tempfile.TemporaryDirectory() as scratch:
        probe = Path(scratch) / "sources.yaml"
        probe.write_bytes(new_text.encode("utf-8"))
        load_registry(probe, settings)
    if _sources_of(new_text) != expected:
        raise ConfigurationError(
            "the edit would not read back as intended, so the registry file was not changed"
        )


def _edit(path: Path, settings: Any, plan: Any) -> bool:
    """Apply `plan(text) -> (new_text, expected_sources)` under the lock.

    Returns False when the plan changed nothing. Any I/O failure that persists is a
    `ConfigurationError`, never a raw OSError.
    """

    path = Path(path)
    try:
        with _EDIT_LOCK:
            for _ in range(_EDIT_ATTEMPTS):
                original = path.read_bytes()
                text = original.decode("utf-8")
                new_text, expected = plan(text)
                if new_text == text:
                    return False
                _validated(new_text, expected, settings)
                if path.read_bytes() != original:
                    continue  # someone else wrote the file meanwhile: start over from theirs
                _write_atomically(path, new_text.encode("utf-8"))
                return True
    except OSError as exc:
        raise ConfigurationError(
            f"could not update the source registry file {path}: {type(exc).__name__}: {exc}"
        ) from exc
    raise ConfigurationError(
        f"the source registry file {path} kept changing while it was being edited; "
        "try again in a moment"
    )


def append_source(path: Path, entry: dict[str, Any], *, settings: Any = None) -> None:
    """Append an entry to the registry file, keeping its comments and layout."""

    wanted = {key: value for key, value in entry.items() if value is not None}

    def plan(text: str) -> tuple[str, list[dict[str, Any]]]:
        existing = _sources_of(text)
        for other in existing:
            same_board = (
                str(other.get("kind", "")).upper() == str(wanted.get("kind", "")).upper()
                and str(other.get("token", "")).lower() == str(wanted.get("token", "")).lower()
            )
            if other.get("id") == wanted.get("id") or same_board:
                raise ValidationFailure(
                    f"{wanted.get('id')!r} is already in the registry. Nothing was added."
                )
        return append_entry_text(text, entry), [*existing, wanted]

    _edit(path, settings, plan)


def set_source_enabled(path: Path, source_id: str, enabled: bool, *, settings: Any = None) -> bool:
    """Flip `enabled` in place. Returns False when the file already said so."""

    def plan(text: str) -> tuple[str, list[dict[str, Any]]]:
        new_text = set_enabled_text(text, source_id, enabled)
        expected = []
        for other in _sources_of(text):
            other = dict(other)
            if other.get("id") == source_id:
                other["enabled"] = enabled
            expected.append(other)
        return new_text, expected

    return _edit(path, settings, plan)
