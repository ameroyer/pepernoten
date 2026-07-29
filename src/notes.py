# Note parsing, topic matching, backlink injection, and changelog utilities

import re
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

from vault import RESEARCH_PATH, fm_field, frontmatter

# Tags that identify structural meta-info, not research topics
_NOT_BENCH = {
    "Authors", "Year", "arXiv", "Connection to Gaps", "Paper",
    "Method", "Model", "Params", "Budget", "Space", "Modality",
    "NFE", "Codebook", "Tokens", "Sec/Step (H100)", "Key Innovation",
    "Key Technique", "Size",
}


# ──────────────────────────────────────────────────────────────────────────────
# Note parsing
# ──────────────────────────────────────────────────────────────────────────────

def parse_note(path: str) -> dict:
    """Extract metadata and key sections from a paper note."""
    text = Path(path).read_text(encoding="utf-8")
    fm = frontmatter(text)

    tags = re.findall(r"^\s+- (.+)$", fm, re.MULTILINE)
    tags = [t for t in tags if t not in {"research", "ai-parsed", "paper", "arxiv", "preprint"}]

    benches = set()
    rb = re.search(r"### Results(.*?)(?=\n###|\Z)", text, re.DOTALL)
    if rb:
        for row_m in re.finditer(r"\|([^\n]+)\|\n\|[-| ]+\|", rb.group(1)):
            cols = [c.strip() for c in row_m.group(1).split("|") if c.strip()]
            benches.update(c for c in cols if c not in _NOT_BENCH and len(c) > 2)

    tldr_m = re.search(r"\[!quote\].*?\n>\s*\*\*TL;DR\*\*\n>\s*(.*?)(?=\n\n|\n##)", text, re.DOTALL)
    if not tldr_m:
        tldr_m = re.search(r"\[!quote\].*?\n>\s*(.*?)(?=\n\n|\n##)", text, re.DOTALL)
    tldr = tldr_m.group(1).replace("\n> ", "\n").strip() if tldr_m else ""

    gaps_m = re.search(r"\[!danger\][^\n]*\n(.*?)(?=\n>\s*\[!|\n##)", text, re.DOTALL)
    lims_m = re.search(r"\[!warning\][^\n]*\n(.*?)(?=\n>\s*\[!|\n##)", text, re.DOTALL)
    gaps = gaps_m.group(1).strip() if gaps_m else ""
    lims = lims_m.group(1).strip() if lims_m else ""

    sections = {}
    for sec in ["Problem", "Methodology", "Results", "Ablation"]:
        m = re.search(rf"### {sec}\n(.*?)(?=\n### |\n## |\Z)", text, re.DOTALL)
        if m:
            sections[sec.lower()] = m.group(1).strip()

    return dict(
        title      = fm_field(fm, "title"),
        authors    = fm_field(fm, "authors"),
        date       = fm_field(fm, "date"),
        arxiv_id   = fm_field(fm, "arxiv_id"),
        tags       = tags,
        benchmarks = sorted(benches),
        tldr       = tldr,
        gaps       = gaps,
        limitations = lims,
        **sections,
    )


def note_xml(d: dict, index: int | None = None) -> str:
    """Render a parsed note as an XML block for prompt inclusion."""
    idx_attr = f' index="{index}"' if index is not None else ""
    parts = [f'<paper{idx_attr} title="{d["title"]}" arxiv_id="{d["arxiv_id"]}" date="{d["date"]}">']
    for key, tag in [
        ("tldr", "tldr"), ("problem", "problem"), ("methodology", "methodology"),
        ("results", "results"), ("ablation", "ablation"), ("gaps", "gaps"),
        ("limitations", "limitations"),
    ]:
        if d.get(key):
            parts.append(f"<{tag}>{d[key]}</{tag}>")
    if d.get("tags"):
        parts.append(f"<tags>{', '.join(d['tags'])}</tags>")
    if d.get("benchmarks"):
        parts.append(f"<benchmarks_evaluated>{', '.join(d['benchmarks'])}</benchmarks_evaluated>")
    parts.append("</paper>")
    return "\n".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Topic matching
# ──────────────────────────────────────────────────────────────────────────────

def match_topics(note: dict, topic_index: dict) -> list[str]:
    """Return slugs of topics this paper belongs to."""
    ptags  = set(note["tags"])
    pbench = set(note["benchmarks"])
    matched = []
    for slug, td in topic_index.items():
        ftags    = set(td["fingerprint_tags"])
        fbenches = set(td.get("fingerprint_benchmarks", []))
        min_ov   = td.get("min_tag_overlap", 2)
        if len(ptags & ftags) >= min_ov or len(pbench & fbenches) >= 1:
            matched.append(slug)
    return matched


def unmatched_notes(idx: dict) -> list[dict]:
    """Return parsed notes that match none of the existing topics."""
    result = []
    for md in sorted(Path(RESEARCH_PATH).glob("*.md")):
        note = parse_note(str(md))
        if note["title"] and not match_topics(note, idx):
            result.append(note)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Note lookup
# ──────────────────────────────────────────────────────────────────────────────

def find_note_for_paper(index: dict, paper_name: str) -> str | None:
    needle = paper_name.lower().strip()
    best_file, best_ratio = None, 0.0
    for info in index.values():
        ratio = SequenceMatcher(None, needle, info["title"].lower()).ratio()
        if ratio > best_ratio and ratio > 0.72:
            best_ratio = ratio
            best_file = info["file"]
    return best_file


# ──────────────────────────────────────────────────────────────────────────────
# Backlinks
# ──────────────────────────────────────────────────────────────────────────────

def inject_backlinks(note_stem: str, paper_title: str) -> list[str]:
    updated: list[str] = []
    for md_file in Path(RESEARCH_PATH).glob("*.md"):
        if md_file.stem == note_stem:
            continue
        content = md_file.read_text(encoding="utf-8")
        if f"[[{note_stem}" in content:
            continue
        new_lines: list[str] = []
        changed = False
        for line in content.split("\n"):
            if "|" in line:
                parts = line.split("|")
                if len(parts) >= 3:
                    raw_cell = parts[1].strip()
                    if raw_cell.startswith("[[") or raw_cell.startswith("["):
                        new_lines.append(line)
                        continue
                    ratio = SequenceMatcher(None, raw_cell.lower(), paper_title.lower()).ratio()
                    if ratio > 0.75 and raw_cell:
                        parts[1] = f" [[{note_stem}\\|{raw_cell}]] "
                        line = "|".join(parts)
                        changed = True
            new_lines.append(line)
        if changed:
            md_file.write_text("\n".join(new_lines), encoding="utf-8")
            updated.append(md_file.name)
    return updated


# ──────────────────────────────────────────────────────────────────────────────
# Related work extraction
# ──────────────────────────────────────────────────────────────────────────────

def extract_related_works(note_path: str) -> list[dict]:
    """Parse the Related Work table from a note file.

    Returns a list of dicts with keys: title, authors, year, arxiv_id.
    """
    try:
        text = Path(note_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    rw_m = re.search(r"## [^\n]*Related Work\n(.*?)(?=\n##|\Z)", text, re.DOTALL | re.IGNORECASE)
    if not rw_m:
        return []
    refs = []
    for line in rw_m.group(1).strip().split("\n"):
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) < 4 or not cells[0] or "---" in cells[0]:
            continue
        title = cells[0]
        if title in ("Paper", "Model", "Method"):
            continue
        authors = cells[1] if len(cells) > 1 else ""
        year    = cells[2] if len(cells) > 2 else ""
        aid_m   = re.search(r"(\d{4}\.\d{4,5})", cells[3] if len(cells) > 3 else "")
        refs.append({
            "title":    title,
            "authors":  authors,
            "year":     year,
            "arxiv_id": aid_m.group(1) if aid_m else "",
        })
    return refs


# ──────────────────────────────────────────────────────────────────────────────
# Topic update helpers
# ──────────────────────────────────────────────────────────────────────────────

def parse_update_response(raw: str) -> tuple[str, str]:
    """Split Claude's response into (document, changelog)."""
    if "---CHANGELOG---" in raw:
        doc, log = raw.split("---CHANGELOG---", 1)
        return doc.strip(), log.strip()
    return raw.strip(), ""


def append_note_changelog(note_path: str, entries: list[dict]):
    """Append changelog entries to the paper note.

    Each entry: {"slug": str, "name": str, "changelog": str, "applied": bool}
    """
    today = str(date.today())
    text  = Path(note_path).read_text(encoding="utf-8")

    blocks = []
    for e in entries:
        marker = "" if e["applied"] else " — NOT APPLIED"
        blocks.append(f"### [{e['slug']}] {today}{marker}\n{e['changelog']}")

    new_content = "\n\n".join(blocks)
    if "## Topic Changelog" in text:
        text = text.rstrip() + "\n\n" + new_content
    else:
        text = text.rstrip() + "\n\n---\n\n## Topic Changelog\n\n" + new_content

    Path(note_path).write_text(text, encoding="utf-8")
