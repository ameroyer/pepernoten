# Codebase Guide

> For developers who want to understand, extend, or debug pepernoten.

---

## Design principles

1. **Minimalism** — no feature added speculatively, no abstraction before it is needed three times
2. **Cleanliness** — one responsibility per file, no cross-layer imports going the wrong way
3. **Modularity** — a single path constant, a single LLM client, a single index format, used everywhere

---

## Repository layout

```
pepernoten/
├── pepernoten_cli.py            REPL entry point (questionary + rich UI)
├── pepernoten_mcp.py            MCP server entry point (stdio, read-only tool layer)
├── pepernoten_prompts.yaml      user-configurable prompt semantics
├── pyproject.toml               dependencies, entry point, ruff + pyright config
│
├── src/                         LIBRARY — pure modules, no fire.Fire, no __main__
│   ├── vault.py                 path constants, index I/O, tag index, topic file I/O
│   ├── mcp_backend.py           MCP note-store backends (local vault / GitHub, read-only)
│   ├── llm.py                   OpenRouter client + call() / call_json()
│   ├── arxiv_utils.py           rate limiter, HTML fetch, ID extraction, Scholar Inbox
│   ├── notes.py                 note parsing, topic matching, backlinks, related-work extraction
│   ├── figures.py               HTML + PDF figure extraction, vision-assisted banner selection
│   ├── prompts.py               all LLM prompt builders (paper synthesis + topic surveys)
│   ├── paper.py                 process_arxiv_paper — the main end-to-end pipeline
│   └── bibtex.py                BibTeX lookup (PWC → CrossRef → Semantic Scholar → DBLP)
│
└── scripts/                     CLI WRAPPERS — thin fire.Fire entry points
    ├── parse.py                 parse / parse_many / sync / backlink_all
    ├── topic_manager.py         create / init / init_all / update / discover / list / backlink_topics commands
    └── bibtex.py                generate / batch / clear_cache commands
```

### Import graph (no cycles)

```
pepernoten_cli
    ├── vault, arxiv_utils
    ├── scripts/parse   ──► paper ──► vault, llm, arxiv_utils, notes, figures, prompts, bibtex
    └── src/bibtex

pepernoten_mcp ──► mcp_backend          (nothing else — the MCP layer never touches the pipeline)

scripts/parse         ──► paper, vault, notes, arxiv_utils
scripts/topic_manager ──► llm, vault, notes, prompts
scripts/bibtex        ──► src/bibtex

src/prompts  ──► notes          (note_xml)
src/figures  ──► vault, arxiv_utils
src/paper    ──► vault, llm, arxiv_utils, notes, figures, prompts, bibtex
src/notes    ──► vault
src/bibtex   ──► vault
```

`src/` modules never import from `scripts/`. The CLI imports from both layers.

---

## Key data structures

### Paper index — `Research/.paper_index.json`

```json
{ "2405.12345": {"title": "Paper Title", "file": "Paper Title.md"} }
```

### Topic index — `Research/Topics/.topic_index.json`

```json
{
  "streaming-video-llms": {
    "name": "Streaming Video LLMs",
    "file": "Topics/streaming-video-llms.md",
    "fingerprint_tags": ["streaming-video", "kv-cache", "video-llm"],
    "fingerprint_benchmarks": ["StreamingBench", "OvO-Bench"],
    "min_tag_overlap": 2,
    "papers": ["2405.12345"],
    "last_updated": "2026-06-20"
  }
}
```

A paper matches a topic if it shares ≥ `min_tag_overlap` tags from `fingerprint_tags` OR any `fingerprint_benchmarks`.

### Tag index — `Research/.tag_index.json`

```json
{"tags": ["kv-cache", "streaming-video", "video-llm"]}
```

Fed back to the LLM as `<existing_tags>` so new papers reuse existing tags rather than inventing synonyms.

### Topic file structure

```
---                              ← YAML frontmatter (topic metadata)
topic: "Streaming Video LLMs"
slug: streaming-video-llms
...
---

## Introduction               ← LLM-written body (never modified by the tooling)
## Benchmarks
## Methods & Baselines
## Techniques & Tricks
## Architecture Overview
## Open Problems & Gaps

---

## Method Index               ← appended by write_topic_file (stripped before LLM sees it)
| Short Name | Full Title | Link | Year |
...

---

## Papers                     ← appended by write_topic_file (Obsidian backlinks)
- [[Paper Title]]
```

`read_topic_content()` strips everything from `## Method Index` downward before handing content to the LLM, so these sections are always regenerated fresh on every write.

---

## Prompt configuration (`pepernoten_prompts.yaml`)

All user-facing semantics (roles, tones) live in the YAML config. Code in `src/prompts.py` loads it via `_cfg_get(dot.path, default)` — missing keys fall back to hardcoded defaults silently.

Configurable keys:
- `paper_synthesis.analyst_role` — role sentence prepended to every synthesis system prompt
- `paper_synthesis.tones.1–4` — audience/tone per verbosity level
- `topics.init_role` — role for initial topic document generation
- `topics.update_role` — role for integrating a new paper into an existing survey
- `topics.discover_role` — role for topic merge/discovery analysis

Not configurable: JSON output format, field specs, word counts, retry logic.

---

## End-to-end: parsing a paper

`paper.process_arxiv_paper(arxiv_url, model, vision_model, openrouter_api_key, verbosity)`

1. **Metadata** — arXiv Atom API, 4-attempt retry with exponential backoff
2. **HTML** — `arxiv_utils.fetch_arxiv_html`: tries `arxiv.org/html/{id}`, falls back to `ar5iv.labs.arxiv.org`
3. **Figure extraction** — `figures.extract_figures_from_html`: parses `<figure>`, downloads rasters. Unresolved figures go into `needs_pdf`.
4. **PDF fallback** — downloaded only if HTML unavailable or `needs_pdf` non-empty. `figures.extract_figures_with_vision` extracts remaining figures.
5. **Banner selection** — `figures._pick_best_figure`: vision model picks best figure from up to 6 candidates (prefers architecture diagrams)
6. **LLM synthesis** — system prompt (verbosity + tag vocab) + user message (figures + paper text as XML) via `llm.call_json` (3-attempt JSON repair loop).
7. **Note assembly** — frontmatter + TL;DR callout + sections + related work table + BibTeX block written to `Research/`
8. **Related-work arXiv ID lookup** — `arxiv_utils.lookup_arxiv_id` fills in missing IDs via fuzzy title search
9. **Index update + backlinks** — paper added to `.paper_index.json`; `notes.inject_backlinks` adds wikilinks in existing notes that mention this paper
10. **Topic update** — `_batch_update_topics` (in `scripts/parse.py`) runs `topic_manager.update` for matching topics and `topic_manager.discover` for unmatched papers

---

## Topic pipeline

### Matching — `notes.match_topics`

```python
len(note_tags ∩ topic.fingerprint_tags) >= topic.min_tag_overlap
OR any(b in topic.fingerprint_benchmarks for b in note.benchmarks)
```

### Init — `topic_manager.init`

Scans `Research/*.md`, collects matching notes, builds a **citation pool** (vault papers + related works extracted from each note's Related Work table), formats it as a citation registry for the LLM, calls `prompts._init_user_prompt`, then calls `_build_jargon_table` to produce the Method Index. Writes the topic file via `vault.write_topic_file`.

### Update — `topic_manager.update`

Reads existing content (`vault.read_topic_content` strips frontmatter and appended sections), builds citation pool for all topic papers + new note, sends to `prompts._update_user_prompt`. LLM returns updated document followed by `---CHANGELOG---` + bullets. Changelog is appended to the paper note.

### Discover — `topic_manager.discover`

Collects unmatched notes, sends to LLM as a batch with existing topic descriptions (to avoid duplicates), gets back JSON topic proposals. For each new topic, immediately runs `init`.

### Citation system

Three helpers in `scripts/topic_manager.py`:

- `_build_citation_pool(notes, paper_index)` — deduplicates vault papers (with wikilink stems) + external related-work references (with arXiv IDs when available)
- `_format_citation_block(pool)` — formats pool as a prompt instruction block the LLM must follow strictly (no hallucination)
- `_build_jargon_table(pool)` — builds the Method Index from the full pool, so even papers with no arXiv ID (e.g. cited baselines) get an entry

`extract_related_works(note_path)` in `src/notes.py` parses the Related Work markdown table from a note file and returns `{title, authors, year, arxiv_id}` dicts.

---

## BibTeX pipeline — `src/bibtex.py`

Lookup cascade with caching (`.bibtex_cache.json`):

1. **Papers With Code** — searches by title, extracts `proceeding` field
2. **CrossRef** — `/works?query.title&filter=type:proceedings-article` + fuzzy match
3. **Semantic Scholar** — `/graph/v1/paper/search` + venue field
4. **DBLP** — `/search/publ/api?q=` + XML parse
5. **`@misc` fallback** — always succeeds, uses arXiv metadata from the note

If a published venue is found → `@inproceedings` or `@article`. Otherwise `@misc` with eprint/arXiv fields.

---

## LLM calls — `src/llm.py`

```python
llm.call(system, user, model, api_key)       → str          # max_tokens=8192
llm.call_json(system, user, model, api_key)  → dict | list  # 3-attempt JSON repair loop
```

Base URL is always `https://openrouter.ai/api/v1`. Never use the Anthropic API directly.

Vision calls in `src/figures.py` take a client object from `llm.make_client(api_key)`.

---

## Rate limiting — `src/arxiv_utils.py`

Two `_RateLimiter` instances, each with its own threading lock:

- `arxiv_api` — 4.0 s between arXiv API calls and HTML/PDF fetches
- `arxiv_asset` — 0.3 s between static figure image downloads

---

## Verbosity levels

| Level | Target reader | TL;DR | Notes |
|---|---|---|---|
| 1 | Expert | 1 sentence | Minimal, bullet-focused |
| 2 | ML researcher (default) | 2 sentences | Standard depth |
| 3 | Subfield newcomer | 2+1 sentences | Intuition for design choices |
| 4 | ML newcomer | 3 sentences | Definitions, analogies, `concepts` section |

---

## MCP server — `pepernoten_mcp.py` + `src/mcp_backend.py`

A read-only consultation layer over the vault. The tool layer (`pepernoten_mcp.py`) is thin; all logic lives in `src/mcp_backend.py`:

- `LocalBackend` / `GitHubBackend` share one read-only interface: `list_markdown()`, `read_text(relpath)`, and the three index accessors. Relpaths are relative to `Research/` (or `$PEPERNOTEN_GITHUB_ROOT`).
- `backend_from_env()` picks the backend: `PEPERNOTEN_GITHUB_REPO` set → GitHub, else `PEPERNOTEN_VAULT` (default: this repo).
- **Safety invariants** (enforced in `_check_relpath` / `LocalBackend._resolve` / `GitHubBackend._get`, covered by `tests/test_mcp_backend.py`): the server exposes no mutating tools at all; no path may escape the notes tree (`..`, absolute, `~`, symlinks); only `.md` + the known index JSONs are readable; GitHub requests go to `api.github.com` only, with validated repo/branch names, timeouts, and a 2 MB size cap; the token never appears in URLs or error messages.
- The GitHub backend lists files via the Git Trees API (cached 60 s) and fetches content via the Blobs API (cached by immutable sha), so searches don't hammer the rate limit.
- Keep stdout clean in server code — it carries the JSON-RPC stream; diagnostics go to stderr.

---

## Testing

`tests/` covers pure logic only — no network, no LLM calls: frontmatter parsing, topic matching, arXiv ID extraction, BibTeX key generation, LLM JSON extraction. Run with `uv run pytest`. CI (`.github/workflows/ci.yml`) runs `ruff check` and `pytest` on every push and PR.

---

## Extending pepernoten

**New CLI command:** implement in the appropriate `src/` module → thin wrapper in the relevant `scripts/` file → register in `fire.Fire({...})` → add a `cmd_*` function in `pepernoten_cli.py` if it needs to be interactive.

**New BibTeX source:** add a `try` block in `src/bibtex.py` between existing sources. Each source returns a venue string or falls through on any exception. The `@misc` fallback at the end is unconditional.

**New note section:** the note markdown is assembled in a single f-string in `src/paper.py` (around line 335). Frontmatter fields that need to be read back should also be added to `vault.note_meta()`.

**New prompt semantics:** add a key to `pepernoten_prompts.yaml` and a corresponding `_cfg_get()` call in `src/prompts.py` with a hardcoded default. The YAML comment block documents what is and isn't configurable.
