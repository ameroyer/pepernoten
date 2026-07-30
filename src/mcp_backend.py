# Read-only note-store backends for the MCP server — local Obsidian vault or GitHub repo.
#
# Both backends expose the same interface over the Research/ tree:
#   list_markdown() -> list[str]     relative paths of .md notes (papers + Topics/)
#   read_text(relpath) -> str        confined to Research/, .md and known indices only
#   paper_index / topic_index / tag_index -> parsed JSON indices ({} / [] if absent)
#
# Safety invariants (do not weaken):
#   - every relpath is validated: no absolute paths, no "..", no symlink escape
#   - only .md files and the three known dot-index JSON files are readable
#   - GitHub requests go to api.github.com only, with timeouts and size caps
#   - the GitHub token lives in request headers only and is never interpolated
#     into URLs, exceptions, or log output

import base64
import json
import os
import re
import time
from pathlib import Path

import requests

MAX_FILE_BYTES   = 2_000_000   # refuse to read anything larger than 2 MB
HTTP_TIMEOUT     = 30          # seconds, every GitHub request
TREE_CACHE_TTL   = 60          # seconds before the GitHub file listing is refreshed
MAX_BLOB_CACHE   = 256         # blobs kept in memory (immutable, keyed by sha)

_INDEX_FILES = {".paper_index.json", ".tag_index.json", "Topics/.topic_index.json"}
_REPO_RE     = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
_BRANCH_RE   = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")


class VaultBackendError(Exception):
    """User-facing backend error — messages must never contain secrets."""


def _check_relpath(relpath: str) -> str:
    """Reject anything that could escape the Research/ tree. Returns the cleaned path."""
    relpath = (relpath or "").strip().replace("\\", "/")
    if not relpath or relpath.startswith("/") or relpath.startswith("~"):
        raise VaultBackendError(f"Invalid note path: {relpath!r}")
    parts = relpath.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise VaultBackendError(f"Invalid note path: {relpath!r}")
    if not (relpath.endswith(".md") or relpath in _INDEX_FILES):
        raise VaultBackendError(f"Only .md notes are readable, got: {relpath!r}")
    return relpath


def _note_tags(text: str) -> list[str]:
    """Extract frontmatter tags from raw note text (backend-agnostic)."""
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return []
    tag_block = re.search(r"^tags:\n((?:\s+- .+\n?)+)", m.group(1), re.MULTILINE)
    if not tag_block:
        return []
    return re.findall(r"^\s+- (.+)$", tag_block.group(1), re.MULTILINE)


class _BaseBackend:
    description: str = ""

    def list_markdown(self) -> list[str]:
        raise NotImplementedError

    def read_text(self, relpath: str) -> str:
        raise NotImplementedError

    def _read_json(self, relpath: str, default):
        try:
            return json.loads(self.read_text(relpath))
        except VaultBackendError:
            return default
        except json.JSONDecodeError:
            raise VaultBackendError(f"Corrupt index file: {relpath}")

    def paper_index(self) -> dict:
        return self._read_json(".paper_index.json", {})

    def topic_index(self) -> dict:
        return self._read_json("Topics/.topic_index.json", {})

    def tag_index(self) -> list[str]:
        return self._read_json(".tag_index.json", {}).get("tags", [])


class LocalBackend(_BaseBackend):
    """Reads notes from Research/ inside a local Obsidian vault."""

    def __init__(self, vault_root: str):
        root = Path(vault_root).expanduser().resolve()
        if not root.is_dir():
            raise VaultBackendError(f"Vault directory does not exist: {root}")
        self.vault_root   = root
        self.research_dir = (root / "Research").resolve()
        if not self.research_dir.is_dir():
            raise VaultBackendError(f"No Research/ directory in vault: {root}")
        self.description = f"local vault at {root}"

    def _resolve(self, relpath: str) -> Path:
        relpath = _check_relpath(relpath)
        path = (self.research_dir / relpath).resolve()
        if not path.is_relative_to(self.research_dir):   # symlink escape
            raise VaultBackendError(f"Path escapes the vault: {relpath!r}")
        return path

    def list_markdown(self) -> list[str]:
        return sorted(
            str(p.relative_to(self.research_dir))
            for p in self.research_dir.rglob("*.md")
            if not any(part.startswith(".") for part in p.relative_to(self.research_dir).parts)
        )

    def read_text(self, relpath: str) -> str:
        path = self._resolve(relpath)
        if not path.is_file():
            raise VaultBackendError(f"Note not found: {relpath}")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise VaultBackendError(f"File too large to read: {relpath}")
        return path.read_text(encoding="utf-8", errors="ignore")


class GitHubBackend(_BaseBackend):
    """Reads notes from <root>/ in a GitHub repo via the REST API. Strictly read-only.

    Uses the Git Trees API for listings (one request, cached briefly) and the
    Blobs API for content (cached by immutable sha), so repeated searches do
    not hammer the API.
    """

    def __init__(self, repo: str, token: str | None = None,
                 branch: str | None = None, root: str = "Research"):
        if not _REPO_RE.match(repo or ""):
            raise VaultBackendError(f"Invalid GitHub repo (expected owner/name): {repo!r}")
        if branch and not _BRANCH_RE.match(branch):
            raise VaultBackendError(f"Invalid branch name: {branch!r}")
        root = (root or "").strip().strip("/")
        if root and any(p in ("", ".", "..") for p in root.split("/")):
            raise VaultBackendError(f"Invalid repo root: {root!r}")
        self.repo    = repo
        self._token  = token or None
        self._branch = branch or None       # resolved lazily to the default branch
        self.root    = root                 # "" means notes live at the repo root
        self._tree: dict[str, dict] | None = None
        self._tree_at = 0.0
        self._blobs: dict[str, str] = {}
        auth = "authenticated" if token else "anonymous"
        self.description = f"GitHub repo {repo} ({auth}, read-only)"

    def _get(self, path: str) -> dict:
        headers = {"Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            resp = requests.get(f"https://api.github.com{path}",
                                headers=headers, timeout=HTTP_TIMEOUT)
        except requests.RequestException as e:
            raise VaultBackendError(f"GitHub request failed: {type(e).__name__}")
        if resp.status_code == 404:
            raise VaultBackendError(
                f"GitHub returned 404 for {self.repo} — check the repo name, branch, "
                "and (for private repos) that PEPERNOTEN_GITHUB_TOKEN is set and has read access."
            )
        if resp.status_code in (401, 403):
            raise VaultBackendError(
                f"GitHub returned {resp.status_code} — token invalid/expired or rate limit exceeded."
            )
        if resp.status_code != 200:
            raise VaultBackendError(f"GitHub API error: HTTP {resp.status_code}")
        return resp.json()

    def _get_branch(self) -> str:
        if not self._branch:
            self._branch = self._get(f"/repos/{self.repo}").get("default_branch", "main")
        return self._branch

    def _get_tree(self) -> dict[str, dict]:
        """{relpath-under-root: {sha, size}} for all blobs, cached for TREE_CACHE_TTL."""
        if self._tree is not None and time.monotonic() - self._tree_at < TREE_CACHE_TTL:
            return self._tree
        branch = self._get_branch()
        data = self._get(f"/repos/{self.repo}/git/trees/{branch}?recursive=1")
        prefix = f"{self.root}/" if self.root else ""
        tree = {}
        for entry in data.get("tree", []):
            if entry.get("type") != "blob":
                continue
            path = entry.get("path", "")
            if prefix and not path.startswith(prefix):
                continue
            tree[path[len(prefix):]] = {"sha": entry["sha"], "size": entry.get("size", 0)}
        if data.get("truncated"):
            print("warning: GitHub tree listing truncated — repo too large, some notes invisible",
                  flush=True)
        self._tree, self._tree_at = tree, time.monotonic()
        return tree

    def list_markdown(self) -> list[str]:
        return sorted(
            p for p in self._get_tree()
            if p.endswith(".md") and not any(part.startswith(".") for part in p.split("/"))
        )

    def read_text(self, relpath: str) -> str:
        relpath = _check_relpath(relpath)
        entry = self._get_tree().get(relpath)
        if entry is None:
            raise VaultBackendError(f"Note not found: {relpath}")
        if entry["size"] > MAX_FILE_BYTES:
            raise VaultBackendError(f"File too large to read: {relpath}")
        if entry["sha"] not in self._blobs:
            blob = self._get(f"/repos/{self.repo}/git/blobs/{entry['sha']}")
            if blob.get("encoding") != "base64":
                raise VaultBackendError(f"Unexpected blob encoding for: {relpath}")
            text = base64.b64decode(blob["content"]).decode("utf-8", errors="ignore")
            if len(self._blobs) >= MAX_BLOB_CACHE:
                self._blobs.clear()
            self._blobs[entry["sha"]] = text
        return self._blobs[entry["sha"]]


# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────

def backend_from_env(environ=None) -> _BaseBackend:
    """Pick a backend from environment variables.

    PEPERNOTEN_GITHUB_REPO=owner/name          → GitHub backend (read-only)
      PEPERNOTEN_GITHUB_TOKEN=ghp_...            optional, for private repos
      PEPERNOTEN_GITHUB_BRANCH=main              optional, defaults to the repo default
      PEPERNOTEN_GITHUB_ROOT=Research            optional, dir containing the notes
    otherwise                                  → local vault
      PEPERNOTEN_VAULT=/path/to/vault            optional, defaults to the pepernoten repo
    """
    env = os.environ if environ is None else environ
    repo = env.get("PEPERNOTEN_GITHUB_REPO", "").strip()
    if repo:
        return GitHubBackend(
            repo,
            token=env.get("PEPERNOTEN_GITHUB_TOKEN", "").strip() or None,
            branch=env.get("PEPERNOTEN_GITHUB_BRANCH", "").strip() or None,
            root=env.get("PEPERNOTEN_GITHUB_ROOT", "Research"),
        )
    default_vault = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return LocalBackend(env.get("PEPERNOTEN_VAULT", "").strip() or default_vault)


# ──────────────────────────────────────────────────────────────────────────────
# Backend-agnostic note operations (used by the MCP tool layer)
# ──────────────────────────────────────────────────────────────────────────────

_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")


def resolve_note(backend: _BaseBackend, identifier: str) -> str:
    """Map an arXiv ID, note filename, or (partial) title to a relpath under Research/."""
    identifier = (identifier or "").strip()
    if not identifier:
        raise VaultBackendError("Empty note identifier.")

    if _ARXIV_ID_RE.match(identifier):
        base_id = identifier.split("v")[0]
        info = backend.paper_index().get(base_id)
        if info and info.get("file"):
            return info["file"]
        raise VaultBackendError(f"No note for arXiv ID {base_id} — try search_notes or list_papers.")

    if identifier.endswith(".md"):
        return _check_relpath(identifier)

    needle = identifier.casefold()
    matches = [p for p in backend.list_markdown() if needle in Path(p).stem.casefold()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise VaultBackendError(f"No note matching {identifier!r} — try search_notes.")
    listing = "\n".join(f"  - {m}" for m in matches[:15])
    raise VaultBackendError(f"Ambiguous identifier {identifier!r}, matches:\n{listing}")


def search_notes(backend: _BaseBackend, query: str, tag: str | None = None,
                 max_results: int = 10) -> list[dict]:
    """Case-insensitive all-terms search over note text, with short snippets."""
    terms = [t.casefold() for t in (query or "").split() if t]
    if not terms and not tag:
        raise VaultBackendError("Provide a search query and/or a tag.")
    max_results = max(1, min(int(max_results), 25))

    results = []
    for relpath in backend.list_markdown():
        try:
            text = backend.read_text(relpath)
        except VaultBackendError:
            continue
        folded = text.casefold()
        if any(t not in folded for t in terms):
            continue
        if tag and tag.casefold() not in [x.casefold() for x in _note_tags(text)]:
            continue
        snippets = []
        for t in terms[:3]:
            i = folded.find(t)
            snippet = " ".join(text[max(0, i - 120):i + 120].split())
            snippets.append(snippet)
        if not snippets:   # tag-only search
            snippets = [" ".join(text[:200].split())]
        results.append({"file": relpath, "snippets": snippets})
        if len(results) >= max_results:
            break
    return results
