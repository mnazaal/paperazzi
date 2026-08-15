"""Config keys, pinned — item 423.

`config.toml`'s key names and meanings freeze at 1.0. Three sources describe
them and nothing checked that they agree: `AppConfig` (what the loader accepts),
`config.template.toml` (what a user is shown), and the README.

The check is **two-way between the loader and the template**, plus a weaker one
against the README — deliberately, because the README lists "common options" in
prose and points at the template for the rest. Demanding an exhaustive README
list would invent a requirement the docs never made; what it *must* not do is
name a key that does not exist.
"""

from __future__ import annotations

import re
from pathlib import Path

from pzi.config import RETIRED_CONFIG_KEYS, AppConfig

TEMPLATE = Path(__file__).parent.parent / "src" / "pzi" / "config.template.toml"
README = Path(__file__).parent.parent / "README.md"

#: Keys of the `[[bibs]]` table, not top-level config. They appear in the
#: template under a `[[bibs]]` header and are validated by `validate_bib_config`,
#: not by `AppConfig`.
BIB_TABLE_KEYS = frozenset({"name", "path", "papers_dir", "default"})

#: A `key = value` line in the template, commented out or not. Every option in
#: the template is shown as an assignment, which is what makes this derivable
#: rather than a second hand-maintained list.
_ASSIGNMENT = re.compile(r"^#?\s*([a-z_][a-z0-9_]*)\s*=", re.MULTILINE)


def _template_keys() -> set[str]:
    text = TEMPLATE.read_text(encoding="utf-8")
    return set(_ASSIGNMENT.findall(text)) - BIB_TABLE_KEYS


def _loader_keys() -> set[str]:
    # `bibs` is the table itself, described by its own `[[bibs]]` section rather
    # than by a `bibs = ...` assignment, so it is not an option in this sense.
    return set(AppConfig.__annotations__) - {"bibs"}


def test_the_template_and_the_loader_describe_the_same_keys() -> None:
    """Drift here is silent in both directions, which is why it needs a test.

    A key in `AppConfig` but not the template is a setting nobody can discover.
    A key in the template but not `AppConfig` is worse: `unknown_config_keys`
    warns "unknown config key" about something pzi's own template told the user
    to write, and the setting does nothing.
    """
    template, loader = _template_keys(), _loader_keys()
    assert template == loader, (
        "config.template.toml and AppConfig disagree.\n"
        f"  in the template, not accepted by the loader: {sorted(template - loader)}\n"
        f"  accepted by the loader, absent from the template: {sorted(loader - template)}\n"
        "Config keys are frozen at 1.0 — fix whichever is wrong, and note it in "
        "CHANGELOG.md if the accepted set changed."
    )


def test_a_retired_key_is_documented_as_retired_and_not_reaccepted() -> None:
    """`rate_limit_rpm` is the worked example of the deprecation policy.

    It must stay out of `AppConfig` (it does nothing) and out of the template
    (nobody should newly write it), while remaining in `RETIRED_CONFIG_KEYS` so
    a config that still carries it gets an explanation instead of "unknown key".
    """
    assert RETIRED_CONFIG_KEYS, "the retired-key registry is empty"
    for key, explanation in RETIRED_CONFIG_KEYS.items():
        assert key not in _loader_keys(), f"{key} is retired but still accepted"
        assert key not in _template_keys(), f"{key} is retired but still advertised"
        assert "retired" in explanation, (
            f"the message for {key} should say it is retired, not merely unknown"
        )


def test_the_readme_names_no_config_key_that_does_not_exist() -> None:
    """The README may cover a subset; it may not invent one.

    Only backticked identifiers that look like config keys are considered, and
    only if they are also known to *some* source — this is a check that the
    README does not document a setting pzi will ignore, not an attempt to
    reverse-engineer prose.
    """
    text = README.read_text(encoding="utf-8")
    mentioned = set(re.findall(r"`([a-z_][a-z0-9_]{4,})`", text))
    known = _loader_keys() | _template_keys() | set(RETIRED_CONFIG_KEYS) | BIB_TABLE_KEYS

    # Anything the README backticks that *looks* like a config key: it shares a
    # name with a real one, or it is one of the compound names config keys use.
    suspicious = {
        name
        for name in mentioned - known
        if name.endswith(("_cmd", "_url", "_dirs", "_path", "_format", "_host"))
    }
    assert not suspicious, (
        f"the README documents config keys the loader does not accept: "
        f"{sorted(suspicious)}. Either they were renamed and the README was not "
        "updated, or they never existed."
    )
