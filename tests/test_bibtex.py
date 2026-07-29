from bibtex import _bibtex_key, _canonical_booktitle


def test_bibtex_key():
    key = _bibtex_key(["Jane Doe", "John Smith"], 2026, "A Great Streaming Method")
    assert key == "Doe2026Great"


def test_bibtex_key_no_authors_falls_back():
    key = _bibtex_key([], 2026, "The Method")
    assert key == "Unknown2026Method"


def test_canonical_booktitle_known_venue():
    assert "Neural Information Processing" in _canonical_booktitle("Proc. of NeurIPS 2025")


def test_canonical_booktitle_unknown_venue_passthrough():
    assert _canonical_booktitle("Some Obscure Workshop") == "Some Obscure Workshop"
