"""The public Python API, pinned — item 426.

Decision 12 made `import pzi` a supported surface rather than an accident, so
its shape is now a compatibility promise: after 1.0 a renamed parameter or a
changed default is a major version bump. This is the mechanism that makes that
promise mean something.

It is also the only surface in the 1.0 freeze whose consumers are not this
repository — everything else (CLI, config, HTTP routes) is used by the user's
own machine. Worth remembering when this file's snapshot next fails.
"""

from __future__ import annotations

import inspect
import os
import re
import warnings
from pathlib import Path
from types import UnionType
from typing import (
    Any,
    NotRequired,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)
from unittest.mock import patch

import pytest

import pzi
from pzi.errors import PziError

SNAPSHOT = Path(__file__).parent / "fixtures" / "public_api.txt"
UPDATE_ENV = "PZI_UPDATE_PUBLIC_API"

ONE_ENTRY = """@article{smith2020,
  title = {A Title},
  author = {Smith, Jane},
  year = {2020},
}
"""


def _library(tmp_path: Path) -> str:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(ONE_ENTRY, encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n',
        encoding="utf-8",
    )
    return str(config_path)


#: `package_version`'s `lookup_version` default is a function object, so its
#: repr carries a memory address that differs on every interpreter run. Scrubbed
#: rather than excluded: *that the default is a function* is part of the frozen
#: signature, while *where it happened to be allocated* is not.
_MEMORY_ADDRESS = re.compile(r" at 0x[0-9a-f]+>")


def _render_type(name: str, obj: type) -> str:
    """A TypedDict rendered as its keys, not as its constructor.

    `inspect.signature` on a TypedDict class gives `(*args, **kwargs)`, which
    pins nothing — and pinning nothing is exactly the complaint that put these
    types here (`-> dict[str, Any]` stayed green through any key rename). What a
    caller depends on is the key set, each key's type, and which keys are
    guaranteed present, so that is what the snapshot records.
    """
    keys = [
        f"    {key}: {_annotation_text(annotation)}"
        for key, annotation in sorted(obj.__annotations__.items())
    ]
    return "\n".join([f"pzi.{name} {{", *keys, "}"])


def _annotation_text(annotation: object) -> str:
    """The annotation as written.

    `from __future__ import annotations` is on in every module here, so
    TypedDict stores each one as a `ForwardRef`, whose repr carries the defining
    module. Recording that would make moving a type between modules a snapshot
    failure, which is not what this pins — and `NotRequired[...]` is already
    visible in the text, so optionality needs no separate marker.
    """
    forward_arg = getattr(annotation, "__forward_arg__", None)
    return forward_arg if isinstance(forward_arg, str) else str(annotation)


def _render_surface() -> str:
    lines = []
    for name in sorted(pzi.__all__):
        obj = getattr(pzi, name)
        # `is_typeddict`, not `isinstance(obj, type)`: `PziError` is also a
        # class, and the looser check rendered it as its (empty) annotations —
        # silently dropping the constructor signature this file exists to pin.
        if is_typeddict(obj):
            lines.append(_render_type(name, obj))
            continue
        if not callable(obj):
            # The *type*, never the value: freezing `__version__`'s contents
            # would make every release a snapshot failure.
            lines.append(f"pzi.{name}: {type(obj).__name__}")
            continue
        signature = str(inspect.signature(obj))
        lines.append(f"pzi.{name}{_MEMORY_ADDRESS.sub('>', signature)}")
    return "\n".join(lines) + "\n"


def test_the_public_api_matches_its_snapshot() -> None:
    """Fails on any change to `__all__` or to a public signature.

    If this failed because of a change you meant to make, it is a change to a
    **frozen public API**: after 1.0 that is a major version bump, not a patch.
    Record it in `CHANGELOG.md`, then regenerate:

        PZI_UPDATE_PUBLIC_API=1 pytest tests/test_public_api.py

    Read the diff first — a renamed keyword argument breaks every caller
    silently at import time, not at review time.
    """
    current = _render_surface()
    if os.environ.get(UPDATE_ENV) == "1":
        SNAPSHOT.write_text(current, encoding="utf-8")
        return
    assert SNAPSHOT.exists(), (
        f"{SNAPSHOT} is missing — regenerate with {UPDATE_ENV}=1 pytest {__file__}"
    )
    assert current == SNAPSHOT.read_text(encoding="utf-8"), (
        "the public API changed. `import pzi` is a frozen surface at 1.0.\n"
        "If the change is intended, record it in CHANGELOG.md and regenerate:\n"
        f"    {UPDATE_ENV}=1 pytest {__file__}\n"
    )


def test_every_public_name_is_importable_and_documented() -> None:
    """`__all__` is the promise; a name in it that does not resolve is a lie."""
    for name in pzi.__all__:
        obj = getattr(pzi, name, None)
        assert obj is not None, f"pzi.{name} is exported but does not exist"
        assert obj.__doc__, f"pzi.{name} is public but undocumented"


def test_py_typed_still_ships() -> None:
    """The annotations are only useful if a caller's type checker sees them.

    `py.typed` is a zero-byte marker, which makes it exactly the kind of file a
    packaging change drops without anyone noticing — and its absence is silent:
    the API keeps working, it just stops being typed for everyone downstream.
    """
    marker = Path(pzi.__file__).parent / "py.typed"
    assert marker.exists(), "py.typed is gone — the public API is no longer typed"


# --- Behaviour: the facade returns data, and raises rather than reporting -----


def test_the_read_only_functions_return_data_not_envelopes(tmp_path: Path) -> None:
    """Exercised through `import pzi`, not through the services underneath.

    A facade tested against its own internals proves only that the internals
    agree with themselves; what is being promised here is the import path.
    """
    config = _library(tmp_path)

    matches = pzi.search(query="Title", config_path=config)
    assert [m["citekey"] for m in matches] == ["smith2020"]

    listed = pzi.entries(config_path=config)
    assert [e["citekey"] for e in listed] == ["smith2020"]

    assert "@article{smith2020" in pzi.export(config_path=config)
    assert '"citekey"' in pzi.export("json", config_path=config)

    report = pzi.dedupe(config_path=config)
    assert report["status"] == "ok"


@pytest.mark.parametrize(
    ("call", "expected_code"),
    [
        (lambda config: pzi.search(query="x", config_path=config, library="nope"), 5),
        (lambda config: pzi.export("yaml", config_path=config), 2),
        (lambda config: pzi.search(config_path=config), 2),
    ],
)
def test_failure_raises_with_the_exit_code_the_cli_would_have_used(
    call, expected_code: int, tmp_path: Path
) -> None:
    """Silence on failure is the wrong default for a library.

    The services return `{"status": "error"}` because the CLI and HTTP API both
    render it; a caller who forgets to check that gets no data and no
    complaint. The facade raises instead, carrying the same classification a
    shell script branching on `$?` would see.
    """
    config = _library(tmp_path)
    with pytest.raises(PziError) as excinfo:
        call(config)
    assert excinfo.value.code == expected_code


def test_no_public_failure_names_a_cli_flag(tmp_path: Path) -> None:
    """A library caller cannot type a flag, so no message may name one.

    Two separate sources used to. `search_bib` words its own precondition as
    "provide at least one of --query, --author, --year, --tag", which the facade
    restates itself; and every unresolved-library failure said `--target` until
    `config._unresolved_target_error` was made flag-neutral. Both are checked
    here, because fixing one and not the other is what happened the first time.
    """
    config = _library(tmp_path)

    with pytest.raises(PziError) as no_filter:
        pzi.search(config_path=config)
    assert "--" not in str(no_filter.value), str(no_filter.value)

    for call in (
        lambda: pzi.entries(config_path=config, library="nope"),
        lambda: pzi.search(query="x", config_path=config, library="nope"),
        lambda: pzi.export(config_path=config, library="nope"),
        lambda: pzi.get("smith2020", config_path=config, library="nope"),
    ):
        with pytest.raises(PziError) as unresolved:
            call()
        assert "--target" not in str(unresolved.value), str(unresolved.value)


# --- The declared types match what the functions actually return -------------


def _required_keys(declared: type) -> set[str]:
    """The keys a value of *declared* must carry.

    Read off the annotation *text*, not `__required_keys__`. Every module here
    has `from __future__ import annotations`, so TypedDict never sees the real
    `NotRequired[...]` object at class creation and puts every key in
    `__required_keys__` — `TagChangeResult.__optional_keys__` is empty despite
    declaring one. Trusting it made this check demand keys that exist only on a
    failure path.
    """
    return {
        key
        for key, annotation in declared.__annotations__.items()
        if not _annotation_text(annotation).startswith("NotRequired[")
    }


def _type_complaints(value: object, hint: object, where: str) -> list[str]:
    """Complaints about *value* against one resolved annotation.

    `get_type_hints(..., include_extras=True)` rather than the annotation text
    `_required_keys` reads: it resolves the `ForwardRef`s that
    `from __future__ import annotations` leaves behind, so a nested TypedDict
    arrives as the class itself and this can recurse into it. The text form is
    still what the snapshot renders, because that must stay independent of
    which module a type lives in.
    """
    if get_origin(hint) is NotRequired:
        (inner,) = get_args(hint)
        return _type_complaints(value, inner, where)

    origin = get_origin(hint)
    if origin in (Union, UnionType):
        if any(not _type_complaints(value, arm, where) for arm in get_args(hint)):
            return []
        return [f"{where}: expected {hint}, got {type(value).__name__}"]
    if origin is list:
        if not isinstance(value, list):
            return [f"{where}: expected a list, got {type(value).__name__}"]
        (item_hint,) = get_args(hint)
        return [
            complaint
            for index, item in enumerate(value)
            for complaint in _type_complaints(item, item_hint, f"{where}[{index}]")
        ]
    if origin is dict:
        if not isinstance(value, dict):
            return [f"{where}: expected a dict, got {type(value).__name__}"]
        _key_hint, value_hint = get_args(hint)
        return [
            complaint
            for key, item in sorted(value.items())
            for complaint in _type_complaints(item, value_hint, f"{where}[{key!r}]")
        ]

    if hint is Any:
        return []
    if hint is type(None):
        return (
            []
            if value is None
            else [f"{where}: expected None, got {type(value).__name__}"]
        )
    if is_typeddict(hint):
        if not isinstance(value, dict):
            return [f"{where}: expected a dict, got {type(value).__name__}"]
        return [f"{where}.{c}" for c in _conforms(value, hint)]
    if isinstance(hint, type):
        # `bool` is a subclass of `int`, so a stray `True` in an `int` field
        # would pass a bare isinstance. That is exactly the confusion worth
        # catching: `counts` is a tally, not a set of flags.
        if hint is int and isinstance(value, bool):
            return [f"{where}: expected int, got bool"]
        if not isinstance(value, hint):
            return [f"{where}: expected {hint.__name__}, got {type(value).__name__}"]
    return []


def _conforms(value: object, declared: type) -> list[str]:
    """Complaints about *value* against the TypedDict *declared*, if any.

    Keys **and** types. Comparing key sets alone was the whole check here for
    one release cycle, which meant the name of this function, the message it
    raises and the snapshot renderer's docstring all claimed something it did
    not do: retyping `EntrySummary.year` from `int | None` to `str` left it
    green.
    """
    assert isinstance(value, dict), f"expected a dict, got {type(value).__name__}"
    keys = set(value)
    known = set(declared.__annotations__)
    complaints = [f"undeclared key {key!r}" for key in sorted(keys - known)]
    complaints += [
        f"missing required key {key!r}"
        for key in sorted(_required_keys(declared) - keys)
    ]
    hints = get_type_hints(declared, include_extras=True)
    for key in sorted(keys & known):
        complaints += _type_complaints(value[key], hints[key], key)
    return complaints


def test_every_declared_type_matches_a_real_call(tmp_path: Path) -> None:
    """A TypedDict is a claim about a value, so check it against one.

    The annotations alone would not notice a service adding a key, and two of
    the shapes are assembled across modules — `AddResult` in `add_planning` and
    `capture_local_pdf`, `EntryRecord` in `bibtex` and read back through
    `bib_service`. This is what makes the types honest rather than decorative,
    and it is the answer to item 428: a key rename now fails something.

    Read-only and local-write calls only. `add`, `check` and `promote` reach the
    network, and `AddResult`'s two builders are statically annotated instead.
    `update` reaches the network too, but only for an entry with a gap to fill,
    so it is checked last against the library the writers below have emptied —
    zero entries, zero requests, and `UpdateBibResult` is a real one. Without
    that it was simply absent, and `UpdateBibResult` was checked against
    nothing while this docstring named three exclusions and meant four.
    """
    bib_path = tmp_path / "ml.bib"
    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "smith2020.pdf").write_bytes(b"%PDF-1.4\n")
    bib_path.write_text(
        "@article{smith2020,\n  title = {A Title},\n  author = {Smith, Jane},\n"
        "  year = {2020},\n  doi = {10.1000/dup},\n  keywords = {ml},\n"
        f"  file = {{{papers / 'smith2020.pdf'}}},\n}}\n\n"
        "@article{smith2020b,\n  title = {A Title},\n  author = {Smith, Jane},\n"
        "  year = {2020},\n  doi = {10.1000/dup},\n  volume = {12},\n}\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\npapers_dir = "{papers}"\n'
        "default = true\n",
        encoding="utf-8",
    )
    config = str(config_path)

    # Ordered: everything read-only first, then the writers, so one call's
    # mutation cannot make a later assertion vacuous.
    cases: list[tuple[str, object, type]] = [
        ("search", pzi.search(query="Title", config_path=config)[0], pzi.SearchMatch),
        ("entries", pzi.entries(config_path=config)[0], pzi.EntrySummary),
        ("get", pzi.get("smith2020", config_path=config), pzi.EntryRecord),
        ("list_bibs", pzi.list_bibs(config_path=config)[0], pzi.BibInfo),
        ("dedupe", pzi.dedupe(config_path=config), pzi.DedupeResult),
        (
            "merge (preview)",
            pzi.merge("smith2020b", "smith2020", config_path=config),
            pzi.MergeResult,
        ),
        (
            "add_tags",
            pzi.add_tags("smith2020", ["nlp"], config_path=config),
            pzi.TagChangeResult,
        ),
        (
            "remove_tags",
            pzi.remove_tags("smith2020", ["nlp"], config_path=config),
            pzi.TagChangeResult,
        ),
        (
            "delete (preview)",
            pzi.delete("smith2020b", dry_run=True, config_path=config),
            pzi.DeleteEntryResult,
        ),
        (
            "merge (write)",
            pzi.merge("smith2020b", "smith2020", dry_run=False, config_path=config),
            pzi.MergeResult,
        ),
        (
            "delete (write)",
            pzi.delete("smith2020", config_path=config),
            pzi.DeleteEntryResult,
        ),
    ]
    # These two run last, on the library `delete` above emptied: an empty tag
    # list is still a valid `TagListResult`, and an `update` with no entry to
    # fill is a real `UpdateBibResult` that reaches no provider.
    cases.append(
        ("list_tags", pzi.list_tags(config_path=config), pzi.TagListResult)
    )
    cases.append(
        ("update", pzi.update(config_path=config), pzi.UpdateBibResult)
    )

    failures = [
        f"{label}: {'; '.join(complaints)}"
        for label, value, declared in cases
        if (complaints := _conforms(value, declared))
    ]
    assert not failures, "declared types disagree with real calls:\n" + "\n".join(
        failures
    )


# --- The six functions added for item 427 ------------------------------------


def test_get_returns_the_record_including_the_pdf_path(tmp_path: Path) -> None:
    """The gap item 427 named: `entries` carries `has_pdf` and no path.

    A script that wants to open, hash or move a paper's PDF had no supported way
    to find it — the whole record was reachable only through `pzi.bib_service`,
    which the README declares internal.
    """
    bib_path = tmp_path / "ml.bib"
    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "smith2020.pdf").write_bytes(b"%PDF-1.4\n")
    bib_path.write_text(
        "@article{smith2020,\n"
        "  title = {A Title},\n"
        "  author = {Smith, Jane},\n"
        "  year = {2020},\n"
        "  keywords = {ml},\n"
        f"  file = {{{papers / 'smith2020.pdf'}}},\n"
        "}\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\npapers_dir = "{papers}"\n'
        "default = true\n",
        encoding="utf-8",
    )

    record = pzi.get("smith2020", config_path=str(config_path))

    assert record["citekey"] == "smith2020"
    assert record["local_pdf_path"] == str(papers / "smith2020.pdf")
    assert record["tags"] == ["ml"]


def test_get_raises_not_found_rather_than_returning_none(tmp_path: Path) -> None:
    """Decision 24. Failure raises here, as it does everywhere else in the API.

    `dict | None` would make this the one function where forgetting to check
    surfaces later as `None['title']`, at a line that has nothing to do with the
    lookup.
    """
    with pytest.raises(PziError) as excinfo:
        pzi.get("nosuchkey", config_path=_library(tmp_path))
    assert excinfo.value.code == 3


def test_list_bibs_names_what_library_accepts(tmp_path: Path) -> None:
    """Every other function takes `library=` by name.

    Without this there was no supported way to learn those names: the config
    file is not part of the API, and `pzi.bib_service` is internal.
    """
    listed = pzi.list_bibs(config_path=_library(tmp_path))

    assert [bib["name"] for bib in listed] == ["ml"]
    assert listed[0]["default"] is True
    # The name it reports is a name the other functions accept.
    assert pzi.entries(config_path=_library(tmp_path), library="ml")


def test_tags_round_trip_through_the_api(tmp_path: Path) -> None:
    config = _library(tmp_path)

    added = pzi.add_tags("smith2020", ["nlp"], config_path=config)
    assert added["tags"] == ["nlp"]
    assert added["changed"] is True

    # Adding a tag the entry already has is a no-op, not a failure.
    again = pzi.add_tags("smith2020", ["nlp"], config_path=config)
    assert again["changed"] is False

    removed = pzi.remove_tags("smith2020", ["nlp"], config_path=config)
    assert removed["tags"] == []
    assert removed["changed"] is True


def test_delete_writes_by_default_and_reports_its_backup(tmp_path: Path) -> None:
    """Decision 23: `delete` names its target, so it acts.

    `promote` previews because a zero-argument call sweeps the whole library;
    this one deletes exactly the citekey in the call.
    """
    config = _library(tmp_path)

    preview = pzi.delete("smith2020", dry_run=True, config_path=config)
    assert preview["dry_run"] is True
    assert pzi.entries(config_path=config), "a dry run must not delete"

    result = pzi.delete("smith2020", config_path=config)
    assert result["dry_run"] is False
    assert Path(result["backup_path"]).exists()
    assert pzi.entries(config_path=config) == []


def test_merge_previews_by_default_and_makes_dedupe_actionable(tmp_path: Path) -> None:
    """`dedupe()` reported clusters the API could not act on.

    Previewing by default is the `promote` split: the CLI writes because you
    typed the command, and the preview names what the merge costs before it
    happens.
    """
    bib_path = tmp_path / "ml.bib"
    # One DOI, two entries: an *exact* duplicate cluster, which is what
    # `dedupe` reports and this merge then resolves.
    bib_path.write_text(
        "@article{a2020,\n  title = {A Title},\n  author = {Smith, Jane},\n"
        "  year = {2020},\n  doi = {10.1000/dup},\n}\n\n"
        "@article{b2020,\n  title = {A Title},\n  author = {Smith, Jane},\n"
        "  year = {2020},\n  doi = {10.1000/dup},\n  volume = {12},\n}\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n', encoding="utf-8"
    )
    config = str(config_path)

    assert pzi.dedupe(config_path=config)["total_clusters"] == 1

    preview = pzi.merge("b2020", "a2020", config_path=config)
    assert preview["dry_run"] is True
    assert preview["carried_fields"] == ["volume"]
    assert len(pzi.entries(config_path=config)) == 2, "a preview must not write"

    merged = pzi.merge("b2020", "a2020", dry_run=False, config_path=config)
    assert merged["dropped_citekey"] == "b2020"
    assert [e["citekey"] for e in pzi.entries(config_path=config)] == ["a2020"]


def test_merge_honours_the_configured_pdf_path_style(tmp_path: Path) -> None:
    """`merge_duplicates` takes `file_path_style`, and both other front ends
    read it from the config (`commands/dedupe.py`).

    The facade resolves a *path* for the services that take one, so passing the
    default here instead of the configured value rewrote a relative-path
    library's `file = {...}` to an absolute path — on a merge the user ran for
    an unrelated reason. Reproduced: the survivor's field came back as
    `/tmp/.../papers/b2020.pdf` where the library uses `papers/b2020.pdf`.
    """
    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "b2020.pdf").write_bytes(b"%PDF-1.4\n")
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        "@article{a2020,\n  title = {A Title},\n  author = {Smith, Jane},\n"
        "  year = {2020},\n}\n\n"
        "@article{b2020,\n  title = {A Title},\n  author = {Smith, Jane},\n"
        "  year = {2020},\n  file = {papers/b2020.pdf},\n}\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'pdf_file_path_style = "relative"\n\n'
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\npapers_dir = "{papers}"\n'
        "default = true\n",
        encoding="utf-8",
    )

    pzi.merge("b2020", "a2020", dry_run=False, config_path=str(config_path))

    assert "file = {papers/b2020.pdf}" in bib_path.read_text(encoding="utf-8")


# --- Remediation after the 2026-08-15 API review -----------------------------


def test_a_missing_bib_warns_instead_of_looking_empty(tmp_path: Path) -> None:
    """An empty result and an unreadable library must not look identical.

    Reading a *missing* bib is a warning, not an error, on purpose: a freshly
    `pzi init`-ed config names a bib that does not exist until the first `add`
    (`export_service._missing_bib_errors`). The CLI prints that warning and the
    HTTP envelope carries it — but returning only the items dropped it, so a
    typo'd `path =` or an unmounted share came back as `[]`, indistinguishable
    from a library with nothing in it.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{tmp_path / "missing.bib"}"\ndefault = true\n',
        encoding="utf-8",
    )

    for call in (
        lambda: pzi.entries(config_path=str(config_path)),
        lambda: pzi.search(query="anything", config_path=str(config_path)),
    ):
        with pytest.warns(UserWarning, match="does not exist"):
            assert call() == []


def test_a_bare_string_is_not_a_tag_list(tmp_path: Path) -> None:
    """`add_tags(key, "nlp")` must refuse, not write one tag per character.

    The annotation said `list[str]` and `api.py` said `list(tags)`, which
    iterates a string happily: the call wrote ``keywords = {l, n, p}`` into the
    library and reported ``status: ok, changed: True``. The CLI cannot reach
    this — it splits CSV into a list — and `POST /tags/add` answers 400 for the
    same shape, so the Python API was the one surface that took the mistake and
    committed it to the file.

    Asserts the file too, not just the raise: the failure being prevented is a
    write, and a guard that raised *after* writing would satisfy the raise
    alone.
    """
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(ONE_ENTRY, encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n',
        encoding="utf-8",
    )
    config = str(config_path)
    before = bib_path.read_text(encoding="utf-8")

    for call in (
        lambda: pzi.add_tags("smith2020", "nlp", config_path=config),  # type: ignore[arg-type]
        lambda: pzi.remove_tags("smith2020", "nlp", config_path=config),  # type: ignore[arg-type]
        lambda: pzi.add("10.1000/x", tags="nlp", config_path=config),  # type: ignore[arg-type]
        lambda: pzi.add_tags("smith2020", ["ok", 7], config_path=config),  # type: ignore[list-item]
    ):
        with pytest.raises(PziError) as excinfo:
            call()
        assert excinfo.value.code == 2, str(excinfo.value)
        assert "list of strings" in str(excinfo.value)

    assert bib_path.read_text(encoding="utf-8") == before


def test_each_facade_call_warns_for_itself_not_once_per_process(
    tmp_path: Path,
) -> None:
    """Three reads of a missing bib are three warnings, at the caller's line.

    `_emit_warnings` counted `stacklevel` to `_public`'s wrapper rather than
    past it, so every warning this module raises reported the same origin — one
    line inside `api.py`. Python's default filter shows a warning once per
    (message, category, module, lineno), so the second and third call were
    dropped: three calls, one warning, pointing at pzi's own source.

    `simplefilter("default")` rather than `pytest.warns`, which installs
    `always` — that is why the test above, covering the same feature, could not
    see this. The dedup *is* the bug.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{tmp_path / "missing.bib"}"\ndefault = true\n',
        encoding="utf-8",
    )
    config = str(config_path)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("default")
        pzi.entries(config_path=config)
        pzi.search(query="anything", config_path=config)
        pzi.list_tags(config_path=config)

    assert len(caught) == 3, [str(w.message) for w in caught]
    assert {Path(w.filename).name for w in caught} == {Path(__file__).name}, [
        f"{w.filename}:{w.lineno}" for w in caught
    ]


def test_an_unreadable_bib_raises_pzi_error_not_oserror(tmp_path: Path) -> None:
    """`except PziError` has to hold, or the documented idiom is a lie.

    `_unwrap` only translates the services' own `status == "error"`; everything
    raised below it — the whole I/O surface — used to escape as a bare
    `OSError`, on failures the CLI reports as exit 5.
    """
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(ONE_ENTRY, encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n', encoding="utf-8"
    )
    bib_path.chmod(0o000)
    try:
        with pytest.raises(PziError) as excinfo:
            pzi.entries(config_path=str(config_path))
    finally:
        bib_path.chmod(0o600)
    assert excinfo.value.code == 5


def test_promote_previews_by_default_like_every_other_surface() -> None:
    """A zero-argument call must not rewrite the library over the network.

    `promote_bib` and `POST /promote` both default to previewing; the facade
    took the CLI's writing default, where the user typed the command. On a
    22k-entry library that is thousands of provider lookups and a rewrite before
    the caller sees anything.
    """
    assert inspect.signature(pzi.promote).parameters["dry_run"].default is True


def test_the_environment_config_is_honoured_like_the_cli(tmp_path: Path) -> None:
    """`pzi search` and `pzi.search()` must read the same file.

    `PZI_CONFIG` is documented CLI precedence (`cli.py`); the facade ignored it
    and went to the XDG default — a user's real library rather than the one they
    pointed at.
    """
    bib_path = tmp_path / "env.bib"
    bib_path.write_text(ONE_ENTRY, encoding="utf-8")
    config_path = tmp_path / "env.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "env"\npath = "{bib_path}"\ndefault = true\n', encoding="utf-8"
    )
    with patch.dict(os.environ, {"PZI_CONFIG": str(config_path)}):
        assert [e["citekey"] for e in pzi.entries()] == ["smith2020"]


def test_entries_validates_what_the_other_front_ends_validate(tmp_path: Path) -> None:
    """`limit` and `sort` are frozen parameters; validating them later breaks callers.

    HTTP and the CLI both clamp `limit` to [1, 500]; `bib_service` falls back to
    citekey for an unknown `sort` *silently*, so a typo returned plausible data
    in the wrong order.
    """
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(ONE_ENTRY, encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n', encoding="utf-8"
    )

    with pytest.raises(PziError) as excinfo:
        pzi.entries(sort="titel", config_path=str(config_path))
    assert excinfo.value.code == 2

    # Clamped, not rejected — matching the other two front ends. `limit=-1`
    # used to reach a negative slice bound and return one item.
    assert pzi.entries(limit=-1, config_path=str(config_path)) == pzi.entries(
        limit=1, config_path=str(config_path)
    )


def test_no_public_name_freezes_a_test_seam() -> None:
    """`api._home`'s own argument, applied to `__all__`.

    `cli_version_text` and `package_version` carried `version_text` and
    `lookup_version` — parameters that exist only so tests can stub — into the
    frozen surface.
    """
    for name in pzi.__all__:
        obj = getattr(pzi, name)
        if not callable(obj) or isinstance(obj, type):
            continue
        for parameter in inspect.signature(obj).parameters.values():
            if parameter.default is inspect.Parameter.empty:
                continue  # no default at all — `inspect._empty` is itself a class
            assert not callable(parameter.default), (
                f"pzi.{name} freezes an injected callable ({parameter.name}) as "
                "public API"
            )
