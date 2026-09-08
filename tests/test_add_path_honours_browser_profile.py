"""`browser_profile_path` reaches `pzi add`, not only `pdf retry` and `promote`.

Item 620. `browser_profile_path` is a documented config key that was read by
`pzi server` alone. Phase 34 wired it into `pdf_service` and `promote_service`;
the add path was left out because its knobs travel on a frozen `CaptureContext`
and a first attempt at threading them turned six capture tests red. This is the
seam that was missing, plus the end of the wire.
"""

from __future__ import annotations

from typing import Any

from pzi.capture_context import build_capture_context
from pzi.capture_local_pdf import attach_pdf_if_available
from pzi.pdf_planning import PdfFallbackSettings

_PROFILE = "~/.mozilla/firefox/abc.default-release"


def _loaded_config(tmp_path: Any) -> tuple[Any, Any]:
    """A real loaded config, so the context gets the total `AppConfig` it wants."""
    from pzi.config import load_config_file

    bib = tmp_path / "t.bib"
    bib.write_text("", encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'translation_server_url = "http://127.0.0.1:1969"\n'
        f'browser_profile_path = "{_PROFILE}"\n'
        "\n[[bibs]]\n"
        'name = "t"\n'
        f'path = "{bib}"\n'
        "default = true\n",
        encoding="utf-8",
    )
    loaded = load_config_file(str(config_path), home_dir=str(tmp_path))
    assert loaded["config"] is not None, loaded["errors"]
    return loaded["config"], loaded["config"]["bibs"][0]


def test_capture_context_carries_settings_resolved_from_config(tmp_path) -> None:
    config, bib = _loaded_config(tmp_path)
    context = build_capture_context(
        config=config, bib=bib, browser_pdf_cmd_override=None, browser=None
    )
    assert context.settings.browser_profile == _PROFILE
    # The browser is inferred from the profile path, not stored separately.
    assert context.settings.browser == "firefox"


def test_settings_are_a_single_resolved_value_on_the_context(tmp_path) -> None:
    """Three seams build these knobs from config. Resolving them per call site
    is how the three came to disagree; the context resolves them once."""
    config, bib = _loaded_config(tmp_path)
    context = build_capture_context(
        config=config, bib=bib, browser_pdf_cmd_override=None, browser=None
    )
    assert isinstance(context.settings, PdfFallbackSettings)


def test_attach_forwards_settings_to_the_fallback_chain() -> None:
    """End of the wire: the profile has to survive as far as the fetch, which is
    the only thing that consumes it."""
    seen: dict[str, Any] = {}

    def fake_fetch(**kwargs: Any):
        seen.update(kwargs)
        raise AssertionError("stop here — we only need the kwargs")

    settings = PdfFallbackSettings.from_config({"browser_profile_path": _PROFILE}, env={})

    try:
        attach_pdf_if_available(
            # A citekey is required: `attach_pdf_if_available` returns early
            # without one, before it reaches the fetch this test is about.
            record={  # type: ignore[arg-type]
                "pdf_url": "https://example.com/a.pdf",
                "citekey": "smith-paper-2024",
            },
            papers_dir="/tmp/papers",
            dry_run=False,
            fetch_binary=None,
            fetch_pdf=fake_fetch,
            settings=settings,
        )
    except AssertionError:
        pass

    assert "settings" in seen, "attach dropped the settings before the fetch"
    assert seen["settings"].browser_profile == _PROFILE


def test_attach_still_works_without_settings() -> None:
    """The parameter is optional: `attach_pdf_if_available` has callers in tests
    and in the HTTP path that do not carry a context."""
    record, warnings = attach_pdf_if_available(
        record={},  # type: ignore[arg-type]
        papers_dir="/tmp/papers",
        dry_run=False,
        fetch_binary=None,
    )
    assert record == {}
    assert warnings == []
