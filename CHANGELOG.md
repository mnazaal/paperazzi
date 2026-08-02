# Changelog

All notable changes to paperazzi (CLI command `pzi`) are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/mnazaal/paperazzi/compare/v0.1.0b4...HEAD
[0.1.0b4]: https://github.com/mnazaal/paperazzi/compare/v0.1.0b3...v0.1.0b4
[0.1.0b3]: https://github.com/mnazaal/paperazzi/compare/v0.1.0b2...v0.1.0b3
[0.1.0b2]: https://github.com/mnazaal/paperazzi/compare/v0.1.0b1...v0.1.0b2
[0.1.0b1]: https://github.com/mnazaal/paperazzi/releases/tag/v0.1.0b1