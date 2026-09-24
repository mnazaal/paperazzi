"""Shared helpers for pzi's ``*_cmd`` subprocess launchers.

Three call sites — ``page_metadata_cmd.run_page_metadata_cmd``,
``browser_pdf.resolve_browser_command``, and
``capture_context.run_shell_command`` — each split a configured command
string into argv and expand ``~`` per token, because all three run
``shell=False`` (so the shell never does it) but still want to let
``config.toml`` carry ``~/bin/hook`` instead of an absolute home path.
``page_metadata_cmd.py`` and ``browser_pdf.py`` separately duplicated a
control-character stripper for forwarded subprocess stderr.

This is the lowest tier all three can import without a layering violation:
``capture_context`` and ``page_metadata_cmd`` are CORE, ``browser_pdf`` is
BROWSER, and the architectural guard (``tests/test_layer_boundaries.py``)
forbids CORE from reaching BROWSER, not the reverse — so a BROWSER-tier
module importing a CORE-tier one is unrestricted. This module is itself
CORE: stdlib only, no pzi imports of its own.

What this deliberately does *not* unify: ``capture_context.run_shell_command``
also screens its command for shell metacharacters
(``_reject_shell_metacharacters``) before parsing it, because a secret
``*_cmd`` is more often copy-pasted from documentation that assumes real
shell syntax. ``page_metadata_cmd.py`` and ``browser_pdf.py`` do not screen at
all — a considered difference, not an oversight (see each module's
docstring). That screening call stays where it already lived, as a step the
caller takes before handing the string to :func:`parse_command_argv`, rather
than becoming a parameter here — it is not argv-parsing, and folding it in
would make one caller's opt-out look like this module's default.
"""

from __future__ import annotations

import os
import re
import shlex

#: Control characters (U+0000-U+001F, excluding tab/CR/LF) stripped from
#: forwarded subprocess stderr before it reaches a terminal, so a hook cannot
#: smuggle a terminal escape sequence into the user's scrollback.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def safe_stderr(text: str) -> str:
    """Strip terminal control characters from subprocess stderr before printing."""
    return _CONTROL_RE.sub("", text)


def parse_command_argv(command: str) -> list[str]:
    """Split a configured command string into argv, expanding ``~`` per token.

    ``shell=False`` execution means the shell never expands ``~`` itself, so
    it is done here — this is what lets a ``*_cmd`` config value carry
    ``~/bin/hook`` instead of an absolute home path.

    Raises ``ValueError`` on unparseable input (e.g. an unbalanced quote),
    exactly as ``shlex.split`` does; an empty/whitespace-only *command*
    returns ``[]`` rather than raising, since callers word that failure
    differently (naming the config key, or not) and check for it themselves.
    """
    return [os.path.expanduser(token) for token in shlex.split(command)]
