import json

import vault

NOTE = '''---
title: "A Great Paper"
authors: "Jane Doe, John Smith"
date: "2026-01-15"
bookmarked: true
verbosity: 2
---

# A Great Paper
Body text.
'''


def test_frontmatter_and_fm_field():
    fm = vault.frontmatter(NOTE)
    assert vault.fm_field(fm, "title") == "A Great Paper"
    assert vault.fm_field(fm, "authors") == "Jane Doe, John Smith"
    assert vault.fm_field(fm, "verbosity") == "2"


def test_frontmatter_missing_returns_empty():
    assert vault.frontmatter("# No frontmatter here") == ""
    assert vault.fm_field("", "title") == ""


def test_note_meta(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, "RESEARCH_PATH", str(tmp_path))
    (tmp_path / "A Great Paper.md").write_text(NOTE, encoding="utf-8")

    meta = vault.note_meta("A Great Paper.md")
    assert meta["title"] == "A Great Paper"
    assert meta["bookmarked"] is True
    assert meta["verbosity"] == 2


def test_paper_index_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, "INDEX_PATH", str(tmp_path / "index.json"))
    assert vault.load_paper_index() == {}

    vault.save_paper_index({"2405.12345": {"title": "T", "file": "T.md"}})
    assert vault.load_paper_index() == {"2405.12345": {"title": "T", "file": "T.md"}}
    assert json.loads((tmp_path / "index.json").read_text())
