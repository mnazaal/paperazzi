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

1. **Record projection.** Done — `bibtex.apply_record_to_entry` /
   `merge_projected_entry`, wired into `tag_service`, `update_service`,
   `plan_bib_write` (update branch, guarded on snapshot consistency),
   `pdf_service._entry_with_pdf_fields`, `bib_repository.merge_bib_entries`.
   The `promote_service` leftovers (the hardcoded `entry_type` and the
   note-stamping updater) were closed with step 3. Still lossy by design:
   `_rebase_insert_plan_against_current:565`, and the dry-run preview callers
   `update_service:197` / the update-in-place branch of `promote_service`
   (misleading diff, not a bad write).
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

   `reindex_service` — **open, next up**:
   - `:83-104` **wrong PDF attached.** `old_pdf_path` comes from
     `plan_pdf_path(citekey=old_citekey)` instead of `record["local_pdf_path"]`,
     so a stray `<old_citekey>.pdf` is renamed onto the entry and the real PDF
     is orphaned. `--dry-run` cannot reveal it: `:111-112` computes
     `old_pdf`/`new_pdf` and `cli_render.py:201` discards them, printing a bare
     `" [PDF renamed]"`.
   - `:96-117` **no rollback.** PDFs are renamed inside the loop, the bib is
     written after; if `rewrite_entries_in_order` raises, every renamed entry's
     `file =` dangles. Realistic trigger is not disk failure but the shared lock
     being released after the read (`:40-41`) and re-taken for the write, so a
     concurrent `pzi add` changes the entry count.

   `clean_service` — **open**:
   - `:110` `papers.rglob("*.pdf")` descends into `papers_dir/.orphans`, so
     quarantined files are re-detected forever and plain `pzi fix clean` exits 1
     permanently once anything has been quarantined. `:162`/`:171` compute
     `dst = orphan_dir / src.name` and `shutil.move` overwrites silently, so the
     **second** `--fix` run destroys the archived original on a basename
     collision.
4. **Proceedings venue key on every insert path.** Found during the step-3
   adversarial review, not by the original audit. `record_to_bibtex_entry`
   always emits the venue as `journal`, so `plan_bib_write` with a resolved
   type of `inproceedings`/`incollection` produces
   `@inproceedings{... journal = {...}}` — bibliographically wrong and broken
   for styles that require `booktitle`. Verified directly:
   `plan_bib_write({...'venue': 'GraphConf'}, [], entry_type='inproceedings')`
   returns fields `{'title', 'journal', 'doi'}`. Affects `pzi add`, import, and
   capture for every conference paper; `promote_service` works around it locally
   with `_relocate_venue_for_entry_type`, which should be deleted once the
   projection is fixed at the source. Needs its own characterization tests
   across the insert paths — the fix touches every entry pzi writes.

5. **Identity normalization.** DOI case/trailing-slash/nested-path in
   `normalize_doi` + `extract_identities`.
6. **Security.** `bib` selector confinement, attach-session enforcement,
   XDG token divergence + auth-state visibility, `config.toml` mode, wildcard
   bind Host check.
7. **Extension.** Manifest version (unblocks the b3 release independently, do
   early), injected-function scope bugs, allowlist boundary, candidate cap.

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
- `pzi fix reindex` and `pzi fix clean --fix` are unsafe on a real library until
  the rest of step 3 lands. `pzi tags`, `pzi update`, and `pzi update --promote`
  are cleared (steps 1 and 3).
