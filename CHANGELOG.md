# Changelog

All notable changes to pzi are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- **Quality**: 100% test coverage, mypy/pyright clean across 46 source files,
  ~90 test files.
- **Security**: SSRF guards for FlareSolverr, tar-slip guard for imports,
  loopback-only HTTP binding, optional token auth, documented security model.
- **Shell completions**: bash/zsh completions via `argcomplete`, included in the
  base install.

### Changed

- Playwright is an optional extra (`pip install 'pzi[playwright]'`) instead of a
  hard dependency. The base install is lighter; the browser-profile PDF fallback
  is only needed by users who configure `browser_pdf_cmd`.
- `bibtexparser` pin is `>=2.0.0b9,<3` to allow patch-level updates within the v2
  beta series.

### Known Limitations

- APIs can rate-limit; promotion is best-effort.
- Browser extension install is manual.
- Touched `.bib` entries may be re-serialized.
- No native Windows support (WSL2 works).
- No sync, group libraries, or desktop reader (by design).
- Not yet on PyPI; install from GitHub for now.

[Unreleased]: https://github.com/mnazaal/paperazzi/compare/v0.1.0b1...HEAD
[0.1.0b1]: https://github.com/mnazaal/paperazzi/releases/tag/v0.1.0b1