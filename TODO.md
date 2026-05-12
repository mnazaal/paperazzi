# pzi Coverage & Slimming TODO

> Target: **100%** branch coverage. Current: **97.53%** (54 lines remaining).
> Style: functional programming — pure logic at core, thin I/O edges.
> Full plan: [PLAN_100PCT.md](PLAN_100PCT.md)

## P0 — Restore green verification ✅ (DONE)

- [x] Fix promote-service config fixture + empty query test
- [x] Remove unused imports
- [x] Add Node-backed browser extension JS tests
- [x] Add mocked Playwright edge tests for browser PDF hook
- [x] Raise coverage gate: 78% → 97%
- [x] Add 29 edge-test files covering all major modules

## P1 — Remaining 54 lines for 100% (see PLAN_100PCT.md)

### browser_pdf_hook.py (74% → 95%, 12 lines remaining)
- [ ] Phase 1a: Extract pure JS builder functions (`build_discovery_js`, `build_post_click_js`)
- [ ] Phase 1b: Extract pure URL processing (`extract_post_click_url`)
- [ ] Phase 1c-1d: Make browser I/O injectable in `discover_pdf_url` / `download_pdf`
- [ ] Phase 3b: Container/Playwright integration test for I/O wrappers

### http_api.py (83% → 95%, 8 lines remaining)
- [ ] Phase 2a: Extract `process_get_request` / `process_post_request` pure functions
- [ ] Phase 2b: Table-driven tests for extracted pure functions

### flaresolverr.py + translation_server.py (89% / 97%, 5+2 lines remaining)
- [ ] Phase 3a: Add `pytest-httpserver` dev dependency
- [ ] Phase 3a: Replace urllib mocking with local HTTP server fixture

### Pure logic edge cases (~28 lines across 18 modules)
- [ ] Phase 4a: Table-driven `@pytest.mark.parametrize` tests for `merge.py`, `update_service.py`, `promote_service.py`
- [ ] Phase 4b: Parametrize remaining pure modules (`add_service.py`, `cli.py`, `pdf.py`, `pdf_metadata.py`, `preprint_detector.py`, `identifiers.py`, `search_service.py`, `similarity.py`, `tag_service.py`, `bib_repository.py`, `crossref.py`, `europepmc.py`, `html_metadata.py`, `openalex.py`, `pdf_discovery.py`, `semantic_scholar.py`)

## P2 — Cleanup after 100%

- [ ] Raise coverage gate to `100` in `pyproject.toml`
- [ ] Remove `mypy ignore_errors = true` for some modules (start with pure ones)
- [ ] Add `@pytest.mark.browser` labels for containerized browser tests
- [ ] Add `pytest-httpserver` to dev dependencies

## P0 — High-impact coverage gaps ✅ (DONE)

### browser_pdf_hook.py (74%)
- [ ] Test `_ensure_browser` success path (mock `sync_playwright`, `subprocess.run`)
- [ ] Test `_ensure_browser` install-failure path
- [ ] Test `_dismiss_cookie_banners` with mocked page
- [ ] Test `_click_downloadish_links` with mocked page
- [ ] Test `_launch_browser` profile vs no-profile branches (mock playwright)
- [ ] Test `_close_browser` tuple vs persistent-context cleanup
- [ ] Test `discover_pdf_url` post-click re-evaluation branch
- [ ] Test `download_pdf` candidate-link fallback branch

### cli.py (88%)
- [ ] Test `init` command success + already-exists
- [ ] Test `add` command with DOI, URL, local PDF, stdin
- [ ] Test `pdf` command with URL vs local path
- [ ] Test `tag` add/remove/list
- [ ] Test `search` with query, tags, year filters
- [ ] Test `bib` export with format variants
- [ ] Test `doctor` command
- [ ] Test `serve` command with security config
- [ ] Test `--config` override path
- [ ] Test error handling for missing config / corrupt config

## P1 — Medium-impact gaps

### promote_service.py (86%)
- [ ] Extract pure promote logic (ranking, selection)
- [ ] Test promote with mocked bib repo + PDF dir
- [ ] Test interactive vs non-interactive modes
- [ ] Test dry-run path

### setup_service.py (72%)
- [ ] Test setup with fresh directory
- [ ] Test setup with existing config (no overwrite)
- [ ] Test template rendering edge cases

### update_service.py (71%)
- [ ] Test update with no changes
- [ ] Test update with metadata changes
- [ ] Test update with PDF re-acquisition

### pdf_service.py (77%)
- [ ] Test PDF acquisition with all fallback methods
- [ ] Test PDF re-acquisition
- [ ] Test missing PDF detection

### http_api.py (83%)
- [ ] Test `/health` endpoint
- [ ] Test `/bibs` GET with filters
- [ ] Test error paths (500, malformed JSON)
- [ ] Test CORS preflight with disallowed origin

## P2 — Slimming / functional cleanup

- [ ] `pdf_discovery.py` — split pure candidate extraction from I/O
- [ ] `add_service.py` — reduce orchestration surface; extract pure decision tree
- [ ] `fetch_helpers.py` — add timeout/size-limit tests; consider pure retry logic
- [ ] `html_metadata.py` — extract pure parser from network I/O
- [ ] `flaresolverr.py` — test retry/backoff logic

## P3 — Polish

- [ ] `__main__.py` — 1 line, trivial but nice to have
- [ ] `doctor_service.py` — test all diagnostic checks
- [ ] `doaj.py` / `europepmc.py` — test error branches
- [ ] `config.py` — test all validation edge cases

## Done

- [x] HTTP API security config + pure request validation
- [x] Type-check fixes (mypy + pyright green)
- [x] Coverage tooling + 78% threshold
- [x] CI workflow (ruff, mypy, pyright, pytest-cov, build)
- [x] `browser_pdf_hook.py` pure helpers + integration tests (chromium + firefox)
- [x] `pdf.py` deduplicate atomic writes + expand fallback tests
