# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `pzi init` — create config from template
- `pzi add <doi|url>` — capture paper metadata + PDF
- `pzi pdf retry|attach` — manage PDF attachments
- `pzi tag add|remove|list` and `pzi search --tag` — tag-based organization
- `pzi list`, `pzi set-default`, `pzi update` — bib management + metadata enrichment
- `pzi doctor` — health check (config + translation-server)
- `pzi server` — HTTP API for browser extension (auto-starts translation-server)
- Metadata fallbacks: Crossref, OpenAlex, Semantic Scholar
- FlareSolverr integration for Cloudflare-gated pages
- Playwright browser hook for PDF discovery
- Similarity-based duplicate detection
- File locking for concurrent bib writes
- Local HTTP API hardening: origin allowlist, optional token auth, request body limit
- Browser extension API token storage + `X-Pzi-Token` forwarding
- Browser extension human-readable popup summary with raw response details
- Browser extension smoke-test checklist for open-access, opaque PDF, and authenticated publisher flows
- Browser extension current-tab PDF candidate support for PDF viewer URLs without `.pdf` suffixes
- CI workflow for ruff, pyright, coverage-gated tests, and package build
- Coverage tooling with branch coverage and baseline fail-under gate
- Structured PDF capture status fields (`pdf_url`, `pdf_status`, `pdf_error`, `pdf_suggestion`) so blocked publisher downloads tell users to use the browser extension or `browser_pdf_cmd`
- `pzi promote` now keeps preprints by default and uses `--replace` for in-place promotion
- Top-level `--target` support for configured library names, configured bib paths, and direct `.bib` paths; `search`, `update`, and `promote` accept multiple targets as `--target a.bib b.bib`
- `contact_email` / `contact_email_cmd` metadata API identity, used for Crossref/OpenAlex and as Unpaywall fallback
- `pzi promote` summary counters and provider-error/no-candidate skip explanations

### Fixed
- Type-checker failures in the Playwright browser hook and callback-based tests
- Direct PDF download failures now distinguish HTTP 401/403 and HTML challenge pages from generic network errors

[Unreleased]: https://github.com/mnazaal/pzi/compare/v0.1.0...HEAD
