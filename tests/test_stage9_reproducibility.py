"""Stage 9 gate: same seed -> byte-identical outputs (separate processes); other seed -> different."""

import pytest

from generator import reproducibility

DATA_FILES = {"csv/transactions.csv", "csv/alerts.csv", "json/transactions.json", "sql/seed.sql",
              "ground_truth.csv", "validation_summary.json"}


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    return reproducibility.check("starter", 20261022, 20261023, tmp_path_factory.mktemp("repro"))


def test_same_seed_is_byte_identical(result):
    assert result["same_seed_identical"], result["same_seed_differing_files"]
    assert result["same_seed_differing_files"] == []
    assert result["checksum_list_digest"]["A"] == result["checksum_list_digest"]["B"]
    assert result["row_counts"]["A"] == result["row_counts"]["B"]


def test_different_seed_changes_the_data(result):
    assert DATA_FILES <= set(result["other_seed_differing_files"])
    assert result["checksum_list_digest"]["A"] != result["checksum_list_digest"]["C"]
    assert set(result["other_seed_identical_files"]) <= {
        "sql/schema.sql", "docs/data_dictionary.md", "docs/er_diagram.md", "docs/risk_rules.md",
        "docs/sample_queries.sql", "csv/risk_rules.csv", "json/risk_rules.json"}
    print(f"\nA={result['checksum_list_digest']['A']}\nB={result['checksum_list_digest']['B']}\n"
          f"C={result['checksum_list_digest']['C']}\n"
          f"seed-independent files: {result['other_seed_identical_files']}")


def test_parameters_hold_in_every_run(result):
    for run in "ABC":
        counts = result["row_counts"][run]
        assert (counts["customers"], counts["accounts"], counts["transactions"], counts["alerts"]) == (200, 280, 4000, 40)


def test_report_renders(result):
    text = reproducibility.report(result)
    assert "IDENTICAL" in text and "| transactions | 4000 | 4000 | 4000 |" in text
