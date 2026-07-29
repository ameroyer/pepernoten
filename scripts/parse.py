# Usage (from project root):
#   uv run scripts/parse.py parse https://arxiv.org/abs/2405.12345
#   uv run scripts/parse.py parse_many 2405.12345 2406.67890
#   uv run scripts/parse.py sync
#   uv run scripts/parse.py backlink_all

import os
import sys
import fire

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from vault import load_paper_index, load_topic_index
from notes import parse_note, match_topics, inject_backlinks
from arxiv_utils import extract_arxiv_id, fetch_digest, arxiv_id_from_paper, paper_score
from paper import process_arxiv_paper, DEFAULT_MODEL, VISION_MODEL


def _batch_update_topics(note_paths: list[str], model: str, openrouter_api_key: str,
                         safe_update: bool = False):
    """Discover + update topics for a batch of freshly created notes."""
    try:
        import topic_manager as tm
    except ImportError:
        print("  [topics] topic_manager.py not found — skipping.")
        return

    api_key = openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
    print(f"\n[topics] Updating topics for {len(note_paths)} new note(s)...")

    unmatched_paths = []
    idx = load_topic_index()
    for path in note_paths:
        note = parse_note(path)
        if not note["title"]:
            continue
        if match_topics(note, idx):
            tm.update(path, model=model, openrouter_api_key=api_key, safe_update=safe_update)
        else:
            unmatched_paths.append(path)

    if unmatched_paths:
        print(f"  {len(unmatched_paths)} note(s) unmatched — running discovery on full vault...")
        tm.discover(model=model, openrouter_api_key=api_key)
        for path in unmatched_paths:
            tm.update(path, model=model, openrouter_api_key=api_key, safe_update=safe_update)


def parse(
    arxiv_url: str,
    model: str = DEFAULT_MODEL,
    vision_model: str = VISION_MODEL,
    openrouter_api_key: str = "",
    verbosity: int = 2,
    update_topics: bool = True,
    safe_update: bool = False,
):
    """Parse a single arXiv paper and add it to the vault."""
    path = process_arxiv_paper(
        arxiv_url, model=model, vision_model=vision_model,
        openrouter_api_key=openrouter_api_key, verbosity=verbosity,
    )
    if path and update_topics:
        _batch_update_topics([path], model=model, openrouter_api_key=openrouter_api_key,
                             safe_update=safe_update)
    return path


def sync(
    model: str = DEFAULT_MODEL,
    vision_model: str = VISION_MODEL,
    openrouter_api_key: str = "",
    top_n: int = 3,
    verbosity: int = 2,
    debug_digest: bool = False,
    update_topics: bool = True,
    safe_update: bool = False,
):
    """Fetch Scholar Inbox digest, pick top N unprocessed papers, generate notes."""
    print("Fetching Scholar Inbox digest...")
    papers = fetch_digest(debug=debug_digest)
    print(f"  {len(papers)} paper(s) in digest.")

    index = load_paper_index()
    existing_ids = set(index.keys())

    candidates: list[tuple[dict, str]] = []
    skipped = 0
    for p in papers:
        aid = arxiv_id_from_paper(p)
        if not aid:
            continue
        if aid in existing_ids:
            skipped += 1
            continue
        candidates.append((p, aid))

    print(f"  {skipped} already in vault, {len(candidates)} new.")

    if not candidates:
        print("Nothing new to process.")
        return

    candidates.sort(key=lambda x: paper_score(x[0]), reverse=True)
    to_process = candidates[:top_n]

    print(f"\nWill process top {len(to_process)} paper(s):")
    for p, aid in to_process:
        title = p.get("title", aid)
        score = paper_score(p)
        score_str = f"  score={score:.3f}" if score else ""
        print(f"  [{aid}]{score_str}  {title[:70]}")

    api_key = openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("No API key: pass --openrouter_api_key or set $OPENROUTER_API_KEY")

    ok, failed = 0, 0
    new_note_paths = []
    for i, (paper, aid) in enumerate(to_process, start=1):
        print(f"\n{'='*60}")
        print(f"[{i}/{len(to_process)}] {aid}")
        print('='*60)
        try:
            note_path = process_arxiv_paper(
                f"https://arxiv.org/abs/{aid}",
                model=model,
                vision_model=vision_model,
                openrouter_api_key=api_key,
                verbosity=verbosity,
            )
            if note_path:
                new_note_paths.append(note_path)
            ok += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1

    print(f"\nSync complete — {ok} succeeded, {failed} failed.")

    if update_topics and new_note_paths:
        _batch_update_topics(new_note_paths, model=model, openrouter_api_key=api_key,
                             safe_update=safe_update)


def backlink_all():
    """Run inject_backlinks for every paper in the index."""
    index = load_paper_index()
    if not index:
        print("Index is empty — no papers to process.")
        return
    total_changes: dict[str, list[str]] = {}
    for _, info in index.items():
        note_stem = os.path.splitext(info["file"])[0]
        updated = inject_backlinks(note_stem, info["title"])
        for f in updated:
            total_changes.setdefault(f, []).append(info["title"])
    if total_changes:
        print(f"\nUpdated {len(total_changes)} note(s):")
        for note, papers in sorted(total_changes.items()):
            print(f"  {note}")
            for p in papers:
                print(f"    ← {p}")
    else:
        print("No backlinks to inject — all notes already up-to-date.")


def parse_many(
    *arxiv_ids: str,
    model: str = DEFAULT_MODEL,
    vision_model: str = VISION_MODEL,
    openrouter_api_key: str = "",
    verbosity: int = 2,
    update_topics: bool = True,
    safe_update: bool = False,
):
    """Parse a batch of arXiv papers and auto-discover topics from them."""
    api_key = openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("No API key: pass --openrouter_api_key or set $OPENROUTER_API_KEY")
    if not arxiv_ids:
        raise ValueError("Pass at least one arXiv ID or URL.")

    index = load_paper_index()
    new_note_paths = []
    ok = failed = skipped = 0

    for i, raw in enumerate(arxiv_ids, start=1):
        try:
            aid = extract_arxiv_id(raw)
        except ValueError:
            print(f"[{i}] Could not parse arXiv ID from: {raw}")
            failed += 1
            continue
        if aid in index:
            print(f"[{i}] {aid} already in vault — skipping.")
            skipped += 1
            continue

        print(f"\n{'='*60}\n[{i}/{len(arxiv_ids)}] {aid}\n{'='*60}")
        try:
            note_path = process_arxiv_paper(
                f"https://arxiv.org/abs/{aid}",
                model=model,
                vision_model=vision_model,
                openrouter_api_key=api_key,
                verbosity=verbosity,
            )
            if note_path:
                new_note_paths.append(note_path)
            ok += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1

    print(f"\nDone — {ok} new, {skipped} skipped, {failed} failed.")

    if update_topics and new_note_paths:
        _batch_update_topics(new_note_paths, model=model, openrouter_api_key=api_key,
                             safe_update=safe_update)


if __name__ == "__main__":
    fire.Fire({
        "parse":        parse,
        "parse_many":   parse_many,
        "sync":         sync,
        "backlink_all": backlink_all,
    })
