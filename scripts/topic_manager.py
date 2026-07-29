# Usage:
#   uv run scripts/topic_manager.py init streaming-vlms
#   uv run scripts/topic_manager.py init_all
#   uv run scripts/topic_manager.py update Research/SomePaper.md
#   uv run scripts/topic_manager.py list
#   uv run scripts/topic_manager.py create "My Topic" --tags a,b --benchmarks B1,B2

import os
import re
import sys
import fire
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import llm
from vault import (
    RESEARCH_PATH,
    load_paper_index, load_topic_index, save_topic_index,
    topic_path, write_topic_file, read_topic_content,
)
from notes import parse_note, match_topics, unmatched_notes, parse_update_response, append_note_changelog, extract_related_works
from prompts import (
    _init_system_prompt, _init_user_prompt,
    _update_system_prompt, _update_user_prompt,
    _remove_system_prompt, _remove_user_prompt,
    _merge_system_prompt, _merge_user_prompt,
)

DEFAULT_MODEL = "anthropic/claude-sonnet-4-5"


# ──────────────────────────────────────────────────────────────────────────────
# Citation helpers
# ──────────────────────────────────────────────────────────────────────────────

def _build_citation_pool(notes: list, paper_index: dict) -> list:
    """Build a deduplicated pool of citable papers for the citation registry.

    Sources:
    1. Vault papers matched to this topic (have Obsidian wikilinks)
    2. Related works listed in those note files (may have arXiv URLs)
    """
    pool: dict = {}  # key → {title, stem, arxiv_id, year, authors, in_vault}

    # Vault papers from matched notes
    for note in notes:
        aid = note.get("arxiv_id", "")
        info = paper_index.get(aid)
        if info and info.get("file"):
            stem = os.path.splitext(info["file"])[0]
            key = f"vault:{stem}"
            pool[key] = {
                "title":    note["title"],
                "stem":     stem,
                "arxiv_id": aid,
                "year":     (note.get("date") or "")[:4],
                "authors":  note.get("authors", ""),
                "in_vault": True,
            }

    # Related works from each matched note file
    for note in notes:
        aid = note.get("arxiv_id", "")
        info = paper_index.get(aid)
        if not info or not info.get("file"):
            continue
        note_file = os.path.join(RESEARCH_PATH, info["file"])
        for ref in extract_related_works(note_file):
            ref_aid = ref.get("arxiv_id", "")
            # If this related work is itself in the vault, register as vault
            if ref_aid and ref_aid in paper_index:
                ref_info = paper_index[ref_aid]
                if ref_info.get("file"):
                    stem = os.path.splitext(ref_info["file"])[0]
                    key = f"vault:{stem}"
                    if key not in pool:
                        pool[key] = {
                            "title":    ref_info["title"],
                            "stem":     stem,
                            "arxiv_id": ref_aid,
                            "year":     ref.get("year", ""),
                            "authors":  ref.get("authors", ""),
                            "in_vault": True,
                        }
            else:
                key = f"ext:{ref['title'][:50]}"
                if key not in pool:
                    pool[key] = {
                        "title":    ref["title"],
                        "stem":     None,
                        "arxiv_id": ref_aid,
                        "year":     ref.get("year", ""),
                        "authors":  ref.get("authors", ""),
                        "in_vault": False,
                    }

    return list(pool.values())


def _format_citation_block(pool: list) -> str:
    """Format the citation registry as a prompt instruction block."""
    vault    = sorted([p for p in pool if p["in_vault"]],     key=lambda x: x.get("year") or "", reverse=True)
    external = sorted([p for p in pool if not p["in_vault"]], key=lambda x: x.get("year") or "", reverse=True)

    lines = [
        "CITATION RULES — strictly enforced:",
        "• Every paper you mention by name MUST be cited using one of the exact formats below.",
        "• Do NOT cite any paper not in this list. Do NOT invent arXiv IDs or paper titles.",
        "",
        "Vault papers — cite as [[exact stem]] (you may add |short_name after the pipe):",
    ]
    for p in vault:
        year = f" ({p['year']})" if p.get("year") else ""
        lines.append(f"  [[{p['stem']}]]{year}")

    lines.append("")
    lines.append("External papers — cite as [Name](url) if URL given, else **Name** in bold:")
    for p in external:
        year    = f" ({p['year']})"    if p.get("year")    else ""
        authors = f", {p['authors']}"  if p.get("authors") else ""
        if p.get("arxiv_id"):
            lines.append(f"  [{p['title']}](https://arxiv.org/abs/{p['arxiv_id']}){year}{authors}")
        else:
            lines.append(f"  **{p['title']}**{year}{authors}")

    return "\n".join(lines)


_SHORT_NAME_STOP = {"A", "An", "The", "On", "Of", "In", "Is", "Are", "For", "To",
                    "Towards", "Leveraging", "Using", "Learning", "Efficient"}


def _short_name(title: str) -> str:
    """Derive the commonly-used short name from a paper title."""
    # "ModelName: Full Title" or "ACRONYM: ..." → everything before the first colon
    m = re.match(r'^([^\s:]{2,25}):', title)
    if m:
        return m.group(1)
    # Skip leading stop words and take the first meaningful word
    for word in title.split():
        clean = re.sub(r'[^A-Za-z0-9\-]', '', word)
        if clean and clean not in _SHORT_NAME_STOP and len(clean) > 1:
            return clean[:20]
    return title[:20]


def _build_jargon_table(pool: list) -> str:
    """Build a method/paper index from the full citation pool.

    Every paper in the pool gets an entry — including those without arXiv IDs
    (e.g. StreamForest, HERMES) that content-scanning would miss entirely.
    """
    if not pool:
        return ""
    rows = []
    for p in pool:
        short = _short_name(p["title"])
        year  = p.get("year") or "—"
        if p.get("in_vault") and p.get("stem"):
            link = f"[[{p['stem']}\\|{short}]]"
        elif p.get("arxiv_id"):
            link = f"[{short}](https://arxiv.org/abs/{p['arxiv_id']})"
        else:
            link = short
        rows.append((short.lower(), f"| {short} | {p['title']} | {link} | {year} |"))

    rows.sort(key=lambda r: r[0])
    lines = ["| Short Name | Full Title | Link | Year |", "|---|---|---|---|"]
    lines += [r[1] for r in rows]
    return "\n".join(lines)




# ==========================================
# COMMANDS
# ==========================================

def create(
    name: str,
    tags: str = "",
    benchmarks: str = "",
    min_overlap: int = 2,
):
    """Register a new topic. Does not generate its file — run init <slug> after this.

    Example:
        uv run topic_manager.py create "Streaming Video LLMs" \\
            --tags streaming-video,real-time,kv-cache,video-llm \\
            --benchmarks StreamingBench,OvO-Bench \\
            --min_overlap 1
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    idx = load_topic_index()
    if slug in idx:
        print(f"Topic '{slug}' already exists. Use a different name or edit .topic_index.json directly.")
        return
    idx[slug] = {
        "name":                  name,
        "file":                  f"Topics/{slug}.md",
        "fingerprint_tags":      [t.strip() for t in tags.split(",") if t.strip()],
        "fingerprint_benchmarks":[b.strip() for b in benchmarks.split(",") if b.strip()],
        "min_tag_overlap":       min_overlap,
        "papers":                [],
        "last_updated":          "",
    }
    save_topic_index(idx)
    print(f"Created topic '{name}' (slug: {slug}). Run: uv run topic_manager.py init {slug}")


def init(
    slug: str,
    model: str = DEFAULT_MODEL,
    openrouter_api_key: str = "",
):
    """Generate a topic file from scratch using all matching paper notes in the vault."""
    api_key = openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("No API key: pass --openrouter_api_key or set $OPENROUTER_API_KEY")

    idx = load_topic_index()
    if slug not in idx:
        raise ValueError(f"Unknown topic slug '{slug}'. Run: uv run topic_manager.py list")
    td = idx[slug]
    topic_name = td["name"]

    # Find all matching notes
    notes = []
    matched_ids = []
    for md in sorted(Path(RESEARCH_PATH).glob("*.md")):
        note = parse_note(str(md))
        if not note["title"]:
            continue
        matches = match_topics(note, {slug: td})
        if matches:
            notes.append(note)
            if note["arxiv_id"]:
                matched_ids.append(note["arxiv_id"])

    if not notes:
        print(f"No matching papers found for '{topic_name}'. Check fingerprint tags.")
        return

    print(f"Generating '{topic_name}' from {len(notes)} paper(s):")
    for n in notes:
        print(f"  • {n['title']}")

    paper_index  = load_paper_index()
    pool         = _build_citation_pool(notes, paper_index)
    cit_block    = _format_citation_block(pool) if pool else ""

    system = _init_system_prompt()
    user   = _init_user_prompt(topic_name, notes, cit_block)
    print(f"\nCalling {model}...")
    content = llm.call(system, user, model, api_key)

    bibliography = _build_jargon_table(pool)
    td["papers"]       = matched_ids
    td["last_updated"] = str(date.today())
    idx[slug] = td
    save_topic_index(idx)
    write_topic_file(slug, topic_name, td, content, bibliography)
    print(f"Written: {topic_path(slug)}")


def init_all(model: str = DEFAULT_MODEL, openrouter_api_key: str = ""):
    """Run init for every registered topic."""
    idx = load_topic_index()
    if not idx:
        print("No topics registered. Run: uv run topic_manager.py discover")
        return
    for slug in idx:
        print(f"\n{'='*60}\n  {idx[slug]['name']}\n{'='*60}")
        init(slug, model=model, openrouter_api_key=openrouter_api_key)




def update(
    note_path: str,
    model: str = DEFAULT_MODEL,
    openrouter_api_key: str = "",
    safe_update: bool = False,
):
    """Integrate a new paper note into all matching topic files.

    With --safe_update, shows the changelog and prompts before applying each change.
    The changelog is always written to the paper note regardless.

    Can also be run manually: uv run topic_manager.py update Research/SomePaper.md
    """
    api_key = openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("No API key: pass --openrouter_api_key or set $OPENROUTER_API_KEY")

    note = parse_note(note_path)
    if not note["title"]:
        print(f"Could not parse note: {note_path}")
        return

    idx = load_topic_index()
    if not idx:
        print("No topics registered yet — running discovery...")

    matched = match_topics(note, idx)
    if not matched:
        print(f"'{note['title']}' matched no topics — running discovery...")
        discover(model=model, openrouter_api_key=api_key)
        idx = load_topic_index()
        matched = match_topics(note, idx)
        if not matched:
            print("  Still no match — paper needs more peers before a topic can form.")
            return

    print(f"'{note['title']}' matched: {', '.join(matched)}")

    today = str(date.today())
    changelog_entries = []

    for slug in matched:
        td = idx[slug]
        topic_name = td["name"]
        topic_file = topic_path(slug)

        if not os.path.exists(topic_file):
            print(f"  [{slug}] No file yet — running init first...")
            init(slug, model=model, openrouter_api_key=api_key)
            idx = load_topic_index()
            td = idx[slug]

        print(f"  [{slug}] Updating '{topic_name}'...")
        existing = read_topic_content(slug)

        # Build citation pool from all papers already in this topic + the new note
        _paper_index = load_paper_index()
        _topic_notes = []
        for _aid in td.get("papers", []):
            _info = _paper_index.get(_aid)
            if _info and _info.get("file"):
                _n = parse_note(os.path.join(RESEARCH_PATH, _info["file"]))
                if _n["title"]:
                    _topic_notes.append(_n)
        _topic_notes.append(note)
        _pool     = _build_citation_pool(_topic_notes, _paper_index)
        _cit_blk  = _format_citation_block(_pool) if _pool else ""

        raw = llm.call(_update_system_prompt(), _update_user_prompt(topic_name, existing, note, _cit_blk), model, api_key)
        document, changelog = parse_update_response(raw)

        if not changelog:
            changelog = "(no changelog produced)"

        apply = True
        if safe_update:
            print(f"\n  Changes to [{slug}]:\n")
            for line in changelog.splitlines():
                print(f"    {line}")
            answer = input(f"\n  Apply these changes to {topic_file}? [y/N] ").strip().lower()
            apply = answer in ("y", "yes")
            if not apply:
                print("    Skipped — changes recorded in note but NOT written to topic file.")

        if apply:
            if note["arxiv_id"] and note["arxiv_id"] not in td.get("papers", []):
                td.setdefault("papers", []).append(note["arxiv_id"])
            td["last_updated"] = today
            idx[slug] = td
            save_topic_index(idx)
            _bib = _build_jargon_table(_pool)
            write_topic_file(slug, topic_name, td, document, _bib)
            print(f"    ✓ Written: {topic_file}")

        changelog_entries.append({
            "slug":      slug,
            "name":      topic_name,
            "changelog": changelog,
            "applied":   apply,
        })

    if changelog_entries:
        append_note_changelog(note_path, changelog_entries)






def discover(
    model: str = DEFAULT_MODEL,
    openrouter_api_key: str = "",
    min_papers: int = 2,
):
    """Find unmatched papers and automatically create new topics for coherent clusters.

    Called automatically by update() when a paper matches nothing.
    Can also be run manually: uv run topic_manager.py discover
    """
    api_key = openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("No API key: pass --openrouter_api_key or set $OPENROUTER_API_KEY")

    idx = load_topic_index()
    unmatched = unmatched_notes(idx)

    if not unmatched:
        print("All notes already covered by existing topics.")
        return

    print(f"  {len(unmatched)} unmatched note(s): {[n['title'][:40] for n in unmatched]}")

    if len(unmatched) < min_papers:
        print(f"  Only {len(unmatched)} unmatched paper(s) — waiting for at least {min_papers} before creating a new topic.")
        return

    # Describe all existing topics so Claude doesn't duplicate them
    existing_desc = "\n".join(
        f'  - "{td["name"]}": tags={td["fingerprint_tags"]}'
        for td in idx.values()
    ) or "  (none yet)"

    # Summarize each unmatched paper compactly
    papers_summary = "\n\n".join(
        f'<paper title="{n["title"]}" arxiv_id="{n["arxiv_id"]}">\n'
        f'<tldr>{n.get("tldr", "")}</tldr>\n'
        f'<tags>{", ".join(n["tags"])}</tags>\n'
        f'<benchmarks>{", ".join(n["benchmarks"])}</benchmarks>\n'
        f'</paper>'
        for n in unmatched
    )

    system = (
        "You are organizing a personal research vault into topic files. "
        "Each topic is a coherent research subfield — not a generic category. "
        "Topics must be specific enough to produce a meaningful survey (e.g. "
        "'Streaming Video LLMs', not 'Video'). "
        "Output valid JSON only."
    )

    user = f"""These papers from my research vault do not fit any existing topic:

<unmatched_papers>
{papers_summary}
</unmatched_papers>

Existing topics (do NOT create duplicates or near-duplicates of these):
{existing_desc}

Group the unmatched papers into coherent research topics. Only create a topic if at least \
{min_papers} papers clearly belong to it. A paper can belong to multiple topics. \
Papers with no coherent peers should be left ungrouped (omit them).

Return a JSON object with this exact schema:
{{
  "topics": [
    {{
      "name": "Human-readable topic name (specific subfield, 3-6 words)",
      "fingerprint_tags": ["tag1", "tag2", ...],
      "fingerprint_benchmarks": ["BenchName1", ...],
      "min_tag_overlap": 1,
      "papers": ["arxiv_id_1", "arxiv_id_2", ...]
    }}
  ]
}}

Rules for fingerprint_tags: use the actual tag strings from the papers' <tags> fields. \
Include 4-8 tags that are specific to this subfield. min_tag_overlap should be 1 or 2 — \
use 1 if the topic has a single defining tag, 2 if overlap is needed to avoid false positives. \
fingerprint_benchmarks: list benchmark names from the papers that are specific to this topic \
(not generic ones like "ImageNet"). Empty list if none are distinctive."""

    print(f"  Asking {model} to discover new topics...")
    result = llm.call_json(system, user, model, api_key)
    new_topics = result.get("topics", [])

    if not new_topics:
        print("  No coherent new topics found.")
        return

    today = str(date.today())

    for td in new_topics:
        name      = td["name"]
        slug      = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        ftags     = td.get("fingerprint_tags", [])
        fbenches  = td.get("fingerprint_benchmarks", [])
        min_ov    = td.get("min_tag_overlap", 2)
        paper_ids = td.get("papers", [])

        if slug in idx:
            print(f"  [{slug}] Already exists — skipping.")
            continue

        print(f"  Creating new topic: '{name}' ({len(paper_ids)} papers)")
        idx[slug] = {
            "name":                   name,
            "file":                   f"Topics/{slug}.md",
            "fingerprint_tags":       ftags,
            "fingerprint_benchmarks": fbenches,
            "min_tag_overlap":        min_ov,
            "papers":                 [],
            "last_updated":           "",
        }
        save_topic_index(idx)

        # Generate the topic file immediately from its papers
        notes_for_topic = [n for n in unmatched if n.get("arxiv_id") in paper_ids]
        if not notes_for_topic:
            # Fall back to fingerprint-based matching on the full vault
            notes_for_topic = [
                parse_note(str(md))
                for md in sorted(Path(RESEARCH_PATH).glob("*.md"))
                if match_topics(parse_note(str(md)), {slug: idx[slug]})
            ]

        if notes_for_topic:
            print(f"    Generating topic file from {len(notes_for_topic)} paper(s)...")
            _pidx   = load_paper_index()
            _pool   = _build_citation_pool(notes_for_topic, _pidx)
            _cblk   = _format_citation_block(_pool) if _pool else ""
            content = llm.call(
                _init_system_prompt(),
                _init_user_prompt(name, notes_for_topic, _cblk),
                model, api_key,
            )
            _bib    = _build_jargon_table(_pool)
            matched_ids = [n["arxiv_id"] for n in notes_for_topic if n.get("arxiv_id")]
            idx[slug]["papers"]       = matched_ids
            idx[slug]["last_updated"] = today
            save_topic_index(idx)
            write_topic_file(slug, name, idx[slug], content, _bib)
            print(f"    Written: {topic_path(slug)}")


def remove_from_topics(
    aid: str,
    model: str = DEFAULT_MODEL,
    openrouter_api_key: str = "",
):
    """Remove a paper from all topics it belongs to, updating each topic file via LLM.

    Called by cmd_update_knowledge when a paper is marked for removal.
    """
    api_key = openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("No API key: pass --openrouter_api_key or set $OPENROUTER_API_KEY")

    idx = load_topic_index()
    affected = {slug: td for slug, td in idx.items() if aid in td.get("papers", [])}

    if not affected:
        print(f"  {aid} is not listed in any topic — nothing to remove.")
        return

    paper_info = load_paper_index().get(aid, {})
    paper_title = paper_info.get("title") or aid

    today = str(date.today())

    for slug, td in affected.items():
        topic_name = td["name"]
        existing = read_topic_content(slug)

        if existing:
            print(f"  [{slug}] Removing '{paper_title}' from '{topic_name}'...")
            content = llm.call(
                _remove_system_prompt(),
                _remove_user_prompt(topic_name, existing, paper_title, aid),
                model, api_key,
            )
            write_topic_file(slug, topic_name, td, content)
            print(f"    ✓ Updated: {topic_path(slug)}")

        td["papers"] = [p for p in td.get("papers", []) if p != aid]
        td["last_updated"] = today
        idx[slug] = td

    save_topic_index(idx)


def find_merge_candidates(
    model: str = DEFAULT_MODEL,
    openrouter_api_key: str = "",
) -> list:
    """Ask the LLM to propose topic merges. Returns a list of merge-group dicts.

    Each dict has: merge_slugs (list[str]), new_name (str), reason (str).
    Also attaches paper_count for each slug as merge_slugs_info for display.
    """
    api_key = openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("No API key: pass --openrouter_api_key or set $OPENROUTER_API_KEY")

    idx = load_topic_index()
    if len(idx) < 2:
        return []

    topic_summaries = [
        {
            "slug":                   slug,
            "name":                   td["name"],
            "paper_count":            len(td.get("papers", [])),
            "fingerprint_tags":       td.get("fingerprint_tags", []),
            "fingerprint_benchmarks": td.get("fingerprint_benchmarks", []),
        }
        for slug, td in idx.items()
    ]

    print("  Asking LLM for merge candidates...")
    result = llm.call_json(_merge_system_prompt(), _merge_user_prompt(topic_summaries), model, api_key)
    merges = result.get("merges", [])

    # Validate: keep only groups where all slugs exist and no slug is reused
    seen_slugs: set = set()
    valid = []
    for m in merges:
        slugs = [s for s in m.get("merge_slugs", []) if s in idx]
        if len(slugs) < 2:
            continue
        if any(s in seen_slugs for s in slugs):
            continue
        seen_slugs.update(slugs)
        m["merge_slugs"] = slugs
        m["merge_slugs_info"] = [(s, len(idx[s].get("papers", [])), idx[s]["name"]) for s in slugs]
        valid.append(m)

    return valid


def execute_merge(
    merge: dict,
    model: str = DEFAULT_MODEL,
    openrouter_api_key: str = "",
):
    """Execute a single merge group produced by find_merge_candidates.

    Generates a new combined topic file from all papers in the merged topics,
    registers it in the index, and removes the old topics + their .md files.
    """
    api_key = openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("No API key: pass --openrouter_api_key or set $OPENROUTER_API_KEY")

    idx         = load_topic_index()
    paper_index = load_paper_index()
    slugs       = merge["merge_slugs"]
    new_name    = merge["new_name"]
    new_slug    = re.sub(r"[^a-z0-9]+", "-", new_name.lower()).strip("-")

    # Avoid collision with an existing slug that isn't one being merged
    if new_slug in idx and new_slug not in slugs:
        new_slug = new_slug + "-merged"

    # Aggregate papers, tags, benchmarks
    all_paper_ids: list = list(dict.fromkeys(
        aid for s in slugs for aid in idx[s].get("papers", [])
    ))
    all_tags: list = list({t for s in slugs for t in idx[s].get("fingerprint_tags", [])})
    all_benches: list = list({b for s in slugs for b in idx[s].get("fingerprint_benchmarks", [])})

    # Load notes for synthesis
    notes = []
    for aid in all_paper_ids:
        info = paper_index.get(aid)
        if info and info.get("file"):
            note = parse_note(os.path.join(RESEARCH_PATH, info["file"]))
            if note["title"]:
                notes.append(note)

    if not notes:
        print(f"  [skip] No readable notes for merge '{new_name}'.")
        return

    print(f"  Generating merged topic '{new_name}' from {len(notes)} paper(s)...")
    pool    = _build_citation_pool(notes, paper_index)
    cblk    = _format_citation_block(pool) if pool else ""
    content = llm.call(_init_system_prompt(), _init_user_prompt(new_name, notes, cblk), model, api_key)
    bib     = _build_jargon_table(pool)

    today = str(date.today())
    new_td = {
        "name":                   new_name,
        "file":                   f"Topics/{new_slug}.md",
        "fingerprint_tags":       all_tags,
        "fingerprint_benchmarks": all_benches,
        "min_tag_overlap":        1,
        "papers":                 all_paper_ids,
        "last_updated":           today,
    }

    # Write new topic
    idx[new_slug] = new_td
    save_topic_index(idx)
    write_topic_file(new_slug, new_name, new_td, content, bib)
    print(f"    ✓ Created: {topic_path(new_slug)}")

    # Remove old topics
    for slug in slugs:
        if slug == new_slug:
            continue
        old_file = topic_path(slug)
        if os.path.exists(old_file):
            os.remove(old_file)
        idx.pop(slug, None)
    save_topic_index(idx)
    print(f"    ✗ Removed: {slugs}")


def backlink_topics():
    """Re-write all topic files to add/refresh the ## Papers backlink section."""
    idx = load_topic_index()
    updated = 0
    for slug, td in idx.items():
        if not os.path.exists(topic_path(slug)):
            continue
        content = read_topic_content(slug)  # strips old papers section
        write_topic_file(slug, td["name"], td, content)  # re-writes with fresh section
        updated += 1
    print(f"Updated {updated} topic file(s) with paper backlinks.")


def list_topics():
    """Show all registered topics and their status."""
    idx = load_topic_index()
    if not idx:
        print("No topics registered. Run: uv run topic_manager.py discover")
        return
    print(f"\n{'Slug':<35} {'Papers':>6}  {'Updated':<12}  File exists")
    print("─" * 70)
    for slug, td in sorted(idx.items()):
        exists = "✓" if os.path.exists(topic_path(slug)) else "✗"
        updated = td.get("last_updated", "never")
        n_papers = len(td.get("papers", []))
        print(f"  {slug:<33} {n_papers:>6}  {updated:<12}  {exists}")
    print()


if __name__ == "__main__":
    fire.Fire({
        "create":           create,
        "init":             init,
        "init_all":         init_all,
        "update":           update,
        "discover":         discover,
        "list":             list_topics,
        "backlink_topics":  backlink_topics,
    })
