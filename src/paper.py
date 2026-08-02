# Core paper processing: fetch arXiv metadata, extract figures, synthesise with LLM, write note
#
# Synthesis is two-stage: a small/fast model (extraction_model) extracts bounded factual data
# as JSON, then the configured writer model (model) writes the human-readable prose sections
# from those facts plus the original paper text as a grounding reference.

import os
import re
import time
import xml.etree.ElementTree as ET

import fitz
import requests

import llm
from arxiv_utils import arxiv_api, extract_arxiv_id, fetch_arxiv_html, html_to_text, lookup_arxiv_id
from bibtex import build_arxiv_misc
from figures import (
    _describe_figure,
    _pick_best_figure,
    extract_figures_from_html,
    extract_figures_with_vision,
    extract_leading_figure,
)
from notes import find_note_for_paper, inject_backlinks, parse_sectioned_response
from prompts import (
    _extraction_system_prompt,
    _extraction_user_message,
    _writer_system_prompt,
    _writer_user_message,
)
from vault import (
    RESEARCH_PATH,
    VAULT_PATH,
    add_paper_to_index,
    cost_footer,
    load_paper_index,
    load_tag_index,
    update_tag_index,
)

DEFAULT_MODEL     = "anthropic/claude-sonnet-4-5"     # writer stage (prose)
EXTRACTION_MODEL  = "anthropic/claude-haiku-4.5"       # extraction stage (bounded JSON facts)
VISION_MODEL      = "google/gemini-2.5-flash-lite"


def _bullet_list(section_text: str) -> list[str]:
    """Parse a writer-stage '- bullet' section into a plain list of strings."""
    items = []
    for ln in section_text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("- "):
            ln = ln[2:].strip()
        elif ln.startswith("-"):
            ln = ln[1:].strip()
        if ln:
            items.append(ln)
    return items


def _thoughts_blockquote(preserved_thoughts: str) -> str:
    if not preserved_thoughts:
        return ">"
    return "\n".join(f"> {ln}" if ln else ">" for ln in preserved_thoughts.splitlines())


def process_arxiv_paper(
    arxiv_url: str,
    model: str = DEFAULT_MODEL,
    extraction_model: str = EXTRACTION_MODEL,
    vision_model: str = VISION_MODEL,
    openrouter_api_key: str = "",
    verbosity: int = 2,
    preserved_thoughts: str = "",
) -> str | None:
    api_key = openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("No API key: pass --openrouter_api_key or set $OPENROUTER_API_KEY")
    client = llm.make_client(api_key)
    tracker = llm.UsageTracker()

    arxiv_id = extract_arxiv_id(arxiv_url)
    print(f"Fetching metadata for {arxiv_id}...")

    _NS = {"a": "http://www.w3.org/2005/Atom", "ax": "http://arxiv.org/schemas/atom"}
    for _attempt in range(4):
        try:
            arxiv_api.wait()
            resp = requests.get(
                f"https://export.arxiv.org/api/query?id_list={arxiv_id}",
                timeout=30,
                headers={"User-Agent": "PeperNoten/2.0"},
            )
            resp.raise_for_status()
            break
        except requests.exceptions.Timeout:
            wait = 2 ** _attempt * 5
            print(f"  arXiv metadata timeout (attempt {_attempt + 1}/4), retrying in {wait}s...")
            time.sleep(wait)
    else:
        raise RuntimeError(f"arXiv metadata fetch failed after 4 attempts for {arxiv_id}")

    entry = ET.fromstring(resp.text).find("a:entry", _NS)
    if entry is None:
        raise ValueError(f"No paper found for arXiv ID {arxiv_id}")
    title = (entry.findtext("a:title", "", _NS) or "").strip().replace("\n", " ").replace('"', "'")
    authors = ", ".join(
        a.findtext("a:name", "", _NS).strip()
        for a in entry.findall("a:author", _NS)
    )
    date = (entry.findtext("a:published", "", _NS) or "")[:10]
    cat_el = entry.find("ax:primary_category", _NS)
    primary_category = cat_el.get("term", "") if cat_el is not None else ""

    os.makedirs(RESEARCH_PATH, exist_ok=True)
    safe_title = "".join(x for x in title if x.isalnum() or x in " -_")
    note_path = os.path.join(RESEARCH_PATH, f"{safe_title}.md")
    if os.path.exists(note_path):
        os.remove(note_path)

    print("Fetching arXiv HTML...")
    html, html_base_url = fetch_arxiv_html(arxiv_id)
    if html:
        print("  HTML available — using as primary source.")
        full_text = html_to_text(html)
        figure_map, caption_map, needs_pdf = extract_figures_from_html(arxiv_id, html, html_base_url)
        print(f"  Figures from HTML: {list(figure_map)}")
        if needs_pdf:
            print(f"  Figures needing PDF: {list(needs_pdf)}")
    else:
        print("  HTML unavailable — falling back to PDF.")
        full_text = ""
        figure_map, caption_map, needs_pdf = {}, {}, {}

    need_pdf = not html or bool(needs_pdf)
    doc: fitz.Document | None = None
    pdf_path = f"/tmp/{arxiv_id}.pdf"
    try:
        if need_pdf:
            print("Downloading PDF...")
            arxiv_api.wait()
            pdf_resp = requests.get(f"https://arxiv.org/pdf/{arxiv_id}", stream=True, timeout=60)
            pdf_resp.raise_for_status()
            with open(pdf_path, "wb") as _f:
                for _chunk in pdf_resp.iter_content(chunk_size=65536):
                    _f.write(_chunk)
            doc = fitz.open(pdf_path)

            if not full_text:
                full_text = "".join(page.get_text() for page in doc)

            if needs_pdf:
                print(f"  Extracting {len(needs_pdf)} PDF-only figures...")
                extra_map, extra_caps = extract_figures_with_vision(
                    doc, arxiv_id,
                    skip_labels=set(figure_map), targets=needs_pdf,
                )
                figure_map.update(extra_map)
                caption_map.update(extra_caps)

            if not html:
                print("  Full PDF figure extraction...")
                extra_map, extra_caps = extract_figures_with_vision(
                    doc, arxiv_id, skip_labels=set(figure_map)
                )
                figure_map.update(extra_map)
                caption_map.update(extra_caps)
                print(f"  Figures from PDF: {list(figure_map)}")

        best_banner = None
        if vision_model and figure_map:
            print("Selecting best banner figure...")
            best_banner = _pick_best_figure(figure_map, caption_map, client, vision_model, tracker=tracker)
            if best_banner:
                print(f"  Banner: {best_banner}")
        thumbnail_name = extract_leading_figure(doc, arxiv_id, figure_map, preferred_label=best_banner)
        if doc is not None:
            doc.close()
    finally:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)

    text_source = "arXiv HTML (structured, equations preserved as LaTeX)" if html else "PDF (plain text extraction)"
    if figure_map:
        figure_descriptions: dict[str, str] = {}
        if vision_model and not html:
            print("Describing figures with vision model...")
            for lbl, path in figure_map.items():
                full_path = os.path.join(VAULT_PATH, path)
                if os.path.exists(full_path):
                    figure_descriptions[lbl] = _describe_figure(
                        full_path, lbl, caption_map.get(lbl, ""), client, vision_model, tracker=tracker
                    )
        for lbl in figure_map:
            figure_descriptions.setdefault(lbl, caption_map.get(lbl, ""))
        figures_context = "\n".join(
            f"  {lbl}: {figure_descriptions[lbl]}" for lbl in figure_map
        )
    else:
        figures_context = ""

    print(f"Extracting facts with {extraction_model}...")
    existing_tags = load_tag_index()
    tags_context = (
        "\n\n<existing_tags>\n" + ", ".join(existing_tags) + "\n</existing_tags>"
        if existing_tags else ""
    )
    extraction_system = _extraction_system_prompt(tags_context)
    extraction_user = _extraction_user_message(figures_context, text_source, full_text)
    facts = llm.call_json(extraction_system, extraction_user, extraction_model, api_key, tracker=tracker)
    if not isinstance(facts, dict):
        raise ValueError(f"Extraction model returned a JSON {type(facts).__name__}, expected an object")

    print(f"Writing note with {model} (verbosity={verbosity})...")
    writer_system = _writer_system_prompt(verbosity)
    writer_user = _writer_user_message(facts, figures_context, text_source, full_text)
    raw_sections = llm.call(
        writer_system, writer_user, model, api_key, tracker=tracker,
        validate=lambda t: bool(parse_sectioned_response(t)),
        retry_hint="Return your response using the exact ===SECTION_NAME=== markers as instructed, nothing else.",
    )
    sections = parse_sectioned_response(raw_sections)

    tldr_summary = sections.get("tldr", "").replace('"', "'")

    tags_list = []
    ai_tags = facts.get("tags", [])
    if isinstance(ai_tags, list):
        normalised = [str(t).strip().replace(" ", "-").lower() for t in ai_tags]
        tags_list.extend(normalised)
        update_tag_index(normalised)
    tags_yaml = "\n".join(f"  - {t}" for t in tags_list)

    gaps = _bullet_list(sections.get("gaps", ""))
    gaps_md = "\n".join(f"> - {g}" for g in gaps) if gaps else "> - None identified"
    limitations = _bullet_list(sections.get("limitations", ""))
    limitations_md = "\n".join(f"> - {l}" for l in limitations) if limitations else "> - None identified"
    oddities = _bullet_list(sections.get("oddities", ""))
    oddities_md = "\n".join(f"> - {o}" for o in oddities) if oddities else ""
    oddities_section_md = (
        f"\n> [!question] Minor Flaws & Confusions\n{oddities_md}" if oddities_md else ""
    )

    figure_placements: list[dict] = []
    if figure_map:
        non_tables = [k for k in figure_map if "Table" not in k]
        tables     = [k for k in figure_map if "Table" in k]
        for candidate in ["Figure 1", "Figure 2", *non_tables]:
            if candidate in figure_map:
                figure_placements.append({"label": candidate, "after": "methodology"})
                break
        placed = {p["label"] for p in figure_placements}
        for candidate in ["Table 1", "Table 2", "Table 3", *tables, *non_tables]:
            if candidate in figure_map and candidate not in placed:
                figure_placements.append({"label": candidate, "after": "results"})
                break

    used_figure_paths: set[str] = set()

    def figs_at(section: str) -> str:
        blocks = []
        for p in figure_placements:
            lbl = p["label"]
            if p["after"] == section and lbl in figure_map:
                path = figure_map[lbl]
                used_figure_paths.add(path)
                cap = caption_map.get(lbl, lbl)
                blocks.append(f"![[{path}]]")
                blocks.append(f"*{lbl}: {cap}*")
        return ("\n\n" + "\n\n".join(blocks) + "\n") if blocks else ""

    related_work = facts.get("related_work", [])
    if related_work and isinstance(related_work[0], dict):
        missing_ids = [
            r for r in related_work
            if not re.match(r'^\d{4}\.\d{4,5}$', re.sub(r'v\d+$', '', (r.get("arxiv_id") or "").strip()))
        ]
        if missing_ids:
            print(f"Looking up {len(missing_ids)} related-work arXiv ID(s)...")
        for r in related_work:
            llm_id = re.sub(r'v\d+$', '', (r.get("arxiv_id") or "").strip())
            if re.match(r'^\d{4}\.\d{4,5}$', llm_id):
                r["arxiv_id"] = llm_id
            else:
                r["arxiv_id"] = lookup_arxiv_id(r.get("name", "")) or ""

    index = load_paper_index()
    if related_work and isinstance(related_work[0], dict):
        rows = []
        for r in related_work:
            name = r.get("name", "")
            authors_r = r.get("authors", "")
            year_r = str(r.get("year", ""))
            arxiv_id_r = (r.get("arxiv_id") or "").strip()
            gap_link = r.get("gap_link", "")
            note_file = find_note_for_paper(index, name)
            if note_file:
                stem = os.path.splitext(note_file)[0]
                name_cell = f"[[{stem}\\|{name}]]"
            else:
                name_cell = name
            arxiv_col = f"[{arxiv_id_r}](https://arxiv.org/abs/{arxiv_id_r})" if arxiv_id_r else ""
            year_int = int(year_r) if year_r.isdigit() else 0
            rows.append((year_int, f"| {name_cell} | {authors_r} | {year_r} | {arxiv_col} | {gap_link} |"))
        rows.sort(key=lambda x: x[0], reverse=True)
        related_md = (
            "| Paper | Authors | Year | arXiv | Connection to Gaps |\n"
            "|---|---|---|---|---|\n"
            + "\n".join(row for _, row in rows)
        )
    else:
        related_md = "*No related work extracted.*"

    concepts_md = ""
    if verbosity == 4:
        concepts_section = sections.get("concepts", "")
        blocks = []
        for line in concepts_section.splitlines():
            m = re.match(r"\*\*(.+?)\*\*:\s*(.+)", line.strip())
            if m:
                term, defn = m.group(1).strip(), m.group(2).strip()
                blocks.append(f"> [!info] {term}\n> {defn}")
        if blocks:
            concepts_md = "\n\n".join(blocks) + "\n"

    rt = facts.get("results_table", {})
    headers = rt.get("headers", []) if isinstance(rt, dict) else []
    rows_data = rt.get("rows", []) if isinstance(rt, dict) else []
    if headers and rows_data:
        sep = "|" + "|".join("---" for _ in headers) + "|"
        results_table_md = (
            "\n| " + " | ".join(headers) + " |\n"
            + sep + "\n"
            + "\n".join("| " + " | ".join(str(c) for c in row) for row in rows_data)
        )
    else:
        results_table_md = ""

    author_list = [a.strip() for a in authors.split(", ") if a.strip()]
    bibtex_entry = build_arxiv_misc(arxiv_id, title, author_list, date[:4], primary_category)

    hr = (
        '<div style="height:1px;background:linear-gradient('
        '90deg,transparent,var(--interactive-accent),transparent);'
        'margin:2.5em 0;opacity:0.35"></div>'
    )

    tokens_used = tracker.total_tokens()
    cost_usd = tracker.total_cost()
    cost_field = f"{cost_usd:.4f}" if cost_usd is not None else "n/a"
    footer_html = cost_footer(tokens_used, cost_usd)
    thoughts_block = _thoughts_blockquote(preserved_thoughts)

    markdown_content = f"""---
title: "{title}"
authors: "{authors}"
arxiv_id: "{arxiv_id}"
url: "https://arxiv.org/abs/{arxiv_id}"
date: "{date}"
tags:
{tags_yaml}
favorite: false
bookmarked: false
image: "{thumbnail_name}"
banner: "{thumbnail_name}"
banner_y: 0.4
summary: "{tldr_summary}"
verbosity: {verbosity}
tokens_used: {tokens_used}
est_cost_usd: "{cost_field}"
---

# {title}

<div style="display:flex;gap:18px;flex-wrap:wrap;align-items:center;margin:1.2em 0 0.5em;font-size:0.8em;opacity:0.5;border-left:3px solid var(--interactive-accent);padding-left:12px;">👤 {authors}&nbsp;&nbsp;·&nbsp;&nbsp;📅 {date}&nbsp;&nbsp;·&nbsp;&nbsp;<a href="https://arxiv.org/abs/{arxiv_id}">arXiv ↗</a></div>

> [!quote] **TL;DR**
> {tldr_summary}

## :LiFeather: My Notes

> [!note] Thoughts
{thoughts_block}

{hr}

## :LiZap: Research Gaps & Open Questions

> [!danger] What This Paper Leaves Open
{gaps_md}

> [!warning] Limitations
{limitations_md}
{oddities_section_md}

{hr}

## :LiBookOpen: Paper Summary

### Problem
{sections.get('problem', '')}
{figs_at('problem')}

### Methodology
{concepts_md}{sections.get('methodology', '')}
{figs_at('methodology')}

### Results
{sections.get('results', '')}
{results_table_md}
{figs_at('results')}

### Ablation
{sections.get('ablation', '')}
{figs_at('ablation')}

{hr}

## :LiShare2: Related Work

{related_md}

{hr}

## :LiClipboard: BibTeX

```bibtex
{bibtex_entry}
```

{footer_html}
"""

    with open(note_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)

    removed = 0
    for _, path in figure_map.items():
        if path not in used_figure_paths:
            full_path = os.path.join(VAULT_PATH, path)
            if os.path.exists(full_path):
                os.remove(full_path)
                removed += 1
    if removed:
        print(f"Removed {removed} unused figure(s).")

    add_paper_to_index(arxiv_id, {"title": title, "file": os.path.basename(note_path)})

    note_stem = os.path.splitext(os.path.basename(note_path))[0]
    backlinked = inject_backlinks(note_stem, title)
    if backlinked:
        print(f"Backlinked in {len(backlinked)} existing note(s): {', '.join(backlinked)}")

    print(f"Done! Note saved to: {note_path}")
    return note_path
