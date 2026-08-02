# Shared vault paths and basic note utilities — imported by all scripts and the CLI

import json
import os
import re
import threading
from pathlib import Path

VAULT_PATH      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH_PATH   = os.path.join(VAULT_PATH, "Research")
TOPICS_PATH     = os.path.join(RESEARCH_PATH, "Topics")
INDEX_PATH      = os.path.join(RESEARCH_PATH, ".paper_index.json")
TOPIC_INDEX     = os.path.join(TOPICS_PATH, ".topic_index.json")
TAG_INDEX_PATH  = os.path.join(RESEARCH_PATH, ".tag_index.json")
IMAGES_PATH     = os.path.join(RESEARCH_PATH, "images")
THUMBNAIL_PATH  = os.path.join(RESEARCH_PATH, "Thumbnails")

# Guards read-modify-write of the on-disk JSON indices — needed once papers
# are parsed concurrently (see parse.py), so one thread's save can't clobber
# another's in-flight update.
_index_lock = threading.Lock()
_tag_lock   = threading.Lock()


def load_paper_index() -> dict:
    if not os.path.exists(INDEX_PATH):
        return {}
    with open(INDEX_PATH) as f:
        return json.load(f)


def save_paper_index(index: dict):
    with open(INDEX_PATH, "w") as f:
        json.dump(index, f, indent=2)


def add_paper_to_index(arxiv_id: str, info: dict):
    """Atomically merge one paper's entry into the index (safe across threads)."""
    with _index_lock:
        index = load_paper_index()
        index[arxiv_id] = info
        save_paper_index(index)


def load_topic_index() -> dict:
    if not os.path.exists(TOPIC_INDEX):
        return {}
    with open(TOPIC_INDEX) as f:
        return json.load(f)


def save_topic_index(idx: dict):
    os.makedirs(TOPICS_PATH, exist_ok=True)
    with open(TOPIC_INDEX, "w") as f:
        json.dump(idx, f, indent=2)


def frontmatter(text: str) -> str:
    """Return the raw YAML frontmatter block of a note ('' if none)."""
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    return m.group(1) if m else ""


def fm_field(fm: str, field: str) -> str:
    """Extract a scalar frontmatter field ('' if absent)."""
    m = re.search(rf'^{field}:\s*"?(.+?)"?\s*$', fm, re.MULTILINE)
    return m.group(1).strip('"') if m else ""


def note_meta(filename: str) -> dict:
    path = os.path.join(RESEARCH_PATH, filename)
    if not os.path.exists(path):
        return {}
    fm = frontmatter(Path(path).read_text(encoding="utf-8", errors="ignore"))
    v = fm_field(fm, "verbosity")
    return {
        "title":      fm_field(fm, "title"),
        "authors":    fm_field(fm, "authors"),
        "date":       fm_field(fm, "date"),
        "bookmarked": fm_field(fm, "bookmarked").lower() == "true",
        "verbosity":  int(v) if v and v.isdigit() else None,
        "file":       filename,
        "full_path":  path,
    }


def delete_paper(aid: str) -> bool:
    """Delete a paper's note file and remove it from all indices. Returns True on success."""
    papers = load_paper_index()
    if aid not in papers:
        return False
    info = papers[aid]

    # Remove note file
    note_file = os.path.join(RESEARCH_PATH, info.get("file", ""))
    if os.path.exists(note_file):
        os.remove(note_file)

    # Remove thumbnail (best-effort)
    thumb = os.path.join(THUMBNAIL_PATH, f"Fig_{aid}.png")
    if os.path.exists(thumb):
        os.remove(thumb)

    # Update paper index
    del papers[aid]
    save_paper_index(papers)

    # Remove from topic index papers lists (does NOT edit topic .md files)
    topics = load_topic_index()
    changed = False
    for td in topics.values():
        if aid in td.get("papers", []):
            td["papers"].remove(aid)
            changed = True
    if changed:
        save_topic_index(topics)

    return True


def cost_footer(tokens: int, cost: float | None) -> str:
    """Small dim footer line reporting how much a note/topic generation cost.
    '' if no tokens were tracked (e.g. tracking wasn't wired up for that call site)."""
    if not tokens:
        return ""
    price = f" (~${cost:.3f})" if cost is not None else ""
    return (
        '<div style="margin-top:1.5em;font-size:0.75em;opacity:0.4;">'
        f"Generated using {tokens:,} tokens{price}"
        "</div>"
    )


def short_authors(authors: str) -> str:
    parts = [a.strip() for a in authors.split(",")]
    return ", ".join(parts[:2]) + (" et al." if len(parts) > 2 else "")


# ──────────────────────────────────────────────────────────────────────────────
# Tag index
# ──────────────────────────────────────────────────────────────────────────────

_META_TAGS = {"research", "ai-parsed", "paper", "arxiv", "preprint",
              "deep-learning", "neural-network"}


def load_tag_index() -> list[str]:
    if os.path.exists(TAG_INDEX_PATH):
        with open(TAG_INDEX_PATH) as f:
            data = json.load(f)
        return data.get("tags", [])
    return []


def save_tag_index(tags: list[str]):
    existing = {}
    if os.path.exists(TAG_INDEX_PATH):
        with open(TAG_INDEX_PATH) as f:
            existing = json.load(f)
    existing["tags"] = sorted(set(tags), key=lambda t: t.lstrip("0123456789"))
    with open(TAG_INDEX_PATH, "w") as f:
        json.dump(existing, f, indent=2)


def update_tag_index(new_tags: list[str]) -> list[str]:
    with _tag_lock:
        current = load_tag_index()
        current_set = set(current)
        added = [t for t in new_tags if t not in current_set and t not in _META_TAGS]
        if added:
            save_tag_index(current + added)
            print(f"  New tags added to index: {added}")
        return added


# ──────────────────────────────────────────────────────────────────────────────
# Topic file I/O
# ──────────────────────────────────────────────────────────────────────────────

def topic_path(slug: str) -> str:
    return os.path.join(TOPICS_PATH, f"{slug}.md")


def write_topic_file(
    slug: str, topic_name: str, td: dict, content: str, bibliography: str = "",
    tokens_used: int = 0, cost_usd: float | None = None,
):
    os.makedirs(TOPICS_PATH, exist_ok=True)
    papers_list = "\n".join(f"  - {p}" for p in td.get("papers", []))
    tags_list   = "\n".join(f"  - {t}" for t in td["fingerprint_tags"])
    bench_list  = "\n".join(f"  - {b}" for b in td.get("fingerprint_benchmarks", []))
    cost_field  = f"{cost_usd:.4f}" if cost_usd is not None else "n/a"
    frontmatter = (
        f"---\n"
        f'topic: "{topic_name}"\n'
        f"slug: {slug}\n"
        f"fingerprint_tags:\n{tags_list}\n"
        f"fingerprint_benchmarks:\n{bench_list}\n"
        f"min_tag_overlap: {td.get('min_tag_overlap', 2)}\n"
        f"paper_count: {len(td.get('papers', []))}\n"
        f"papers:\n{papers_list}\n"
        f"last_updated: \"{td.get('last_updated', '')}\"\n"
        f"tokens_used: {tokens_used}\n"
        f"est_cost_usd: \"{cost_field}\"\n"
        f"---\n\n"
    )
    refs_section = (
        "\n\n---\n\n## Method Index\n\n" + bibliography
        if bibliography else ""
    )
    paper_ids = td.get("papers", [])
    if paper_ids:
        paper_index = load_paper_index()
        links = []
        for aid in paper_ids:
            info = paper_index.get(aid)
            if info and info.get("file"):
                stem = os.path.splitext(info["file"])[0]
                links.append(f"- [[{stem}]]")
            else:
                links.append(f"- {aid}")
        papers_section = "\n\n---\n\n## Papers\n\n" + "\n".join(links)
    else:
        papers_section = ""
    footer_html = cost_footer(tokens_used, cost_usd)
    footer = f"\n\n{footer_html}" if footer_html else ""
    Path(topic_path(slug)).write_text(
        frontmatter + content + refs_section + papers_section + footer, encoding="utf-8"
    )


def read_topic_content(slug: str) -> str:
    text = Path(topic_path(slug)).read_text(encoding="utf-8")
    m = re.match(r"^---\n.*?\n---\n+", text, re.DOTALL)
    body = text[m.end():].strip() if m else text.strip()
    # Strip appended sections (method index + backlinks) — always regenerated on write
    for marker in ["\n---\n\n## Method Index", "\n---\n\n## References", "\n---\n\n## Papers", "\n## Papers"]:
        if marker in body:
            body = body.split(marker)[0]
            break
    return body.strip()
