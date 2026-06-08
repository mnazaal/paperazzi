from pzi.bib_repository import plan_bib_write
from pzi.bibtex import record_to_bibtex_entry
from pzi.capture_local_pdf import build_add_record_result, dry_run_diff, plan_with_applied_record


def test_plan_with_applied_record_rebases_citekey() -> None:
    plan = {"record": {"citekey": "old", "doi": "10.1234/a"}, "action": "insert"}
    updated_entry = record_to_bibtex_entry(
        {"citekey": "new", "doi": "10.1234/a", "title": "Paper"}
    )

    result = plan_with_applied_record(
        plan,
        {"doi": "10.1234/a"},  # type: ignore[arg-type]
        [updated_entry],
    )

    assert result["record"]["citekey"] == "new"
    assert result["entry"] is updated_entry


def test_build_add_record_result_shapes_dry_run_message() -> None:
    plan = plan_bib_write(
        {"citekey": "smith2024paper", "title": "Paper"},
        [],
    )

    result = build_add_record_result(
        bib={"name": "ml", "path": "/tmp/ml.bib"},
        plan=plan,
        warnings=[],
        dry_run=True,
    )

    assert result["status"] == "ok"
    assert result["message"] == "would insert entry"
    assert result["citekey"] == "smith2024paper"


def test_dry_run_diff_mentions_new_entry() -> None:
    plan = plan_bib_write(
        {"citekey": "smith2024paper", "title": "Paper"},
        [],
    )

    diff = dry_run_diff(plan=plan, existing_entries=[])

    assert "new entry" in diff
    assert "smith2024paper" in diff
