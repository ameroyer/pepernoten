import json
import os

import pytest

import mcp_backend as mb

NOTE = """---
title: "A Great Paper"
authors: "Jane Doe"
arxiv_id: "2405.12345"
tags:
  - kv-cache
  - streaming-video
---

# A Great Paper
Sliding window attention over video tokens.
"""

OTHER_NOTE = """---
title: "Another Paper"
tags:
  - diffusion
---

# Another Paper
Latent diffusion for world models.
"""


@pytest.fixture
def vault(tmp_path):
    research = tmp_path / "Research"
    topics = research / "Topics"
    topics.mkdir(parents=True)
    (research / "A Great Paper.md").write_text(NOTE, encoding="utf-8")
    (research / "Another Paper.md").write_text(OTHER_NOTE, encoding="utf-8")
    (topics / "my-topic.md").write_text("---\ntopic: T\n---\n\n## Introduction\n", encoding="utf-8")
    (research / ".paper_index.json").write_text(
        json.dumps({"2405.12345": {"title": "A Great Paper", "file": "A Great Paper.md"}}))
    (research / ".tag_index.json").write_text(json.dumps({"tags": ["kv-cache", "diffusion"]}))
    (topics / ".topic_index.json").write_text(json.dumps({"my-topic": {"name": "T", "papers": []}}))
    (tmp_path / "secret.txt").write_text("token")
    return tmp_path


@pytest.fixture
def backend(vault):
    return mb.LocalBackend(str(vault))


# ── path confinement ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "", "../pepernoten_cli.py", "../../etc/passwd.md", "/etc/passwd.md",
    "~/notes.md", "Topics/../../secret.txt", "a/./b.md", "..\\..\\secret.md",
])
def test_check_relpath_rejects_escapes(bad):
    with pytest.raises(mb.VaultBackendError):
        mb._check_relpath(bad)


def test_check_relpath_rejects_non_markdown():
    with pytest.raises(mb.VaultBackendError):
        mb._check_relpath("secret.txt")
    with pytest.raises(mb.VaultBackendError):
        mb._check_relpath(".bibtex_cache.json")


def test_check_relpath_allows_notes_and_indices():
    assert mb._check_relpath("A Great Paper.md") == "A Great Paper.md"
    assert mb._check_relpath("Topics/my-topic.md") == "Topics/my-topic.md"
    assert mb._check_relpath(".paper_index.json") == ".paper_index.json"


def test_local_backend_blocks_symlink_escape(vault, backend):
    outside = vault.parent / "outside.md"
    outside.write_text("outside the vault")
    os.symlink(outside, vault / "Research" / "sneaky.md")
    with pytest.raises(mb.VaultBackendError):
        backend.read_text("sneaky.md")


# ── local backend reads ──────────────────────────────────────────────────────

def test_local_list_and_read(backend):
    assert backend.list_markdown() == ["A Great Paper.md", "Another Paper.md", "Topics/my-topic.md"]
    assert "Sliding window" in backend.read_text("A Great Paper.md")
    with pytest.raises(mb.VaultBackendError):
        backend.read_text("Nonexistent.md")


def test_local_indices(backend):
    assert "2405.12345" in backend.paper_index()
    assert backend.tag_index() == ["kv-cache", "diffusion"]
    assert "my-topic" in backend.topic_index()


def test_local_backend_requires_research_dir(tmp_path):
    with pytest.raises(mb.VaultBackendError):
        mb.LocalBackend(str(tmp_path))
    with pytest.raises(mb.VaultBackendError):
        mb.LocalBackend(str(tmp_path / "missing"))


# ── github backend validation (no network at init) ───────────────────────────

@pytest.mark.parametrize("repo", ["", "no-slash", "a/b/c", "own er/repo", "-x/repo", "a/b;rm"])
def test_github_rejects_bad_repo(repo):
    with pytest.raises(mb.VaultBackendError):
        mb.GitHubBackend(repo)


def test_github_rejects_bad_branch_and_root():
    with pytest.raises(mb.VaultBackendError):
        mb.GitHubBackend("owner/repo", branch="a b")
    with pytest.raises(mb.VaultBackendError):
        mb.GitHubBackend("owner/repo", root="../up")


def test_github_valid_init_is_offline():
    b = mb.GitHubBackend("owner/repo", token="secret", branch="main", root="Research")
    assert "owner/repo" in b.description
    assert "secret" not in b.description


# ── configuration ────────────────────────────────────────────────────────────

def test_backend_from_env_selects_github():
    b = mb.backend_from_env({"PEPERNOTEN_GITHUB_REPO": "owner/repo"})
    assert isinstance(b, mb.GitHubBackend)


def test_backend_from_env_selects_local(vault):
    b = mb.backend_from_env({"PEPERNOTEN_VAULT": str(vault)})
    assert isinstance(b, mb.LocalBackend)


# ── note resolution & search ─────────────────────────────────────────────────

def test_resolve_note_by_arxiv_id(backend):
    assert mb.resolve_note(backend, "2405.12345") == "A Great Paper.md"
    assert mb.resolve_note(backend, "2405.12345v2") == "A Great Paper.md"
    with pytest.raises(mb.VaultBackendError):
        mb.resolve_note(backend, "1234.99999")


def test_resolve_note_by_filename_and_title(backend):
    assert mb.resolve_note(backend, "A Great Paper.md") == "A Great Paper.md"
    assert mb.resolve_note(backend, "great paper") == "A Great Paper.md"
    with pytest.raises(mb.VaultBackendError):
        mb.resolve_note(backend, "paper")   # ambiguous
    with pytest.raises(mb.VaultBackendError):
        mb.resolve_note(backend, "../A Great Paper.md")


def test_search_notes(backend):
    hits = mb.search_notes(backend, "sliding window")
    assert [h["file"] for h in hits] == ["A Great Paper.md"]
    assert "Sliding window" in hits[0]["snippets"][0]

    hits = mb.search_notes(backend, "paper", tag="diffusion")
    assert [h["file"] for h in hits] == ["Another Paper.md"]

    assert mb.search_notes(backend, "no-such-term-anywhere") == []
    with pytest.raises(mb.VaultBackendError):
        mb.search_notes(backend, "   ")


def test_note_tags():
    assert mb._note_tags(NOTE) == ["kv-cache", "streaming-video"]
    assert mb._note_tags("# no frontmatter") == []
