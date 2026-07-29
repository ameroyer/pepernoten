# Contributing

## Setup

```bash
git clone <repo>
cd pepernoten
uv sync
export OPENROUTER_API_KEY=sk-or-...
```

Test a round-trip:

```bash
uv run scripts/parse.py parse https://arxiv.org/abs/2405.12345 --verbosity=1
```

---

## Design principles

Every change should respect three rules:

- **Minimalism** — don't add it unless it's needed now. No speculative features, no extra parameters, no fallback paths for scenarios that don't exist yet.
- **Cleanliness** — one responsibility per module. If a function needs to be called from more than one place, it belongs in `src/`, not duplicated.
- **Unified code** — use the existing path constants, LLM client, and index I/O functions. Don't define `VAULT_PATH` again; import it from `src/vault.py`.

---

## Module responsibilities

| Module | Owns | Does not own |
|--------|------|-------------|
| `src/vault.py` | All path constants, index read/write, topic file I/O | LLM calls, network I/O |
| `src/llm.py` | OpenRouter client, `call()` / `call_json()` | Prompt text, retry logic beyond basic |
| `src/arxiv_utils.py` | Rate limiters, arXiv HTML fetch, ID extraction, Scholar Inbox | Figure parsing, note writing |
| `src/notes.py` | Note parsing, topic matching, backlinks, changelog | Index I/O, network |
| `src/figures.py` | HTML + PDF figure extraction, vision selection | Note assembly, LLM prompts |
| `src/prompts.py` | All prompt strings for paper synthesis and topic surveys | LLM calls, file I/O |
| `src/paper.py` | End-to-end `process_arxiv_paper` pipeline | CLI, topic update orchestration |
| `src/bibtex.py` | BibTeX lookup cascade + cache | Note UI, clipboard |
| `scripts/*.py` | `fire.Fire` CLI wrappers, batch orchestration | Business logic (that lives in `src/`) |
| `pepernoten_cli.py` | Interactive REPL, rich/questionary UI | All logic (delegates to `src/` and `scripts/`) |

---

## Import rules

- `src/` modules import from other `src/` modules only — never from `scripts/`
- `scripts/` modules import from `src/` — never from each other (except `parse.py` importing `topic_manager` lazily inside `_batch_update_topics`)
- `pepernoten_cli.py` imports from both `src/` and `scripts/`

If you find yourself needing to import from `scripts/` inside `src/`, the logic belongs in `src/` instead.

---

## LLM calls

Always use `llm.call()` or `llm.call_json()` for new LLM calls. Never instantiate `OpenAI` directly — use `llm.make_client(api_key)` if you need a client object (e.g. for vision calls that take a client parameter).

Always use OpenRouter (`https://openrouter.ai/api/v1`). Never use the Anthropic API directly.

---

## Rate limiting

For any new arXiv API calls, use `arxiv_api.wait()` before the request. For static asset downloads (images, PDFs from non-arXiv CDNs), use `arxiv_asset.wait()`. Both are imported from `src/arxiv_utils.py`.

---

## Testing

```bash
uv run ruff check .
uv run pytest
```

`tests/` covers pure logic only (no network, no LLM calls) — extend it when you touch parsing, matching, or key-generation logic. CI runs the same two commands on every push and PR.

---

## Code style

- No comments unless the WHY is genuinely non-obvious (a hidden constraint, a workaround, a subtle invariant)
- No docstrings for obvious functions — the name should explain it
- No type annotations are required, but use them where they clarify complex signatures
- No emoji in code or comments
- Prefer flat code over deeply nested helpers for one-off logic

---

## Common tasks

**Add a new paper field** — extend the f-string in `src/paper.py` and add the field spec to `src/prompts.py`'s `_field_specs()` for the relevant verbosity levels.

**Add a new BibTeX source** — add a `try/except` block in `src/bibtex.py`'s `generate()` between the existing source attempts.

**Add a new CLI command to the REPL** — implement in `src/`, add a `cmd_*` function in `pepernoten_cli.py`, and add an entry to `COMMANDS`.

**Add a new topic metric** — update the topic index schema (a dict) and the `notes.match_topics()` logic in `src/notes.py`.
