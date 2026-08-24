# Changelog

All notable changes to paperazzi (CLI command `pzi`) are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**One line per entry.** `release.yml` extracts a version's section verbatim as
the GitHub Release body, so a section is release notes, not a design record.
`[0.1.0b5]` predates this rule and runs to 683 lines of root-cause narrative;
it is left as it is rather than rewritten, since the history is real. New
entries state what changed and, where it is not obvious, what the user will
notice — the reasoning belongs in the commit message, which `git log` keeps
next to the diff it explains.

## [Unreleased]

### Changed

- **Breaking:** `pzi import <missing-file>` and `pzi add --from-file <missing-file>` now exit `2` (usage), not `5`. Their `--json` envelopes already said `"reason": "usage"`, which maps to `2`; the code and the field now agree.
- **Breaking:** `entries`, `entries --stats`, `library dedupe`, `library clean` and `library reindex` exit `1`, not `0`, when the library was only partly read (a block the parser had to drop). A script gating on `library dedupe` succeeding on a partly-read library will now see a finding.
- **Breaking:** `pzi.promote()` raises, and `POST /promote` answers non-200, when every promotion in a sweep failed. Both previously reported `status: "ok"`.
- **Breaking:** a DOI no metadata provider knows now answers HTTP `400` from `POST /capture`, not `503`. It is a settled verdict, not a transient outage.
- `pzi import`'s error envelope carries the same keys as its success envelope (`source_path`, `dry_run`, and the counters) plus `reason`.
- Unsupported HTTP methods (`PUT`, `HEAD`) and malformed request lines now return the JSON error shape rather than an HTML page, and the server no longer discloses its Python patch version.
- `library check` no longer re-dials a provider that has failed three times running, streams `--jsonl` per entry instead of buffering to the end, reports progress for runs of 200+ entries, and accepts `--limit`.
- `130` is documented as SIGINT *or* SIGTERM to `pzi server`, which is what it always returned under systemd.
- Minimum Python is now 3.11.4 (was 3.11), which removes a hand-rolled tar-extraction guard superseded by `filter="data"`.

### Fixed

- A failed write no longer leaves a stale `.bak` behind in `update`, `delete` or `merge`.
- A stalled `git clone` during translation-server install is reported instead of escaping as a traceback.
- Crossref, DOAJ and Europe PMC PDF-lookup failures are reported under `--verbose` instead of being indistinguishable from "this paper has no open-access copy".
- The desktop-browser PDF fallback honours caller-supplied settings, and an unwritable downloads directory no longer aborts the whole fallback chain.
- Parallel PDF discovery ranks sources in the same order as sequential discovery (affects `pdf_discovery_parallel = true` only).
- A second `Host` header is refused rather than resolved last-wins.
- A failed Node upgrade no longer leaves a machine with no cached Node.
- Losing a write race during `update --promote` is re-raised rather than reported once per preprint.

## [0.2.0] - 2026-08-18

### Added

- **`update --promote` remembers preprints that are still unpublished**, in `<pzi_data_home>/promote-checked.json`, and skips them for `promote_recheck_after_days` (new config option, default 30). A sweep over a large library no longer redoes the whole search every run. Recorded under `--dry-run` too, but never when a provider errored or was dropped by the circuit breaker; `promote_recheck_after_days = 0` disables it.
- `update --promote`'s summary gains `skipped_recently_checked`.
- `pzi.check()` accepts `limit`, the bound the CLI has had since `--limit`. A whole-library audit is hours, and the programmatic surface had no way to ask for a smaller one.
- `update --promote` writes its promotions in batches rather than one locked read-modify-write per entry. Measured on a synthetic 4,010-entry library, 60 promotions went from ~11.8 s to ~0.85 s, writing the same bytes. Batches are committed as the sweep runs, so an interrupted sweep keeps the promotions it already wrote.
- **`update --promote --best-of N`** (default 1) stops searching once N candidates good enough to promote have been found, so a resolving entry no longer pays for providers it does not need. `--best-of 5` restores the previous exhaustive behaviour.
- **`pzi.update()` and `pzi.list_tags()`**, completing the Python API's coverage
  of the read and write paths. `update` previews by default like `promote`;
  `list_tags` with no citekey returns the library's whole tag vocabulary.

- **Six more functions in `import pzi`, and every return value is now typed.**
  The API covered seven of ~20 commands, and the gaps were the ones a script
  reaches for first.
  - `get(citekey)` returns the whole record — the only way to find a paper's
    PDF path, since `entries()` and `search()` report `has_pdf` and not where.
    An unknown citekey raises `PziError` with code 3.
  - `list_bibs()` names the libraries `library=` accepts. Nothing supported
    could discover them before.
  - `delete`, `add_tags`, `remove_tags` and `merge` make the write side
    reachable; `merge` is what turns `dedupe()`'s clusters into something the
    API can act on. `merge` and `delete` preview by default; the two tag
    functions act, because they name exactly what they touch and are
    reversible.
  - Every function returns a named `TypedDict`, exported alongside it
    (`pzi.EntryRecord`, `pzi.SearchMatch`, …). The snapshot used to pin
    `-> dict[str, Any]`, which stays green through any key rename; a test now
    compares each declared type against a real call.

- **`import pzi` corrections, before the surface freezes.** A three-way review
  of the API added earlier the same day found real defects; these are the fixes,
  each with a test that fails without it.
  - `search()` and `entries()` returned `[]` for a bib that is missing or
    unreadable — indistinguishable from an empty library, while `export()`
    raised on the same input. Reading a missing bib is a *warning* by design (a
    freshly `pzi init`-ed config names one that does not exist yet), so the
    warnings the CLI prints and the HTTP envelope carries are now re-raised
    through `warnings.warn`, and the result type is unchanged.
  - `PziError` is exported from `pzi`. The documented idiom is to catch it,
    while everything outside `__all__` is declared internal — so catching it
    required an unsupported import. Raw `OSError` from the I/O layer is also
    translated now, so `except PziError` actually holds.
  - **`promote()` previews by default** (`dry_run=True`), matching
    `promote_bib` and `POST /promote`. It swept and rewrote the library over the
    network on a zero-argument call.
  - `PZI_CONFIG` is honoured, matching the CLI's documented precedence — the two
    read different files before.
  - `entries()` clamps `limit` to 1-500 and rejects an unknown `sort` instead of
    silently returning citekey-ordered data, matching both other front ends.
  - The `config` parameter is renamed **`config_path`** — it is a path, and
    every service spells it that way.
  - `cli_version_text` and `package_version` are no longer public: both carry
    test seams (`version_text`, `lookup_version`) in their signatures. Use
    `pzi.__version__`.

- **`import pzi` is now a supported Python API.** Seven functions — `search`,
  `entries`, `export`, `add`, `check`, `dedupe`, `promote` — take an optional
  config path and an optional library, and nothing else that is not their own
  concern. `pzi.search(query="collapse")` works with no arguments at all.
  This is a facade, not a set of re-exports: the service functions behind it
  take `home_dir` and up to twelve injected fetcher parameters that exist as
  test seams, and exporting those would have handed callers arguments they
  cannot supply. Two conventions hold throughout — **failure raises `PziError`**
  rather than returning `{"status": "error"}`, carrying the exit code the CLI
  would have used, and **functions return the answer, not the envelope**.
  `__all__` is the whole of the public surface and is frozen: everything else
  is internal regardless of its docstring. `py.typed` still ships, and a test
  fails if it stops.
- The browser extension now tells you when it and the `pzi` server have drifted
  apart. `GET /health` reports the server's version, the extension compares it
  against its own, and a mismatch shows a banner in the popup naming both — but
  **nothing is refused and every route keeps working**. Refusing would turn a
  routine `pzi` upgrade into a broken capture flow until the extension was
  reloaded, and an extension that silently stops capturing is the worse failure.
  A server too old to report a version produces no warning at all. The fix for a
  mismatch, and the reasoning, are in the README under "Extension and server
  versions". Note that the popup now issues one local `/health` request when it
  opens.
- The `--json` envelope, the config key set and the HTTP route inventory are now
  pinned by tests, alongside the CLI shape and exit codes. Every command that
  offers `--json` is checked to emit exactly one document carrying `command`,
  `status`, `bib_name`, `items` and `errors` with the right types — on failure
  as well as success, which is what the README promises a script can rely on —
  and the per-command fields are snapshotted so none can silently change name.
  `config.template.toml` and the loader's accepted key set are checked to
  describe the same options, with `rate_limit_rpm` pinned as a retired key that
  still loads with an explanation. All 21 HTTP routes are pinned, including the
  two binary `GET`s, which had lived in inline conditionals until this window
  moved them into a route table of their own,
  and the fact that authentication is one gate in front of every route rather
  than a per-route flag.
- The CLI shape and the exit-code table are now pinned by tests, so neither can
  change without a failure that says so. `tests/fixtures/cli_surface.txt`
  records every command, subcommand, flag, positional, `nargs`, `choices` and
  default across the whole parser tree — 189 lines covering 26 parsers — and
  `tests/test_exit_code_table.py` checks the README's exit-code table against
  `pzi.exit_codes` in both directions, that every documented code is reachable,
  and that no two codes share a meaning. Nothing asserted any of this before: a
  renamed flag, a dropped subcommand or a changed default passed every gate.
  These are the first of the 1.0 freeze mechanisms — a frozen surface without a
  test that fails when it changes is a promise with no mechanism.

- **Every reader reports the BibTeX entry type.** `pzi.get()`, `pzi entries
  <citekey> --json` and `GET /detail/<citekey>` all gain `entry_type`, which
  `entries()`, `--stats` and `export` already reported — so the full record is
  a superset of the summary again and an entry can be round-tripped. The HTTP
  route was initially missed (a fix landing at one call site, found by the
  pre-release review) and now has its own handler-level test.

- `pzi library list` prints the configured libraries and which is the default.
  `GET /bibs` and `pzi.list_bibs()` already answered this; on the CLI it was
  only available inside `pzi doctor`'s health output.

### Changed

- **The Python API returns the answer, not the transport envelope.** Ten of the
  fifteen functions returned `status`, `errors` and sometimes `reason`. Each of
  the nine result shapes now has a public twin (`pzi.TagChangeResult` ->
  `pzi.TagChangeReport`, and the same for `Add`, `Check`, `Dedupe`,
  `DeleteEntry`, `Merge`, `Promote`, `TagList` and `UpdateBib`); the services
  keep the envelope, because the CLI reads `status` for its exit code and the
  HTTP API for its status line. `status` and `reason` can never vary in a
  returned value — a failure raises `PziError`, which carries both the code
  and the reason — so they are stripped everywhere. `errors` is stripped from
  six reports and **kept in `CheckReport`, `PromoteReport` and
  `UpdateBibReport`**: those sweeps deliberately report a *partial* failure as
  ok-with-errors (the CLI exits 4 on the same result), so there the key is the
  answer. A test derives each public type from its service type, with that
  three-member exception encoded explicitly, so a service growing a key cannot
  leave its twin behind. `pzi.CheckItem` is exported beside its siblings, so
  `CheckReport["items"]` has a nameable, snapshot-pinned type. **Breaking**:
  the type names changed, and the stripped keys are gone from the returned
  dicts.

- **`pzi.entries()` returns an `EntryPage`, not a list.** `{items, total,
  offset, limit}` — the service computed `total` and the facade threw it away,
  so a caller paginating had no way to know when to stop except by requesting
  until a short page came back. `offset` and `limit` come back *resolved*, so
  they say what was used rather than what was asked for. **Breaking**: entries
  are in `page["items"]`.

- **`PziError` carries `reason`.** `_unwrap` computed the exit code from the
  service's structured reason and then dropped it, so `except PziError` could
  not separate a bad config from an unreadable library — both exit 5.

- **`pzi fix` is now `pzi library`, and `pzi check` moved into it.** The group
  was a verb among nouns (`pzi tag add`, `pzi pdf attach`) while three of its
  four subcommands were read-only, and `check` — also pure validation — sat at
  the top level, so where a command lived said nothing about what it did.

  | before | after |
  |---|---|
  | `pzi check` | `pzi library check` |
  | `pzi fix clean` | `pzi library clean` |
  | `pzi fix dedupe` | `pzi library dedupe` |
  | `pzi fix merge` | `pzi library merge` |
  | `pzi fix reindex` | `pzi library reindex` |

  No aliases: there are no external users, and a shim would freeze the old
  spelling into the surface this is tidying. **The `--json` envelope's `command`
  field changes with it** — `"fix clean"` becomes `"library clean"` and
  `"check"` becomes `"library check"` — which is a breaking change for anything
  branching on `.command`.

- **`import pzi` costs one module again, not forty-four.** The public API is
  bound lazily (PEP 562), so a script calling `pzi.search()` no longer pays for
  argparse, `http.client` and the entire capture stack at import time. This is
  structural rather than a micro-optimisation: importing the API eagerly from
  the package root created real import cycles, because dozens of leaf modules do
  `from pzi import exit_codes`, which executes that same file. Type checkers
  still see the full API — the names are declared under `TYPE_CHECKING`.
- The reason→exit-code mapping moved from `pzi.commands.common` to `pzi.errors`,
  beside the `REASON_*` vocabulary it maps. `pzi.api` needed it, and reaching
  into a CLI module for it dragged the argument parser into the library's
  dependency graph.

- **`--target` names one library, and several are spelled by repeating the
  flag.** `search` and `update` accepted `--target a b` as two libraries while
  the other twelve commands handed `b` to the command's own positional —
  `pzi entries --target main main` reported "no entry with citekey main". Same
  spelling, opposite meanings, no error either way. The greedy form is removed
  rather than made uniform, because it also swallows a command's positional
  (`pzi add --target lib 10.1234/x` would lose the DOI); `--target a --target b`
  is now the one way to name several, and `--target a b` is a plain
  "unrecognized arguments" error.
- **Every `--json` failure carries `reason`.** It reached 4 of 18 error
  envelopes: failures reported by a service had it, failures raised at the
  command boundary did not, so the *same* user error carried it through one code
  path and not another. `PziError` now carries the structured reason where the
  raiser knows it, with a documented coarse fallback from the exit code where
  nothing better exists.
- `pzi library check --force` (then `pzi check --force`) is refused when neither `--report` nor `--jsonl` is given.
  It was accepted and did nothing — on the longest-running command, where a
  silently ignored flag costs a whole network run to discover.

- A capture answered by the translation server now reports that, the way a
  capture answered by Crossref/OpenAlex/Semantic Scholar always did:
  `metadata from translation_server` appears in `--verbose` output and in
  `--json`'s `metadata_diagnostics`. Previously only the fallback cascade named
  its winner, so the primary metadata path was identifiable only by that line
  being *absent* — which meant nothing could tell a real translation-server
  capture from a silent fallback.

### Fixed

- **`pzi library check` (then `pzi check`) and `pzi.check()` now agree about whether a library was
  verified.** An audit that reached no metadata source at all exited 5 from the
  CLI while returning `"status": "ok"` from Python and raising nothing — the
  rule lived in the CLI runner and nowhere else. It is part of the audit now, so
  the report file, the `--json` envelope, the exit code and the Python API all
  say the same thing. `pzi.check()` raises `PziError` for it.

- **Capture failures say why they failed.** `pzi.add("garbage input")` was exit
  5, "could not run", for a malformed argument the CLI's own parser calls a
  usage error; it is 2 now. Provider and translation-server failures are
  classified as unavailable, which over HTTP is 503 rather than 400, and
  config/target failures as config. "No metadata found" stays unclassified —
  none of the documented codes fits it.

- Errors that named `--target` no longer do. Three front ends read those
  strings, so `pzi.export(library="nope")` reported a CLI flag the caller never
  typed. `pzi.entries(library="nope")` also said only "failed to resolve bib"
  where every other command named the library and listed the configured ones.

- The prose error renderers no longer print the same sentence twice when a
  service sets its message and its single error to the same string.

- `pzi pdf retry --failed-only` now exits **5** when every retry failed, not 4.
  Exit code 4 means a batch partly succeeded; this command open-coded its own
  rule instead of using the shared one, so a retry that fetched nothing reported
  a partial success. `pzi.exit_codes`, the `pzi --help` epilog and the README's
  exit-code table omitted the command from the batch list as well, so the
  documented rule and the code agreed on the wrong answer.

- `pzi init --force` writes the backup of the config it replaces into the
  configured `pzi_data_home`, not the XDG default. With that key set, the token
  went to one directory and the only copy of the replaced config to another —
  outside the directory documented as holding the files that cannot be rebuilt.

- The live smoke job can no longer pass while capturing nothing. It skips when
  the translation server cannot start (which skips every capture test at once),
  skips again when a provider returns no metadata, and is `continue-on-error` by
  design — so "nothing ran" and "everything passed" were the same green check.
  The job now fails, visibly, when no real translation-server capture happened,
  and says which of the three causes it was; every run writes a ran-vs-skipped
  table with skip reasons to the GitHub step summary. The capture tests also
  assert that the translation server, not a fallback provider, answered — with
  no server running at all, both of them previously passed on Crossref and
  OpenAlex fallbacks.

- `pzi update --promote` no longer accepts a *different paper by the same
  authors* as a published version. The confidence gate scored title and author
  agreement into one number, so a perfect author match — which is exactly what a
  same-group follow-up paper has — could carry a weak title over the bar. On 20
  real preprints that admitted two: a multi-armed-bandit preprint promoted to
  "On Adaptivity and Confounding in **Contextual** Bandit Experiments", and a
  system paper promoted to the conference *demo* paper about that system. A
  promotion must now clear a title-similarity floor of its own; the two above
  scored 62 and 70 against it, while all twelve correct promotions in the same
  run scored 100. Refusals say which test failed. Note the trade-off: a
  published version that is substantially retitled — an "extended version" with
  a new title — is now refused rather than guessed at, and has to be linked by
  hand.

- `pzi update --promote` no longer relabels a preprint as its own published
  version. Three defects, all in the same place — the code knew what a preprint
  looked like and only ever applied that knowledge to the entry it was
  promoting *from*:
  - "Has a venue" stood in for "is published", and an arXiv record has a venue
    (`CoRR`, `arXiv (Cornell University)`, `CoRR2019`). The test was repeated at
    each of six providers, so there was no single place to ask the better
    question. A candidate is now rejected when it carries preprint identity, and
    the rejection is reported rather than silent.
  - The candidate score awarded `+2` for *any* DOI, so arXiv's own
    `10.48550/…` DataCite DOI earned it while a real conference record — which
    often has no DOI at all — did not. On a real preprint the arXiv record beat
    the actual ICLR version 105 to 103 by exactly that bonus: the published
    version was found and then rejected in favour of the preprint.
  - The promoted entry inherited `publisher = {arXiv}` and
    `number = {arXiv:2110.09348}` from the preprint it was built from, so a
    paper promoted to ICLR claimed arXiv as its publisher.

  Measured against 20 real preprints, before and after: **18 promotions of which
  3 carried a real publication venue, against 14 of which all 14 do** — and 12
  of those 14 are verifiably the same paper as the preprint. Promotions that
  cannot be confirmed now stop at the confidence gate instead of writing a
  relabelled preprint.

- `citekey_format` is documented in the README, not just in the config template.
  Unset, pzi uses its own scheme, so a library exported from Better BibTeX gains
  keys in a second shape with every capture; the README now says so where a new
  user meets the config, and gives the formula that matches a Better BibTeX
  export key-for-key.

- Editing one entry no longer reformats the space around every comment. pzi
  learned a file's block separation only from gaps between two *entries*, then
  applied it at every block boundary — so a library that separates its entries
  by a blank line while keeping each `% ==` comment flush against the entry it
  describes (what Better BibTeX exports look like) gained a blank line before
  every comment on the first write. On a real 22,232-entry library a single
  `pzi tag add` inserted **18,650 blank lines** alongside the one field it was
  asked to add; it is now a one-line diff. Nothing was ever lost — entries,
  citekeys and comments all survived, and the reformat happened once rather
  than per write — but the diff made the actual edit impossible to review.
  The gap before a comment is now sniffed separately, and a file with no
  comments is written exactly as before.

- Concurrent writes no longer fail each other. `execute_write_plan` hashed the
  bib on the line before taking the lock and aborted with "bib file was modified
  externally" when the hash moved — so a second `pzi add` that had queued for
  the lock and got it exited 5 and lost its capture, and `add`'s single retry
  could lose twice under load. The write is rebased onto the library read under
  the lock instead. A plan whose target entry was deleted or renamed meanwhile
  is still refused.
- `pzi update` no longer reports a bib that moved under it as a per-record
  "update failed" note: the refusal its dry-run preview raises is now the one
  the run aborts on, which is what its guard always claimed to do.
- The HTTP `/capture` and `/update` routes answer 409 with the reason a write
  was refused. Both previously returned 500 for it, having guarded an exception
  neither could raise; `/promote`, `/update` and `/capture` now all report the
  specific refusal rather than a fixed "modified externally" line.
- A write plan that is malformed (rather than stale) is no longer retried as
  though a retry could fix it, and a failure while replanning after a stale
  plan no longer leaves the already-downloaded PDF orphaned in `papers_dir`.
- Two writers editing the same entry no longer lose each other's fields. A
  write plan is the whole entry, not a diff, so it carried stale copies of
  fields its writer never touched and those won — a `keywords` value added
  meanwhile was reverted, and `journal` was deleted outright, both silently and
  both at exit 0. The plan now applies as a three-way merge against the entry
  it was built from: what this writer changed wins, what it merely carried
  defers to whoever else edited it. Predates this release (reproduced against
  the previous version), and is independent of the lock change above.
- A capture now reports the entry it actually wrote. The rebase above re-projects
  the write onto the library as read under the lock, but the record `--json` and
  the HTTP capture payload were built from was still the plan-time one — so a
  concurrent writer's `journal`, correctly preserved in the `.bib`, was absent
  from the reported record. Nothing was ever written wrongly; the report
  disagreed with the file. The same reconciliation also stops a citekey
  collision from swallowing its own "requested citekey was already taken"
  warning.
- A write plan whose record carries no citekey is refused instead of writing
  onto whatever entry sits at the plan's index. The guard that compares the
  planned citekey against the one on disk treated a missing citekey as "nothing
  to check" and waved the write through; against the unguarded code such a plan
  replaced an unrelated entry outright. No command could produce one — a keyless
  record is refused earlier, when the entry is projected — so this closes a hole
  in the guard rather than a reachable failure.

### Documentation

- **A compatibility policy**, in the README under "Versioning and
  compatibility". Seven surfaces are frozen at 1.0 and each already had a test
  that fails when it changes; what was missing was what "frozen" obliges.
  Breaking one is a major version, additive change is a minor. A deprecated
  thing keeps working, with a notice, until the next major — `rate_limit_rpm`
  is the worked example, and the notice appears in `pzi doctor`, which the
  section says rather than implying every command reports it. A data clause
  states what pzi promises about the `.bib` itself, which is the surface that
  actually matters.

- **`pip install paperazzi` installs someone else's package.** The README said
  paperazzi was "not yet on PyPI"; in fact the name is taken by an unrelated
  project. The install section now says so, next to the `git+https` commands
  that are the correct way in.

- One more known source-preservation limitation, found while checking the data
  clause against a real write: a comment sitting flush *above* a block gains a
  blank line under it on the first write. The mirror of the flush-*below* case
  fixed earlier; a Better BibTeX export's `% ==` reports are unaffected.

- The README now says what `pzi export` writes where: a bare JSON array for
  `--format json` (not the `--json` envelope — the array *is* the document), one
  prose line for `-o PATH`, and nothing at all on failure. Three cases, one
  table, and a test that runs all three.

- `README.md` gains **Recovering from a refused or interrupted write**: what
  each write refusal says and that every one of them means nothing was written,
  which backups exist and how to restore each, and why a leftover `<bib>.lock`
  must never be deleted to clear a jam (it is `flock`-based, so it is never
  stale, and removing it lets two writers in). The section states which commands
  leave no backup at all, and every claim in it was observed against a sandbox
  library rather than read off the source.

## [0.1.0b6] - 2026-08-13

### Added

- Captured entries now carry `volume`, `number`, `pages`, `publisher`, `issn`
  and `isbn` when the source reports them — no capture path had ever written
  them, so `@article` entries lacked the fields most journal styles require.
  Existing values are never overwritten and never deleted, only gaps filled.
- `pzi server --log-requests` — one line per HTTP request on stderr (method,
  path, status, ms). Off by default; the query string is never logged.
- `pzi inbox --json`, the last runner without it.

### Removed

- **Breaking:** the inbound HTTP rate limiter, and its `rate_limit_rpm` config
  key. It was keyed on the peer address — so on loopback every local process
  shared one bucket — and ran after the auth gate, so it never metered a failed
  token: it throttled your own tools and not an attacker. A config still
  carrying `rate_limit_rpm` loads, with a warning saying the key is retired.
  (The *outbound* limiter that spaces provider requests is unaffected.)
- `.dockerignore` (there is no Dockerfile, and none is documented), an
  unreferenced test fixture, and three dead definitions: `TAG_SEPARATOR_PATTERN`
  and a private `_to_ascii` in `tag_service` — a diverged copy of the naive
  folding that `normalize_tag`'s own docstring identifies as the bug that erased
  non-Latin tags — plus a `source_notes` list `check` accumulated and never
  returned.

### Documentation

- `--no-auth` is documented in the security model, which never mentioned the one
  flag that turns the API's only authorization control off; and the token is
  described as mandatory rather than "optional but recommended".
- The route count is 21, not nineteen, itemized by kind.
- The PDF fallback chain is the five stages the code runs, not three, with
  `browser_hook` correctly described as gating the two headless stages rather
  than the desktop one.
- `browser_engine` is set in `config.toml`; `pzi init --browser` writes
  `browser_pdf_cmd`, which the README claimed otherwise.
- `pzi check --force` and `pzi server --log-requests` appear in the synopsis,
  `PZI_NODE_VERSION` in the environment table, and `pzi import -` in the help.
- The exit-code epilog lists 130 and 141 and describes 4 as covering `update`
  and `inbox` too, matching `exit_codes.py`.
- The data directory is described as rebuildable *except* the token and the new
  config backups, and the sidecar files a write leaves are listed.
- "No test reaches the network" is qualified: `tests/live/`, behind `PZI_LIVE=1`,
  deliberately does.
- The extension README describes the payload it actually sends and the
  `/attach-pdf-raw`-first upload order; the manifest description matches.
- The packaged config template links to `docs/security.md` on GitHub, since
  `MANIFEST.in` prunes `docs/` from the installed package.
- `metadata_confidence_min_score` is validated as 0-100, which the template
  documented and nothing enforced.

### Security

- The translation-server child is pinned to `127.0.0.1`. pzi passed it `PORT`
  and **nothing read that variable**: at the pinned upstream commit,
  `config/default.json5` sets `host: "0.0.0.0"`, `src/server.js` listens on
  `config.get('host')`, and node-config maps only `translatorsDirectory` — so
  the child ignored the port pzi chose and bound every interface. For as long
  as pzi ran it, a server that fetches any URL it is handed was reachable from
  the whole network, unauthenticated, on port 1969. It is now steered with
  node-config's own `NODE_CONFIG` override, which also makes a non-default
  `translation_server_url` port work rather than leaving `wait_for_ts` polling
  a port nothing listens on.
- **Breaking:** every spelling of the wildcard bind is refused, by both entry
  points into the server. The guard enumerated `0.0.0.0`, `::` and `*`, so the
  legacy short forms `0`, `0.0`, `0x0`, `00` — and the empty string — passed it
  while `socket.bind` binds them all to `0.0.0.0`. `--host ""` was the worst:
  the security config normalizes an empty host to `127.0.0.1`, so the
  DNS-rebinding Host check then *accepted* requests arriving from the LAN. The
  HTTP entry point had no wildcard check at all — a token was enough to expose
  the API on every interface there, while the CLI refused the same thing.
  Binding a specific address, with a token, is unchanged.
- Landing-page URLs are validated before being handed to the translation
  server, which fetches whatever it is given. They come from provider metadata
  and captured pages, so an unvalidated one made the local server a proxy into
  this machine's network — the same defect `flaresolverr.py` documents and
  fixed for the local browser. EZProxy is unaffected: its rewrite and host
  exemption live on the download path.
- The browser extension decides publisher trust in its own realm, from the URL
  it already passes, and reads a generic page from the ISOLATED world. The
  check ran *inside* the page as `location.hostname.endsWith(...)`, where a
  page can replace `String.prototype.endsWith` — so any page could pass the
  gate, populate the publisher global, and have nine forged fields promoted by
  the server into authoritative metadata beating the real Crossref lookup.
- Context-menu capture forwards cookies only when the link is on the same
  origin as the tab, and validates the URL with `isSafePublicHttpUrl`. A link
  to `http://127.0.0.1:9999/x` previously had loopback cookies read and
  transmitted before the server rejected the capture.

### Changed

- A relative `file` path survives an unrelated edit. `file` is read as an
  absolute `local_pdf_path` and written back absolute, so any command that
  touched an entry rewrote a portable `papers/x.pdf` into a machine-specific
  `/home/you/bibs/papers/x.pdf` — one `tag add` was enough, and a git-tracked
  library drifted to machine-specific one entry at a time. What an entry
  already has is now preserved; `pdf_file_path_style` still decides how a
  *newly* attached PDF is written.
- A PDF that pypdf cannot parse is stored with a warning saying so, instead of
  being indistinguishable from a scan with no extractable text. `%PDF-` at the
  front was the only gate anything applied, and the parse verdict was
  discarded. Not a refusal: some legitimate publisher PDFs defeat pypdf.
- The click-based PDF discovery step returns the URLs it found instead of
  opening up to five hidden cross-origin iframes, each carrying your cookies,
  to read an observer cache that is always empty in the realm it runs in — the
  cache is written by the service worker, and capture runs in the popup.
- Semantic Scholar failures are reported on the title-search path, which is
  what `check` uses. The guard returned one line before the reader could see
  the body, so the reporting branch was unreachable for exactly the payloads it
  was written for — an S2 rate limit was invisible there while the by-DOI path
  named it. All three S2 entry points now read both `error` and `message`.
- `export --format csv|ris` carry `volume`, `number`, `pages`, `publisher`,
  `issn` and `isbn`. RIS gets the standard tags, including `SP`/`EP` for a page
  range, so an export handed to another reference manager is a complete
  citation; `export_json` had emitted all six since the records gained them.
- Attach URLs derive from `api_listen_host`/`api_listen_port` when `api_url` is
  unset, instead of assuming `127.0.0.1:8765`.
- **Breaking:** `citekey_format` validates Better BibTeX formulas. Nothing
  without `{{` was checked at all, so `authr.lower + year` (unknown variable)
  and `auth.lowr + year` (unknown filter) were accepted and silently rendered a
  shorter key — the exact failure the check documents itself as preventing.
- A citekey folds like Better BibTeX instead of deleting: `Weiß` → `weiss`, not
  `wei`; `Søndergaard` → `sondergaard`; `Łukasz` → `lukasz`. NFKD has no
  combining form for a stroked letter, so ASCII-encoding dropped it and the key
  lost a letter. Author *matching* keeps its own folding (`ü` → `ue`), which is
  correct for comparison and wrong for a BBT key.
- A name with a nobiliary particle keys the same in either storage form:
  `"van der Berg, Anna"` and `"Anna van der Berg"` both give `vanderberg`,
  where the second used to give `berg`. Existing citekeys are never rewritten.
- A PDF this run downloaded is removed when the BibTeX write *raises*, not only
  when the entry disappeared. A refused write left the file on disk with nothing
  referring to it, and a later `fix clean --fix` quarantined it — one command
  tidying up after another. Files that were already there are never touched.
- `update --promote --replace` leaves one backup per run instead of one per
  promoted entry. `update_bib_entry` copies the whole library, so promoting 100
  preprints against a 15.8 MB library wrote roughly 1.6 GB of `.bak` files.
- Re-adding a paper no longer reorders its `keywords`. Merging compared a sorted
  set against the user's own order, so it reported a change that was only the
  comparison's doing and rewrote the field.
- The metadata cache keys on the API key and polite-pool identity as well as the
  URL — an anonymous-quota response could be served to an authenticated caller
  for the whole TTL — and is now bounded, sweeping expired entries and capping
  the directory. Entries were reclaimed only by a repeat lookup of the same URL.
- A typo inside a `[[bibs]]` table is reported. Only top-level keys were
  checked, so `papers_dirs` or `defualt` was accepted silently — the two
  settings that decide where PDFs are written and which library is acted on.
- `--target` pointing at a symlink resolves to the configured library instead of
  falling through to an ad-hoc one with a different `papers_dir`, and a
  *directory* named `something.bib` is refused rather than accepted as a library.
- **Breaking:** every batch command answers with one rule. A batch in which
  *nothing* succeeded exits 5, not 4 — `add --from-file` already did this while
  `inbox` and `import` returned 4 regardless, so identical all-invalid input
  exited differently depending on which command consumed it. `pzi update` uses
  the same rule.
- **Breaking:** `pzi search` and `pzi import` derive their exit code from the
  failure the service reported instead of hardcoding 5. A tag that normalizes to
  nothing, and an unparseable import source, are usage errors (2) — both already
  said `"reason": "usage"` in the JSON envelope while exiting 5, and
  `pzi.http_status` reads that same field, so the CLI and HTTP API disagreed
  about one failure.
- **Breaking:** `pzi check` reports an unwritable `--report`/`--jsonl`
  destination as 5, not 2. The flag is spelled correctly; permission or a
  missing parent directory is an environment failure.
- `pzi fix clean --fix` exits 0 when it resolved everything. The issue list is
  computed before the quarantine, so a successful run kept reporting the orphans
  it had just filed away and `pzi fix clean --fix && next-step` could never
  proceed.
- `pzi inbox` honours `--json` on the failures that happen before the drain
  (missing file, unloadable config, backend not running). They printed prose to
  stderr and left stdout empty — for the command most likely to run from cron.
- `pzi pdf attach|retry` print the service's warnings in text mode, including
  the FlareSolverr terms-of-service notice and the "a previous PDF was
  superseded" notice. The services were fixed to return them; the runner never
  read them.
- `entries --stats --json` reports `bib_name` instead of `null`.
- A capture no longer stops at the first provider that answers *anything*. Every
  normalizer returns a record even when the response was empty, so a thin
  Crossref answer won permanently and OpenAlex and Semantic Scholar were never
  consulted — the fallbacks existed for exactly that case and could not be
  reached. A provider now has to supply a title to end the cascade; a thin answer
  is still used if nothing better arrives.
- A renderer indexing a missing key reports it as an error instead of a
  traceback (and, under `--json`, instead of a truncated document).
- **Breaking:** a capture whose metadata identifies no paper is refused, and
  the refusal says how to proceed. This is no longer tied to
  `--strict-metadata`: a `.bib` entry naming nothing is not something a flag
  should have to opt out of. `pzi add scan.pdf` on a PDF nothing can identify
  now exits 5 having written neither the entry nor a copy of the PDF, and
  suggests `--metadata-json`; supplying a title that way captures it as before.
  Previously such an add wrote `@article{unknownxxxxuntitled}` carrying only a
  `file` field, at exit 0 with no warning — and `--strict-metadata`, whose help
  promises to "refuse to capture a paper the metadata does not identify",
  produced byte-identical output, because the local-PDF branch returned before
  the gate that was added for the URL branch.
- `--strict-metadata` no longer fails on a provider error that a later provider
  recovered from: a Crossref 429 used to fail an add OpenAlex completed.
- `pzi init --rotate-token` works on an installation that has a config. The
  "config already exists" refusal fired first, so the flag exited 2 and rotated
  nothing unless `--force` was also given — and `--force` replaces the config
  with the shipped template, so the documented way to replace a token was to
  discard your configuration. It now rotates the token and leaves the config
  alone.
- `pzi init --force` backups go to `<data-home>/config-backups/config.toml.<timestamp>`
  and are never overwritten. They were a single `config.toml.bak` beside the
  config, so a second `--force` replaced the backup with the first run's
  template output and the original was unrecoverable. The config write is now
  atomic, and resolves a symlinked config so a config tracked in a dotfiles
  repository keeps its symlink and gets its content replaced in place.
- Three `--dry-run` previews that disagreed with the run they preview. `import`
  counted every entry as an import, so a source the library already had
  previewed `imported 2/2` and ran as `imported 1/2, skipped 1 duplicates`; it
  now classifies exactly as the run does, differing only in tense
  (`would_update` / `updated`). `fix merge` reported the survivor's author as
  kept when the merge replaces it, and named the fields it overwrites only in
  the preview — so the run that destroys them was the silent one. `fix reindex
  --dry-run` ran a weaker set of gates than the write, so a library the real run
  refuses outright previewed as a feasible rename list, including a rename for
  the entry causing the refusal.
- `pzi import` no longer marks a successful update or a correctly skipped
  duplicate with ✗; only an error is a failure.
- `pzi check` no longer discards a completed audit because one BibTeX block was
  dropped. A single duplicate citekey made it exit 5 with no report file and
  nothing printed — after every network lookup had been made — while `--json`
  still emitted the audited items, so the two output modes disagreed about
  whether the run had produced anything. The dropped block is now a warning
  beside the results, as in `entries`, `search` and `fix dedupe`, and the run
  exits 1: ran fine, has something to report.
- `--force-new` says what it duplicated: "inserted a second entry for a paper
  already in the library as `<citekey>`". The near-duplicate hint returned
  early on an *exact* match — the one case `--force-new` is defined to bypass —
  so `pzi import --force-new` doubled a library and reported `warnings: []`.
- A failed `pzi add` prints its warnings. They were rendered on the success
  path only, so a service explaining how to get past a refusal explained it to
  nobody. Provider errors during a local-PDF capture are now among them, rather
  than being dropped unless `--strict-metadata` was set.
- `pzi fix`, `pzi tag` and `pzi pdf` with no subcommand print that group's help
  instead of `error: the following arguments are required: fix_command`. Still
  exit 2.
- `fix reindex --rename-citekeys` says when no `citekey_format` is configured
  and the built-in author+year+title scheme would be used — in the prompt, the
  warning above it, and the non-interactive refusal.
- `add --from-file` exits 5, not 4, when *no* item succeeded (4 is documented as
  "some items succeeded", and the JSON envelope already said `error`).
- Node is resolved from the data home before any network call, and
  `PZI_NODE_VERSION` pins the version.
- The translation-server is no longer reinstalled on every pzi version bump.

### Fixed

- **A write no longer reformats the whole library.** Every write built its own
  formatter and set only the indent, so bibtexparser's remaining defaults were
  imposed on the file: tabs became two spaces, every trailing comma was
  stripped, and a blank line was added between entries. Adding one tag to one
  entry of a 200-entry Zotero export changed 1800 lines; on a 22k-entry library
  the first write was a 59.5k-line diff that touched no content at all. The
  indent, trailing-comma and entry-separation conventions are now read off the
  file being rewritten — as its line endings and BOM already were — so a
  one-entry edit is a one-entry diff. A `.bib` with no conventions to read
  (new, or a single entry) gets one blank line between entries, which is also
  what an entry-separator default of "two blank lines" used to mean. `pzi
  export --format bibtex` preserves the source layout too, since an export is
  billed as a backup.
- **Breaking:** `citekey_format` can now reproduce a Better BibTeX key. Quoted
  literals may contribute `-`, `_`, `:` and `.`, which the final sanitizer used
  to delete — so `auth.lower + "-" + shorttitle(1,0).lower + "-" + year` gave
  `smith2024` instead of `smith-graph-2024`, with no error. Measured against a
  real 22k-entry BBT library, exact-key agreement went from 93.5% to 98.0%.
- **Breaking:** `shorttitle(n, m)`'s second argument capitalizes the first `m`
  selected words, as Better BibTeX documents; it was read as a per-word
  truncation length. `m` defaults to 0, so that reading made every plain
  `shorttitle()` — including the `shorttitle(3,3)` in pzi's own config
  template — render the empty string.
- **Breaking:** a `citekey_format` template no longer force-lowercases its
  result, so `.upper` works and `auth` keeps the family name's capitals. The
  built-in scheme used when no template is set is unchanged.
- `citekey_format` drops Better BibTeX's full default `skipWords` list rather
  than ten words, so a title starting "Towards …" no longer keys on "towards".
- A hyphenated surname keeps its hyphen (`domingo-enrich`, not
  `domingoenrich`).
- A Crossref subtitle is folded into the title, so the MapReduce paper stores
  `MapReduce: simplified data processing on large clusters` rather than
  `MapReduce`. Crossref deposits the two as separate fields.
- Semantic Scholar lookups now request the `journal` field they read, so volume
  and pages actually come back.
- `pzi init --bib` without `--setup` says "requires", not "require".
- `doctor` explains that a keyless `semantic scholar: ok` means the shared
  anonymous quota, so it no longer appears to contradict `check` reporting S2
  unreachable.
- `pzi update` no longer adopts an unrelated paper's metadata when a search
  returns a different work.
- Usage mistakes exit 2 instead of 5, and HTTP status no longer depends on how
  you named a paper — every failure now carries a structured reason.
- Repeated `--target a --target b` keeps both libraries; it used to keep only
  the last, silently halving `search` results and updating one library of two.
- `pzi check --report -` can be piped into `jq`; `--report`/`--jsonl` no longer
  clobber an existing file without `--force`, and write atomically.
- Bare arXiv IDs (`2301.07041`, `arXiv:2301.07041v2`) are accepted.
- A text file named `.pdf` is refused instead of writing an empty placeholder.
- Tags in non-Latin scripts work; they used to normalize to nothing and be
  rejected as "no valid tags supplied".
- `add --from-file` interrupted with Ctrl-C still writes its failures file and
  prints its summary, and exits 130.
- Many silent failures now report: skipped PDF stages, unreachable
  server-browser, discovery providers that raised, tag writes that failed,
  imports that updated an entry, and inbox tokens that were ignored.
- FlareSolverr no longer forwards arbitrary URLs (it made the local headless
  browser an open proxy); `--cookie-file` cookies stay on the origin they were
  captured for; the attach token is header-only.
- Abandoned clones of your Chrome profile are swept from `$TMPDIR`.
- Firefox `strict_min_version` raised to 128, which is what the manifest needs;
  109–127 installed cleanly and silently lost cross-origin PDF fetching.
- `pzi init --force` writes a `.bak` before overwriting your config.

## [0.1.0b5] - 2026-08-03

### Fixed

- **The hermetic `$HOME` fixture hid the Playwright browsers, so every browser
  test failed in CI.** `$HOME` is repointed at a tmpdir per test to stop leaks
  into the developer's real `~/.config/pzi`, but Playwright resolves its browser
  cache from `$HOME` too, so all 20 tests died on "Executable doesn't exist at
  `<tmpdir>/.cache/ms-playwright`". The fixture now pins
  `PLAYWRIGHT_BROWSERS_PATH` to the real cache before the switch. It was
  invisible locally because the fixture clears `XDG_CONFIG_HOME` and
  `XDG_DATA_HOME` but not `XDG_CACHE_HOME`, which a developer machine sets and
  a CI runner does not.
- **"Browsers were never downloaded" could not be reported as a skip.** The
  probe tested `executable_path is None`, which Playwright never returns — it
  yields a path whether or not anything is there, and for headless Chromium not
  even the path that must exist. The case is now recognised from the launch
  error, and stays a *failure* under `CI`, where downloading browsers is a job
  step rather than an optional local install.

### Changed

- **The browser test suite runs green, and now gates the release.** Every
  browser test that expected a result was failing in CI: the page request guard
  and the landing check both apply the public-URL predicate, and the fixture
  servers are necessarily on loopback. Both now take that predicate as an
  injected parameter — a dependency-injection seam, deliberately not a config key
  or environment variable, since a user- or HTTP-reachable switch that disables
  SSRF protection is the exact thing the guard exists to prevent. `release.yml`
  installs browsers and runs `pytest -m browser`, so a red browser suite can no
  longer be tagged.
- **A browser that is installed but will not launch is a failure, not a skip.**
  The probe returned a bare `False` for any exception, so a machine where
  Firefox times out reported "20 skipped" while CI reported "10 failed" — and the
  skip was indistinguishable from the honest "no browsers installed" case.
- **Two browser tests could not fail.** `assert url is None or url.endswith(...)`
  and `if body is not None: assert ...` are both satisfied by the feature not
  working at all, and duly passed throughout.
- **Gaps closed in the test suite itself.** `extract_pdf_metadata` had no
  positive test — every case asserted `doi is None and title is None`, which a
  constant stub satisfies. `pzi delete` and `pzi fix merge`, the two commands
  that destroy a block, had no command-level test at all. The layer-boundary
  guard resolved a relative import to a bare stem matching no module, so a
  back-edge written `from .common import x` was invisible to it. `$HOME` is
  pinned per test, so a test that forgets `home_dir` can no longer write into the
  developer's real config and data directories. A node failure in the extension
  bridge test is asserted rather than skipped, `pypdf`'s `importorskip` guards
  are gone (it is a hard dependency, so they could only mask a broken install),
  a swallowed `except OSError: pass` became `pytest.raises`, DNS rebinding got
  the test it never had, and the `add --from-file` tests no longer contact
  Crossref, Europe PMC and DOAJ on every run.
- **CI and the release use `uv sync --locked`, not `--frozen`.** Only `--locked`
  verifies the lockfile is up to date with `pyproject.toml`; `--frozen` skips
  resolution and installs the lock as-is, so a lockfile out of step with the
  manifest passed silently. Three comments claiming `--frozen` validates the
  lock were wrong and are corrected.

### Fixed

- **`pzi tag` matches a stored tag however it was spelled.** `--tags` is
  normalized to a slug on the way in and stored tags were compared raw, so a tag
  written `Machine Learning` could not be removed by any input — `pzi tag remove`
  reported "no changes" and exited 0 forever — and adding it again left the entry
  carrying both spellings. Both sides are now normalized for the comparison, and
  the stored spelling is kept: this fixes the matching without rewriting tags the
  user typed.
- **Two `pzi tag add` runs on one entry no longer lose one of the tags.** The new
  tag set was computed from the pre-lock snapshot and written verbatim inside the
  lock, so the second writer silently reverted the first — and reported success.
  The arithmetic now runs against the entry as it is under the lock, and the
  result reports what was actually written.
- **One provider's search miss no longer condemns an entry two others
  confirmed.** `pzi check` unioned defect flags across every source that returned
  anything, including one whose own match was flagged `title_mismatch` — so
  `--strict` reported `problematic` alongside `confidence_score: 100`. A source
  that did not identify the work no longer testifies about it; one that *did* and
  disagrees still does.
- **`pzi check` now exits 1 whenever an entry is problematic or could not be
  verified**, as `exit_codes` and the README's table always said. It fired only
  for `problematic` and only under `--strict`, so a CI gate written from that
  table passed a library of fabricated references. `--strict` keeps its real
  meaning: harder checks, not whether findings are reported.
- **`pzi add --json` answers with an envelope when the backend is not ready.**
  The commonest failure of all printed prose to stderr and nothing to stdout, so
  a script driving `--json` got an empty document. Translation-server bootstrap
  progress now goes to stderr too, where it cannot precede that document.
- **`pzi doctor --reinstall-server --json` emits an envelope.** That subpath
  returned before the `--json` branch and answered a documented invocation with
  prose on stdout.
- **`pzi check --report - --json` is refused.** Both write to stdout, so together
  they produced neither a valid report nor the single document `--json`
  promises; the `--jsonl -` twin of this guard already existed.
- **`entries <citekey>` and `entries --stats` refuse the list-only flags.**
  `--limit`, `--offset` and `--sort` were parsed on every form and applied only
  to the list, which reads as a working filter that silently is not one.
- **`pzi add --from-file --verbose` prints its metadata diagnostics.** The flag
  was parsed and never read on the batch path — the mode where per-item provider
  choices are hardest to follow was the one that would not explain them.
- **Two concurrent bootstraps no longer corrupt each other.** They shared one
  `ts.new` staging directory, so the second deleted the first's half-finished
  clone and both raced to rename over `ts`. Staging is now per-process under one
  cross-process lock. Git and npm steps also get timeouts and
  `GIT_TERMINAL_PROMPT=0`, so a private repo or a stalled registry fails with a
  message instead of hanging forever on a prompt nobody can see.
- **Bulk capture no longer reads and uploads cookies for every search result.**
  Each result is a *different* site the user is not on; their cookies were read
  and forwarded to the server, which forwards them to the publisher. The comment
  directly above the call already said this did not happen.
- **The extension only talks to a loopback endpoint.** The configured URL was
  used unchecked, so one mistyped character in the options box — or anything
  able to write extension storage — sent the page HTML, the site's cookies, the
  downloaded PDF and the API token to a remote host. A pzi server is always
  local, so a non-loopback endpoint falls back to the default.
- **The API token survives a browser restart.** The popup stored it in
  `storage.session`, which the browser clears on close *and* which shadows
  `storage.local` in the background's merge — so the first capture of a new
  session wrote the empty token box over the token onboarding had saved, and
  every request 401'd until it was retyped. An empty box no longer overwrites a
  stored token.
- **The Firefox build no longer requests response-body access.**
  `webRequestFilterResponse` gates `webRequest.filterResponseData`, which the
  extension calls nowhere — reading response *headers* needs only `webRequest`.
- **The hidden-iframe PDF bypass actually runs.** Its timeout was read from
  module scope inside a function serialized into the *page*, so it threw a
  `ReferenceError`, the injected promise rejected, and every bypass silently
  fell through to opening a visible tab. The bypass is also now capped at three
  attempts per capture, instead of one per candidate at up to 20 seconds each.
- **A temporary host permission is released even when the fetch throws.** It was
  released after the call it protects, so a failure left the user holding a
  permission granted for one PDF fetch.
- **A response that is not JSON no longer throws out of the capture path.**
  `jsonOrNull` wrapped `response.json()` in a `try`, but the failure arrives as
  a rejected promise the `try` cannot see. Same for the cookie-header fallback,
  whose second `getAll` sat bare inside the `catch`.
- **Bulk capture shows why an item failed.** It read only `message`, while a
  route rejection reports under `error` and a service failure under `errors[]` —
  so every one of them rendered as the literal word "failed".
- **Removed the dead popup→background capture bridge.** Nothing sent the message
  it listened for: capture runs in the popup deliberately, because Firefox only
  grants an optional host permission while the user's click is in scope. The
  listener was unreachable code asserting a property the extension lacks.
- **A PDF failure no longer throws away the metadata that was just fetched.** An
  exception out of the PDF stage — a dead `browser_pdf_cmd`, a full disk, a
  provider hanging up mid-download — aborted the whole `pzi add`, so the record
  resolved over the network was discarded and had to be fetched again. The entry
  is written, the failure is reported as a warning, and the PDF stays retryable
  with `pzi pdf retry`.
- **PDFs can be stored on a filesystem without hardlinks.** `os.link` is what
  makes "create only if absent" atomic, but exFAT, many CIFS mounts and some
  FUSE filesystems reject it outright — and only `FileExistsError` was handled,
  so `pzi add` could not attach a PDF at all on such a `papers_dir`. It now
  falls back to a checked rename.
- **A failed PDF write no longer leaks a temp file.** `temp_path` was bound
  after the write, so a write that raised skipped the cleanup entirely, leaving
  one `.pdf-*.tmp` in the papers directory per failure.
- **The desktop-download watcher survives a file that vanishes mid-scan.** It
  called `stat` inside a sort key over a directory the browser is actively
  writing to, so a partial download being renamed at that instant killed the
  fallback with `FileNotFoundError` — at the moment it was about to succeed.
- **`pzi server` refuses to start without an API token.** Without one every
  route — search, export, the stored PDFs, `capture`, `update` and `delete` —
  was reachable by any process on the machine, behind a printed warning the
  server then ignored. Run `pzi init` to write a token (the extension reads the
  same one), or pass the new `--no-auth` to serve unauthenticated deliberately.
  The opt-out is a CLI flag rather than a config key on purpose: nothing
  reachable from `config.toml` or from the HTTP API itself can disable
  authentication.
- **A PDF URL from any discovery step is validated before it is used.** Only the
  browser step checked what it had found; a URL supplied by a Crossref,
  Unpaywall, EuropePMC or DOAJ response, or built from a captured page, went
  straight into the record — and the server then fetched it, or handed it to the
  extension to fetch with the user's cookies. `http://169.254.169.254/…` and
  `file:///…` are now dropped at the one place a step's result is accepted.
- **A hostile page can no longer hold the browser lock indefinitely.** The list
  of PDF candidate links comes from JavaScript running in the fetched page, and
  every entry cost a navigation with a 30-second timeout while the server's
  single browser lock was held. Capped at 10 candidates and a 60-second sweep.
- **A failed browser launch no longer leaves a copy of your Chrome profile in
  `$TMPDIR`.** The clone — cookie database included — was abandoned there, along
  with the Playwright driver process, whenever the launch after it raised.
- **The desktop-browser PDF fallback refuses a non-http(s) URL.**
  `webbrowser.open` hands the string to the OS scheme handler, and the URL comes
  from a provider or a captured page, so `file:`, `javascript:` and `data:` URLs
  were opened by whatever the desktop runs for them.
- **The sessionless `/attach-pdf-bytes` upload enforces the PDF size cap.** The
  session path checked `max_bytes`; the sessionless one — which is deliberate
  and documented — checked nothing.
- **A NUL byte in a request path is refused, not a 500.** `Path.resolve` raises
  `ValueError`, and the confinement helper caught only `OSError`.
- **401, 403 and 429 close the connection**, as the 413 path already did, rather
  than leaving a keep-alive socket open for a caller the server has just refused.
- **A malformed `Host` or `Origin` header is a 403, not an unauthenticated 500.**
  `urlsplit` raises `Invalid IPv6 URL` on an unbalanced bracket, and that ran
  inside the request gate — before the token is checked — so any caller could
  crash a request with one header. The 500 handler sends CORS headers too, so
  the same `Origin` faulted the error path in turn and the caller received zero
  bytes instead of a diagnostic.
- **A non-ASCII API token is a 401, not an unauthenticated 500.**
  `hmac.compare_digest` raises `TypeError` on a str with non-ASCII characters.
  The comparison now runs on UTF-8 bytes, keeping the constant-time property and
  giving the answer that was always right: a token that is not the token is
  invalid.
- **A metadata candidate claiming a different DOI than the one asked for is
  refused.** It cost 50 points, which a rich record outweighs — and when it was
  the only candidate it won outright however low it scored, so `pzi add
  10.1145/A` could store the record for `10.9999/B` under the DOI and citekey
  the user asked for. The cascade now falls through to Crossref/OpenAlex
  instead. A candidate that agrees, or says nothing about the DOI, is unaffected.
- **A local PDF no longer adopts a different paper's identifiers.** The first
  title-search hit was taken whatever it was, so a near-miss handed its DOI to
  the user's file — which then deduped against that paper and resolved to it on
  every later `update` and `check`. The hit is now scored with the same
  comparison `pzi check` uses, the *best* hit is taken rather than the first, and
  its `item_type` is carried across so a conference paper stops becoming
  `@article` with `journal = {proceedings}`.
- **A DOI scraped out of a PDF is normalized before it is resolved.** It arrives
  with whatever surrounded it in the text — a `https://doi.org/` prefix, a
  trailing sentence period — and was resolved and stored verbatim; a value that
  is not a DOI at all (`see front matter`) was resolved as one instead of
  falling through to the title search.
- **The local-PDF path uses the configured contact email and HTTP seam.** It
  called Crossref and OpenAlex anonymously, off the polite pool and outside the
  injected fetcher, unlike every other input kind.
- **arXiv IDs are normalized, so one paper is one identity.** Zotero reports
  `archiveID: "arXiv:2301.12345"` and arXiv itself serves `v2` suffixes; stored
  verbatim, the same paper had a different identity per capture route, dedupe
  missed it, and `eprint = {arXiv:2301.12345}` rendered a citation reading
  "arXiv:arXiv:2301.12345" because `archiveprefix` supplies the prefix again.
- **Crossref organizational, mononym and suffixed authors are no longer
  dropped.** Only `given` + `family` was read, so a consortium or standards-body
  author (which Crossref reports as `name`) produced an entry with *no* author at
  all — which `pzi check` then flagged `author_unknown` — and `King, Jr.` was
  silently merged with `King`.
- **Crossref's deposit date is no longer mistaken for the publication year.**
  `created` — when the DOI record was deposited — was consulted before `issued`
  and `posted`, so a 1998 paper back-deposited in 2015 was captured as
  `year = {2015}` and then disagreed with every other source.
- **The DOI→`/web` fallback scores its results like its two siblings.** It took
  the translator's first result unscored and carried no `item_type`.
- **A placeholder DOI is no longer an identity two papers can share.** A `doi`
  field the DOI parser rejects — `n/a`, `-`, `TBD`, `10.xxxx/xxxxx` — fell back
  to its own lowercased text as an exact-match key, so every entry carrying the
  same filler matched every other: `pzi import` folded two unrelated papers into
  one entry and reported a duplicate skipped, and `fix dedupe` offered the same
  merge. Measured against a 22k-entry library, exactly one of 14,510 DOIs stops
  being an identity, and it is a stray file path.
- **`pzi fix dedupe` is roughly 38× faster on a large library.** The fuzzy pass
  rebuilt an N-element candidate list per record and re-tokenized every title N
  times, so a 22k-entry library spent about 33 minutes on pure recomputation
  before printing anything; the same run now takes under a minute. The answers
  are unchanged — an inverted index over title tokens supplies the intersection
  size directly, and Jaccard at any positive threshold requires a shared token.
- **A drain no longer mistakes an edited inbox for an appended one.** Lines
  written during the drain were detected by *line count*, so a concurrent writer
  that edited an existing line handed back the edited line as "new" and dropped
  the genuinely new one. The snapshot is now matched as a prefix; a file that was
  rewritten rather than appended to is reported and left alone instead of being
  overwritten with the drain's stale view.
- **A failed command always says why in `--json`.** `fix merge` reported each of
  its refusals as `status: error` with a `message` and an empty `errors[]` — the
  documented failure channel — so a consumer branching on it saw a failed command
  with nothing wrong. `fix merge` now populates it, and the shared envelope falls
  back to the message for any service that forgets.
- **`pzi doctor --reinstall-server` no longer deletes a translation-server
  directory pzi did not install.** The ownership sentinel was skipped whenever
  `force` was set, and `doctor` is the sole caller that sets it — so the one
  command a user runs to repair an install destroyed an unrelated checkout at
  the same path. `force` now means only what it says: reinstall even though the
  sentinel reports the install current.
- **`pzi fix reindex --rename-citekeys` confirms first and leaves a backup.** It
  rewrites every citekey in the library and breaks any `\cite{}` that used the
  old ones, with no undo — while `delete` and `fix merge`, which destroy far
  less, both prompt and write a `.bak`. It now prompts (or takes `--force`,
  refusing to prompt into a pipe) and copies the library under the lock
  immediately before the rewrite.
- **`pzi fix clean --fix` no longer quarantines a sibling library's PDFs.** The
  default layout points every configured bib at one `papers_dir`, so checking
  one library saw the others' PDFs as unreferenced and moved them into
  `.orphans/`, leaving `file =` fields dangling in a library the user never
  named. Libraries sharing the target's `papers_dir` now contribute their
  references, and one that cannot be read stops orphan detection rather than
  being treated as referencing nothing.
- **`pzi fix merge` keeps the survivor's `@string` references and field
  spelling.** It was the one write path that did not route the rebuilt block
  through `merge_preserving_unchanged_source`, so `journal = jmlr` was rewritten
  as the literal token `{jmlr}` — severing the macro reference while leaving the
  now-unreferenced `@string` behind — and `Title` was lowercased, on an entry the
  command was not asked to touch.
- **`pzi update --promote --replace` reports removals, backs up, and drops the
  arXiv DOI.** `changed_fields` iterated only the updated record, so a field the
  promotion *deleted* was applied and never named; the command overwrote an entry
  with a different paper's metadata leaving no `.bak`; and a preprint's
  `10.48550/arXiv.…` DOI was inherited by the published entry, labelling it as
  the version it had just stopped being.
- **A year the record model cannot represent is no longer deleted.**
  `NormalizedRecord.year` is an `int`, so `2020a` (the standard same-author
  disambiguator), `in press` and `{\noopsort{1997}}1997` parsed to `None`, were
  omitted from the projection, and were then removed from the entry — on every
  `add` onto an existing paper, every `update`, and every import onto a
  duplicate. A year the record *can* model is still cleared when the record
  clears it.
- **A field value ending in a backslash no longer destroys its entry.**
  `title = {Graph Networks\ }` — a legal LaTeX forced inter-word space — was read
  as `Graph Networks\` and written back as `{Graph Networks\}`, where the
  backslash escaped the writer's own closing brace. `pzi tag add` reported
  success and exited 0; the entry then vanished from every read and *every*
  later write to that library was refused. A trailing backslash run is now
  removed before the value is enclosed, however long the run is: bibtexparser's
  splitter looks only at the character before a `}`, so an even run broke the
  block exactly as an odd one did.
- **A field name that swallowed a `%` comment is refused instead of imported.**
  bibtexparser folds a comment inside an entry into the following field's key
  (`'% private note\n  doi'`), and `pzi import` carried it verbatim into the
  user's library: the `doi` was attached to a name no reader matches, and the
  library accepted no further writes. Field names are now checked where the
  citekey already was, and `pzi import` skips and reports such an entry — once,
  naming the entry and the field it hides, rather than counting it twice and
  reporting `imported 0/2` for a one-entry file.
- **The write gate now checks the parse *result*, not just that parsing did not
  raise.** bibtexparser v2 collects a block it cannot read in `failed_blocks`
  instead of raising, so serialized text that parsed back to zero entries and one
  failure passed the gate — the no-op that let every corrupting write through to
  disk. The gate now refuses unless there are no failed blocks, the entry count
  round-trips, and every entry reads back with the same type, citekey and fields.
- **A refusal to write reaches the user as an error, not a stack trace.**
  "malformed BibTeX: refusing to rewrite the file", the round-trip refusal, and a
  write plan invalidated by a concurrent edit were all raised as bare
  `ValueError`: exit 1, a Python traceback on stderr, and — under `--json` — zero
  bytes on stdout. They are now `PziError`s carrying exit code 5, and the CLI
  boundary catches `ValueError` as well so no future slip can empty the `--json`
  channel again.

- **A partial write can no longer install a truncated bibliography.** The atomic
  replace discarded `os.write`'s return value, so a short write committed
  whatever had made it to the temp file — verified by installing a library
  holding `@articl`. All bytes are now written or the original is left in place.
- **`tag`, `pdf attach/retry`, `update`, `promote` and `delete` now validate
  their result before writing it.** `update_bib_entry` and `delete_bib_entry`
  were the two of six write sinks that never ran the serialize→parse round-trip
  gate — and they are the paths behind exactly those commands.
- **A duplicate citekey is refused with a message instead of wedging the
  library.** Constructing a library with two entries under one key silently
  produced a `% WARNING Parsing failed` block, which was then written to disk;
  from there an entry vanished from `pzi entries`, `pzi export` refused, and the
  next edit raised a raw `ValueError` traceback.
- **`pzi add` into a bibliography containing an unparseable block now refuses**
  rather than re-emitting the block under a `% WARNING Parsing failed` header,
  accumulating a fresh marker on every subsequent add. Inserts are gated by the
  same parseability check updates always had.
- **A symlinked bibliography path now locks the file it writes.** The lock was
  named after the configured path while the write replaced the resolved path, so
  a symlink and its real path — or two symlinks to one bib — took two different
  locks over the same file.
- **A rewrite no longer changes the bibliography's permissions, line endings or
  byte-order mark.** Writes reset the file to `0600` (from `0644`), converted
  CRLF to LF (making a Windows-authored bib a 100%-changed file in git after one
  `pzi tag add`), and moved a BOM below the first entry.
- The lock-timeout message no longer suggests removing "a stale `<bib>.lock`":
  `flock` is released by the kernel on exit, so the file is never stale, and
  deleting it while a holder is live lets a second writer straight in.

- **A hand-written citekey is no longer silently renamed.** Every write ran the
  citekey sanitizer over *every* entry it rebuilt, including entries read
  verbatim off disk — so `pzi tag add 'Müller2020' readme` reported success and
  left the file holding `@article{Mller2020,`, breaking every
  `\cite{Müller2020}` in the user's LaTeX. Keys are now written back exactly as
  found; keys pzi *composes* are still sanitized where they enter, and
  serialization refuses (rather than rewrites) a key carrying a BibTeX
  delimiter.
- **Capitalized field names are no longer invisible.** BibTeX field names are
  case-insensitive, but pzi compared them case-sensitively, so a JabRef/IEEE
  style `Author =` / `Title =` / `Doi =` / `File =` reached none of the record
  model: `pzi add` inserted a second copy of a paper it should have deduped,
  an update wrote lowercase twins *beside* the capitalized originals (producing
  an entry bibtexparser then refuses), `fix clean --fix` quarantined a
  referenced PDF, and `pzi entries` rendered the entry blank.
- **A touched entry keeps its field order and its field-name capitalization.**
  Both were rewritten on every write, so a library slowly drifted into a mix of
  two conventions, one entry at a time.
- **LaTeX-escaped braces survive a write.** The brace balancer was escape-blind,
  so `note = {a \} b}` lost its `\}` whenever an unrelated field of the same
  entry changed.
- **A `%` comment inside an entry is reported instead of silently eating the
  next field.** bibtexparser folds the comment into the following field's *key*,
  which hides that field from the record model while still round-tripping
  cleanly — so the write gate saw nothing wrong. pzi now refuses to rewrite such
  a file and says which entry to fix.

- **`pzi import` no longer discards every field the record model does not
  carry.** `volume`, `pages`, `publisher`, `editor`, `series`, `isbn` and
  `crossref` were dropped and the run reported
  `{"status":"ok","imported":2,"errors":[]}`; losing `crossref` also broke the
  inheritance link to an `@proceedings` entry imported alongside it. The source
  entry is now carried through the write and the projection merged onto it.
- **`pzi fix merge` no longer destroys the dropped entry's unmodelled fields**,
  and writes a `.bak` inside the lock the way `delete` does. The dry run now
  names both the fields the survivor will take over and the ones it cannot
  (a conflict) — it previously returned a `NormalizedRecord`, which
  structurally cannot show `volume`/`pages`/`isbn`.
- **`pzi export --format bibtex` — billed as a backup — no longer drops
  `@preamble`, `@string`, `@comment` and `%` comments, or breaks macro
  concatenation** (`publisher = acm # { Press}` became the literal
  `{acm # { Press}}`). It re-serializes the parsed library instead of a
  projection of its entries.
- **`pzi export -o` writes all-or-nothing.** It truncated the destination
  before the replacement was complete, so a failed export could destroy the
  backup it was overwriting.
- `pzi import --json` now reports `dry_run`, as `add` and `update` do. A
  preview's `"imported": 0` was indistinguishable from a real run.

- **Storing a PDF no longer hangs on a dangling symlink.** `Path.exists()` is
  false for a broken symlink but `os.link` still refuses the name, so
  `write_pdf_bytes` kept choosing the same occupied path — ~16k iterations a
  second, forever, at 100% CPU. It wedged `pzi add`, `pzi pdf retry`,
  `pzi pdf attach` and any HTTP worker that reached them, and a papers
  directory holding a link into a moved or unmounted store is an ordinary way
  to organize a library. Occupancy is now decided with `lexists` and the
  collision search is bounded.
- **A corrupt, empty or encrypted PDF is no longer a traceback (CLI) or a 500
  (HTTP).** pypdf raises its own `PyPdfError` subclasses, none of which derive
  from `OSError` or `ValueError`; the page access was outside the guard as well,
  which is where an encrypted file actually fails.
- **`pzi fix reindex --rename-citekeys` no longer leaves a dangling `file =`
  when two entries share a PDF** — the second entry is repointed at where the
  first rename moved it, and the conflict is reported instead of silently
  skipped — **and no longer relocates PDFs stored outside `papers_dir`**
  (a citekey rename used to move `~/Documents/my-paper.pdf` into the library).
- **`pzi fix reindex --dry-run` applies the same tests as the real run**, so it
  no longer promises a rename the real run refuses because the destination is
  occupied.

- **`pzi init` no longer rotates the API auth token.** It minted a fresh token
  on every run, so running `init` for any reason — including against a throwaway
  `--config`, which `browser-extension/README.md` prescribes as a smoke test —
  silently un-paired the browser extension from the server. An existing token is
  reused and reported as reused; `pzi init --rotate-token` replaces it
  deliberately. An *unreadable* token file is an error, not a reason to mint a
  new one.
- **`pzi init` writes the token where the runtime reads it.** It always wrote to
  the XDG default while `resolve_api_auth_token` reads
  `<pzi_data_home>/api_token`, so setting that key left the token orphaned and
  the server fell back to `auth: DISABLED` while `init` reported success.
- **The Semantic Scholar API key is no longer sent to Crossref, OpenAlex, DBLP
  and OpenReview.** One shared metadata fetcher is handed to every provider, and
  it attached the `x-api-key` header to all of them.
- **A failing `*_cmd` secret command no longer echoes the command or its
  stderr** into CLI, `--json` or HTTP errors — those routinely name a password
  store or account, and the stderr can carry the secret itself. The message
  names the config key and the exit code.
- **`pzi doctor --reinstall-server` no longer destroys a working
  translation-server before the replacement exists.** The install is staged in
  `ts.new` and swapped in only after every step succeeds; a failed clone now
  leaves the existing install untouched. Automatic setup additionally refuses to
  delete a `ts/` directory pzi did not create.
- **`pzi doctor --config-only --reinstall-server` is a usage error** instead of
  silently running the network reinstall that `--config-only` excludes.
- **`pzi init --bib/--name/--papers-dir/--browser` without `--setup` is a usage
  error** instead of being accepted and dropped.
- **HTTP GET and binary routes now honour the configured-library gate.** Only
  the POST side checked it, so `GET /export?bib=/elsewhere/private.bib` and
  `/export/raw` returned the contents of a bibliography the config never
  declared.
- **HTTP booleans must be JSON booleans.** Flags were read with Python
  truthiness, so `{"dry_run": null}` authorized a real write and
  `{"replace": "false"}` selected replace mode. Anything that is not a boolean
  now falls back to the route's default, which for destructive routes is the
  preview.
- **`POST /attach-pdf-raw` requires a `request_id`**, so the TTL, size,
  source-URL and citekey checks `docs/security.md` presents as this route's
  control can no longer be skipped by omitting it. The JSON `/attach-pdf-bytes`
  fallback still accepts a sessionless upload — that is now documented rather
  than implied otherwise.
- **Path confinement now operates on the path it checked.** Both the local
  capture path and the inbox drain resolved the path, tested containment, then
  passed the caller's *unresolved* string to the service.
- **`embedded_pdf_url` is validated like every other URL field**, instead of
  reaching acquisition planning unchecked.
- **The browser extension shows the server's actual error.** Every pre-service
  rejection (invalid API token, origin not allowed, rate limit, unconfigured
  bib) answers with the singular `error` key, which the extension never read —
  the popup showed a bare `HTTP 401`.
- **Bulk capture no longer forwards the search page's cookies to every captured
  domain**; each capture sends cookies for its own URL.
- **Extension onboarding survives a browser restart** — the endpoint and token
  were stored in `chrome.storage.session`, which is cleared when the browser
  closes.

- **Malformed request framing is answered, not crashed through.** A chunked
  body was silently read as empty and processed as `{}` with a 200; an oversize
  body got a 413 while the client was still writing (surfacing as a broken pipe
  rather than the status just sent); and a deeply nested JSON body became a 500
  instead of a 400.
- **Playwright-backed routes are now held to the same URL policy as everything
  else.** `safe_http` protects what pzi fetches itself, but a browser page
  follows redirects and resolves DNS through the browser's own stack, so
  validating only the URL handed in left `/browser/discover` and
  `/browser/download` able to reach private and loopback addresses. Every
  browser request is now routed through the public-URL predicate, the landing
  URL is re-checked after navigation, and a direct fetch that redirects
  somewhere non-public returns nothing. *(Verified by unit tests against
  injected Playwright doubles; the real-browser suite needs Playwright binaries,
  which are not installed here.)*

- **A conference paper captured through the translation-server is now
  `@inproceedings` with a `booktitle`.** The translator reports `item_type` as a
  sibling of the record and the add path took only the record, so every such
  capture became `@article` with `journal = {proceedings title}` — and because
  Crossref, OpenAlex and DBLP *do* put the item type inside their records, the
  entry type silently depended on which provider answered. (Zotero's `webpage`
  item type is still treated as "unknown" rather than `@unpublished`: it is the
  translator's fallback, not a claim about the work.)
- **Filling a venue on an entry with neither `journal` nor `booktitle` now uses
  the entry type to choose.** `pzi update` on an `@inproceedings` wrote
  `journal = {NeurIPS}`.
- **`pzi update` has an acceptance gate.** It wrote metadata from the best
  candidate regardless of score — a candidate scoring −33 had its `venue`,
  `year` and `pdf_url` written in. `metadata_confidence_min_score` is now a
  write gate rather than a warning (default 0, so only negative-scoring
  candidates are refused), and a candidate whose DOI contradicts the entry's is
  refused outright unless the entry is a preprint.
- **`pzi update --promote` no longer creates duplicate published entries** for
  DOIs and titles differing only in case or whitespace (`10.1145/ABC` vs
  `10.1145/abc`). The write uses `force_new=True`, so nothing downstream caught
  it.
- **A percent-encoded `doi.org` URL is recognized as a DOI.** It was classified
  as a plain URL, so the entry was written with no identifier and never deduped.
- **A provider being unreachable no longer aborts the whole cascade.** Only
  `HTTPError` was absorbed, so a connection-refused translation-server ended DOI
  resolution with Crossref and OpenAlex sitting right behind it. A
  `ValueError`/`KeyError`/`TypeError` still propagates — that is a bug, not a
  provider outage.
- **`tag add/remove` and `promote --replace` no longer revert a concurrent
  edit.** Both computed the new entry before taking the lock and discarded the
  record the repository handed them under it, so another writer's corrections
  were silently written back to their old values at `status: ok`.

- **`pzi check` compares the year.** `README.md` and the module docstring have
  always advertised a title/author/**year** mismatch check and nothing ever
  compared it, so an entry claiming `year = {1999}` for a 2017 paper scored
  `verified, confidence 100, flags: []` in strict and loose mode alike. A wrong
  year is one of the commonest fingerprints of a hallucinated citation. A
  one-year gap is still fine — online-first and print years differ.
- **`pzi check` no longer hides entries the parser dropped.** A three-entry
  bibliography containing a duplicate citekey audited as `total: 1,
  verified: 1, problematic: 0, status: "ok"` at exit 0 — an unaudited entry
  inside a clean bill of health. The same read now reports what it could not
  read, in `check` and in `tag list`.
- **`pzi check` no longer suppresses defect evidence from a lower-scoring
  source.** Flags were taken from the best match alone, so a sparse title-only
  record that happened to score higher hid a Crossref record's `doi_mismatch`.
- **`pzi check` stops accusing correct entries.** Three separate causes:
  `Jan van der Berg` and `van der Berg, Jan` split to different family names
  (particles are now absorbed); `Mueller` and `Müller` did not match (umlauts
  are transliterated, not just stripped); and an entry listing 2 of a paper's 4
  authors scored 50 under symmetric Jaccard (author agreement is now
  containment — how much of the entry's claim the source confirms). Under
  `--strict` each of these failed CI on a correct bibliography.
- **An unindexed-but-real reference is `could_not_verify`, not
  `problematic`.** The by-title fetchers ask for one row and return the top hit
  with no relevance gate, and Crossref always answers — so a workshop paper a
  source does not index came back as a *different* paper and the tool called a
  genuine citation fabricated.
- **`pzi check --report PATH` records the same status as the run.** It wrote
  `"status": "ok"` for a run whose stdout envelope said `"error"` and which
  exited 5 — and the report file is the artifact CI archives.

- **Multi-target `--json` output is built once, by a shared merge.** Each
  command hand-built its own envelope, which is what dropped the keys:
  `search --json` lost the partial-parse `warnings` text mode prints, and
  `update --promote --json` lost `summary`, where `provider_errors` live.
- **A failing `--target` is named.** "search failed" / "update failed" with
  several targets left the user to work out which library it was.
- **`POST /update` no longer answers 200 `{"status":"ok","errors":[]}` when
  every item failed.** The status and the errors are derived from the item
  outcomes, so the HTTP route and the CLI read one verdict. (A *partial*
  failure stays `ok` and is reported through `errors`, which the CLI turns into
  exit 4.)
- **`update --promote` can report a partly-failed run.** `PromoteItem` had no
  `failed` key, so `exit_codes.PARTIAL` was unreachable and a run where every
  promotion raised exited 0 with `errors: []`.
- **`fix clean --fix` no longer renders a *failed* quarantine move as
  `would do`** — the dry-run wording — with `errors: []` and `status: "ok"`.
- **`add --from-file --json` says why an item failed.** `first_error` was given
  the whole result mapping, which always returns `None`, so the documented
  failure channel was the literal string `failed` for every item.
- **Every PDF fallback stage contributes a failure reason.** Only the direct
  stage did, so a broken `browser_pdf_cmd`, a FlareSolverr failure, or the
  server browser returning HTML were stderr-only — under `--json` the operator
  could not tell which stage broke, or whether it ran.
- **`pdf retry`/`attach` surface the chain's warning**, including FlareSolverr's
  "may violate publisher terms of service" notice. `pzi add` always did, so the
  same acquisition reported differently depending on the command.
- **A Semantic Scholar rate-limit, quota or auth failure is reported as an
  error** rather than as "no such paper". S2 answers those with HTTP 200 and an
  `error` key, so `--strict-metadata` never fired on them.
- **The `/attach-pdf-bytes` fallback inherits the capture's library** instead of
  sending the popup's `null` and 403-ing, which discarded an
  already-downloaded PDF.
- **`pzi search --tag` refuses a tag that normalizes to nothing** instead of
  dropping the filter and returning every entry, and compares normalized tags on
  both sides.
- **`fix clean` scans for orphan PDFs even when the bibliography is empty** —
  the case where *every* stored PDF is an orphan was the one it skipped.
- **`fix dedupe` reports one cluster per connected component**, instead of
  repeating a pair once per shared identity and splitting a transitive
  three-way duplicate in two.
- **CSV export neutralizes cells a spreadsheet would evaluate as a formula.**

- **Flags that were accepted and ignored are now refused or honoured.**
  `entries CITEKEY --stats` discarded the citekey and printed library-wide
  statistics; `add --delay`/`--failures-out` were silently ignored outside
  `--from-file` mode; `init`'s library flags without `--setup` and
  `doctor --config-only --reinstall-server` are covered above.
- **`--config` may precede the subcommand.** `pzi --config X entries` failed
  with `argument command: invalid choice: '/path.toml'`.
- **A nonexistent direct `--target` path is an environment error (exit 5)**, as
  `README.md` promises, instead of reading as an empty library at exit 0.
- **`-` means stdout for `check --report` and `export -o`**, as it already does
  in six other places; both used to create a file literally named `-`.
- **`entries --offset` past the end reports the total** instead of a bare
  `(no entries)` indistinguishable from an empty library.
- **`entries --json` reports the real entry type.** It read `entry_type` off a
  normalized record, which never carries one, so every entry was `"unknown"`.
- **`pzi add --force-new` exists.** The capture path has always read the flag
  and the browser extension exposes it, but it was registered only on `import`.
- **An explicitly empty `desktop_fallback_hosts = []` is honoured** rather than
  re-expanded to the built-in host list.
- **Unknown config keys are reported.** A typo in `capture_source_dirs` or
  `pdf_file_path_style` silently reverted to the default; `pzi doctor` now says
  so. Still a warning, not an error, so a config written for a newer pzi loads.
- **`update --promote --replace --dry-run` previews the in-place rewrite it
  performs**, not a duplicate insert — it told the user their original entry
  would survive when `--replace` overwrites it. **Keep mode previews both
  writes** (the new entry *and* the cross-reference note stamped on the
  preprint) and diffs `changed_fields` against the preprint rather than the
  candidate.

### Documentation

- `README.md` no longer claims *every* command accepts `--json`: `inbox`,
  `export`, `server` and `init` do not, and `inbox` — a batch command with
  per-item outcomes and a partial-failure exit code — is the real gap.
- `browser-extension/README.md`'s smoke test no longer breaks your setup (see
  the `pzi init` fix above), and points at `<data-home>/api_token` rather than
  an `api_auth_token` config key that no longer exists there.
- `docs/security.md` recommends the auto-provisioned token file rather than the
  plaintext config key, and discloses that the Semantic Scholar key used to
  reach every metadata provider.
- `config.template.toml` documents `semantic_scholar_api_key`, the only config
  key that was missing from it.

### Packaging

- The release workflow verifies that the tag, `pyproject.toml`'s version and
  the `CHANGELOG.md` section agree, and runs the tests and linter, before
  building. A `v*` tag used to publish whatever it pointed at.
- CI and the release both install from the committed `uv.lock`, which nothing
  consumed before.
- The sdist ships `packaging/systemd/pzi.service`, which `README.md` tells the
  user to copy, and the build requires `setuptools>=77` for the PEP 639 license
  metadata this project uses.
- Both browser-extension ZIPs ship the AGPL `LICENSE`.
- `tools/publish_smoke.py` no longer invokes a nonexistent `pzi add --title`
  (it exited 2 on every run) and sandboxes `XDG_DATA_HOME`, so a release check
  cannot rotate the developer's own API token.

### Removed

- Dead code with no behaviour attached: the `_pdf_result_fields` alias,
  `InboxLine.raw`, `AttachSession.created_at`, `browser_session_manager`'s
  `_last_used` (the vestige of an idle-close that was never implemented), the
  `discover_pdf_url(doi=)` parameter that silently discarded the extension's
  hint, the `pdf_candidates` payload the server never read, a duplicate test
  module, and a permanently-skipped empty test (replaced by a real one).

### Known limitations

- A bibliography with more than one hard link keeps only the written name
  pointing at the new content. This is inherent to replace-based atomic writes.
- The first write to a `.bib` reformats the whole file (indentation, whitespace,
  entry-type case). It is a one-time normalization and stable thereafter; the
  README section on external `.bib` files documents exactly what is preserved.

## [0.1.0b4] - 2026-07-29

### Added

- **`pzi pdf retry` and `pzi pdf attach` now run the full PDF fallback chain** —
  direct, server browser, `browser_pdf_cmd` hook, FlareSolverr, then the
  desktop-download watcher — the same one `pzi add` has always used. They
  previously made a single direct request while their failure message told you
  to "configure `browser_pdf_cmd`", machinery that code path never invoked.

- **`pzi check` now detects two more fabricated-citation signals.** Both were
  already half-wired: `doi_mismatch` was listed among the flags that make an
  entry `problematic`, but nothing ever emitted it because `score_match` never
  compared DOIs; and `classify_given_pair` existed with no caller.
  - **Contradicting DOIs** are flagged and penalized. Only when *both* records
    carry a DOI and they disagree — an absent DOI is sparse data, not evidence.
    Preprint entries are exempt, because an arXiv DOI legitimately differs from
    the published one and `pzi update --promote` scores exactly that pairing
    with the same function.
  - **Given-name substitution** is flagged: an author whose surname matches but
    whose first name does not. Author comparison was surname-only, so
    "Yao, Denny" scored identically to "Yao, Shunyu" — the fingerprint of a
    citation that borrows a real surname. Initials, added middle names, and
    transliteration differences are still treated as the same person.

### Changed

- **`pzi pdf retry <citekey> --failed-only` is now a usage error (exit 2).** The
  citekey was previously accepted and discarded; that was documented, but doing
  something other than what was typed is worse than refusing.
- **`pzi entries --limit 0` is now a usage error** instead of being silently
  clamped to 1 and reported back as `"limit": 1`. The 500 ceiling is documented
  in `--limit`'s help. `--offset 0` is unchanged and still valid.
- **Writes parse the `.bib` once instead of twice** (a `--dry-run` followed by a
  write parsed it four times). No behaviour change; the round-trip validation
  now covers the exact entry list that gets serialized.

### Fixed

- **`add --from-file` no longer calls a partly-successful batch an error.** A
  batch of "1 added, 1 failed" reported `"status": "error"` with an empty
  `errors` list, contradicting its own exit code — `4` means *partly* failed, so
  a script reading `.status` classified a half-successful capture as a total
  failure. It now reports `"ok"` with the per-item reasons in `errors`, matching
  `import`; only a batch that captured nothing at all is an error.

- **Boolean environment variables no longer invert.** `bool("0")` is `True`, so
  `PZI_SKIP_BROWSER_HOOK=0` *enabled* the skip — as did
  `PZI_DISABLE_DESKTOP_BROWSER_FALLBACK=0` and `PZI_SKIP_AUTO_START=0`, despite
  the README documenting "set to `1`". `0`, `false`, `no`, `off` and the empty
  string now read as off.
- **RIS export emitted every URL twice** (`canonical_url` and `source_url` are
  both filled from the single BibTeX `url` field) and broke on multiline
  abstracts: RIS has no continuation syntax, so a wrapped abstract emitted bare
  untagged lines that strict readers drop, mis-assign, or — when a line happens
  to begin with two characters and `  - ` — reparse as a new field.
- **Old-style arXiv IDs with a dotted subject class** (`math.GT/0309136`) were
  not recognized and lost their DOI mapping; **publisher display paths**
  (`/doi/abs/`, `/doi/full/`, `/doi/pdf/`, `/doi/epdf/`) yielded no DOI at all.
- **`is_ts_reachable` leaked a response object** on every 30-second watchdog
  probe, as did the health-check poll beside it.
- `--target` failures now report "no matching library target found or selection
  is ambiguous" rather than "bib not found", and no longer drop the per-line
  config errors.

- **A duplicate citekey no longer silently drops an entry.** bibtexparser keeps
  only the first block of a repeated key and files the rest as parse failures,
  which every reader built on `read_bib_file` discarded. `pzi entries` reported
  "1-1 of 1 entries" for a two-entry file, and `pzi fix dedupe` — the command
  for finding duplicates — reported zero clusters. `entries` (list, detail and
  `--stats`), `search` and `fix dedupe` now name the dropped entry and its line
  while still showing what they could read.
- **`fix clean`'s `duplicate_citekeys` finally reports duplicates.** The field
  has existed since June and could never be non-empty, because it counted
  repeated keys among entries the parser had already de-duplicated. A duplicate
  is now a finding (exit 1) rather than a refusal, and an unparseable block
  still fails. **Orphan-PDF detection is skipped whenever the parser dropped
  anything**: a dropped entry contributes no referenced path, so its PDF looked
  orphaned and `--fix` would have quarantined a file the library still refers to.
- **`fix clean --json` populates `errors`** on a failure, instead of reporting
  `"status": "error"` with an empty list and the detail stranded in `issues[]`.
- **Five command runners returned 1 for genuine failures**, which the exit-code
  contract reserves for "ran fine, has something to report". `fix clean` and
  `export` now exit 5 when they cannot read the library; `export` and `init`
  exit 2 when refusing to overwrite without `--force`; `server` exits 5 when the
  auth token or server plan cannot be resolved. **`export --output <existing>`
  without `--force` changes from 1 to 2.**
- **A raw `Unknown block type <class '...DuplicateBlockKeyBlock'>` line** no
  longer reaches the terminal. It is bibtexparser's own logger, printed by
  Python's fallback handler because pzi configured no logging.
- The refusal to rewrite a malformed bib now names the duplicate citekey and its
  real line number; it previously reported a 0-based index, so a duplicate on
  line 4 was described as "around line 3".

### Removed

- **`pzi/__init__.py` no longer re-exports ten names** — `BibtexEntry`,
  `HttpSecurityConfig`, `NormalizedRecord`, `WritePlan`,
  `build_http_security_config`, `merge_entries`, `parse_bibtex`,
  `plan_bib_write`, `safe_public_http_url`, and `serialize_bibtex`. **Breaking
  for anyone importing `pzi` as a library**: import from the defining module
  instead (`from pzi.bib_repository import plan_bib_write`), which is what the
  codebase itself already does. `cli_version_text` and `package_version` stay.
  Nothing in `pzi` imported the removed names through the package root.

### Security

- **`pzi init` now provisions the API auth token on the plain path too**, not
  only under `--setup`. The template it copies states that "`pzi init` writes a
  token to `<data-home>/api_token` (0600)", so the default path shipped a config
  asserting a file it had never created, and the server started with
  `auth: DISABLED`. The token is written 0600 and auto-read at runtime, so
  `config.toml` still holds no secret. `docs/security.md` no longer describes the
  token as "None (optional)" or "stored in plain text in `config.toml`" — both
  predated the `api_token`-file design.
- **An explicit empty `api_allowed_origins = []` now means "no cross-origin
  requests"**, instead of silently reverting to the permissive defaults
  (`chrome-extension://`, `moz-extension://`, localhost). The default was
  re-expanded at two layers — config normalization and
  `build_http_security_config` — so writing `[]` to lock the API down had the
  opposite of the intended effect. Omitting the key still selects the defaults.

- **`POST /capture` no longer accepts arbitrary local filesystem paths.** Its
  SSRF check was conditional on the value having a URL scheme, so a bare path
  skipped it entirely: the server would read the file (sending extracted text to
  metadata providers) and copy any readable PDF into `papers_dir`, from where
  `GET /pdf/<citekey>` serves it — laundering around the confinement that route
  applies. Local paths are now accepted only from directories listed in the new
  **`capture_source_dirs`** config key, which is **empty by default**, so this is
  refused over HTTP until you opt in. `pzi add /path/to.pdf` on the CLI is
  unchanged.

- **`POST /inbox/drain` no longer rewrites arbitrary files.** It took any
  client-named `file`, then read it, created a `.lock` and parent directories
  beside it, and atomically rewrote it in place — following symlinks. It now
  drains only the new **`inbox_path`** config key and refuses anything else; with
  the key unset the route is closed. Its `delay` is also bounded, defaults to the
  CLI's `1.0` rather than `0`, and rejects non-numeric or negative values instead
  of silently coercing them to zero.

  Both routes are reachable from a browser-originated request and the API
  requires no auth token by default, so they sat on a different trust boundary
  than the "local machine is trusted" model `docs/security.md` describes. That
  document now records both controls and the qualification.

### Fixed

- **A held bib lock no longer hangs pzi forever.** `with_bib_lock` called
  `portalocker.lock`, which takes no timeout and blocks in the kernel;
  `ConcurrentEditError` only fires *after* the lock is acquired, so nothing could
  produce the exit 5 that `exit_codes` documents for "a locked or externally
  modified bib". Waits now time out after 300s — well above any legitimate hold,
  since a batch import keeps the lock for the whole run — and report exit 5
  naming the bib. `inbox_service` had the same blocking pattern and gets the same
  treatment. `README.md`'s exit-code table now matches `exit_codes.py`.
- **`pzi delete`'s backup is taken under the exclusive lock** that guards the
  write, rather than before it. Another writer could rewrite the bib in that
  window, making the `.bak` the command advertises as the undo artifact a
  snapshot of a version that no longer existed — restoring it would have reverted
  that writer's work too. The backup is also no longer written when the citekey
  is absent, so a no-op delete leaves no stray file.
- **Five optional string config keys reject a wrong type instead of discarding
  it**: `flaresolverr_url`, `api_url`, `browser_pdf_cmd`, `node_path`, and
  `browser_profile_path`. A non-string was indistinguishable from unset, so
  `node_path = 22` took exactly the silent fallback that the template, `README`,
  and this changelog all disclaim ("a set-but-broken value is a hard error").
  `api_url` and `flaresolverr_url` are additionally checked for an http(s)
  scheme; a schemeless `flaresolverr_url` used to be nulled without a word,
  leaving the user who had configured it told to configure it.
- **`desktop_fallback_hosts = []` now means "no host needs the desktop
  fallback"** rather than re-expanding the default host list, which happened at
  three separate layers. An explicit empty list also round-trips through
  `dump_app_config`, which previously dropped it as falsy.


- **The HTTP server no longer drops the connection on an unexpected error.**
  `BaseHTTPRequestHandler` catches only `TimeoutError`, so any other exception
  closed the socket having sent zero bytes — indistinguishable from the server
  not running. Unhandled failures now return `500` with a JSON body. The
  exception text is not sent to the client; the traceback still goes to stderr.

- **Percent-encoded citekeys resolve.** `/pdf/`, `/detail/` and `/tags/` never
  decoded their path segment, and the browser extension builds them with
  `encodeURIComponent`, which escapes `:` to `%3A` — so every citekey containing
  a colon or non-ASCII character 404'd.

- **PDF route errors carry a body.** The three error exits sent a bare status,
  discarding the JSON error already built and omitting the CORS headers, so a
  cross-origin caller could not read even the status.

- **`pzi server --port 0` and `--stop-after 0` are rejected** instead of doing
  something surprising. `--port 0` was swallowed by a falsiness test and replaced
  by the configured port — except when the config failed to load, where it bound
  an ephemeral port nothing reports back. `--port 99999` died with an
  `OverflowError` traceback rather than a usage error. `--stop-after 0` made the
  idle watchdog shut the server down at its first 30s poll regardless of traffic;
  omitting the flag already means "never stop".

### Changed

- **`--json` now really does emit one envelope on every failure.** A previous
  release announced this for `add`, `tag` and `check`; those three, and a dozen
  other sites, still did not conform. Two shapes of violation:

  - **Emitted nothing at all.** `entries <citekey>`, `search` with no criteria,
    `update --replace` without `--promote`, `delete` on a non-tty, `pdf retry`
    with no citekey, `import` with a missing source, `add --from-file` with an
    unreadable list, and — via `resolve_target` raising at the top of seven
    runners — *any* command given an unknown `--target`. Scripts had to scrape
    stderr to classify exactly the failures the contract says they never do.
  - **Emitted the wrong shape.** `tag add`/`tag remove`, `add`'s error path and
    `check`'s error path used the raw serializer, so their documents had no
    `command` and no `.items[]`. `add --from-file` hand-built its own document
    and carried only one of the five keys.

  The `PziError` family is now handled once at the CLI boundary rather than in
  each runner, so every present and future one is covered. `cli_json.emit` is
  private (`_emit`): after this sweep it had no legitimate caller, and making
  the envelope the only way out is what stops the next bypass.

- **`pzi doctor --json` no longer contradicts its own exit code.** `status` was
  hardcoded `"ok"` whenever the config merely loaded, while the exit code
  consulted four signals — so a missing bib file, an unreachable
  translation-server or a broken `semantic_scholar_api_key_cmd` all produced
  `"status": "ok"` on a run that exited 5. The health verdict now lives in one
  place and both read it. The envelope's `errors` list is also populated: doctor
  only ever filled `config_errors`, so the documented failure channel was empty
  even on a hard config failure. **`doctor --config-only` honours `--json` at
  all** — on a *passing* run it used to write `config valid: …` to stdout,
  breaking `| jq`.

- **`pzi check --jsonl - --json` is rejected as a usage error (exit 2).** Both
  write to stdout, so the combination produced an NDJSON stream followed by a
  pretty-printed document — neither valid NDJSON nor the single document
  `--json` promises. `check` also no longer reports `"status": "ok"` on a run
  that reached no metadata source and exits 5.

- **Eleven commands now return the exit code they document.** A previous release
  announced that exit codes have one meaning each; these sites never conformed,
  so the guarantee that `1` means "ran fine and has something to report" — and
  never "failed" — did not actually hold. **Scripts that branch on a specific
  code need re-checking; scripts that branch on non-zero are unaffected.**

  | Command | Was | Now |
  |---|---|---|
  | `add --from-file` / `inbox` with some items failed | 1 | 4 |
  | `add` / `inbox` backend not ready, unreadable input file | 1 | 5 |
  | `pdf retry` / `pdf attach` unknown citekey | 5 | 3 |
  | `pdf retry --failed-only` with failures, text mode | 0 | 4 |
  | `fix merge` unknown citekey | 5 | 3 |
  | `tag list` unknown citekey / bad `--target` | 1 | 3 / 5 |
  | `doctor --reinstall-server` failures | 1 | 5 |
  | `import` missing source file | 3 | 5 |
  | `import` unloadable config or unknown `--target` | 4 | 5 |
  | `update` where a record failed | 0 | 4 |
  | `fix reindex` audit that found renames to make | 0 | 1 |

  Two of these were format-dependent rather than merely wrong:
  `pdf retry --failed-only` returned 4 with `--json` and 0 without it for the
  same result, and `fix merge` computed its code separately in each branch.
  Both now share one mapper.

  Root causes, both fixed: `pdf_service` and `dedupe_service` never set the
  structured `reason` field that four runners already branch on, so every
  not-found fell through to the "could not run" default; and `update` recorded
  per-record failures only as free text in each item's `note`, which nothing
  read — so a run in which every record failed still reported success.
  `update` also swallowed `ConcurrentEditError`, the one condition every other
  command reports as 5; it now propagates, because continuing to write after
  losing that race is not safe.

  `import` with an unloadable config or unknown `--target` previously fanned the
  failure out into one error per record, reporting a partly-failed batch when in
  fact nothing had been attempted.

### Added

- **`pzi add` now warns when it inserts a probable duplicate.** A capture with
  no DOI and no arXiv id cannot exact-match an existing entry, so it inserts a
  second one; the fuzzy near-duplicate match was recorded only in the new
  entry's `note` field, which meant the terminal output looked identical to a
  clean capture. It now also prints ``warning: possibly a duplicate of
  <citekey> — compare them with `pzi fix dedupe` `` to stderr, and carries it
  in the `warnings` list of `--json` output. Applies to single and bulk
  (`--from-file`, `inbox`) capture alike.

### Fixed

- **`pzi fix dedupe` never reported a single fuzzy near-duplicate.**
  `find_duplicates` passed each record inside its own candidate corpus, but
  `compute_similarity_hint` returns only the single *best* match and a record
  always scores highest against itself (identical title tokens, full author
  overlap). The self-match therefore won every time and was discarded by the
  `hint != citekey` guard immediately after, so the fuzzy pass was silent for
  every library — the exact-identity pass (DOI / arXiv id / canonical URL) was
  doing all the work. Near-duplicates that carry *different* DOIs, which is
  exactly what the fuzzy pass exists to catch, were invisible. The record is
  now excluded from its own corpus, and a pair is reported once rather than
  once per direction. No test covered `fuzzy_candidates` at all, which is why a
  green suite never caught it.
- **`pzi fix dedupe` exited 0 when its only finding was fuzzy.** The exit code
  was derived from `total_clusters`, which counts exact clusters only. Per the
  documented vocabulary, `1` means "ran fine, has something to report", so a
  library whose sole finding is a near-duplicate now exits `1`. Scripts
  branching on the exit code will see runs flip from `0` to `1` once real
  near-duplicates are detected. `total_clusters` keeps its exact-only meaning.
- **The layering guard could not see `from pzi import <module>`.**
  `tests/test_layer_boundaries.py` parsed `import pzi.x`, `from pzi.x import y`
  and both relative forms, but package-level imports fell through its
  `startswith("pzi.")` check, so 22 import edges — including
  `commands/init.py` → `setup_service` and `errors.py` → `exit_codes`, which
  are written *only* that way — were invisible to all five architectural
  checks. Every one of them was legal, which is why nothing ever failed; a
  future core-module import of a front-end would have passed just as quietly.
  The extractor now reads that form, and the graph drops candidate names that
  are re-exported functions rather than modules.
- **`update --promote` carried a stale confidence-threshold fallback.**
  `promote_service` read `config.get("promote_confidence_threshold", 3)`, a
  leftover from before the move to `score_match`'s 0-100 scale, where the
  documented default is 60 — on the current scale a threshold of 3 is
  effectively no gate at all. It was unreachable (`AppConfig` is a total
  `TypedDict`, so the loader always supplies the key, and a config omitting it
  resolves to 60), so no run was ever affected; the fallback is now gone in
  favour of a plain subscript, and the default itself is a named constant
  (`config.DEFAULT_PROMOTE_CONFIDENCE_THRESHOLD`) rather than the literal `60`
  repeated across the loader and the validator.
- **`NormalizedRecord.similarity_hint` was declared but never written.** The
  fuzzy dedup hint went into `note` prose only. The field now carries the
  matched citekey, which is what the new duplicate warning reads. It is not
  serialized into BibTeX (`record_to_bibtex_entry` projects an explicit
  allowlist), so `.bib` output is unchanged apart from the existing note.

## [0.1.0b3] - 2026-07-26

### Changed

- **Internal: `config.load_and_resolve_bib` is now `config.load_bib_target`**
  and returns a `BibResolutionFailure` (carrying a structured `reason`) instead
  of a bare list of error strings. Affects anything importing it directly.


- **`--json` output is now one envelope for every command**:
  `{command, status, bib_name, items, errors}`, with command-specific fields
  (`imported`, `dry_run`, `total`, …) alongside. Previously there were four
  shapes — `search` emitted a bare array of per-library objects,
  `entries` an envelope, `entries <citekey>` a bare record, and
  `add --from-file` an unlabelled summary — so every consumer needed its own jq
  path. `.items[]` now works everywhere.
- **`authors` is always a list.** `pzi entries --json` emitted a single
  joined string while `export --format json` emitted a list, so the same field
  had two types depending on which command produced it.
- **`pzi search --json` with several `--target` values emits one document**
  instead of one object per library; each item carries its own `bib_name`.
- **`pzi doctor` prints a human-readable summary by default** (`--json` for the
  machine dump) and **exits non-zero when a probe fails** — it used to print
  raw JSON to a human and exit 0 with an unreachable translation-server, which
  made it useless as a health gate.

- **Exit codes now have one meaning each**, documented in the README and
  `pzi --help`. `1` is reserved for "ran successfully and has something to
  report" (no search matches, duplicates found, integrity issues, unverified
  citations), so a command that *could not run* never exits `1` — that is `5`
  (bad config, unknown `--target`, locked bib, permission denied). `2` stays
  usage, `3` is a missing entry, `4` is a partly-failed batch. Scripts that
  treated any non-zero as failure still work; scripts that treated `1` as
  failure need updating.
- **`pzi search` with no matches exits `1` and writes nothing to stdout** (it
  printed `no matches` on stdout and exited `0`). The note moved to stderr.
- **`pzi entries` writes five fixed tab-separated columns** — citekey, year,
  title, authors, and `pdf` when a PDF is attached. The PDF marker used to be
  appended to the authors column without a separator, so `awk -F'\t'` read it as
  part of an author's name. The authors column is also no longer omitted when
  empty, so the column count is stable.
- **`pzi delete` refuses to prompt when stdin is not a terminal**, exiting `2`
  instead of reading EOF, cancelling, and reporting success.
- **`--json` now also emits JSON when the command fails** (`add`, `tag`,
  `check`), instead of printing prose to stderr and no JSON at all.

- **`promote_confidence_threshold` is now a 0-100 match score; the default moves
  from `3` to `60`.** The promotion gate used to score a handful of coarse
  features (title exact/substring, capped author overlap, year proximity) on an
  implicit 0-6 scale, while the explainable 0-100 `score_match` breakdown was
  computed *after* the gate and only decorated the diagnostics — so `pzi update
  --promote` could print `match confidence 0/100 … title_mismatch` and write the
  entry anyway. Both the gate and candidate selection now use the same 0-100
  score. **A `promote_confidence_threshold` set in `config.toml` must be
  restated on the new scale**; values above 100 are now rejected at config
  validation.

### Added

- **`pzi check --jsonl -`** streams one JSON object per entry to stdout (the
  flag previously accepted only a file path); the human table is suppressed so
  the stream stays parseable.
- **`--json` on every command that reports a result**, including the mutating
  ones: `update` (and `update --promote`), `delete`, `import`, `pdf
  attach|retry`, `fix merge`, `fix reindex`, `tag add|remove`, and `doctor`.

- **`PZI_CONFIG`** sets the config file path; `--config` still takes precedence.
- **`pzi import -`** reads BibTeX from stdin, so
  `pzi export --target a | pzi import - --target b` needs no temp file.
- **`--json` on `pzi tag add` and `pzi tag remove`** (previously only `tag list`).

### Security

- **The HTTP API no longer accepts an arbitrary `.bib` path in `bib`.** A direct
  path is a CLI convenience; over the API it let any request that reached it
  make pzi create and write a library anywhere the user can write. Requests are
  confined to libraries declared in `config.toml` (by name or configured path).
- **Binding to a wildcard address (`0.0.0.0`, `::`) is refused.** It started the
  server and then rejected every request, because the Host check that guards
  against DNS rebinding had no bind address to match. Bind to a specific address.
- **`pzi server` states whether auth is enabled at startup.** A token resolves
  from the data home, so a differing `XDG_DATA_HOME` between `pzi init` and the
  server silently yielded no token and served the API unauthenticated.
- **`pzi init` creates `config.toml` with mode `0600`** — it may hold a plaintext
  `api_auth_token` and `*_cmd` hooks that pzi executes.
- **The extension's bot-bypass allowlist now matches on a domain boundary.**
  `evil-nature.com` cleared an allowlist entry of `nature.com`.

### Fixed

- **The browser extension is installable again.** The manifest carried the PEP
  440 project version verbatim (`0.1.0b2`), which Chrome and AMO reject, so the
  v0.1.0b2 zips could not be installed. The version is translated to
  dot-separated integers, with pre-releases ordered below the final release.
- **DOM-based PDF discovery never ran.** The injected function referenced a
  service-worker module name that does not exist in the page, so it threw and
  the failure was swallowed.
- **A page with many PDF links no longer fails the whole capture.** Candidates
  are capped client-side at the limit the server enforces.
- **Re-capturing a paper with a differently-cased or trailing-slash DOI no
  longer creates a duplicate entry.** DOI identities are canonicalized before
  comparison.

- **`pzi fix reindex` renamed the wrong PDF.** The file to rename was derived
  from the old citekey rather than read from the entry, so an unrelated
  `<old_citekey>.pdf` sitting in `papers_dir` was renamed onto the entry while
  the entry's real PDF was left orphaned. The move now comes from the entry's
  own `file =` field, and `--dry-run` prints both paths so a wrong move is
  visible before it happens.
- **`pzi fix reindex` could leave `file =` fields dangling.** PDFs were renamed
  before the `.bib` was written, under a lock that was released in between; if
  the write failed (a concurrent `pzi add` changing the entry count is enough),
  the renames stood while the bib still pointed at the old paths. Renames and
  the write now share one exclusive lock, and a failed write undoes every
  rename.
- **`pzi fix reindex` silently replaced a PDF already at the target path.**
  `os.rename` overwrites its destination; the rename is now refused and reported
  as an error instead.
- **`pzi fix clean` reported quarantined files as orphans forever.** The orphan
  scan descended into `papers_dir/.orphans`, so after the first `--fix` run the
  plain audit exited non-zero permanently.
- **`pzi fix clean --fix` destroyed archived PDFs on a name collision.** A later
  orphan sharing a basename with one already quarantined overwrote it. The
  quarantine directory is an archive: a taken name now gets a numbered suffix
  (`stale-1.pdf`).
- **Conference papers were written with `journal` instead of `booktitle`.**
  Every insert path (`add`, `import`, capture, promote) projected the venue as
  `journal` regardless of entry type, producing `@inproceedings` entries no
  booktitle-requiring citation style can format.
- **`pzi import` retyped every entry as `@article`.** The source entry type was
  read and then dropped before the write, so an imported `@inproceedings`,
  `@incollection`, or `@book` lost its type.
- A PDF download cut short mid-transfer is no longer stored as the paper. Two
  cases: a body with a known `Content-Length` that stops early was completely
  silent (`HTTPResponse.read(amt)` clips to the bytes remaining and returns
  short instead of raising), and a chunked body cut short raised
  `http.client.IncompleteRead`, which derives from neither `OSError` nor
  `ValueError` and so escaped the download error handler as a raw traceback out
  of `pzi add` / `pzi pdf retry`. Reads now reconcile against `Content-Length`
  (skipped when a `Content-Encoding` makes the comparison meaningless), and the
  download path reports both cases as a normal download failure, leaving the
  fallback chain to try the next source.

- **Entry mutations no longer delete BibTeX fields pzi does not model.**
  `pzi tag add/remove`, `pzi update`, and `pzi add` on an entry already in the
  library regenerated the whole `@entry{}` block from the internal record model,
  which carries ~13 field names — so `volume`, `pages`, `publisher`, `editor`,
  `isbn`, `series`, `number`, `month` and any custom field were silently dropped
  on every touch, and `booktitle` was rewritten as `journal` (turning a
  conference paper into a malformed `@article`-shaped entry). The operation
  reported success while destroying data. Updates now merge onto the entry as it
  exists on disk: fields the record model owns are rewritten from the record,
  the entry's type is preserved, and everything else is left byte-for-byte
  alone. Found by the 2026-07-25 audit. Covers `pzi tag`, `pzi update`,
  `pzi add` on an existing entry, `pzi pdf attach`, `pzi pdf retry`
  (including `--failed-only` and the extension's attach route), and
  `pzi fix dedupe --merge`, and now also `pzi update --promote` (see below).

- **`pzi update --promote --keep-preprint` overwrote the preprint instead of
  adding the published version.** The merged published record inherits the
  preprint's `url`, and no metadata provider emits `canonical_url`, so the
  intended insert identity-matched the preprint itself and became an in-place
  update of it — reporting `action=create` with a `published_citekey` that
  existed nowhere in the file, and leaving a dangling
  `note = {Published version: …}`. `--dry-run` previewed the same wrong outcome.
  The published entry is now planned as an unconditional insert, and identity
  that belongs to the preprint (its arXiv/bioRxiv/etc. URL, alongside the
  `arxiv_id` already dropped) no longer carries over to the published record —
  which also stops the published entry from being typed `@unpublished`.

- **A promotion could half-apply, committing an entry behind a reported
  failure.** Keep-mode committed the new entry first and stamped the two
  cross-reference notes after, and only the note path refuses to patch a library
  containing a malformed block — so a single broken entry anywhere in the `.bib`
  left the published entry committed with a `file =` pointing at the PDF the
  rollback had just deleted, while the run reported `created 0,
  skipped_failed 1`. No fault injection needed. The insert and the preprint's
  note are now one atomic batch write, which also refuses up front to patch an
  unparseable library.

- **Promoting no longer blanks fields the published candidate left empty.** The
  merge copied every candidate key including explicit `None` — and
  `_openreview_normalize` always emits `doi: None` — so promoting against those
  providers deleted a populated DOI. Empty candidate values are now treated as
  absent metadata rather than an instruction to clear.

- **`pzi update --promote --replace` no longer retypes every promoted entry
  `@article`.** The in-place branch hardcoded the type and rebuilt the block from
  the record model, dropping unmodelled fields; it now projects onto the entry on
  disk and resolves the type the same way keep-mode does. The translation
  server's item type is also carried into the candidate record, so a promoted
  conference paper becomes `@inproceedings` rather than defaulting to `@article`.

- **A candidate matching on authors alone is no longer promoted.** Three shared
  surnames scored exactly the old default threshold, so an unrelated paper by
  the same group could be written in.

- **A source carrying no author list no longer counts as author disagreement.**
  Several providers return title and venue only, and scoring that identically to
  conflicting authors rejected exact title matches outright. It is now tracked
  as a distinct `author_unknown` flag. For `pzi check` this deliberately does
  *not* become a free pass: an entry whose only corroborating source cannot
  confirm authorship now reports `could_not_verify` rather than `verified` —
  reproducing a real title with invented authors is what a fabricated citation
  looks like, so silence there would be false assurance. It previously reported
  `problematic`, which was a false alarm in the other direction.

## [0.1.0b2] - 2026-07-25

### Added

- The HTTP API auth token is now auto-discovered from `<data-home>/api_token`
  when neither `api_auth_token` nor `api_auth_token_cmd` is set, so a config can
  carry no token reference at all. Resolution precedence: `api_auth_token_cmd` →
  `api_auth_token` → the auto-read token file.
- `api_auth_token_cmd` config option: resolve the HTTP API auth token from a
  command's stdout (e.g. `pass show pzi-token`), matching the existing `*_cmd`
  secret-indirection pattern used for emails and the S2 key.
- `PZI_NODE` env var and `node_path` config option to point pzi at an explicit
  Node.js >=22 binary for the translation-server, instead of PATH auto-detect or
  the portable download. Intended for version-manager users (fnm/nvm/volta/asdf)
  and daemon contexts (systemd) whose PATH does not include the shell's Node
  shims. `PZI_NODE` overrides `node_path`; both override auto-detect. A
  set-but-broken value is a hard error, not a silent fallback to download.

### Changed

- `pzi init --setup` no longer writes the generated API auth token as plaintext
  into `config.toml`. Because users commonly symlink that file into a
  git-tracked dotfiles repo, the inline token was a footgun that leaked a
  secret into version control. Setup now writes the token to a separate `0600`
  file (`<data-home>/api_token`) and writes **nothing** token-related into the
  config — pzi auto-reads that file at runtime from the running user's resolved
  data home. So `config.toml` carries neither the secret nor an absolute home
  path (which would expose a username/directory layout) and is safe to commit
  and portable across machines. Existing configs with a plaintext
  `api_auth_token` continue to work unchanged. **If you ran an older
  `pzi init`, rotate that token: replace the plaintext value (and scrub it from
  any committed history).**
- `pzi init --setup` now writes home-relative `~/...` paths instead of absolute
  ones in the generated `config.toml`: the bib `path`/`papers_dir`, the
  interpreter in `browser_pdf_cmd`, and any Firefox `--profile` are folded to
  `~` when they live under the home directory (paths outside home, e.g. a system
  `/usr/bin/python3`, stay absolute). This keeps a committed config from
  exposing the home layout and makes it consistent with the commented example
  lines. To support this, the browser hook command now expands a leading `~` in
  each token at run time (it is split and run with `shell=False`, so the shell
  never would) — which also makes a hand-written `--profile ~/...` work.
- Default config and data directories now follow the XDG Base Directory spec:
  the config path resolves under `$XDG_CONFIG_HOME` (default `~/.config`) and
  the data home (`pzi_data_home`, cache for Node.js + translation-server) under
  `$XDG_DATA_HOME` (default `~/.local/share`), instead of hardcoding
  `~/.config` / `~/.local/share`. Non-absolute `XDG_*` values are ignored per
  the spec. An explicit `pzi_data_home` in config still takes precedence, and
  when unset the value now respects the injected home directory consistently
  with bib paths. `pzi init --setup` no longer writes a hardcoded
  `pzi_data_home` line (it emits a commented example), so the XDG-aware default
  applies. Chrome/Chromium profile auto-detection likewise honors
  `$XDG_CONFIG_HOME`.
- **Breaking, `.bib` format:** `pdf_url` and `abstract_url` are no longer
  packed into the `note` field with `" | "` delimiters and `PDF:`/`Abstract:`
  labels — a user's own note text containing that same shape could corrupt
  the parse, and the packed values weren't readable by other BibTeX tools.
  Each value now has its own field: `pzi-pdf-url`, `pzi-abstract-url`. `note`
  is now pure free text. Entries written by 0.1.0b1 keep their PDF/abstract
  URL as inert text inside `note` on next read (nothing is deleted from the
  file) — re-run `pzi update` or `pzi add` on the affected DOI/URL to
  repopulate the new fields, or move the value over by hand.
- `pzi search` output labels the matched-fields column (`[matched: title,tags]`)
  instead of a bare `[title,tags]`, which read as the same column `pzi entries`
  uses for actual author names.
- `.bib`, inbox, and metadata-cache writes now also fsync the containing
  directory after the atomic rename, so the rename itself survives a crash
  immediately after a write (previously only the new file's content was
  guaranteed durable, not the directory entry pointing at it).
- Releases now attach the prebuilt Firefox and Chrome extension zips
  (`paperazzi-capture-firefox.zip`, `paperazzi-capture-chrome.zip`) alongside
  the sdist/wheel, so installing the browser extension no longer requires a
  repo checkout.

### Fixed

- `pzi server` under systemd (or any non-interactive/no-TTY context) silently
  refused to bootstrap Node.js: `ensure_node` reached the interactive install
  prompt, `input()` raised `EOFError` on the missing stdin, and that was caught
  as "cancelled" — so the translation-server never started and every capture
  failed while `systemctl status` still showed the server "active". When stdin
  is not a TTY, pzi now downloads portable Node.js automatically (as it already
  did for `interactive=False`) instead of prompting.
- `download_node` re-downloaded and re-extracted Node.js on every call even when
  a matching version was already installed: the reuse check compared against
  `detect_node()` (system PATH) rather than the actual cached extraction path.
  It now reuses the previously extracted, runnable binary at
  `<data_home>/node/node-v<version>-<dist>/bin/node`.

- Inbox drain (`pzi inbox drain`) could silently drop a line appended to the
  inbox file (e.g. by the browser extension) while the drain's network calls
  were in flight: the final rewrite used a stale in-memory snapshot from the
  start of the drain. The rewrite now re-reads the file and merges in any
  lines appended after the snapshot was taken, under a short-lived advisory
  lock scoped to just the final read+rewrite.
- The HTTP API accepted requests whose `Host` header named an attacker
  domain even when bound to loopback, which let a DNS-rebinding page (its own
  domain's DNS pointed at 127.0.0.1) reach the API via a plain GET carrying
  no `Origin` header. Requests are now also validated against the server's
  bind host (`api_listen_host`); see `docs/security.md`.
- The HTTP API server only bounded `accept()` on the listening socket, not
  reads on already-accepted connections, so a client that opened a
  connection and trickled bytes (or sent none) could hold a handler thread
  open indefinitely. Each accepted connection now gets a 30s read timeout.
- PDF attach sessions (`/attach-pdf-bytes`, `/attach-pdf-raw`) had a
  get-then-later-consume race: two concurrent requests for the same
  `request_id` could both pass validation before either was marked consumed,
  double-spending a one-shot attach token. The session is now claimed
  (atomically removed) before validation and restored only if that attempt
  doesn't succeed, so a legitimate retry after a bad token or transient
  failure still works, but concurrent racers cannot both proceed.
- `url_safety.public_ip_address` (SSRF guard) treated 100.64.0.0/10
  (carrier-grade NAT) as publicly routable, and could disagree with the
  embedded address of an IPv4-mapped IPv6 literal (`::ffff:127.0.0.1`) on
  some Python patch releases. Now derived from `ip.is_global`, canonicalizing
  IPv4-mapped literals to their embedded address first.
- Writing a `.bib` file at a path that was itself a symlink (e.g. pointing
  into synced cloud storage) silently deleted the symlink and replaced it
  with a regular file, since `os.replace` treats a symlink destination as
  the directory entry to replace rather than the file it points at. Bib
  writes now resolve through the symlink first, so the symlink survives.
- A non-arXiv `eprint` field (e.g. a bioRxiv preprint ID) was classified as
  an arXiv ID whenever it was merely non-empty, regardless of
  `archiveprefix`, fabricating a bogus `arxiv.org` PDF URL. Now gated
  strictly on `archiveprefix` (case-insensitive `arXiv`).
- Better BibTeX `shorttitle(N,M)` citekey/filename templates truncated one
  title word to N characters instead of taking the first N words (each
  optionally truncated to M characters), and an invalid `match`/`replaceFrom`
  regex in a copied Zotero template raised instead of degrading safely.
- `pzi add`'s fallback metadata (e.g. browser-extension page metadata) was
  never applied when the fetched value was an empty list (`authors: []`) —
  only `None`/blank-string values were treated as missing.
- PDF discovery via DOI (Crossref/Europe PMC/DOAJ) always called the real
  network fetchers, bypassing the same dependency-injection seam used by
  every other discovery step; and a single discovery step raising an
  exception aborted the whole sequential discovery chain, unlike the
  parallel path, which already isolated per-step failures.
- The publisher PDF-gateway hostname table matched with an unanchored regex,
  so an unrelated lookalike host (e.g. `evilsciencedirect.com`) could be
  misidentified as a known publisher gateway. Matching is now a proper
  domain/subdomain check. Also: the Authorea/SAGE URL rewrites silently
  returned the unchanged landing-page URL (instead of no PDF found) when
  their expected path substring was absent; the Better BibTeX renderer's
  unrecognized-field fallback looked up the raw filter-suffixed token instead
  of the parsed field name, so any field without a dedicated branch always
  rendered empty; and `arxiv.org` URL detection didn't recognize
  `www.arxiv.org`.
- `pzi add`'s metadata-fetch step caught any exception (not just
  network/parsing failures) and reported it as "translation server error" —
  or silently fell back to manually-provided metadata — which could mask an
  unrelated bug as a network problem. Now scoped to the exception types the
  fetchers actually raise.
- A `.bib` write that failed after the temp file was created but before (or
  during) the atomic rename — e.g. a disk error — left the `.tmp` file behind
  permanently instead of cleaning it up (the original `.bib` was never at
  risk, only the leftover temp file). Now removed on any failure, matching
  the inbox writer's existing behavior.
- Crash when a fallback-sourced record (e.g. browser-extension page metadata)
  supplied `year` as a string: the similarity/dedup check compared it against
  an `int` and raised `TypeError`. Year is now coerced at the point a
  `NormalizedRecord` is produced, with a defensive coercion at the comparison
  site as well.
- DOI normalization no longer keeps a trailing `?query`/`#fragment` from a
  pasted `doi.org` link (`doi.org` forwards query strings to the resolved
  target rather than treating them as part of the DOI). A `doi:` prefix
  (e.g. `doi:10.1234/abc`) is now also recognized.
- `pip install`/`pipx install` guidance in `pzi init --setup`, the browser
  session hook, and the browser PDF hook referenced the wrong package name
  (`pzi[playwright]`, which does not exist) instead of the actual
  distribution name, `paperazzi[playwright]`.

### Docs

- Corrected several stale/inaccurate docs: README pipx install syntax for
  the `[playwright]` extra, a false "requires git" claim on the Node.js
  download line, `config.template.toml`'s `browser_engine` value list
  (dropped `chrome`, added `webkit`), the attach-session TTL in
  `docs/security.md` (was documented as 5 min, actually 10 min), the
  `packaging/systemd/pzi.service` `Documentation=` URL, a stale
  `pzi-browser-hook` console-script reference in `tools/`, and the 0.1.0b1
  changelog's inflated "100% coverage, mypy clean" quality claim. Also
  documented `--config PATH`, `pzi pdf retry --failed-only`, previously
  omitted `add`/`init`/`tag`/`export` flags, and shell-completion
  enablement in the README CLI reference; added missing env vars
  (`PZI_NODE_MIRROR`, `PZI_NPM_REGISTRY`,
  `PZI_DISABLE_DESKTOP_BROWSER_FALLBACK`, `PZI_DOWNLOAD_DIR`,
  `PZI_DESKTOP_BROWSER_TIMEOUT`, `PZI_SKIP_BROWSER_HOOK`) to the env-var
  table.
- Clarified that `PZI_BROWSER` (desktop browser fallback) and `pzi init
  --browser`/`browser_engine` (headless Playwright automation browser) are
  independent settings with independent defaults, not the same knob. Also
  documented that `pzi search --json` always returns a JSON array (one result
  per searched library), even for a single default target.

## [0.1.0b1] - 2026-07-01

First public beta.

### Added

- **CLI**: `init`, `add` (single, bulk `--from-file`, `inbox`), `pdf retry|attach`,
  `tag`, `search`, `check`, `update [--promote]`, `entries`, `delete`,
  `fix clean|dedupe|merge|reindex`, `export`, `import`, `doctor`, `server`.
- **Capture pipeline**: DOI/URL/PDF input → BibTeX entry + PDF download.
  Metadata sources: Zotero translation-server (local child), Crossref, OpenAlex,
  Semantic Scholar, DBLP, OpenReview, Unpaywall, DOAJ, Europe PMC.
- **PDF acquisition**: direct download → browser-profile hook (Playwright, optional
  extra) → FlareSolverr (optional, opt-in).
- **Local HTTP API**: loopback-bound, token-auth optional, for browser extension.
- **Browser extension**: Firefox + Chrome, multi-source capture, search detection,
  onboarding flow.
- **Backend**: Zotero translation-server auto-installed as a local Node.js child
  process; auto-downloaded if missing.
- **BibTeX**: plain `.bib` file + `papers/` dir as sole source of truth; no
  database. Portalocker file lock; aborts on concurrent external edits.
- **Architecture**: pure planning logic separated from side effects; dependency-
  injected fetcher seams; layer-boundary tests enforce no CLI/HTTP/browser
  imports in pure modules.
- **Quality**: ~81% test coverage, pyright clean (no mypy) across ~90 source
  files, ~100 test files.
- **Security**: SSRF guards for FlareSolverr, tar-slip guard for imports,
  loopback-only HTTP binding, optional token auth, documented security model.
- **Shell completions**: bash/zsh completions via `argcomplete`, included in the
  base install.

### Changed

- Playwright is an optional extra (`pip install 'paperazzi[playwright]'`) instead of a
  hard dependency. The base install is lighter; the browser-profile PDF fallback
  is only needed by users who configure `browser_pdf_cmd`.
- `bibtexparser` pin is `>=2.0.0b9,<3` to allow patch-level updates within the v2
  beta series.

### Known Limitations

- APIs can rate-limit; promotion is best-effort.
- Browser extension install is manual.
- The first `.bib` write reformats the whole file (once; stable thereafter).
- No native Windows support (WSL2 works).
- No sync, group libraries, or desktop reader (by design).
- Not yet on PyPI; install from GitHub for now.

[Unreleased]: https://github.com/mnazaal/paperazzi/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/mnazaal/paperazzi/compare/v0.1.0b6...v0.2.0
[0.1.0b6]: https://github.com/mnazaal/paperazzi/compare/v0.1.0b5...v0.1.0b6
[0.1.0b5]: https://github.com/mnazaal/paperazzi/compare/v0.1.0b4...v0.1.0b5
[0.1.0b4]: https://github.com/mnazaal/paperazzi/compare/v0.1.0b3...v0.1.0b4
[0.1.0b3]: https://github.com/mnazaal/paperazzi/compare/v0.1.0b2...v0.1.0b3
[0.1.0b2]: https://github.com/mnazaal/paperazzi/compare/v0.1.0b1...v0.1.0b2
[0.1.0b1]: https://github.com/mnazaal/paperazzi/releases/tag/v0.1.0b1