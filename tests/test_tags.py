from pzi.tags import normalize_tag, normalize_tags, parse_tag_csv


def test_normalize_tag_lowercases_and_slugifies() -> None:
    assert normalize_tag("Machine Learning") == "machine-learning"


def test_normalize_tag_strips_punctuation() -> None:
    assert normalize_tag("graphs, trees & parsing!") == "graphs-trees-parsing"


def test_normalize_tag_transliterates_unicode() -> None:
    assert normalize_tag("Café") == "cafe"


def test_normalize_tag_rejects_empty_result() -> None:
    assert normalize_tag("!!!") is None


def test_normalize_tags_deduplicates_and_sorts() -> None:
    assert normalize_tags(["ML", "machine learning", "ml", " Machine-Learning "]) == [
        "machine-learning",
        "ml",
    ]


def test_parse_tag_csv_normalizes_multiple_values() -> None:
    assert parse_tag_csv("NLP, machine learning, NLP ,, graphs ") == [
        "graphs",
        "machine-learning",
        "nlp",
    ]
