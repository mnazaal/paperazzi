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

1. **Record projection** (`bibtex.py:82-141` + write paths). Carry
   `BibtexEntry["fields"]` forward; write back only keys the record owns. Closes
   field annihilation, `booktitle`→`journal`, entry-type flattening, relative
   `file` rewriting, tag round-trip asymmetry.
2. **PDF integrity.** Reject truncated bodies (`%%EOF` + length +
   Content-Length reconciliation) before storing; catch `http.client.HTTPException`.
3. **Destructive write paths.** `promote` (keep-mode overwrite, author-only
   gate, `None`-clobber, hardcoded `entry_type`, partial apply), `reindex`
   (PDF identity, no rollback, dry-run reporting gap), `clean` (quarantine
   overwrite + self-rescan).
4. **Identity normalization.** DOI case/trailing-slash/nested-path in
   `normalize_doi` + `extract_identities`.
5. **Security.** `bib` selector confinement, attach-session enforcement,
   XDG token divergence + auth-state visibility, `config.toml` mode, wildcard
   bind Host check.
6. **Extension.** Manifest version (unblocks the b3 release independently, do
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
- `pzi tags`, `pzi update`, `pzi update --promote`, `pzi fix reindex`, and
  `pzi fix clean --fix` are unsafe on a real library until step 1 and step 3
  land.
