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

import ast
import inspect
import os
import re
import warnings
from pathlib import Path
from types import UnionType
from typing import (
    Any,
    Literal,
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
import pzi.api as pzi_api
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
    Say so in the commit subject (`CHANGELOG.md` is closed — `git log` is the
    record), then regenerate:

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
        "If the change is intended, say so in the commit subject and regenerate:\n"
        f"    {UPDATE_ENV}=1 pytest {__file__}\n"
    )


def test_dir_advertises_the_public_surface_and_nothing_else() -> None:
    """`dir(pzi)` is the docstring's claim, made mechanical.

    The module docstring says "``__all__`` is the whole of the public surface",
    and `__dir__` returned it unioned with `globals()` — so tab-completion
    offered `logging`, `TYPE_CHECKING`, `_PUBLIC_API` and six more internals as
    though they were part of it.
    """
    assert dir(pzi) == sorted(pzi.__all__)


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

    page = pzi.entries(config_path=config)
    assert [e["citekey"] for e in page["items"]] == ["smith2020"]
    assert page["total"] == 1

    assert "@article{smith2020" in pzi.export(config_path=config)
    assert '"citekey"' in pzi.export("json", config_path=config)

    report = pzi.dedupe(config_path=config)
    assert report["total_entries"] == 1
    # And not the envelope: `status` and `errors` are the CLI's business.
    assert "status" not in report and "errors" not in report


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

    All **three** reachable branches of `_unresolved_target_error`, not the one
    this used to reach: none marked default, a `.bib` path that does not exist,
    and an unknown name. Only the last was exercised, so `--target` could come
    back in the other two without failing anything. They are all flag-neutral
    today; this is the guard, not a fix.

    A config with no libraries is not a fourth branch — the loader refuses an
    empty `bibs` before resolution runs, which is checked here too, since the
    message a caller sees is what matters and not which function wrote it.
    """
    config = _library(tmp_path)

    with pytest.raises(PziError) as no_filter:
        pzi.search(config_path=config)
    assert "--" not in str(no_filter.value), str(no_filter.value)

    # A config the loader refuses outright, and one that resolves but has no
    # default — different functions produce these, same requirement.
    no_bibs = tmp_path / "no-bibs.toml"
    no_bibs.write_text("bibs = []\n", encoding="utf-8")
    no_default = tmp_path / "no-default.toml"
    no_default.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{tmp_path / "ml.bib"}"\n'
        f'\n[[bibs]]\nname = "other"\npath = "{tmp_path / "other.bib"}"\n',
        encoding="utf-8",
    )
    for label, other_config in (("no libraries", no_bibs), ("no default", no_default)):
        with pytest.raises(PziError) as unresolved:
            pzi.entries(config_path=str(other_config))
        assert "--" not in str(unresolved.value), f"{label}: {unresolved.value}"

    # A `.bib` path that does not exist, which takes its own return.
    with pytest.raises(PziError) as missing_path:
        pzi.entries(config_path=config, library=str(tmp_path / "gone.bib"))
    assert "--" not in str(missing_path.value), str(missing_path.value)

    # An unknown library name.
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


def _report_returning_annotations() -> set[str]:
    """Return-type names of every `api.py` function that returns via `_report`.

    Read from the source rather than from runtime introspection: the question
    is which functions *are written* to go through the derivation regime, and a
    decorator or a re-export could hide that from `dir()`.
    """
    import ast
    from pathlib import Path as _Path

    import pzi.api

    tree = ast.parse(_Path(pzi.api.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name == "_report":
            continue
        goes_through_report = any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "_report"
            for call in ast.walk(node)
        )
        if goes_through_report and node.returns is not None:
            names.add(ast.unparse(node.returns))
    return names


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
    if origin is Literal:
        # Without this the function fell through every branch and returned no
        # complaint, so a `Literal` field accepted anything at all. Dormant
        # while no public type uses one, and silently unchecked from the moment
        # one does — which is the failure mode worth closing early, because the
        # test that should have caught it would still be green.
        allowed = get_args(hint)
        if value in allowed:
            return []
        return [f"{where}: expected one of {allowed!r}, got {value!r}"]
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


def test_each_public_report_is_its_service_type_minus_the_envelope() -> None:
    """The nine pairs, derived rather than transcribed.

    Ten of the fifteen public functions returned the *transport* envelope —
    `status` always `"ok"` because a failure raises, `errors` always empty for
    the same reason, and `reason` never set on a success by any service. Three
    keys that cannot vary, frozen into nine types that 1.0 would freeze
    further, against `api.py`'s own stated convention that the facade returns
    the answer.

    The service types keep them, because the CLI reads `status` to pick an exit
    code and the HTTP API to pick a status line. So each has a public twin, and
    this is what stops the twin drifting: a service growing a key that the
    report does not gain is a failure here, not a silently narrower public type
    that no snapshot notices — the snapshot records whatever it is given.
    """
    from pzi.api import _ENVELOPE_KEYS, _REPORT_TYPES, _REPORTS_KEEPING_ERRORS

    # Derived from the source, not hand-counted. `len(_REPORT_TYPES) == 9` was
    # the old guard, and a count does not conscript anything: a tenth
    # envelope-returning function could be added with its type left out of the
    # pairing, and the number would simply be updated to 10. Set equality names
    # the offender in both directions — a type that skipped the derivation
    # regime, and a pair left behind by a function that no longer exists.
    #
    # Ten functions, nine types: `add_tags` and `remove_tags` share
    # `TagChangeReport`, which is why the count and the call sites never agreed.
    declared = {public.__name__ for public, _service in _REPORT_TYPES}
    returned = _report_returning_annotations()
    assert declared == returned, (
        "every function returning through `_report` must have its return type "
        "paired in `_REPORT_TYPES`:\n"
        f"  returned but unpaired: {sorted(returned - declared)}\n"
        f"  paired but returned by nothing: {sorted(declared - returned)}"
    )
    # Decision 40's exception, as an exact list: the three network sweeps where
    # ok-with-errors is a real outcome (a partial failure the CLI exits 4 on).
    # Exact, not a subset check — a fourth member is a decision, not a drift.
    assert _REPORTS_KEEPING_ERRORS == {
        "CheckReport", "PromoteReport", "UpdateBibReport"
    }

    for public, service in _REPORT_TYPES:
        stripped = (
            _ENVELOPE_KEYS - {"errors"}
            if public.__name__ in _REPORTS_KEEPING_ERRORS
            else _ENVELOPE_KEYS
        )
        expected = set(service.__annotations__) - stripped
        actual = set(public.__annotations__)
        assert actual == expected, (
            f"{public.__name__} should be {service.__name__} minus "
            f"{sorted(stripped)}:\n"
            f"  missing from the public type: {sorted(expected - actual)}\n"
            f"  not in the service type: {sorted(actual - expected)}"
        )
        # Same annotation, not merely the same name.
        for key in sorted(actual):
            assert (
                _annotation_text(public.__annotations__[key])
                == _annotation_text(service.__annotations__[key])
            ), f"{public.__name__}.{key} disagrees with {service.__name__}.{key}"

    assert {public for public, _service in _REPORT_TYPES} <= {
        getattr(pzi, name) for name in pzi.__all__ if name[0].isupper()
    }, "a report type that is not exported is not public"


def test_no_public_return_carries_the_transport_envelope(tmp_path: Path) -> None:
    """The rule the types describe, checked against real values.

    The derivation above is about declarations. This is the same claim made of
    what the functions actually hand back, because `_report` could be forgotten
    at one call site and the type would still read correctly — which is
    exactly what the pre-0.2.0 review proved by mutation: `check()` and
    `promote()` returned the raw envelope with the whole suite green, because
    this list stopped at the functions that need no network. It covers all
    nine `_report` sites now; the last three run against the library `delete`
    emptied, where `check_bib` audits nothing and `promote`/`update` sweep
    nothing, so none reaches a provider.

    Decision 40's exception is asserted, not just allowed: the three sweep
    reports must *carry* `errors` (empty on a clean run), because a caller
    branching on `report["errors"]` after a real sweep needs the key to exist.
    """
    from pzi.api import _ENVELOPE_KEYS

    config = _library(tmp_path)
    returned: list[tuple[str, dict]] = [
        ("dedupe", pzi.dedupe(config_path=config)),
        ("add_tags", pzi.add_tags("smith2020", ["nlp"], config_path=config)),
        ("remove_tags", pzi.remove_tags("smith2020", ["nlp"], config_path=config)),
        ("list_tags", pzi.list_tags(config_path=config)),
        ("delete", pzi.delete("smith2020", dry_run=False, config_path=config)),
        ("check", pzi.check(config_path=config)),
        ("promote", pzi.promote(config_path=config)),
        ("update", pzi.update(config_path=config)),
    ]
    sweeps = {"check", "promote", "update"}
    for label, value in returned:
        if label in sweeps:
            leaked = sorted(set(value) & (_ENVELOPE_KEYS - {"errors"}))
            assert value.get("errors") == [], (
                f"{label}: a clean run's errors must be present and empty"
            )
        else:
            leaked = sorted(set(value) & _ENVELOPE_KEYS)
        assert not leaked, f"{label}: {value} still carries {leaked}"


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
        ("entries (page)", pzi.entries(config_path=config), pzi.EntryPage),
        ("entries", pzi.entries(config_path=config)["items"][0], pzi.EntrySummary),
        ("get", pzi.get("smith2020", config_path=config), pzi.EntryRecord),
        ("list_bibs", pzi.list_bibs(config_path=config)[0], pzi.BibInfo),
        ("dedupe", pzi.dedupe(config_path=config), pzi.DedupeReport),
        (
            "merge (preview)",
            pzi.merge("smith2020b", "smith2020", config_path=config),
            pzi.MergeReport,
        ),
        (
            "add_tags",
            pzi.add_tags("smith2020", ["nlp"], config_path=config),
            pzi.TagChangeReport,
        ),
        (
            "remove_tags",
            pzi.remove_tags("smith2020", ["nlp"], config_path=config),
            pzi.TagChangeReport,
        ),
        (
            "delete (preview)",
            pzi.delete("smith2020b", dry_run=True, config_path=config),
            pzi.DeleteEntryReport,
        ),
        (
            "merge (write)",
            pzi.merge("smith2020b", "smith2020", dry_run=False, config_path=config),
            pzi.MergeReport,
        ),
        (
            "delete (write)",
            pzi.delete("smith2020", dry_run=False, config_path=config),
            pzi.DeleteEntryReport,
        ),
    ]
    # These two run last, on the library `delete` above emptied: an empty tag
    # list is still a valid `TagListResult`, and an `update` with no entry to
    # fill is a real `UpdateBibResult` that reaches no provider.
    cases.append(
        ("list_tags", pzi.list_tags(config_path=config), pzi.TagListReport)
    )
    cases.append(
        ("update", pzi.update(config_path=config), pzi.UpdateBibReport)
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
    assert pzi.entries(config_path=_library(tmp_path), library="ml")["items"]


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


def test_delete_previews_by_default_like_the_other_two_surfaces(
    tmp_path: Path,
) -> None:
    """Decision 34, revising 23: the one destructive call does not act first.

    "A function naming its target acts" gave `delete` a writing default, which
    made the Python API the only surface that removed an entry with no second
    step — the CLI refuses without `--force` and `POST /delete` previews. Three
    surfaces disagreeing about a destructive default is worse than the rule
    being uniform, and this is the call where being wrong is unrecoverable
    without the backup.
    """
    config = _library(tmp_path)

    default = pzi.delete("smith2020", config_path=config)
    assert default["dry_run"] is True
    assert pzi.entries(config_path=config)["items"], "the default must not delete"

    result = pzi.delete("smith2020", dry_run=False, config_path=config)
    assert result["dry_run"] is False
    assert Path(result["backup_path"]).exists()
    assert pzi.entries(config_path=config)["items"] == []


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
    assert pzi.entries(config_path=config)["total"] == 2, "a preview must not write"

    merged = pzi.merge("b2020", "a2020", dry_run=False, config_path=config)
    assert merged["dropped_citekey"] == "b2020"
    assert [e["citekey"] for e in pzi.entries(config_path=config)["items"]] == ["a2020"]


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
        lambda: pzi.entries(config_path=str(config_path))["items"],
        lambda: pzi.search(query="anything", config_path=str(config_path)),
    ):
        with pytest.warns(UserWarning, match="does not exist"):
            assert call() == []


def test_entry_page_reports_the_library_total_not_the_page(tmp_path: Path) -> None:
    """`total` is the whole library; `offset` is echoed. Pinned off-page.

    Every prior assertion on `total` used a library that fit in one page, so
    `total == len(items)` held by construction and `"total": len(items)` — the
    mutation that guts the field the type exists for — survived the whole
    suite. The pre-0.2.0 review proved that; this is the kill. Three entries,
    pages of one, and the docstring's own pagination loop walked to the end.
    """
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        "@article{a1,\n  title = {A},\n}\n\n"
        "@article{b2,\n  title = {B},\n}\n\n"
        "@article{c3,\n  title = {C},\n}\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n',
        encoding="utf-8",
    )
    config = str(config_path)

    middle = pzi.entries(offset=1, limit=1, config_path=config)
    assert middle["total"] == 3, "total is the library, not this page"
    assert middle["offset"] == 1 and middle["limit"] == 1
    assert [e["citekey"] for e in middle["items"]] == ["b2"]

    # The loop the docstring advertises terminates and covers everything once.
    seen: list[str] = []
    page = pzi.entries(limit=1, config_path=config)
    seen += [e["citekey"] for e in page["items"]]
    while page["offset"] + len(page["items"]) < page["total"]:
        page = pzi.entries(
            offset=page["offset"] + len(page["items"]), limit=1, config_path=config
        )
        seen += [e["citekey"] for e in page["items"]]
    assert seen == ["a1", "b2", "c3"]

    # Off the end: an empty page still reports the real total.
    past = pzi.entries(offset=10, limit=1, config_path=config)
    assert (past["items"], past["total"], past["offset"]) == ([], 3, 10)


def test_get_is_a_superset_of_the_summary_entries_returns(tmp_path: Path) -> None:
    """`get()` must not answer less about an entry than `entries()` does.

    `EntryRecord` had no `entry_type` while `EntrySummary` did and
    `export --format json` emitted it, so the "full record" was missing a field
    the *summary* carried, and an entry could not be round-tripped through the
    Python API — nothing in it said `@article` rather than `@inproceedings`.

    The value comes off the parsed entry, not the record:
    `bibtex.bibtex_entry_to_record` deliberately never sets `entry_type`, and
    reading it off the record reported `"unknown"` for everything.
    """
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        "@inproceedings{jones2021,\n  title = {Another},\n"
        "  author = {Jones, Ada},\n  year = {2021},\n}\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n',
        encoding="utf-8",
    )
    config = str(config_path)

    record = pzi.get("jones2021", config_path=config)
    summary = pzi.entries(config_path=config)["items"][0]

    assert record["entry_type"] == "inproceedings"
    # `has_pdf` is the one deliberate difference and `get`'s docstring says so:
    # the summary answers "is there a PDF", the record answers "where", in
    # `local_pdf_path`. Anything else appearing here is a new divergence.
    assert set(summary) - set(record) == {"has_pdf"}, (
        "entries() reports a key get() does not: "
        + repr(set(summary) - set(record) - {"has_pdf"})
    )


def test_two_failures_sharing_an_exit_code_are_told_apart_by_reason(
    tmp_path: Path,
) -> None:
    """Exit code 5 covers config *and* unavailable, so it cannot be the answer.

    `_unwrap` computed the exit code from the service's structured `reason` and
    then threw the reason away, so a caller catching `PziError` got the lossy
    half. `PziError.reason` has existed for this the whole time and the CLI
    reads it (`exc.reason or <coarse map>` — the map is the fallback for
    raisers that did not know).

    The pair below is the point: both are code 5 and only `reason` separates a
    library that is misconfigured from one that is there and unreadable.
    """
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(ONE_ENTRY, encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n',
        encoding="utf-8",
    )
    config = str(config_path)

    with pytest.raises(PziError) as misconfigured:
        pzi.entries(config_path=config, library="nope")

    bib_path.chmod(0o000)
    try:
        with pytest.raises(PziError) as unreadable:
            pzi.entries(config_path=config)
    finally:
        bib_path.chmod(0o600)

    assert misconfigured.value.code == unreadable.value.code == 5
    assert misconfigured.value.reason == "config"
    assert unreadable.value.reason == "unavailable"

    # And the reason a service reported survives the trip, rather than being
    # recomputed from the code it was mapped to.
    with pytest.raises(PziError) as missing:
        pzi.get("nosuchkey", config_path=config)
    assert (missing.value.code, missing.value.reason) == (3, "not_found")


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
        assert [e["citekey"] for e in pzi.entries()["items"]] == ["smith2020"]


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


#: Modules whose `raise PziError` sites predate the reason contract and each
#: need their own classification decision (audit item 526 fixed the four that
#: had an obvious one). Listed by name rather than skipped silently, so the
#: debt is countable and a *new* unclassified raise anywhere else still fails.
_UNCLASSIFIED_RAISERS = frozenset(
    {
        "bib_repository",
        "bib_serialize",
        "bibtex",
        "capture_context",
        "cli_parser",
        "fileio",
        "pdf_download",
    }
)


def test_every_raised_pzi_error_carries_a_reason() -> None:
    """A `PziError` without a `reason` is an unclassifiable failure.

    `http_status.status_for_service_result` says so in its own docstring: an
    unclassified failure takes the 400 fallback, "a bug in that service". The
    exit-code side is just as lossy — `ENVIRONMENT` covers config, unavailable
    *and* conflict — so the raiser is the only place that knows.

    Read from the source rather than by calling, because these raises are the
    failure paths that are hardest to reach: three of the four this test was
    written for are triggered by a mistyped `page_metadata_cmd`.
    """
    src_dir = Path(pzi.__file__).parent
    missing: list[str] = []
    for path in sorted(src_dir.rglob("*.py")):
        if path.stem in _UNCLASSIFIED_RAISERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            func = node.exc.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "PziError":
                continue
            if not any(keyword.arg == "reason" for keyword in node.exc.keywords):
                missing.append(f"{path.name}:{node.lineno}")
    assert not missing, (
        "raise PziError without a reason (the HTTP API cannot classify these, "
        f"so they answer 400): {sorted(missing)}"
    )


def _capture_result(**overrides: object) -> dict[str, object]:
    """An `AddResult` as `capture_to_bib` builds one, envelope included."""
    result: dict[str, object] = {
        "status": "ok",
        "bib_name": "ml",
        "bib_path": "/tmp/ml.bib",
        "action": "insert",
        "citekey": "smith2020",
        "pdf_path": None,
        "changed_fields": ["title"],
        "dry_run": False,
        "message": "added smith2020",
        "warnings": [],
        "errors": [],
    }
    result.update(overrides)
    return result


def test_add_returns_the_capture_minus_the_envelope(tmp_path: Path) -> None:
    """`pzi.add()`'s own `_unwrap` + `_report` pass, on a stubbed capture.

    Stubbed because the real one makes network requests and wants a running
    translation server, which is why this function was the one public entry
    point with no coverage at all: the single test calling it passed
    ``tags="nlp"`` to trip `_tag_list`, and raised before `capture_to_bib` was
    ever reached.
    """
    config = _library(tmp_path)
    with patch("pzi.api.capture_to_bib", return_value=_capture_result()) as capture:
        report = pzi.add("10.1/x", tags=["nlp"], config_path=config, library="ml")

    assert set(report) & {"status", "errors", "reason"} == set(), report
    assert report["citekey"] == "smith2020"
    assert report["action"] == "insert"
    assert report["message"] == "added smith2020"

    # And the arguments reached the seam the CLI and the HTTP API also use.
    capture_input, capture_options = capture.call_args.args
    assert capture_input.value == "10.1/x"
    assert capture_input.record_overrides == {"tags": ["nlp"]}
    assert capture_input.bib_selector == "ml"
    assert (capture_options.dry_run, capture_options.force_new) == (False, False)


def test_add_raises_the_classified_failure_rather_than_returning_it(
    tmp_path: Path,
) -> None:
    """The other half of the same pass: a failed capture is an exception."""
    failed = _capture_result(
        status="error",
        message="no metadata for 10.1/x",
        errors=["no metadata for 10.1/x"],
        reason="not_found",
    )
    with patch("pzi.api.capture_to_bib", return_value=failed):
        with pytest.raises(PziError) as excinfo:
            pzi.add("10.1/x", config_path=_library(tmp_path))

    assert (excinfo.value.code, excinfo.value.reason) == (3, "not_found")
    assert excinfo.value.details == ["no metadata for 10.1/x"]


def test_check_accepts_the_bound_the_cli_has_had_since_item_550(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A whole-library audit is hours, so both front ends need an escape hatch.

    `check_bib` has taken `limit` since the CLI gained `--limit`; only the
    Python wrapper dropped it on the floor, leaving the programmatic surface
    with no way to ask for a smaller run.
    """
    config = _library(tmp_path)
    seen: list[object] = []

    def _fake_check_bib(**kwargs):
        seen.append(kwargs.get("limit"))
        return {"status": "ok", "bib_name": "ml", "items": [], "warnings": [], "errors": []}

    monkeypatch.setattr(pzi_api, "check_bib", _fake_check_bib)

    pzi.check(config_path=config)
    pzi.check(limit=5, config_path=config)

    assert seen == [None, 5]


@pytest.mark.parametrize("limit", [0, -1])
def test_check_rejects_a_limit_below_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    """`limit=0` used to reach `check_bib`, which audits nothing and returns
    `status: "ok"` — a clean bill of health for a run that checked zero
    entries. A negative `limit` meant "unlimited" to `check_bib`, silently
    auditing the whole library. The CLI's `--limit` rejects both at the
    parser (`cli_parser._positive_int`); this front end has no parser of its
    own, so it has to reject them itself.
    """
    config = _library(tmp_path)
    seen: list[object] = []

    def _fake_check_bib(**kwargs):
        seen.append(kwargs.get("limit"))
        return {"status": "ok", "bib_name": "ml", "items": [], "warnings": [], "errors": []}

    monkeypatch.setattr(pzi_api, "check_bib", _fake_check_bib)

    with pytest.raises(PziError) as excinfo:
        pzi.check(limit=limit, config_path=config)

    assert (excinfo.value.code, excinfo.value.reason) == (2, "usage")
    assert not seen, "check_bib must not run once the limit is rejected"


def test_every_read_emits_its_warnings_through_pythons_own_channel(
    tmp_path: Path,
) -> None:
    """Item 508's rule, pinned across all six reads at once.

    The channel had three behaviours: emit-and-drop, emit-and-return, and
    return-without-emitting. The last is the one that bit — `-W error` stopped a
    script reading a missing bib through `list_tags` and let the identical
    condition through `dedupe`. A per-function test would not have shown the
    disagreement, so this asserts the whole surface in one place.
    """
    config = tmp_path / "config.toml"
    missing = tmp_path / "not-created-yet.bib"
    config.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{missing}"\ndefault = true\n', encoding="utf-8"
    )

    reads = {
        "entries": lambda: pzi.entries(config_path=str(config)),
        "search": lambda: pzi.search("anything", config_path=str(config)),
        "list_tags": lambda: pzi.list_tags(config_path=str(config)),
        "dedupe": lambda: pzi.dedupe(config_path=str(config)),
        # Reaches the network only when there are records to check, and a
        # missing bib has none — verified offline under `unshare -rn`.
        "check": lambda: pzi.check(config_path=str(config)),
    }
    # `get` is absent on purpose: it raises on a missing entry rather than
    # returning, so it has no "returned quietly" failure mode to pin.

    silent = []
    for name, call in reads.items():
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            call()
        if not [w for w in caught if issubclass(w.category, UserWarning)]:
            silent.append(name)

    assert silent == [], f"reads that swallowed a missing-bib warning: {silent}"


def test_a_report_shaped_read_both_emits_and_returns_its_warnings(
    tmp_path: Path,
) -> None:
    """The half of the rule that says emitting does not replace returning."""
    config = tmp_path / "config.toml"
    missing = tmp_path / "not-created-yet.bib"
    config.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{missing}"\ndefault = true\n', encoding="utf-8"
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        report = pzi.dedupe(config_path=str(config))

    assert [w for w in caught if issubclass(w.category, UserWarning)]
    assert report["warnings"], "a report-shaped return must carry them too"


def test_type_complaints_checks_a_literal_instead_of_waving_it_through() -> None:
    """Item 506, and the test matters more than the branch.

    No public type uses `Literal` yet, so the gap was dormant: the checker fell
    through every branch and returned no complaint, meaning the first `Literal`
    field to appear would have been validated by a function that always agreed
    with it. A conformance check that cannot fail is the same defect as a
    `# pragma: no cover` claiming coverage it does not have.
    """
    status = Literal["ok", "error"]

    assert _type_complaints("ok", status, "status") == []
    assert _type_complaints("error", status, "status") == []

    complaints = _type_complaints("draft", status, "status")
    assert complaints, "a value outside the Literal must be reported"
    assert "status" in complaints[0]
    assert "draft" in complaints[0]

    # Nested, because that is how one would really arrive — inside a report.
    assert _type_complaints({"status": "draft"}, dict[str, status], "r") != []


def test_the_sweep_item_types_are_checked_against_real_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 505: `UpdatePlanItem`/`CheckItem`/`PromoteItem` had no value to check.

    The conformance table runs the three sweeps against an emptied library for
    hermeticity, so `items == []` and these three shapes were pinned only as
    declarations — a service renaming a key inside an item would not have failed
    anything here.

    The providers are stubbed rather than the services, so the item value is
    built by the real sweep code. Stubbing `check_bib`/`promote_bib` instead
    would check the types against this test's own fixtures, which is the same
    vacuity in a new place. Verified to make no network call.
    """
    import pzi.check_service as check_service
    import pzi.promote_planning as promote_planning
    import pzi.update_service as update_service

    published = {
        "title": "Graph Parsers for Structured Prediction",
        "authors": ["Jane Smith"],
        "year": 2024,
        "venue": "Proceedings of ACL",
        "doi": "10.1000/acl.2024",
    }

    def _search(query: str, *, server_url: str):
        return [{"item_type": "journalArticle", "record": dict(published),
                 "attachments": []}]

    def _by_title(title, *, contact_email=None, errors=None, **_kwargs):
        return dict(published)

    monkeypatch.setattr(update_service, "fetch_search_translations", _search)
    monkeypatch.setattr(promote_planning, "fetch_search_translations", _search)
    # The four title-search providers now come from one shared table
    # (`pzi.provider_cascade.TITLE_SEARCH_PROVIDERS`), consumed by both
    # `check_service` and `promote_planning` so the two cannot drift apart.
    # They are therefore no longer individual attributes of `check_service`;
    # patching the table it reads at call time replaces all four at once.
    monkeypatch.setattr(
        check_service,
        "TITLE_SEARCH_PROVIDERS",
        tuple((name, _by_title) for name, _fn in check_service.TITLE_SEARCH_PROVIDERS),
    )
    monkeypatch.setattr(
        check_service, "fetch_semantic_scholar_record_by_title",
        lambda title, **_kwargs: dict(published),
    )

    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        "@article{smith2024graph,\n"
        "  title = {Graph Parsers for Structured Prediction},\n"
        "  author = {Smith, Jane},\n"
        "  year = {2024},\n"
        "  eprint = {2401.12345},\n"
        "  archiveprefix = {arXiv},\n"
        "}\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "promote_recheck_after_days = 0\n\n"
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n',
        encoding="utf-8",
    )
    config = str(config_path)

    # All three preview: the point is the item's shape, not the write.
    sweeps: list[tuple[str, object, type]] = [
        ("update", pzi.update(config_path=config), pzi.UpdatePlanItem),
        ("check", pzi.check(config_path=config), pzi.CheckItem),
        ("promote", pzi.promote(config_path=config), pzi.PromoteItem),
    ]

    failures: list[str] = []
    for label, report, item_type in sweeps:
        items = report["items"]  # type: ignore[index]
        assert items, f"{label} produced no item, so its type is unchecked again"
        for index, item in enumerate(items):
            failures += [
                f"{label}[{index}]: {c}" for c in _conforms(item, item_type)
            ]
    assert not failures, "sweep item types disagree with real items:\n" + "\n".join(
        failures
    )


def test_the_pep_563_caveat_the_readme_documents_is_still_true() -> None:
    """Item 509: a documented caveat nothing checks is a caveat that goes stale.

    Under `from __future__ import annotations` every key lands in
    `__required_keys__` and `__optional_keys__` is empty, so a runtime validator
    trusting them demands keys pzi does not always send. Not a bug pzi can fix
    without dropping PEP 563 across the package; it is written down in the
    README instead, and pinned here so the README stops being right loudly
    rather than quietly.
    """
    optional_by_declaration = {
        "diff", "metadata_diagnostics", "pdf_error", "pdf_status",
        "pdf_suggestion", "pdf_url",
    }

    # The trap: these all look required at runtime.
    assert pzi.AddReport.__optional_keys__ == frozenset()
    assert optional_by_declaration <= set(pzi.AddReport.__required_keys__)

    # The workaround the README gives, and what pzi's own checks use.
    resolved = get_type_hints(pzi.AddReport, include_extras=True)
    actually_optional = {
        key for key, hint in resolved.items() if get_origin(hint) is NotRequired
    }
    assert actually_optional == optional_by_declaration
