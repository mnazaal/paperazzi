"""The capture handoff rejects service_kwargs that collide or do not exist."""

from __future__ import annotations

import pytest

from pzi.capture_core import capture_to_bib
from pzi.capture_models import AuthHints, CaptureInput, CaptureOptions, PdfCandidate

_CAPTURE = CaptureInput(
    value="https://example.com/paper",
    pdf_candidates=(PdfCandidate("https://example.com/a.pdf", source="cli"),),
    auth_hints=AuthHints(cookies="sid=1"),
)


def _run(service_kwargs, add_fn):
    return capture_to_bib(
        _CAPTURE,
        CaptureOptions(),
        config_path="/c.toml",
        home_dir="/h",
        add_fn=add_fn,
        service_kwargs=service_kwargs,
    )


@pytest.mark.parametrize(
    "key",
    [
        "config_path",
        "home_dir",
        "value",
        "record_overrides",
        "bib_selector",
        "dry_run",
        "force_new",
        "metadata_strict",
        "pdf_url_candidates",
        "cookies",
    ],
)
def test_service_kwargs_cannot_override_capture_arguments(key: str) -> None:
    calls: list[dict] = []

    def add_fn(**kwargs):
        calls.append(kwargs)
        return {"status": "ok"}

    with pytest.raises(TypeError, match=key):
        _run({key: "x"}, add_fn)
    assert calls == []


def test_unknown_service_kwarg_fails_before_the_real_service_runs() -> None:
    from pzi.add_service import add_input_to_bib

    with pytest.raises(TypeError, match="bogus"):
        _run({"bogus": 1}, add_input_to_bib)


def test_supported_injections_still_reach_a_fake() -> None:
    calls: list[dict] = []

    def add_fn(**kwargs):
        calls.append(kwargs)
        return {"status": "ok"}

    sentinel = object()
    _run({"fetch_web": sentinel, "batch_preview": sentinel}, add_fn)
    assert calls[0]["fetch_web"] is sentinel
    assert calls[0]["batch_preview"] is sentinel


@pytest.mark.parametrize("key", ["pdf_url_candidates", "cookies"])
def test_optional_capture_arguments_are_owned_even_when_absent(key: str) -> None:
    with pytest.raises(TypeError, match=key):
        capture_to_bib(
            CaptureInput(value="10.1/x"),
            CaptureOptions(),
            config_path="/c.toml",
            home_dir="/h",
            add_fn=lambda **kw: {"status": "ok"},
            service_kwargs={key: "x"},
        )
