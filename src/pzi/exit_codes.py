"""Exit codes shared by every CLI command.

One meaning per code, so a script can branch on the status alone instead of
parsing stderr.  ``1`` is reserved for "the command ran fine and has something
to report" (grep's convention), which is why a failure to *run* is never ``1``.
"""

from __future__ import annotations

OK = 0
"""Success, and nothing worth reporting."""

FINDINGS = 1
"""Ran successfully and found something: no search matches, duplicate clusters,
integrity issues, entries `check` could not verify."""

USAGE = 2
"""Bad invocation — unknown command, missing or invalid arguments."""

NOT_FOUND = 3
"""The named entry does not exist — an unknown citekey.  A `--target` naming no
configured library is `ENVIRONMENT` instead: the config is what defines the set
of libraries, so that is a misconfiguration, not a missing entry."""

PARTIAL = 4
"""A batch ran, some items succeeded and some failed.

Returned by `add --from-file`, `import`, `inbox`, `update`, `update --promote`
and `pdf retry --failed-only`.
A batch in which *nothing* succeeded is `ENVIRONMENT`, not this: see
`commands.common.batch_exit_code`, which is where every batch command gets the
answer."""

ENVIRONMENT = 5
"""The command could not run: unreadable or invalid config, a bib locked by
another process or changed out from under a prepared write, permission denied,
an unreachable service."""

INTERRUPTED = 130
"""A stop signal reached the process and it stopped: SIGINT (128 + 2), and also
SIGTERM, which `pzi server` catches so it can shut the translation-server child
down instead of dying mid-request.

One code for both on purpose. 143 (128 + 15) would be the shell's spelling of
SIGTERM, but the only sender of a SIGTERM here is a supervisor — systemd's
default stop signal — and a supervisor already knows which signal it sent, so
the second code would carry no information any caller lacks. What a caller does
need is to tell "asked to stop" apart from "failed", and that is what 130 says.
"""

BROKEN_PIPE = 141
"""SIGPIPE (128 + 13) — a downstream reader closed the pipe."""
