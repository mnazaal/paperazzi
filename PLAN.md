# pzi Plan

## Status (2026-05-12)

Current verification focus: restore green local checks and raise the release quality bar with browser-extension and browser-hook regression tests.

Verification baseline:
- Python coverage gate raised to 85% branch coverage.
- Browser extension JavaScript is exercised from pytest via Node with mocked WebExtension APIs.
- Browser PDF hook edge branches are covered with fake Playwright/browser objects.

Decisions:
- `semantic_scholar_api_key` is an app-level config key, not a per-bib key.
- `bib promote` should skip records that are not preprints before calling search providers.
- Empty/blank-title records should not trigger provider searches during promotion.

## Status (2026-04-24)

All phases 1–13 shipped. 347 tests green. 408 functions.

Completed since 2026-04-16:
- **PDF discovery pipeline refactor** — `pdf_discovery.py` with 7 pure composable steps, replacing duplicated fallback chains in `add_service.py`
- **Browser extension build system** — `tools/build_extension.py` generates Firefox/Chrome MV3 manifests from `manifest.base.json`
- **Extension popup dynamic dropdown** — fetches `/bibs` for live bib selection
- **CORS headers** on all HTTP API responses
- **Security fixes** — `shell=True` removed from `add_service.py` and `browser_pdf.py` (now uses `shlex.split` + `shell=False`)
- **Exception narrowing** — broad `except Exception` narrowed to specific types across `add_service.py`, `pdf_service.py`, `update_service.py`, `doctor_service.py`, `browser_pdf.py`
- **Function bloat reduction** — 519 → 408 functions (-21%) via inlining trivial helpers and parameterizing tests
- **Preprint promotion** — `pzi bib promote` scans for published versions of preprints (arXiv, bioRxiv, etc.), scores confidence, updates in place or creates new entry (`--keep-preprint`), cross-links via notes
- **Local PDF ingestion** — `pzi add /path/to/paper.pdf` extracts DOI/title from PDF text (via pypdf), resolves full metadata, copies PDF to papers_dir with citekey naming
- **Abstract field support** — `NormalizedRecord` now includes `abstract` text; round-trips through BibTeX read/write, merge logic prefers longer text, translation-server extracts `abstractNote`
- **Full-text search** — `pzi search` supports `--query` (title/abstract/note), `--author`, `--year`, `--tag` with combined AND filters

Deviations from original plan:
- CLI added `pzi serve` verb to launch local HTTP API.
- HTTP API uses stdlib `http.server.ThreadingHTTPServer`.
- Bib write lock uses `fcntl.flock` on sibling `<bib>.lock` file.
- `bib set-default` rewrites TOML via minimal in-repo emitter (`config_writer.py`).
- `pdf attach` added as explicit CLI command (not in original plan).
- `bib update` uses translation-server search, not just Semantic Scholar.
- `bib promote` added as new subcommand (not in original plan).
- `pzi add <local-pdf>` supports local PDF file ingestion (not in original plan).

## Goal

Build `pzi`, a minimal Python tool for capturing papers into local BibTeX libraries.

Primary workflows:
- browser extension button saves current paper page
- CLI command saves by DOI or URL
- metadata is written to a `.bib` file
- PDF is downloaded when available and renamed deterministically
- exact duplicates are merged conservatively
- likely similar items are kept and annotated for manual review
- existing entries can later be checked for updates such as preprint → published version
- user-defined tags support overlapping topics
- multiple `.bib` files are supported

## Principles

- Keep one source of truth: local `.bib` files plus local PDFs.
- Use one simple backend path for both browser and CLI.
- Use `zotero/translation-server` for webpage translation whenever possible.
- Prefer conservative behavior over clever automation.
- Minimize user prompts in the common case.
- Avoid extra databases in v1.
- Prefer plain data plus pure functions over object-oriented stateful designs.
- Isolate side effects at the edges: network, filesystem, locks, and CLI/HTTP I/O should be thin orchestration layers.
- Make normalization and merge rules deterministic and composable.

## Core User Experience

### Browser extension
- Click extension icon on current tab.
- If one bib is configured, save directly.
- If multiple bibs are configured and one is default, save directly to default.
- If multiple bibs are configured and no default exists, prompt for target bib.
- Show success or failure notification.

### CLI
- `pzi add <doi-or-url> [--tags tag1,tag2] [--bib NAME|PATH] [--dry-run]`
- `pzi pdf retry <citekey> [--bib NAME|PATH]`
- `pzi tag add <citekey> <tag...> [--bib NAME|PATH]`
- `pzi tag remove <citekey> <tag...> [--bib NAME|PATH]`
- `pzi tag list [<citekey>] [--bib NAME|PATH]`
- `pzi search --tag <tag> [--bib NAME|PATH]`
- `pzi bib list`
- `pzi bib set-default <name>`
- `pzi doctor`

Post-MVP:
- `pzi bib update <name> [--dry-run]`

## Data Model

### Bib model
In v1, one logical bib = one configured `.bib` file.

Each configured bib has:
- `name`
- `path` to one `.bib` file
- optional `papers_dir`
- optional `default: true`

Important note:
- `.bib` files may live in different directories
- PDF storage should therefore be configurable per bib
- if `papers_dir` is omitted, default to a sibling `papers/` directory near that `.bib`

### Entry fields
Standard BibTeX metadata plus:
- `file` for local PDF path
- `keywords` for tags
- `note` for similarity hints such as `Possibly similar to smith2024foo`
- optional provenance fields later if needed

### Internal normalized record
Use one plain internal record shape through the pipeline, converted to or from external formats at the boundaries.

Suggested fields:
- `title`
- `authors`
- `year`
- `venue`
- `doi`
- `arxiv_id`
- `canonical_url`
- `source_url`
- `pdf_url`
- `abstract_url`
- `tags`
- `note`
- `citekey`
- `local_pdf_path`
- `source_name`
- `source_payload`

Rules:
- keep absent values explicit as `None`/null-equivalent, not hidden defaults
- pure normalization functions should produce this record from DOI, URL, or translator output
- BibTeX writing should be a separate projection from this record into BibTeX fields

### Tags
Tags are stored per entry in:
- `keywords = {tag1, tag2, tag3}`

Rules:
- tags are entry-local, not global objects
- tag operations are scoped to one selected bib
- normalize tags consistently, preferably lowercase slugs
- overlapping topics are naturally supported
- preserve user tags during metadata updates

## Main Behaviors

### 1. Add item
Input can be DOI, article URL, or direct PDF URL.

Flow:
1. resolve target bib
2. classify input
3. fetch metadata
4. normalize metadata into internal record
5. check for exact existing match in selected bib
6. if exact match exists, enrich existing entry
7. otherwise create new entry
8. detect possible similar entries and annotate with `note`
9. fetch PDF if available
10. rename and move PDF into bib-specific papers directory
11. write or update BibTeX entry

### 2. PDF handling
PDF acquisition order:
1. attachments returned by translation-server
2. direct PDF URL if input is already a PDF
3. fallback resolver logic later if needed

V1 behavior:
- validate content type or file signature
- stage in temp location first
- move atomically into final destination
- use stable filename pattern, preferably `{citekey}.pdf`
- if PDF fails, still save metadata entry and report partial success

### 3. Exact duplicates vs similar items
Exact duplicate means one of:
- same DOI
- same arXiv ID
- same canonical URL

Behavior:
- update/enrich existing entry
- add missing PDF or better metadata if found
- do not create a second entry

Possible similar item means:
- high title similarity
- author overlap
- nearby year
- but no exact shared identity

Behavior:
- keep as separate entry
- annotate note field, for example `Possibly similar to smith2024foo`
- let user decide manually later

### 4. Update existing bib entries
`pzi bib update <name>` should detect cases like preprint later published.

Primary source:
- Semantic Scholar

Fallback sources:
- Crossref
- OpenAlex

Behavior:
- scan selected bib
- identify preprint-like or incomplete entries
- query update sources
- conservatively enrich existing entries or report likely published successors
- preserve user fields such as tags and local file paths
- support dry-run mode

This is valuable but not required for the first end-to-end release. Treat it as a post-MVP feature unless the core capture flow is already stable.

## Architecture

### Components
- thin browser extension
- local Python app
  - CLI
  - local HTTP capture endpoint
- translation-server client
- BibTeX read/write layer
- PDF downloader and organizer
- exact/similarity matcher
- update checker
- tag manager
- config, cache, lock, logging modules

### Functional design style
Prefer modules of related pure functions over classes with internal mutable state.

Examples of preferred boundaries:
- `classify_input(value) -> InputKind`
- `normalize_identifier(value) -> NormalizedIdentifier`
- `translate_webpage(url, deps) -> TranslatorResult`
- `select_pdf_attachment(record, attachments) -> PdfPlan | None`
- `match_exact(record, entries) -> ExistingMatch | None`
- `score_similarity(record, entries) -> list[SimilarityHit]`
- `merge_entry(existing, incoming) -> MergedEntry`
- `plan_bib_write(state, operation) -> WritePlan`
- `execute_write_plan(plan, io_deps) -> WriteResult`

Guidelines:
- keep business rules in pure functions
- pass dependencies explicitly as function arguments
- keep filesystem and HTTP logic in thin wrapper functions
- avoid hidden caches or singleton-style global state
- model each pipeline stage as input data -> output data

### Recommended boundary
Browser and CLI should share the same backend pipeline.

Preferred browser handoff:
- browser extension sends current tab URL to local HTTP endpoint on localhost

Reason:
- simpler than native messaging
- one code path for browser and CLI
- easy to debug and script

Preferred backend shape:
- CLI parses args into plain command data
- HTTP handler parses request JSON into the same command data
- both call one ingest function such as `run_add(command, deps)`
- side-effectful dependencies are passed in explicitly

### External services
- `zotero/translation-server` for webpage translation and attachment discovery
- Semantic Scholar for update checks
- Crossref and OpenAlex as fallbacks

## Config Model

Use one config file under `~` or `$HOME`.

Suggested contents:
- translation-server URL
- list of configured bibs
  - `name`
  - `path`
  - optional `papers_dir`
  - optional `default`
- citekey format
- PDF filename format
- relative path policy
- similarity threshold
- update source preferences
- API listen address for local capture service

Config resolution should be functional:
- read raw config
- validate into plain config data
- derive computed defaults such as sibling `papers/`
- return either validated config or structured errors

## Storage Policy

- Treat the selected `.bib` as the system of record for metadata.
- Treat the selected bib's paper directory as the system of record for PDFs.
- Store PDF paths relative to the bib or papers directory when practical.
- Do not require a sidecar database in v1.

Additional rules:
- citekeys should be immutable once written unless the user explicitly requests migration
- PDF filenames should be deterministic from citekey by default
- canonical URLs should be normalized before duplicate checks by stripping known tracking parameters and normalizing DOI/arXiv forms

## Command Semantics

### Command naming rules
Prefer a small, regular command tree:
- top-level verbs for globally meaningful actions: `add`, `doctor`
- grouped nouns for scoped actions: `bib ...`, `tag ...`, `pdf ...`
- avoid one-off hyphenated verbs when the action clearly belongs to a resource group

This keeps the CLI easier to learn and keeps parsing/dispatch simple in a functional implementation.

### `pzi add`
- auto-detect DOI vs URL vs PDF URL
- auto-select bib when unambiguous
- otherwise require `--bib` or browser selection
- support optional `--tags`
- support `--dry-run`
- human-readable output by default
- machine-readable JSON output can be added later if needed, but dry-run output should already be structured enough to test

### `pzi tag list`
Two modes:
- `pzi tag list <citekey>`: list tags on one entry
- `pzi tag list`: list all tags in selected bib, ideally with counts

### `pzi search --tag`
Keep MVP narrow:
- filter entries in one selected bib by normalized tag
- output citekey plus a short human-readable summary
- this is the only supported search mode in MVP
- defer full-text and multi-field search until later expansion of the generic `search` command

Note:
- keep `pzi search --tag` in MVP because a broader generic `search` command is expected later

### `pzi bib list`
- list configured bibs
- indicate default bib
- show paths

### `pzi bib update <name>`
- post-MVP command for bib-scoped metadata refresh
- explicit bib name is clearer than a global `update-bib` verb
- supports `--dry-run`

### `pzi pdf retry <citekey>`
- retry PDF acquisition for an existing entry in one selected bib
- keep metadata unchanged unless a PDF path must be added
- explicit `pdf` grouping is more consistent than a standalone `retry-pdf`

### `pzi doctor`
Check:
- translation-server reachability
- config validity
- selected/default bib validity
- write access to bib and papers dir
- optional API availability

## Local HTTP API contract

MVP endpoints:
- `POST /capture`
- `GET /bibs`
- `GET /health`

`POST /capture` request:
- `url`
- optional `bib`
- optional `tags`
- optional `dry_run`

`POST /capture` response:
- `status`
- `bib`
- optional `citekey`
- optional `pdf_path`
- `message`
- optional structured `warnings`

`GET /bibs` response:
- configured bib names
- paths
- default bib marker

`GET /health` response:
- service status
- config status
- translation-server reachability summary

Rule:
- CLI and HTTP should report the same underlying result shape, only rendered differently at the boundary

## MVP Scope

### Include
- Python CLI
- local HTTP capture service
- Firefox/Chrome extension button
- translation-server integration
- DOI and URL ingestion through one `add` command
- `.bib` insert/update
- PDF download when available
- stable citekey-based PDF naming
- exact duplicate merge
- simple note-based similar-item hints
- multiple bib support
- tags via `keywords`
- `pdf retry`
- `doctor`
- dry-run
- file locking for writes
- `GET /bibs` and `GET /health` for browser support
- only tag-filter search via `pzi search --tag` for now

### Defer
- `bib update`
- advanced similarity scoring beyond simple note-based hints
- native messaging
- advanced browser UI
- global tag registry
- hierarchical tags
- aggressive automatic merging
- citation rewriting in TeX documents
- cloud sync
- advanced attachment scraping heuristics
- full-text search
- other non-tag search modes under `pzi search`

## Implementation Order

### Phase 1: Contracts and pure utilities
- internal record types
- bib config types
- input classifier
- DOI and URL normalization
- citekey generator
- filename formatter
- tag normalizer
- canonical URL normalization rules
- duplicate identity contracts

### Phase 2: Bib registry and config
- config loader
- bib resolution rules
- default bib handling
- validation

### Phase 3: BibTeX persistence
- read existing entries
- insert/update entries
- write `file`, `keywords`, `note`
- preserve formatting as much as practical
- add write locks

### Phase 4: Core ingest pipeline
- compose pure stages for classify -> fetch -> normalize -> match -> write
- define explicit dependency bundle for I/O edges
- make dry-run follow the same pipeline but skip side effects

### Phase 5: translation-server integration
- Python client for `/web` and `/search`
- normalize translator output into internal records
- live integration tests

### Phase 6: PDF pipeline
- attachment selection
- PDF validation
- temp download and atomic move
- bib-specific destination planning

### Phase 7: Exact match logic
- exact identity matcher
- enrichment rules

### Phase 8: CLI
- `add`
- `pdf retry`
- `tag add/remove/list`
- `search --tag`
- `bib list`
- `bib set-default`
- `doctor`

### Phase 9: Local HTTP API
- `POST /capture`
- `GET /bibs`
- `GET /health`
- share same ingest pipeline as CLI
- clear machine-readable response format

### Phase 10: Browser extension
- toolbar button
- current tab capture
- default bib behavior
- picker only when needed
- notification UI

### Phase 11: Similarity hints
- similar-item detector
- note annotation rules
- conservative thresholds and tests

### Phase 12: Hardening
- retries
- caching
- logging
- idempotency checks
- partial failure handling
- end-to-end tests

### Phase 13: Post-MVP update flow
- `bib update`
- preprint/published successor detection
- dry-run reporting
- field-preservation rules

## Risks and Mitigations

### PDF retrieval is weaker than metadata retrieval
Mitigation:
- metadata save succeeds even if PDF fails
- add `pzi pdf retry`
- keep logs

### False positives in update or similarity detection
Mitigation:
- conservative thresholds
- note-based hints instead of aggressive merges
- dry-run support

### Multiple bibs increase path complexity
Mitigation:
- keep one bib = one `.bib`
- require explicit config per bib
- store relative paths when possible

### BibTeX formatting churn
Mitigation:
- choose parser/writer carefully
- keep updates narrow and deterministic

## Summary

`pzi` should be a minimal hybrid tool:
- browser extension for one-click capture
- Python CLI for scriptable use
- local HTTP endpoint shared by both
- functional core with thin I/O edges
- Zotero translation-server for page translation
- one configured bib per `.bib` file
- per-bib PDF storage
- conservative exact-match updates
- note-based handling of possible similar items
- tags stored in `keywords`

This delivers the desired workflow with low conceptual overhead and a strong local-first design.
