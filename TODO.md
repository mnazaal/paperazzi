# pzi Coverage & Slimming TODO

> Target: maintain >=85% total branch coverage while expanding module-level coverage.
> Style: functional programming — pure logic at core, thin I/O edges.

## P0 — Restore green verification

- [x] Fix `tests/test_promote_service.py` config fixture so app-level keys are written before `[[bibs]]`
- [x] Align empty-query promotion test with intended behavior: non-preprint/blank records do not call providers
- [x] Remove unused `pytest` import from `tests/test_promote_service.py`
- [x] Run targeted promote tests, `ruff`, `pyright`, and full `pytest`
- [x] Add Node-backed pytest coverage for browser extension capture, bib lookup, auth token, and PDF byte streaming
- [x] Add mocked Playwright edge tests for browser PDF hook install, cleanup, discovery, and download branches
- [x] Raise coverage gate from 78% to 85%

## P0 — High-impact coverage gaps

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
