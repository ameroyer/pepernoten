# PeperNoten

 <img align="right" src="logo.png" width="200" >

An LLM-assisted tool that turns arXiv papers into a knowledge library inside an Obsidian vault. The name puns on _paper (reading) notes_ and _pepernoten_, the Dutch spiced cookie.

`pepernoten_cli.py` parses papers into structured, interconnected notes: AI synthesis, figure extraction, topic clustering, and BibTeX generation.

## What it does

- Fetches arXiv HTML/PDF, extracts figures, and calls an LLM (via OpenRouter) to **synthesise a structured markdown note** for Obsidian
- **Notes include**: TL;DR, problem, methodology, results, ablation, related work, gaps/limitations, BibTeX, embedded figures
- **Clusters notes into topic survey files** and keeps them updated as new papers arrive, gradually building a living review per subfield. Each note gets a changelog of what it contributed to each topic
- Generates **up-to-date BibTeX** by searching Papers With Code, CrossRef, Semantic Scholar, and DBLP
- Optionally **syncs with Scholar Inbox** to parse your daily digest

---

## Setup

**Prerequisites**

- [uv](https://docs.astral.sh/uv/) for Python dependency management
- An [OpenRouter](https://openrouter.ai) API key (or a compatible alternative)
- *Optional:* a [Scholar Inbox](https://www.scholarinbox.com) account

```bash
git clone <repo>
cd pepernoten
uv sync
export OPENROUTER_API_KEY=sk-or-...

# Optional — authenticate scholarinboxcli for the "inbox" command:
#  1. Open a digest email from Scholar Inbox, right-click any paper link, and copy it.
#     It looks like: https://www.scholar-inbox.com/login?sha_key=YOUR_KEY&date=...&paper_id=...
#  2. Run:
uvx scholarinboxcli auth login --url "https://www.scholar-inbox.com/login?sha_key=YOUR_KEY"
```

The vault is the project root itself — point Obsidian at `/path/to/pepernoten`. Notes are stored under `Research/`.

## Usage (CLI)

```bash
uv run pepernoten_cli.py
```

| Command | What it does |
|---|---|
| **parse** | Paste one or more arXiv URLs or IDs. Fetches HTML/PDF, extracts figures, synthesises a structured note, updates matching topic files. |
| **inbox** | Fetches your Scholar Inbox digest. Tick the papers you want — only those are parsed. |
| **topics** | Lists all registered topics with paper counts and last-updated date. |
| **list** | Browse all papers in the vault; press `d` to delete one. |
| **update_knowledge** | Add or remove papers from topic files; propose and execute topic merges. |
| **bibtex** | Generate BibTeX for a paper — checks PWC, CrossRef, Semantic Scholar, DBLP before falling back to `@misc`. |
| **quit** | Exit. |

## Configuring prompts

Edit `pepernoten_prompts.yaml` — no code changes needed:

```yaml
paper_synthesis:
  analyst_role: "You are a sharp, critical research analyst writing notes for a PhD researcher"
  tones:
    1: "Precision and brevity. Expert reader."
    2: "Precision and depth. ML researcher, not a subfield specialist."
    3: "Subfield newcomer — explain design choices."
    4: "ML newcomer — define all jargon, use analogies."

topics:
  init_role: "You are a senior researcher writing a living review document…"
  update_role: "You are maintaining a living review document…"
  discover_role: "You analyze a vault's topic structure to find merge opportunities…"
```

Keys not present fall back to hardcoded defaults silently. Only **semantics** are configurable — JSON format, field specs, and retry logic are not exposed.

Tones correspond to verbosity levels, set per paper when parsing. Default levels:

| Level | Target reader | Depth |
|---|---|---|
| 1 | Expert | Minimal — terse bullets, key numbers only |
| 2 | ML researcher (default) | Standard — full sections, key comparisons |
| 3 | Subfield newcomer | Expanded — intuition for design choices |
| 4 | ML newcomer | Full — definitions, analogies, concepts section |

## Scripts (for automation / one-shot use)

```bash
# Parse
uv run scripts/parse.py parse https://arxiv.org/abs/2405.12345
uv run scripts/parse.py parse_many 2405.12345 2406.67890
uv run scripts/parse.py sync                   # Scholar Inbox top-N

# Topics
uv run scripts/topic_manager.py list
uv run scripts/topic_manager.py create "My Topic" --tags a,b --benchmarks B1
uv run scripts/topic_manager.py init <slug>
uv run scripts/topic_manager.py init_all
uv run scripts/topic_manager.py update Research/SomePaper.md
uv run scripts/topic_manager.py discover        # cluster unmatched papers into new topics
uv run scripts/topic_manager.py backlink_topics # refresh ## Papers sections

# BibTeX
uv run scripts/bibtex.py generate 2405.12345 --update_note
uv run scripts/bibtex.py batch 2405.12345 2406.67890 --bib_file=refs.bib
```

## Note structure

Each parsed paper produces a `.md` note in `Research/` with:

- **Frontmatter** — title, authors, date, arXiv ID, tags, thumbnail, verbosity
- **TL;DR callout** — one-sentence summary
- **Sections** — Problem, Methodology, Results, Ablation (depth varies by verbosity)
- **Callouts** — Gaps (`[!danger]`) and Limitations (`[!warning]`)
- **Related Work table** — direct competitors with arXiv links and gap analysis
- **BibTeX block** — auto-populated after `bibtex generate`

## Topic files

Topic files live in `Research/Topics/` and are maintained automatically:

- **Auto-matched** — a paper joins a topic if its tags or benchmarks overlap the topic's fingerprint
- **Living surveys** — Introduction, Benchmarks, Methods & Baselines table, Techniques & Tricks, Architecture Overview, Open Problems & Gaps
- **Method Index** — appended automatically; maps every method short name → full title → link (covers cited baselines even without an arXiv ID)
- **Paper backlinks** — `## Papers` section lists all vault papers as Obsidian wikilinks

## Vault layout

```
pepernoten/
├── Research/
│   ├── Paper Title.md          ← synthesised paper notes
│   ├── .paper_index.json       ← {arxiv_id: {title, file}}
│   ├── .tag_index.json         ← accumulated tag vocabulary
│   ├── images/                 ← extracted figures
│   ├── Thumbnails/             ← banner images
│   └── Topics/
│       ├── my-topic.md         ← living survey files
│       └── .topic_index.json   ← topic metadata + fingerprints
├── pepernoten_cli.py
├── pepernoten_prompts.yaml     ← user-configurable prompt semantics
├── src/                        ← library modules
└── scripts/                    ← fire.Fire CLI entry points
```

See [`docs/index.html`](docs/index.html) for the full developer guide.
