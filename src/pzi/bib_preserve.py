"""Lossless-ish BibTeX source parser and patch helpers.

This module treats a .bib file as source text. It parses enough structure to
locate entries and fields, then applies small text patches instead of rewriting
the whole file.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextSpan:
    start: int
    end: int


@dataclass(frozen=True)
class BibFieldNode:
    name: str
    value: str
    span: TextSpan
    value_span: TextSpan
    raw: str


@dataclass(frozen=True)
class BibEntryNode:
    entry_type: str
    citekey: str
    span: TextSpan
    fields: dict[str, BibFieldNode]
    raw: str


@dataclass(frozen=True)
class BibDocument:
    source: str
    entries: list[BibEntryNode]
    entries_by_key: dict[str, BibEntryNode]


@dataclass(frozen=True)
class BibtexSourceError:
    message: str
    offset: int
    line: int
    column: int


def bibtex_source_errors(source: str) -> list[BibtexSourceError]:
    """Return source-level errors that make preserving patches unsafe."""
    errors: list[BibtexSourceError] = []
    i = 0
    while True:
        at = source.find("@", i)
        if at < 0:
            break
        kind_start = at + 1
        kind_end = kind_start
        while kind_end < len(source) and (source[kind_end].isalnum() or source[kind_end] in "_-:"):
            kind_end += 1
        open_pos = _skip_ws_to_open(source, kind_end)
        if open_pos is None:
            i = kind_end
            continue
        close_pos = _matching_close(source, open_pos)
        if close_pos is None:
            line, column = _line_column(source, at)
            errors.append(
                BibtexSourceError(
                    message="entry has no matching closing delimiter",
                    offset=at,
                    line=line,
                    column=column,
                )
            )
            i = open_pos + 1
            continue
        i = close_pos + 1
    return errors


def parse_bibtex_document(source: str) -> BibDocument:
    entries: list[BibEntryNode] = []
    i = 0
    while True:
        at = source.find("@", i)
        if at < 0:
            break
        kind_start = at + 1
        kind_end = kind_start
        while kind_end < len(source) and (source[kind_end].isalnum() or source[kind_end] in "_-:"):
            kind_end += 1
        entry_type = source[kind_start:kind_end]
        open_pos = _skip_ws_to_open(source, kind_end)
        if open_pos is None:
            i = kind_end
            continue
        close_pos = _matching_close(source, open_pos)
        if close_pos is None:
            i = open_pos + 1
            continue
        end = close_pos + 1
        if entry_type.lower() not in {"comment", "string", "preamble"}:
            node = _parse_entry(source, at, kind_start, kind_end, open_pos, end)
            if node is not None:
                entries.append(node)
        i = end
    return BibDocument(
        source=source, entries=entries, entries_by_key={e.citekey: e for e in entries}
    )


def append_entry_preserving_source(source: str, rendered_entry: str) -> str:
    rendered = rendered_entry if rendered_entry.endswith("\n") else rendered_entry + "\n"
    if not source:
        return rendered
    separator = "" if source.endswith("\n\n") else ("\n" if source.endswith("\n") else "\n\n")
    return source + separator + rendered


def patch_entry_fields_preserving_source(
    source: str,
    citekey: str,
    fields: dict[str, str],
) -> str:
    doc = parse_bibtex_document(source)
    entry = doc.entries_by_key.get(citekey)
    if entry is None:
        raise ValueError(f"entry not found: {citekey}")

    patches: list[tuple[int, int, str]] = []
    missing: list[tuple[str, str]] = []
    for name, value in fields.items():
        node = entry.fields.get(name.lower())
        if node is None:
            missing.append((name.lower(), value))
            continue
        patches.append((node.value_span.start, node.value_span.end, _brace_value(value)))

    if missing:
        insert_at = _entry_insert_position(source, entry)
        indent = _entry_indent(entry) or "  "
        rendered = "".join(f"{indent}{name} = {{{value}}},\n" for name, value in missing)
        patches.append((insert_at, insert_at, rendered))

    return _apply_patches(source, patches)


def _parse_entry(
    source: str, at: int, kind_start: int, kind_end: int, open_pos: int, end: int
) -> BibEntryNode | None:
    comma = _find_top_level_comma(source, open_pos + 1, end - 1)
    if comma is None:
        return None
    citekey = source[open_pos + 1 : comma].strip()
    if not citekey:
        return None
    fields = _parse_fields(source, comma + 1, end - 1)
    return BibEntryNode(
        entry_type=source[kind_start:kind_end],
        citekey=citekey,
        span=TextSpan(at, end),
        fields=fields,
        raw=source[at:end],
    )


def _parse_fields(source: str, start: int, end: int) -> dict[str, BibFieldNode]:
    fields: dict[str, BibFieldNode] = {}
    i = start
    while i < end:
        while i < end and (source[i].isspace() or source[i] == ","):
            i += 1
        name_start = i
        while i < end and (source[i].isalnum() or source[i] in "_-:"):
            i += 1
        if i == name_start:
            i += 1
            continue
        name = source[name_start:i].lower()
        while i < end and source[i].isspace():
            i += 1
        if i >= end or source[i] != "=":
            continue
        i += 1
        while i < end and source[i].isspace():
            i += 1
        value_start = i
        value_end = _field_value_end(source, i, end)
        span_end = value_end
        while span_end < end and source[span_end].isspace():
            span_end += 1
        if span_end < end and source[span_end] == ",":
            span_end += 1
        raw_value = source[value_start:value_end]
        fields[name] = BibFieldNode(
            name=name,
            value=_unbrace_value(raw_value.strip()),
            span=TextSpan(name_start, span_end),
            value_span=TextSpan(value_start, value_end),
            raw=source[name_start:span_end],
        )
        i = span_end
    return fields


def _skip_ws_to_open(source: str, pos: int) -> int | None:
    while pos < len(source) and source[pos].isspace():
        pos += 1
    if pos < len(source) and source[pos] in "{(":
        return pos
    return None


def _matching_close(source: str, open_pos: int) -> int | None:
    opener = source[open_pos]
    closer = "}" if opener == "{" else ")"
    depth = 0
    quoted = False
    escaped = False
    for i in range(open_pos, len(source)):
        ch = source[i]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            quoted = not quoted
        if quoted:
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i
    return None


def _find_top_level_comma(source: str, start: int, end: int) -> int | None:
    depth = 0
    quoted = False
    for i in range(start, end):
        ch = source[i]
        if ch == '"':
            quoted = not quoted
        if quoted:
            continue
        if ch in "{(":
            depth += 1
        elif ch in "})" and depth > 0:
            depth -= 1
        elif ch == "," and depth == 0:
            return i
    return None


def _field_value_end(source: str, start: int, end: int) -> int:
    if start >= end:
        return start
    if source[start] in "{(":
        match = _matching_close(source, start)
        return min((match + 1) if match is not None else end, end)
    if source[start] == '"':
        i = start + 1
        escaped = False
        while i < end:
            if escaped:
                escaped = False
            elif source[i] == "\\":
                escaped = True
            elif source[i] == '"':
                return i + 1
            i += 1
        return end
    comma = source.find(",", start, end)
    return end if comma < 0 else comma


def _unbrace_value(value: str) -> str:
    if len(value) >= 2 and (
        (value[0] == "{" and value[-1] == "}") or (value[0] == '"' and value[-1] == '"')
    ):
        return value[1:-1]
    return value


def _brace_value(value: str) -> str:
    return "{" + value + "}"


def _line_column(source: str, offset: int) -> tuple[int, int]:
    line = source.count("\n", 0, offset) + 1
    line_start = source.rfind("\n", 0, offset) + 1
    return line, offset - line_start + 1


def _entry_insert_position(source: str, entry: BibEntryNode) -> int:
    close_pos = entry.span.end - 1
    line_start = source.rfind("\n", entry.span.start, close_pos) + 1
    return line_start if line_start > 0 else close_pos


def _entry_indent(entry: BibEntryNode) -> str | None:
    if not entry.fields:
        return None
    first = next(iter(entry.fields.values()))
    line = entry.raw[: first.span.start - entry.span.start]
    line_start = line.rfind("\n") + 1
    indent = line[line_start:]
    return indent if indent.strip() == "" else None


def _apply_patches(source: str, patches: list[tuple[int, int, str]]) -> str:
    updated = source
    for start, end, replacement in sorted(patches, key=lambda p: p[0], reverse=True):
        updated = updated[:start] + replacement + updated[end:]
    return updated
