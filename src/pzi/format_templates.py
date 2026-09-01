"""Zotero-style filename templates and small Better BibTeX citekey subset."""

from __future__ import annotations

import re
import shlex
import unicodedata
from collections.abc import Mapping
from typing import Any

from pylatexenc.latex2text import LatexNodes2Text

from pzi.bibtex import generate_citekey_base, normalize_authors, resolve_citekey_collision
from pzi.similarity import split_family_given

_TEMPLATE_RE = re.compile(r"{{\s*:?\s*([A-Za-z][A-Za-z0-9_]*)\s*([^{}]*)}}")
_NON_CITEKEY = re.compile(r"[^a-z0-9]+")
#: Applied to each rendered *variable*, so a raw `title` cannot contribute a
#: separator — only a quoted literal may. BBT's own `citekeyUnsafeChars` is
#: `\"#%'(),={}~`, so the four separators kept below are safe in a key.
_ALPHANUMERIC_ONLY = re.compile(r"[^A-Za-z0-9]+")
_ALPHANUMERIC_OR_INNER_HYPHEN = re.compile(r"[^A-Za-z0-9-]+")
#: Trailing/leading/doubled separators, once empty components are dropped.
_SEPARATOR_RUN = re.compile(r"[-_:.]{2,}")
_NON_CITEKEY_KEEPING_SEPARATORS = re.compile(r"[^A-Za-z0-9\-_:.]+")
_FILENAME_FORBIDDEN = re.compile(r"[\\/\x00-\x1f:]+")
_WHITESPACE = re.compile(r"\s+")
#: Zotero's capture-group syntax in a `replaceTo` value: `$1`, or `$$` for a
#: literal dollar.
_CAPTURE_GROUP_REF = re.compile(r"\$(\$|\d+)")
#: Better BibTeX's default `skipWords`, which `title`/`shorttitle` always apply.
#: pzi shipped a 10-word list, so a title beginning "Towards …" or "From …"
#: built its key on the skipped word — a plausible-looking key that simply did
#: not match the one the same formula produces in Zotero. Kept separate from
#: `bibtex._STOPWORDS`, which belongs to the built-in scheme and is unaffected.
_STOPWORDS = frozenset(
    """a ab aboard about above across after against al along amid among an and
    anti around as at before behind below beneath beside besides between beyond
    but by d da das de del dell dello dei degli della delle dem den der des
    dessus dopo down du during ein eine einem einen einer eines el en et except
    for from gli i il in inside into is l la las le les like lo los near nor of
    off on onto or over past per plus round save since so some sur than the
    through to toward towards un una unas under underneath une unlike uno unos
    until up upon versus via vom von vor while with within without yet zu
    zum""".split()
)


def render_zotero_template(template: str, record: Mapping[str, Any]) -> str:
    """Render the useful Zotero 7 file-renaming template subset.

    Supports `{{ field ... }}` and `{{ :field ... }}` variables with `prefix`,
    `suffix`, `start`, `truncate`, `replaceFrom`, `replaceTo`, `replaceFrom2`,
    `replaceTo2`, `regexOpts`, and `case` options. Unsupported variables render
    empty so copied Zotero templates degrade safely.

    `replaceFrom2`/`replaceTo2` are a pzi addition; every template Zotero
    itself accepts renders the same here.
    """

    def replace(match: re.Match[str]) -> str:
        variable = match.group(1)
        options = _parse_options(match.group(2))
        value = _template_value(variable, record)
        return _apply_options(value, options)

    return _TEMPLATE_RE.sub(replace, template).strip()


def format_pdf_filename(template: str | None, record: Mapping[str, Any]) -> str:
    """Render a safe PDF filename, appending `.pdf` when needed."""
    stem = render_zotero_template(template, record) if template else ""
    if not stem:
        citekey = record.get("citekey")
        stem = str(citekey) if citekey else generate_citekey_base(_citekey_input(record))
    stem = _sanitize_filename_stem(stem)
    if not stem:
        stem = "paper"
    # Strip any existing .pdf suffix to avoid double extension
    if stem.lower().endswith(".pdf"):
        stem = stem[:-4]
    return f"{stem}.pdf"


def format_citekey(
    template: str | None,
    record: Mapping[str, Any],
    existing_keys: set[str],
) -> str:
    """Render a citekey from Zotero-style or common Better BibTeX templates."""
    if template:
        if "{{" in template:
            base = render_zotero_template(template, record)
        else:
            base = _render_better_bibtex_formula(template, record)
        # A template is an explicit instruction, so its separators are kept —
        # a library exported from Better BibTeX is full of `auth-title-year`
        # keys, and stripping the literals the user wrote made that shape
        # impossible to reproduce while reporting no error.
        cleaned = _sanitize_citekey_keeping_separators(base)
    else:
        base = generate_citekey_base(_citekey_input(record))
        # The built-in scheme never emits separators; keep it strictly
        # alphanumeric so existing keys are unaffected by any of this.
        cleaned = _sanitize_citekey(base)

    if not cleaned:
        cleaned = generate_citekey_base(_citekey_input(record))
    return resolve_citekey_collision(cleaned, existing_keys)


def _sanitize_citekey_keeping_separators(value: str) -> str:
    """Strip unsafe characters but keep the separators a template asked for.

    An absent component (a record with no year) leaves an adjacent separator
    with nothing to join, so runs are collapsed and the ends trimmed: the key
    is `tsiotras-algorithmic`, never `tsiotras-algorithmic-`.
    """
    cleaned = _NON_CITEKEY_KEEPING_SEPARATORS.sub("", _ascii(value))
    cleaned = _SEPARATOR_RUN.sub(lambda m: m.group(0)[0], cleaned)
    return cleaned.strip("-_:.")


def _parse_options(text: str) -> dict[str, str]:
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    options: dict[str, str] = {}
    try:
        for token in lexer:
            if "=" not in token:
                options[token] = "true"
                continue
            key, value = token.split("=", 1)
            options[key] = value
    except ValueError:
        # An unbalanced quote inside `{{ … }}` — `shlex` raises during
        # *iteration*, not construction. Degrade like the bad-regex and bad-int
        # handlers below rather than raising: this runs deep inside `add`/`pdf`,
        # where a one-character config typo would otherwise surface as a raw
        # traceback. `validate_app_config` reports the typo at config load, so
        # degrading here does not make it silent.
        return {}
    return options


def describe_template_error(template: str | None) -> str | None:
    """Return why *template* is unparseable, or None when it is fine.

    Used by config validation so a typo is reported once, at load, instead of
    silently changing every filename it touches.
    """
    if not template:
        return None
    if "{{" not in template:
        # Better-BibTeX style formulas take a different renderer that never
        # reaches `shlex` — but they still have a grammar, and returning None
        # here meant the dialect most of these templates are written in was
        # never checked at all.
        return describe_bbt_formula_error(template)
    for match in _TEMPLATE_RE.finditer(template):
        options_text = match.group(2)
        lexer = shlex.shlex(options_text, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        try:
            list(lexer)
        except ValueError as exc:
            return f"{exc} in {match.group(0)!r}"
    return None


def _replacement_text(text: str) -> str:
    """Translate Zotero's `$1` capture-group references into `re.sub`'s `\\1`.

    Zotero documents `$1`; `re.sub` reads it as a literal dollar sign, so a
    template copied from Zotero's own docs inserted `$1` into the filename. `$$`
    is an escaped dollar. Existing `\\1` references keep working — the
    substitution runs through a function, so the result is never rescanned.
    """
    return _CAPTURE_GROUP_REF.sub(
        lambda m: "$" if m.group(1) == "$" else "\\" + m.group(1), text
    )


def _apply_options(value: str, options: Mapping[str, str]) -> str:
    if not value:
        return ""

    if "match" in options:
        try:
            if re.search(options["match"], value) is None:
                return ""
        except re.error:
            return ""

    flags = re.IGNORECASE if "i" in options.get("regexOpts", "") else 0
    count = 0 if "g" in options.get("regexOpts", "") else 1
    # Two pairs, applied in order. Zotero allows one, which cannot express a
    # rename needing two different replacements on the same value: reducing
    # `Smith and Doe` to `Smith-Doe` while also dropping the `.` from
    # `Smith et al.` needs both. `regexOpts` governs both pairs.
    for from_key, to_key in (("replaceFrom", "replaceTo"), ("replaceFrom2", "replaceTo2")):
        if from_key not in options:
            continue
        try:
            value = re.sub(
                options[from_key],
                _replacement_text(options.get(to_key, "")),
                value,
                count=count,
                flags=flags,
            )
        except re.error:
            pass

    if "start" in options:
        try:
            value = value[int(options["start"]):]
        except ValueError:
            pass

    if "truncate" in options:
        try:
            value = value[: int(options["truncate"])]
        except ValueError:
            pass

    value = value.strip()
    case = options.get("case")
    if case in {"lower", "lowercase"}:
        value = value.lower()
    elif case in {"upper", "uppercase"}:
        value = value.upper()
    elif case in {"hyphen", "kebab"}:
        value = _WHITESPACE.sub("-", value.lower())
    elif case == "snake":
        value = _WHITESPACE.sub("_", value.lower())

    if not value:
        return ""
    return f"{options.get('prefix', '')}{value}{options.get('suffix', '')}"


def _template_value(variable: str, record: Mapping[str, Any]) -> str:
    key = variable[0].lower() + variable[1:]
    if key in {"firstCreator", "firstcreator"}:
        return _zotero_first_creator(record)
    if key == "auth":
        # Better BibTeX's `auth`, kept to its own meaning here. Aliasing it to
        # `firstCreator` made one word mean two things depending on which
        # config key the template appeared under.
        return _first_creator(record)
    if key in {"authors", "creators"}:
        return " and ".join(_author_family_names(record))
    if key == "year":
        year = record.get("year")
        return str(year) if year is not None else ""
    if key == "title":
        return str(record.get("title") or "")
    if key in {"citationKey", "citationkey", "citekey"}:
        return str(record.get("citekey") or "")
    if key in {"publicationTitle", "publicationtitle", "venue"}:
        return str(record.get("venue") or "")
    if key == "doi":
        return str(record.get("doi") or "")
    if key in {"itemType", "itemtype"}:
        return str(record.get("item_type") or record.get("itemType") or "")
    value = record.get(key)
    return str(value) if value is not None else ""


#: Filters `_render_bbt_part` implements. An unrecognized one is *ignored* at
#: render time, which is exactly the silent degradation validation exists to
#: catch: `auth.lowr` renders `Smith`, not `smith`.
_BBT_FILTERS = frozenset({"lower", "upper", "fold", "clean"})

#: Variables with dedicated handling. Any other bare word is read as a record
#: field (`_render_bbt_part`'s fallback), so the validator accepts a real
#: `NormalizedRecord` key and rejects everything else — `authr` used to render
#: as the empty string and silently vanish from every generated key.
_BBT_VARIABLES = frozenset({"auth", "title", "year", "doi", "venue"})

_SHORTTITLE_RE = re.compile(r"^shorttitle(\((\d+)(\s*,\s*\d+)?\))?$")


def describe_bbt_formula_error(template: str) -> str | None:
    """Why *template* is not a usable Better BibTeX formula, or None.

    `describe_template_error` returned None for anything without `{{`, so *no*
    Better BibTeX formula was validated at all — the dialect this project's own
    config uses. `'authr.lower + year'`, `'auth.lowr + year'` and `'this is not
    a formula'` were all accepted, and each renders as a silently shorter key,
    which is precisely what the config-level check says it exists to prevent.

    Deliberately permissive about *fields*: any `NormalizedRecord` key is a
    valid variable, because the renderer falls back to reading one.
    """
    from pzi.bibtex import NormalizedRecord

    known_fields = set(NormalizedRecord.__annotations__)
    for raw_part in template.split("+"):
        part = raw_part.strip()
        if not part:
            return "empty component (a stray '+'?)"
        if (part.startswith("'") and part.endswith("'")) or (
            part.startswith('"') and part.endswith('"')
        ):
            continue
        segments = part.lower().split(".")
        head = segments[0]
        if not _SHORTTITLE_RE.match(head) and head not in _BBT_VARIABLES:
            if head not in known_fields:
                return (
                    f"unknown variable {segments[0]!r} — expected one of "
                    f"{', '.join(sorted(_BBT_VARIABLES))}, shorttitle(n[,m]), "
                    "a record field, or a quoted literal"
                )
        for flt in segments[1:]:
            if flt not in _BBT_FILTERS:
                return (
                    f"unknown filter {flt!r} on {segments[0]!r} — expected one of "
                    f"{', '.join(sorted(_BBT_FILTERS))}"
                )
    return None


def _render_better_bibtex_formula(template: str, record: Mapping[str, Any]) -> str:
    parts = [part.strip() for part in template.split("+")]
    rendered: list[str] = []
    for part in parts:
        rendered.append(_render_bbt_part(part, record))
    return "".join(rendered)


def _render_bbt_part(part: str, record: Mapping[str, Any]) -> str:
    is_single_quoted = part.startswith("'") and part.endswith("'")
    is_double_quoted = part.startswith('"') and part.endswith('"')
    if is_single_quoted or is_double_quoted:
        # A quoted literal is the *only* way a separator may enter a key. It
        # keeps its own text as well: `'fixed' + year` is a documented use.
        # Anything outside [alnum] + the four safe separators is dropped, since
        # it would either break BibTeX or reintroduce the punctuation the
        # sanitizer exists to remove.
        return _NON_CITEKEY_KEEPING_SEPARATORS.sub("", _ascii(part[1:-1]))

    lower = part.lower()
    filters = lower.split(".")
    head = filters[0]
    value = ""
    if head == "auth":
        value = _first_creator(record)
    elif head.startswith("shorttitle"):
        value = _shorttitle(record, head)
    elif head == "title":
        value = str(record.get("title") or "")
    elif head == "year":
        value = str(record.get("year") or "")
    elif head in {"doi", "venue"}:
        value = str(record.get(head) or "")
    else:
        value = str(record.get(head) or "")

    # Sanitized *here*, per part, rather than once over the joined result. The
    # join now carries separators that must survive, and `title` (and the
    # unknown-field fallback) hand back a raw field value — so a single loose
    # sanitizer at the end would either delete the separators or let a title's
    # spaces and punctuation into the key.
    #
    # `auth` is the exception: a hyphen inside "Domingo-Enrich" is part of the
    # name, and BBT keeps it. Every other variable is stripped to alphanumerics,
    # so a title still cannot smuggle a separator in.
    pattern = _ALPHANUMERIC_OR_INNER_HYPHEN if head == "auth" else _ALPHANUMERIC_ONLY
    value = pattern.sub("", _ascii(value)).strip("-")

    for flt in filters[1:]:
        if flt == "lower":
            value = value.lower()
        elif flt == "upper":
            value = value.upper()
        elif flt == "fold":
            value = _ascii(value)
        elif flt == "clean":
            value = _sanitize_citekey(value)
    return value


def _shorttitle(record: Mapping[str, Any], token: str) -> str:
    """Better BibTeX ``shorttitle(n=3, m=0)``.

    Per BBT's own reference: "the first `n` (default: 3) words of the title,
    apply capitalization to first `m` (default: 0) of those". Stopwords are
    dropped first, and the words are concatenated with no separator.

    `m` used to be read as a per-word truncation length, which cannot be what
    it means: it *defaults to 0*, so on that reading every plain
    ``shorttitle()`` — including the ``shorttitle(3,3)`` in pzi's own shipped
    config template — would render the empty string.
    """
    title = str(record.get("title") or "")
    match = re.search(r"shorttitle\((\d+)(?:\s*,\s*(\d+))?\)", token)
    n_words = int(match.group(1)) if match else 3
    capitalize = int(match.group(2)) if match and match.group(2) else 0
    words = [_sanitize_citekey(w) for w in title.split()]
    words = [w for w in words if w and w not in _STOPWORDS]
    selected = words[:n_words]
    return "".join(
        word.capitalize() if index < capitalize else word
        for index, word in enumerate(selected)
    )


def _first_creator(record: Mapping[str, Any]) -> str:
    """One family name, for Better BibTeX's ``auth``.

    A citekey component must stay a single word, so this stops at the first
    name. Zotero's ``firstCreator`` summarizes the whole creator list instead —
    see :func:`_zotero_first_creator`, which is why the two are separate
    functions rather than one shared helper.
    """
    names = _author_family_names(record)
    return names[0] if names else ""


def _zotero_first_creator(record: Mapping[str, Any]) -> str:
    """Zotero's ``firstCreator``: the item list's Creator column.

    Zotero documents this as "the parent item's creator (1-2 authors or
    editors)", and renames files with it: one name alone, two joined by ``and``,
    three or more collapsed to ``et al.``. pzi returned the bare first surname
    for all three, so the documented default template — the one in
    ``config.template.toml`` — silently dropped every co-author.
    """
    names = _author_family_names(record)
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{names[0]} et al."


def _author_family_names(record: Mapping[str, Any]) -> list[str]:
    """Family names, split the same way author *matching* splits them.

    This had its own split — `text.split()[-1]` for an unreversed name — so
    `"van der Berg, Anna"` gave `vanderberg` while `"Anna van der Berg"`, the
    same author written the other way round, gave `berg`: one person, two
    citekeys, decided by how the source happened to store the name.
    `similarity.split_family_given` already absorbed the particles, and its
    comment describes this exact defect for the matching path.
    """
    authors = normalize_authors(record.get("authors"))
    if not authors:
        return []
    families: list[str] = []
    _bare = re.compile(r"^[A-Z]\.$")  # skip single-initial entries like "N."
    for author in authors:
        if not isinstance(author, str) or not author.strip():
            continue
        text = author.strip()
        if _bare.match(text):
            continue
        family, _given = split_family_given(text)
        if family:
            families.append(family)
    return families


def _citekey_input(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "authors": normalize_authors(record.get("authors")),
        "title": record.get("title"),
        "year": record.get("year"),
    }


def _sanitize_citekey(value: str) -> str:
    return _NON_CITEKEY.sub("", _ascii(value).lower())


#: A `%` opens a LaTeX comment, so the decoder drops the rest of the line.
#: `record_to_bibtex_entry` writes provider titles verbatim (`bibtex.py`), so an
#: unescaped one really does reach here — "50% Faster Training" would have been
#: filed as "50". Escaping a bare `%` first costs nothing on the common path.
_BARE_PERCENT = re.compile(r"(?<!\\)%")

#: The start of `\cmd{` — a macro taking a braced argument. The argument itself
#: is found by scanning for its matching close brace rather than by regex,
#: because Better BibTeX protects case with braces of its own, so an argument
#: routinely arrives already braced (`\texttt{{{MiniMol}}}`). A `[^{}]*` pattern
#: matched only the brace-free form and silently ignored the nested one.
_MACRO_ARGUMENT_START = re.compile(r"\\[A-Za-z]+\s*\{")

_LATEX_TEXT = LatexNodes2Text(math_mode="text")


def _macro_arguments(value: str) -> list[tuple[int, int, int]]:
    """Spans of every ``\\cmd{...}`` in *value*, brace nesting respected.

    Each item is ``(macro_start, argument_start, argument_end)`` with the
    argument bounds exclusive of the outer braces.
    """
    spans: list[tuple[int, int, int]] = []
    for match in _MACRO_ARGUMENT_START.finditer(value):
        depth, index = 1, match.end()
        while index < len(value) and depth:
            if value[index] == "{":
                depth += 1
            elif value[index] == "}":
                depth -= 1
            index += 1
        if not depth:  # a balanced argument; an unbalanced one is not a macro call
            spans.append((match.start(), match.end(), index - 1))
    return spans


def _unwrapped(value: str) -> str:
    """*value* with macro names removed but their braced content kept."""
    previous = None
    while previous != value:
        previous = value
        spans = _macro_arguments(value)
        if not spans:
            break
        # Rightmost first, so earlier spans' offsets stay valid.
        for macro_start, arg_start, arg_end in reversed(spans):
            value = value[:macro_start] + value[arg_start:arg_end] + value[arg_end + 1:]
    return value


def _lost_braced_content(source: str, decoded: str) -> bool:
    """Did decoding drop the *content* of a macro it could not render?

    Counting characters cannot answer this: rendering legitimately shrinks text
    (``\\infty`` is five characters and ``∞`` is one), so a length test flagged
    every well-decoded formula. What matters is narrower — a macro that wraps
    text, whose text is gone afterwards. ``$\\texttt{MiniMol}$`` decoded to the
    empty string and took the paper's name with it, while ``$\\mathcal{H}$``
    became ``ℋ``, which still *is* the H once folded to ASCII.
    """
    folded = _ascii(decoded)
    for _macro_start, arg_start, arg_end in _macro_arguments(source):
        # Inner braces are Better BibTeX's case protection, not content.
        content = source[arg_start:arg_end].replace("{", "").replace("}", "")
        stripped = _ascii(content).strip()
        if stripped and stripped not in folded:
            return True
    return False


def _latex_to_text(value: str) -> str:
    """Decode LaTeX markup in a stored field to the text it represents.

    A Zotero/Better BibTeX export escapes specials into LaTeX *commands* and
    protects case with braces, so a title is stored as
    ``\\$21\\textasciicircum\\textbraceleft st\\textbraceright\\$ {{Century ...}}``.
    :func:`_sanitize_filename_stem` strips backslashes and braces, which left
    each command's *name* behind as a word — a real 2026-09-01 repair filed 22
    PDFs as ``... textasciicircum textbraceleft st textbraceright ...``.

    `pylatexenc` does the decoding; it already ships with pzi as a required
    dependency of `bibtexparser`, so this adds nothing to an install.

    **Content is never traded for prettiness.** The decoder renders known math
    well but silently drops a macro it does not know inside math mode. When that
    costs a macro's text (see :func:`_lost_braced_content`), the value is
    decoded again with those macros unwrapped to their content first — less
    pretty than a rendered symbol, but complete.
    """
    if "\\" not in value and "{" not in value and "%" not in value:
        return value  # the overwhelming majority: nothing to decode
    escaped = _BARE_PERCENT.sub(r"\\%", value)
    try:
        decoded = _LATEX_TEXT.latex_to_text(escaped)
        if _lost_braced_content(escaped, decoded):
            decoded = _LATEX_TEXT.latex_to_text(_unwrapped(escaped))
    except Exception:  # pragma: no cover - defensive; a name is not worth raising
        return value
    return decoded.strip() or value


def _sanitize_filename_stem(value: str) -> str:
    cleaned = _ascii(_latex_to_text(value))
    cleaned = _FILENAME_FORBIDDEN.sub(" ", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip().strip(".")
    # Truncate by encoded byte length to avoid exceeding filesystem limits
    encoded = cleaned.encode("utf-8")
    if len(encoded) > 240:
        # Decode back, dropping incomplete multi-byte sequences at the boundary
        cleaned = encoded[:240].decode("utf-8", errors="ignore")
    return cleaned


#: Letters NFKD cannot decompose, because they have no combining form — a
#: stroke or a ligature is part of the letter. `encode("ascii", "ignore")`
#: therefore *deletes* them, which is how `Weiß` became `Wei`, `Søndergaard`
#: became `Sndergaard` and `Łukasz` became `ukasz`: a name silently missing a
#: letter, in the key and in the filename.
#:
#: Deliberately *not* `similarity._TRANSLITERATIONS`, which maps `ü`→`ue` for
#: author matching. Better BibTeX folds `ü` to `u`, and reproducing a BBT key is
#: the whole point of this dialect — sharing that table would have swapped one
#: parity break for another. The two tables differ because the two jobs do.
_CITEKEY_TRANSLITERATIONS = {
    ord("ß"): "ss", ord("æ"): "ae", ord("Æ"): "Ae", ord("œ"): "oe",
    ord("Œ"): "Oe", ord("ø"): "o", ord("Ø"): "O", ord("ł"): "l",
    ord("Ł"): "L", ord("đ"): "d", ord("Đ"): "D", ord("ð"): "d",
    ord("Ð"): "D", ord("þ"): "th", ord("Þ"): "Th", ord("ħ"): "h",
    ord("Ħ"): "H", ord("ı"): "i", ord("İ"): "I",
}


def _ascii(value: str) -> str:
    """Fold to ASCII the way Better BibTeX does, rather than deleting."""
    folded = value.translate(_CITEKEY_TRANSLITERATIONS)
    return unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode("ascii")
