# PLAN — post-audit remediation (2026-07)

Supersedes [`docs/remediation-plan-2026-07.md`](docs/remediation-plan-2026-07.md),
which is closed (all phases done). This plan covers the 2026-07-25 full audit:
8 parallel review tracks over the whole tree plus 3 adversarial verification
passes. Findings live in the audit conversation; this file holds decisions,
order, and the tier manifest.

## Decisions

- **Release.** `v0.1.0b2` is published and broken (extension zips carry a PEP 440
  version Chrome/AMO reject; every defect below ships in it). It stays published;
  supersede with `0.1.0b3` once the critical tier is fixed. Do not retag.
- **Breaking changes are acceptable** (no external users), documented in
  CHANGELOG.md. Prefer the clean fix over a shim — unchanged from last cycle.
- **CHANGELOG.md stays** despite the standing-docs default against it: it is
  parsed by `.github/workflows/release.yml` to build GitHub Release notes, so it
  has a real consumer.

## Fix order

Approved 2026-07-25. Rationale: one architectural fix closes ~7 reported
findings; PDF integrity is the other silent-corruption class; the rest is
ranked by blast radius.

Steps 1-4 are done (2026-07-26); 5-7 remain. A follow-up review on 2026-07-26
added a second, non-defect track — functional-programming and UNIX-philosophy
improvements — recorded under "Design track" below.

1. **Record projection.** Done — `bibtex.apply_record_to_entry` /
   `merge_projected_entry`, wired into `tag_service`, `update_service`,
   `plan_bib_write` (update branch, guarded on snapshot consistency),
   `pdf_service._entry_with_pdf_fields`, `bib_repository.merge_bib_entries`.
   The `promote_service` leftovers (the hardcoded `entry_type` and the
   note-stamping updater) were closed with step 3. Still lossy by design:
   `_rebase_insert_plan_against_current:565`, and the dry-run preview callers
   `update_service:197` / the update-in-place branch of `promote_service`
   (misleading diff, not a bad write) — **both closed 2026-07-26** with the
   write-plan contract unification (design track item 1).
2. **PDF integrity.** Done, with one deliberate narrowing. Implemented:
   Content-Length reconciliation in `_read_limited` (the silent truncation case
   — `HTTPResponse.read(amt)` clips and returns short rather than raising), and
   `http.client.HTTPException` in the download `except` (`IncompleteRead`, the
   chunked case, derives from neither `OSError` nor `ValueError`, so it escaped
   as a traceback).
   **Not implemented: the `%%EOF`-trailer and minimum-size heuristics.** 128
   `%PDF-` stubs across 32 test files would need migrating to realistic
   fixtures, and the check carries a false-reject risk (a legitimate PDF refused
   is worse than a rare truncation stored). With the two checks above, the
   remaining uncovered case is a body sent with neither Content-Length nor
   chunked framing, closed cleanly mid-transfer. Revisit as its own change if
   that shows up in practice; it also needs a warning channel, since the
   download helpers currently return `(path, error)` with no slot for
   "stored, but looks truncated".
3. **Destructive write paths.** `promote_service` done 2026-07-25 (all five
   defects, each characterized by a failing test first); `reindex_service` and
   `clean_service` still open. All were reproduced through the real CLI by an
   adversarial verifier; details inlined here so the (ephemeral) probe scripts
   are not needed.

   `promote_service` — **done**, findings as originally recorded:
   - `:761` **keep-mode overwrites the preprint.** `_merge_published_metadata`
     copies the preprint's `canonical_url` into the published record, so
     `plan_bib_write`'s `find_exact_match` matches the *preprint itself* on URL
     identity and the intended insert becomes an in-place update. Reports
     `action=create` with a `published_citekey` that exists nowhere, plus a
     dangling `note = {Published version: …}`. Trigger: preprint has `url` and
     the candidate has no `canonical_url` — and **no** provider normalizer in
     `metadata_sources.py` ever emits `canonical_url`, so every fallback-provider
     promotion hits it. `--dry-run` shows the same wrong outcome.
     Fix: plan with `force_new=True`, or strip the inherited identity.
   - `:188` **author-only match accepted.** Gate uses `_score_confidence`
     (`:640`), where ≥3 shared surnames scores exactly 3 = the default
     `promote_confidence_threshold`. The real 0-100 `score_match` is computed at
     `:217`, *after* the gate, and only decorates diagnostics — so the tool
     prints `match confidence 0/100 … title_mismatch` and writes anyway.
     Fix: gate on `score_match`; never let authors alone clear it.
   - `:885-893` **candidate `None` blanks populated fields.** The merge loop
     copies every candidate key including explicit `None`;
     `_openreview_normalize` (`metadata_sources.py:676`) always emits
     `doi: None`. Fix: skip `None`/`""`/`[]`.
   - `:819` **`--replace` hardcodes `entry_type="article"`**, so a promoted
     `@misc`/`@inproceedings` becomes `@article`. Keep-mode disagrees (it routes
     through `_resolve_entry_type`). Fix here also routes through
     `apply_record_to_entry` (step 1 leftover).
   - `:765-771` **partial apply.** `execute_write_plan` commits, then
     `_add_note_to_citekey` raises and the rollback removes only the PDF —
     leaving a committed entry whose `file =` dangles, reported as
     `created 0, skipped_failed 1`. Needs no fault injection: one malformed
     block anywhere in the `.bib` triggers it, because the insert path skips
     `_validate_library_parseable` (only the update path calls it).

   `reindex_service` — **done 2026-07-26**, restructured into plan/execute
   (`plan_reindex` is pure; `reindex_library` executes). Both recorded defects
   fixed, plus one found while fixing:
   - **wrong PDF attached** — the move now comes from `record["local_pdf_path"]`,
     never from `plan_pdf_path(citekey=old_citekey)`.
   - **no rollback** — renames and the bib write share one exclusive lock
     (`rewrite_entries_in_order` became `rewrite_entries_in_order_locked`,
     caller-locks), and a failed write undoes every rename.
   - **new: `os.rename` clobbered the destination.** A PDF already sitting at
     the planned path was silently replaced; the rename is now refused and
     reported.
   `cli_render` prints the PDF paths, so a wrong move is visible in `--dry-run`.

   `clean_service` — **done 2026-07-26**. Quarantine planning is pure
   (`plan_orphan_quarantine`); execution is the `shutil.move` loop.
   - `.orphans` is excluded from the orphan scan, so quarantined files are no
     longer re-detected forever.
   - A basename already taken in the archive gets a numbered suffix
     (`stale-1.pdf`) instead of overwriting what is stored.
4. **Proceedings venue key on every insert path.** **Done 2026-07-26.**
   `record_to_bibtex_entry` now picks the venue field from the entry type
   (`venue_field_for_entry_type`, the single source of truth for which types
   take `booktitle`). Two things the plan did not anticipate:
   - **`merge_projected_entry` had to change with it.** It read the venue from
     `projected["journal"]` alone, so fixing only the projection would have
     *deleted* the venue of every proceedings entry it merged. It now reads
     either home and still writes back to the key the on-disk entry used.
   - **`_relocate_venue_for_entry_type` stays.** The plan expected to delete it;
     only its two insert-path call sites were dead. The retype-on-merge case
     (promotion changing an entry's type) still needs it, exactly as the
     2026-07-25 adversarial review recorded.

   **Also found here: import dropped the source entry type entirely.**
   `import_service` set `record["entry_type"]`, but nothing downstream read it —
   `add_service` never mentions `entry_type` — so every imported
   `@inproceedings` was written as `@article`. `entry_type` is now a declared
   `NormalizedRecord` key and `resolve_entry_type` honors it, ranked below a
   provider's `item_type` (fresh evidence) and above the preprint heuristic.
   Records parsed out of the library deliberately do **not** carry it, so
   promotion stays free to retype an entry.

5. **Identity normalization.** **Done 2026-07-26.** `normalize_doi` strips a
   trailing slash (it already folded case), and `extract_identities`
   canonicalizes the DOI before indexing — comparing stored DOIs verbatim meant
   `10.1145/abc` did not match an incoming `10.1145/ABC`, so re-capture wrote a
   duplicate. A value `normalize_doi` cannot parse falls back to a case-folded
   strip so it still matches itself. Verified through the CLI.
6. **Security.** **Done 2026-07-26.**
   - **`bib` selector confinement.** A direct `.bib` path is a CLI convenience;
     over HTTP it let any request reaching the API create and write a library
     anywhere the user can write. POST requests are confined to configured
     libraries, checked once at the dispatch point so a new route cannot miss it.
   - **Wildcard bind.** `0.0.0.0` started the server and then rejected every
     request (the DNS-rebinding Host check has no bind address to match), so it
     is refused up front with an actionable message.
   - **Auth-state visibility.** The server now states whether a token is
     required at startup: a differing `XDG_DATA_HOME` between `pzi init` and the
     server resolves the token to None, and an unauthenticated API looked
     identical to an authenticated one.
   - **`config.toml` mode.** `pzi init` creates it `0600`; it was written with
     the default umask, so pzi created a file `pzi doctor` then warned about.
   - **Attach-session enforcement — verified already correct**, not changed:
     single-use, expiry, `compare_digest` on the token, citekey/bib match, size
     cap, `%PDF-` magic check and a source-URL allowlist, invoked on both attach
     routes.
7. **Extension.** **Done 2026-07-26.** The manifest carried the PEP 440 version
   verbatim, so the b2 zips were uninstallable; it is now translated
   (`0.1.0b2` -> `0.1.0.2002`) with the pre-release in the 4th component so it
   still sorts below the final release, and the PEP 440 string is kept as
   `version_name`. Also fixed: DOM PDF discovery never ran (the injected
   function referenced a service-worker module name that does not exist in the
   page), the bot-bypass allowlist matched without a domain boundary
   (`evil-nature.com` cleared `nature.com`), and PDF candidates are capped
   client-side at the server's limit, which rejects an over-long list outright.

## Design track — from the 2026-07-26 review

Not defects; the owner's two stated directions for the tool (functional
programming, UNIX philosophy). Ordered by value-to-blast-radius. Full findings
live in the review conversation; each item below is self-contained.

1. **Unify the write-plan contract.** **Done 2026-07-26.** Both sinks apply
   `plan["entry"]` verbatim; every update plan is merged at construction. This
   also made `pzi update --dry-run` and `pzi update --promote --dry-run` honest —
   their preview plans were bare projections, so they reported deleting every
   unmodelled field and retyping `@article` to `@unpublished`, writes neither
   run performs. Remaining bare-projection constructors: none.
2. **CLI machine interface.** **Done 2026-07-26.** Landed: an exit-code
   vocabulary (`pzi/exit_codes.py`) documented in README and `pzi --help`, with
   `1` reserved for "ran fine, has something to report" so a failure to run is
   never `1`; target resolution raising `PziError` with its code; stdout hygiene
   (empty results write nothing; `pzi entries` emits five fixed tab-separated
   columns); `pzi delete` refusing to prompt when stdin is not a tty;
   `PZI_CONFIG`; `pzi import -`; `check --jsonl -` streaming NDJSON to stdout;
   and one JSON envelope (`{command, status, bib_name, items, errors}`) on every
   command that reports a result — including the mutating ones, and including
   failure paths. `authors` is a list everywhere. `doctor` renders for humans by
   default and exits non-zero when a probe fails.

   **Deliberately not done: a separate NDJSON mode for every command.** The
   review proposed it so `jq -r .citekey | xargs` would be a one-liner; with the
   envelope that is already `jq -r '.items[].citekey'`, so a second output mode
   would be new surface buying nothing. `check --jsonl -` exists because a
   long-running audit genuinely benefits from streaming. Revisit only if a
   library large enough to make buffering hurt shows up.

3. **Stop labelling effectful modules pure.** **Done 2026-07-26.**
   `pdf_discovery` no longer infers a step's execution phase from its
   `__name__` — each step declares it with `@discovery_phase`, and `add_service`
   excludes the `/web` translator by comparing the step object. Four docstrings
   that advertised purity over I/O were corrected: `add_planning` (its fetch half
   is the provider network cascade), `pdf_discovery`, `pdf_planning` (two
   filesystem/config readers) and `bib_serialize` (path resolution follows
   symlinks).

   **Not done, and optional:** physically moving `fetch_record_for_input` /
   `build_discovery_context` out of `add_planning` into a fetch-orchestration
   module. The harm the review named was the mislabelling, which the docstring
   now states plainly; the ~200-line move is cosmetic layering on top of that.
   Do it only if the module's size becomes the problem.

4. **One error channel per layer.** **Done 2026-07-26.**
   `load_and_resolve_bib` is now `load_bib_target`, returning a frozen
   `BibResolutionFailure` with a structured `reason` instead of
   `tuple | list[str]`; the rename forced all 21 call sites to be visited and
   pyright caught the three a regex sweep missed. `add_service` no longer tells
   the ambiguous-target case apart by comparing the error list to an exact
   message string.

   The `except TypeError` capability probes are gone. `add_planning`'s is deleted
   outright (every fetcher conforms to `MetadataRecordFetcher`, which now
   declares `errors`); the three seams whose shapes genuinely differ decide by
   signature inspection via `protocols.accepts_keyword`. Test doubles that had
   relied on the probe were widened rather than kept working by production code.
   A regression test pins the guarantee: a `TypeError` raised *inside* a fetcher
   propagates instead of triggering a retry with fewer arguments.

5. **Smaller, mechanical.** **Done 2026-07-26.**
   - Preprint classifiers moved from `promote_service` to `identifiers`, and the
     PDF byte-storage helpers from `pdf` to `pdf_download` — the two import
     cycles are gone.
   - `tests/test_layer_boundaries.py` scans recursively (all 19 `commands/`
     modules are classified FRONTEND instead of invisible) and has a cycle test
     that covers function-level imports, checked against a reintroduced cycle.
   - The hidden data channels are closed: `add_service` no longer wraps the
     fetcher seams to assign `nonlocal` diagnostics (`fetch_record_for_input`
     returns the translation results it fetched), and `update_service` no longer
     mutates a captured `change_box` (`update_bib_entry` returns the pre-update
     record so the service diffs two values). Nothing covered add's diagnostics
     path, so a test was added and confirmed to fail when the computation is
     disabled.
   - The five `PZI_*` PDF-fallback variables are resolved once into a frozen
     `PdfFallbackSettings` at the fallback entry point instead of being read from
     `os.environ` wherever each helper needed them. Names and defaults unchanged.

Explicitly **not** doing: converting `NormalizedRecord` to a frozen dataclass.
Every module would change for modest payoff; the frozen types in
`capture_models` already cover the boundaries where immutability pays.

## Tier manifest — signed off 2026-07-25

Criticality per `dev-audit`. **A** = subtly-wrong is silently critical (data
corruption, security boundary, external contract). **B** = real behavior, fails
loudly. **C** = no meaningful behavior or external contract; reviewed, listed,
excluded from the coverage denominator.

Assignments come from the track that owned each file. One override: `capture_context`
was proposed B, raised to **A** — it resolves the API auth token, a secret boundary.

| Tier | Count | Modules |
|---|---|---|
| **A** | 48 | `bib_serialize` `bibtex` `bib_repository` `similarity` · `identifiers` `add_planning` `capture_local_pdf` `add_service` `html_metadata` `capture_core` `capture_context` · `http_security` `url_safety` `safe_http` `http_api` `http_post_routes` `http_binary_routes` `pdf_attach_session` `pdf_attach_session_store` `cli_server` · `pdf_download` `pdf` `pdf_discovery` `pdf_planning` `browser_session` `browser_session_manager` `node_runtime` · `promote_service` `update_service` `tag_service` `dedupe_service` `reindex_service` `clean_service` `metadata_sources` `metadata_cache` · `config` `config.template.toml` `setup_service` `cli` `cli_parser` `commands/{init,server,add,inbox,delete,update,reindex,dedupe}` |
| **B** | 32 | `format_templates` `bib_service` `resolution_match` · `capture_models` `import_service` `inbox_service` · `http_get_routes` `flaresolverr` `rate_limit` · `ts_backend` `browser_pdf` `browser_pdf_hook` `server_browser` `pdf_service` `pdf_acquisition_plan` `translation_server` · `check_service` `search_service` `export_service` · `page_metadata_cmd` `__init__` `commands/{clean,import_,export,doctor,tags,pdf,check,search,entries,common,fix}` |
| **C** | 9 | `fileio` `http_payloads` `http_status` `doctor_service` `cli_render` `protocols` `errors` `__main__` `commands/__init__` |

**Audited scope: the write-path core (12 modules).** Auditing all 48 Tier A units
at full rigour is a multi-week campaign; the confirmed data-loss findings all
live in a much smaller set, so that set is what gets strong-audited now:

`bibtex` `bib_serialize` `bib_repository` `similarity` `identifiers`
`add_planning` `add_service` `promote_service` `update_service` `tag_service`
`dedupe_service` `reindex_service`

The other 36 Tier A modules keep their tier (they are still critical) but are
held at **reviewed, not audited** — fixed where the audit found defects, without
the characterization-test + second-reviewer gate. Revisit the scope once the
per-unit cost of the first few is known.

## Campaign mechanics

- Per Tier A/B unit: state contract → adversarial pass → characterization test
  (red) → improve (green) → commit with `Human-audited: yes`.
- **Characterization test first, then touch.** Every defect above gets a failing
  regression test before the fix, so the fix is what turns it green.
- Tier A commits need a second human reviewer; never self-certified.
- Tracking: `git audit-coverage src/`. Baseline 2026-07-25: **0/22202 lines
  (0.0%)** — no commit has ever carried the trailer.
- Coverage is process evidence, not a correctness claim. The tests are the
  assurance artifact.

## Risks

- The record-projection fix touches every write path; it is the highest-value
  and highest-blast-radius change here. It needs the characterization tests
  (unmodelled fields survive a mutation) written first.
- The suite is load-bearing but has known holes (mutation testing: 20/29 killed).
  Four survivors — `ezproxy_host` forwarding, `_validate_bibtex_roundtrip`,
  `entry_type` sanitization, layer-boundary blindness to `commands/` — should be
  closed early, since they are exactly the guards this campaign will lean on.
- **No command is currently known-unsafe against a real library.** Steps 1-4 are
  done; `pzi fix reindex` and `pzi fix clean --fix` were the last two blockers
  and are cleared. Steps 5-7 (identity normalization, server hardening,
  extension) are quality and security work, not data-safety blockers, provided
  the server keeps its loopback bind and token.
