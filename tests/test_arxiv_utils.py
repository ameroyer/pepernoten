import pytest

from arxiv_utils import extract_arxiv_id


@pytest.mark.parametrize("raw, expected", [
    ("https://arxiv.org/abs/2405.12345", "2405.12345"),
    ("https://arxiv.org/pdf/2405.12345v2", "2405.12345"),
    ("2405.12345", "2405.12345"),
    (2405.12345, "2405.12345"),  # fire parses bare IDs as floats
])
def test_extract_arxiv_id(raw, expected):
    assert extract_arxiv_id(raw) == expected


def test_extract_arxiv_id_invalid():
    with pytest.raises(ValueError):
        extract_arxiv_id("not a paper id")
