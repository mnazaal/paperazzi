# Releasing paperazzi

This document describes the release process for paperazzi.

## Version source of truth

The version lives in **one place**: `pyproject.toml` (`[project] version`).

- `src/pzi/__init__.py` reads it at runtime via `importlib.metadata`.
- `tools/build_extension.py` reads it at build time via `tomllib`.
- `browser-extension/manifest.base.json` has a placeholder that is overwritten
  on build.

Do not hardcode the version anywhere else. To check the current version:

```sh
grep '^version' pyproject.toml
```

## Versioning scheme

paperazzi follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html) with
[PEP 440](https://peps.python.org/pep-0440/) pre-release identifiers during
the initial beta series:

- `0.1.0bN` — pre-1.0 beta pre-releases (`b1`, `b2`, ...). Tags: `v0.1.0b1`,
  `v0.1.0b2`, etc.
- `0.x.y` — beta once the `bN` series ends. Breaking changes are allowed
  within `0.x`.
- `1.0.0` — first stable release.

While in `0.x`, bump the minor for new features and the patch for fixes.

## Release process

### 1. Update CHANGELOG.md

Move the `[Unreleased]` section's contents into a new versioned section:

```markdown
## [0.2.0] - 2025-07-15

### Added
- ...

## [Unreleased]
```

Update the comparison links at the bottom of the file.

### 2. Bump the version in pyproject.toml

Edit the `version` field under `[project]`:

```sh
# e.g., 0.1.0 → 0.2.0
# Linux:
sed -i 's/^version = "0.1.0"/version = "0.2.0"/' pyproject.toml
# macOS (BSD sed needs the empty backup suffix):
sed -i '' 's/^version = "0.1.0"/version = "0.2.0"/' pyproject.toml
```

### 3. Verify locally

```sh
uv pip install -e ".[dev]"
ruff check src tools tests
pyright
pytest -m "not browser" -q
python -m build
twine check dist/*.tar.gz dist/*.whl
```

### 4. Commit and tag

```sh
git add pyproject.toml CHANGELOG.md
git commit -m "release: v0.2.0"
git tag v0.2.0
git push origin main
git push origin v0.2.0
```

### 5. CI handles the rest

Pushing the tag triggers `.github/workflows/release.yml`:

1. **build** — builds sdist + wheel, runs twine check, builds the Firefox +
   Chrome extension zips (`tools/build_extension.py`), uploads all of it as a
   workflow artifact.
2. **github-release** — creates a GitHub Release with the changelog section as
   the release notes, attaches the sdist, wheel, and both extension zips
   (`paperazzi-capture-firefox.zip`, `paperazzi-capture-chrome.zip`) — so
   installing the extension no longer requires a repo checkout.
3. **pypi** — dormant. Only runs when the repo variable `PYPI_ENABLED` is set
   to `true` (see below).

### 6. Verify the release

- Check the GitHub Release page: `https://github.com/mnazaal/paperazzi/releases`
- Confirm the sdist, wheel, and both extension zips are attached.
- Smoke-test the install:

```sh
uv tool install 'paperazzi @ git+https://github.com/mnazaal/paperazzi.git@v0.2.0'
pzi --version
```

## Enabling PyPI (future)

paperazzi is not yet on PyPI. When ready:

1. **Register the project** on PyPI: `https://pypi.org/manage/account/`
2. **Configure trusted publishing** (OIDC, no API tokens):
   - Go to PyPI → Account settings → Publishing → Add a publisher.
   - Repository: `mnazaal/paperazzi`, workflow: `.github/workflows/release.yml`,
     environment: `pypi`.
3. **Create the `pypi` environment** in GitHub repo settings:
   - Settings → Environments → New environment → `pypi`.
4. **Set the repo variable** `PYPI_ENABLED=true`:
   - Settings → Secrets and variables → Actions → Variables → New variable.
5. **Update README** install instructions to use `pip install paperazzi` instead of
   the git-URL form.
6. **Update RELEASING.md** — remove this section and note that PyPI publishing
   is active.

After the first PyPI release, the git-URL install commands in the README remain
as an alternative for tracking `main` or unreleased versions.

## Distribution channels

| Channel | Status | Notes |
|---|---|---|
| GitHub Releases | Active | sdist, wheel, and both extension zips attached to each tag |
| PyPI | Dormant | wired, not yet enabled |
| Homebrew (custom tap) | Planned | `homebrew-paperazzi` tap, git archive URL |
| AUR | Planned | stable PKGBUILD, updated per tag |
| Nix flake | Planned | `github:mnazaal/paperazzi` |
