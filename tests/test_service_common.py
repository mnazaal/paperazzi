from __future__ import annotations

from pathlib import Path

from pzi import errors, exit_codes
from pzi.config import AppConfig, BibResolutionFailure, load_bib_target
from pzi.errors import exit_code_for_error
from pzi.http_status import status_for_service_result


def _dummy_config(tmp_path: Path, default_bib_name: str = "main") -> tuple[str, AppConfig]:
    """Create a minimal config and write it to a temp path."""
    config_data = f"""
translation_server_url = "http://127.0.0.1:1969"
api_listen_host = "127.0.0.1"
api_listen_port = 8765

[[bibs]]
name = "{default_bib_name}"
path = "/some/path/ml.bib"
papers_dir = "/some/path/papers"
default = true
"""
    config_path = tmp_path / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_data)
    return str(config_path), {"bibs": [], "dummy": True}  # type: ignore[dict-item]


def test_load_bib_target_success(tmp_path: Path) -> None:
    bib_path = tmp_path / "test.bib"
    bib_path.write_text("")

    config_text = f"""
translation_server_url = "http://127.0.0.1:1969"
api_listen_host = "127.0.0.1"
api_listen_port = 8765

[[bibs]]
name = "main"
path = "{bib_path}"
papers_dir = "{tmp_path}/papers"
default = true
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)

    result = load_bib_target(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
    )
    assert isinstance(result, tuple)
    config, bib = result
    assert bib["name"] == "main"
    assert bib["path"] == str(bib_path)


def test_load_bib_target_missing_config_returns_errors(tmp_path: Path) -> None:
    result = load_bib_target(
        config_path=str(tmp_path / "nonexistent.toml"),
        home_dir=str(tmp_path),
        bib_selector=None,
    )
    assert isinstance(result, BibResolutionFailure)
    assert result.errors


def test_load_bib_target_ambiguous_selection_returns_errors(tmp_path: Path) -> None:
    bib1 = tmp_path / "test1.bib"
    bib2 = tmp_path / "test2.bib"
    bib1.write_text("")
    bib2.write_text("")

    config_text = f"""
translation_server_url = "http://127.0.0.1:1969"
api_listen_host = "127.0.0.1"
api_listen_port = 8765

[[bibs]]
name = "main"
path = "{bib1}"
papers_dir = "{tmp_path}/papers1"

[[bibs]]
name = "alt"
path = "{bib2}"
papers_dir = "{tmp_path}/papers2"
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)

    result = load_bib_target(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
    )
    assert isinstance(result, BibResolutionFailure)
    # The structured reason is what callers branch on, not the message text.
    assert result.reason == "target_unresolved"


def test_load_bib_target_by_name(tmp_path: Path) -> None:
    bib1 = tmp_path / "test1.bib"
    bib2 = tmp_path / "test2.bib"
    bib1.write_text("")
    bib2.write_text("")

    config_text = f"""
translation_server_url = "http://127.0.0.1:1969"
api_listen_host = "127.0.0.1"
api_listen_port = 8765

[[bibs]]
name = "main"
path = "{bib1}"
papers_dir = "{tmp_path}/papers1"

[[bibs]]
name = "alt"
path = "{bib2}"
papers_dir = "{tmp_path}/papers2"
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)

    result = load_bib_target(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector="alt",
    )
    assert isinstance(result, tuple)
    config, bib = result
    assert bib["name"] == "alt"


def test_load_bib_target_invalid_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("not toml at all\n=invalid")

    result = load_bib_target(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
    )
    assert isinstance(result, BibResolutionFailure)
    assert result.reason == "config_invalid"


# ── structured failure reasons ────────────────────────────────────
#
# One vocabulary (`pzi.errors.REASON_*`) drives both the CLI exit code and the
# HTTP status, so a service classifies a failure once and both surfaces agree.


def test_exit_code_maps_every_reason_in_the_vocabulary() -> None:
    """Each documented reason has a deliberate exit code, not the catch-all."""
    assert exit_code_for_error({"reason": errors.REASON_NOT_FOUND}) == exit_codes.NOT_FOUND
    assert exit_code_for_error({"reason": errors.REASON_USAGE}) == exit_codes.USAGE
    assert exit_code_for_error({"reason": errors.REASON_CONFIG}) == exit_codes.ENVIRONMENT
    assert exit_code_for_error({"reason": errors.REASON_UNAVAILABLE}) == exit_codes.ENVIRONMENT
    assert exit_code_for_error({"reason": errors.REASON_CONFLICT}) == exit_codes.ENVIRONMENT


def test_exit_code_for_unclassified_failure_is_never_findings() -> None:
    """An unknown or absent reason must not be mistaken for "ran fine, found something".

    `1` is reserved for a successful run with something to report, so a failure
    that nobody classified has to land on ENVIRONMENT rather than anything a
    script would read as success-with-findings.
    """
    for result in ({}, {"reason": "something-nobody-defined"}, {"reason": 7}):
        assert exit_code_for_error(result) == exit_codes.ENVIRONMENT


def test_http_status_maps_every_reason_in_the_vocabulary() -> None:
    assert status_for_service_result({"status": "error", "reason": errors.REASON_NOT_FOUND}) == 404
    assert status_for_service_result({"status": "error", "reason": errors.REASON_USAGE}) == 400
    assert status_for_service_result({"status": "error", "reason": errors.REASON_CONFIG}) == 400
    assert (
        status_for_service_result({"status": "error", "reason": errors.REASON_UNAVAILABLE}) == 503
    )
    assert status_for_service_result({"status": "error", "reason": errors.REASON_CONFLICT}) == 409


def test_a_citekey_cannot_change_the_status_code() -> None:
    """The status must not depend on how the user named a paper.

    Message text was read for keywords, testing `"config" in text` before
    `"not found" in text` — and citekeys are echoed into these messages, so
    `POST /tags/add` on a missing entry answered 404 for `nosuch2020` and 400
    for `myconfig2020`: the same failure, two statuses. The heuristic is gone
    rather than reordered, because the message is the user's data.
    """
    for citekey in ("nosuch2020", "myconfig2020", "library2020"):
        classified = {
            "status": "error",
            "reason": errors.REASON_NOT_FOUND,
            "error": f"citekey not found: {citekey}",
        }
        assert status_for_service_result(classified) == 404

    # Unclassified, every one of them takes the same default — including the
    # three the heuristic disagreed on. A service that forgets to classify is
    # now uniformly wrong in one direction rather than sometimes right.
    for citekey in ("nosuch2020", "myconfig2020", "library2020"):
        unclassified = {"status": "error", "error": f"not found: {citekey}"}
        assert status_for_service_result(unclassified) == 400


def test_http_status_ok_result_is_200_regardless_of_reason() -> None:
    assert status_for_service_result({"status": "ok", "reason": errors.REASON_NOT_FOUND}) == 200


def test_the_three_usage_refusals_exit_2_not_5(tmp_path: Path, write_app_config) -> None:
    """A mistake the user must retype is not "this machine cannot run it".

    `exit_code_for_error` mapped every failure without `reason == "not_found"`
    to ENVIRONMENT, and no service produced any other reason — four of the five
    `REASON_*` constants had no producer at all, so the vocabulary both mappers
    were written against existed only on paper. These are the three refusals
    that are unambiguously the invocation's fault.
    """
    from pzi import exit_codes
    from pzi.dedupe_service import merge_duplicates
    from pzi.errors import exit_code_for_error
    from pzi.search_service import search_bib
    from pzi.tag_service import add_tags

    config_path = write_app_config(tmp_path)
    (tmp_path / "ml.bib").write_text("@article{real2024, title = {Real}}\n")
    located = {"config_path": config_path, "home_dir": str(tmp_path), "bib_selector": None}

    self_merge = merge_duplicates(
        bib_path=str(tmp_path / "ml.bib"), citekey_a="real2024", citekey_b="real2024"
    )
    no_valid_tags = add_tags(**located, citekey="real2024", tags=["!!"])
    unsearchable = search_bib(**located, tag="!!")

    for result in (self_merge, no_valid_tags, unsearchable):
        assert result["status"] == "error"
        assert result["reason"] == errors.REASON_USAGE
        assert exit_code_for_error(result) == exit_codes.USAGE
        assert status_for_service_result(result) == 400
