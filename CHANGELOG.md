# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `pzi init` — create config from template
- `pzi add <doi|url>` — capture paper metadata + PDF
- `pzi pdf retry|attach` — manage PDF attachments
- `pzi tag add|remove|list|search` — tag-based organization
- `pzi bib list|set-default|update` — bib management + metadata enrichment
- `pzi doctor` — health check (config + translation-server)
- `pzi serve` — HTTP API for browser extension
- Metadata fallbacks: Crossref, OpenAlex, Semantic Scholar
- FlareSolverr integration for Cloudflare-gated pages
- Playwright browser hook for PDF discovery
- Similarity-based duplicate detection
- File locking for concurrent bib writes
- Local HTTP API hardening: origin allowlist, optional token auth, request body limit
- Browser extension API token storage + `X-Pzi-Token` forwarding
- CI workflow for ruff, pyright, coverage-gated tests, and package build
- Coverage tooling with branch coverage and baseline fail-under gate

### Fixed
- Type-checker failures in the Playwright browser hook and callback-based tests

[Unreleased]: https://github.com/mnazaal/pzi/compare/v0.1.0...HEAD
