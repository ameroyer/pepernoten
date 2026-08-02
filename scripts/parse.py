# Usage (from project root):
#   uv run scripts/parse.py parse https://arxiv.org/abs/2405.12345
#   uv run scripts/parse.py parse_many 2405.12345 2406.67890
#   uv run scripts/parse.py sync
#   uv run scripts/parse.py reparse_all
#   uv run scripts/parse.py backlink_all

import concurrent.futures
import os
import sys
import fire

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from vault import RESEARCH_PATH, load_paper_index, load_topic_index, note_meta
from notes import parse_note, match_topics, inject_backlinks, extract_my_notes
from arxiv_utils import extract_arxiv_id, fetch_digest, arxiv_id_from_paper, paper_score
from paper import process_arxiv_paper, DEFAULT_MODEL, EXTRACTION_MODEL, VISION_MODEL

DEFAULT_MAX_WORKERS = 4


def _process_batch(
    items: list[tuple[str, str, int, str]], *, model: str, extraction_model: str, vision_model: str,
    api_key: str, max_workers: int,
) -> tuple[int, int, list[str]]:
    """Run process_arxiv_paper over (label, arxiv_id, verbosity, preserved_thoughts) tuples
    concurrently. Papers fetch/parse/call the LLM in parallel; shared state (paper index, tag
    index, backlinks) is locked internally so concurrent writes stay safe.
    """
    ok = failed = 0
    new_note_paths: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                process_arxiv_paper,
                f"https://arxiv.org/abs/{aid}",
                model=model,
                extraction_model=extraction_model,
                vision_model=vision_model,
                openrouter_api_key=api_key,
                verbosity=verbosity,
                preserved_thoughts=preserved_thoughts,
            ): (label, aid)
            for label, aid, verbosity, preserved_thoughts in items
        }
        for fut in concurrent.futures.as_completed(futures):
            label, aid = futures[fut]
            try:
                note_path = fut.result()
                if note_path:
                    new_note_paths.append(note_path)
                ok += 1
                print(f"[{label}] {aid} done.")
            except Exception as e:
                print(f"[{label}] {aid} ERROR: {e}")
                failed += 1
    return ok, failed, new_note_paths


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
            try:
                tm.update(path, model=model, openrouter_api_key=api_key, safe_update=safe_update)
            except Exception as e:
                print(f"  [topics] ERROR updating topics for {os.path.basename(path)}: {e}")
        else:
            unmatched_paths.append(path)

    if unmatched_paths:
        print(f"  {len(unmatched_paths)} note(s) unmatched — running discovery on full vault...")
        try:
            tm.discover(model=model, openrouter_api_key=api_key)
        except Exception as e:
            print(f"  [topics] ERROR during discovery: {e}")
        for path in unmatched_paths:
            try:
                tm.update(path, model=model, openrouter_api_key=api_key, safe_update=safe_update)
            except Exception as e:
                print(f"  [topics] ERROR updating topics for {os.path.basename(path)}: {e}")


def parse(
    arxiv_url: str,
    model: str = DEFAULT_MODEL,
    extraction_model: str = EXTRACTION_MODEL,
    vision_model: str = VISION_MODEL,
    openrouter_api_key: str = "",
    verbosity: int = 2,
    update_topics: bool = True,
    safe_update: bool = False,
):
    """Parse a single arXiv paper and add it to the vault."""
    path = process_arxiv_paper(
        arxiv_url, model=model, extraction_model=extraction_model, vision_model=vision_model,
        openrouter_api_key=openrouter_api_key, verbosity=verbosity,
    )
    if path and update_topics:
        _batch_update_topics([path], model=model, openrouter_api_key=openrouter_api_key,
                             safe_update=safe_update)
    return path


def sync(
    model: str = DEFAULT_MODEL,
    extraction_model: str = EXTRACTION_MODEL,
    vision_model: str = VISION_MODEL,
    openrouter_api_key: str = "",
    top_n: int = 3,
    verbosity: int = 2,
    debug_digest: bool = False,
    update_topics: bool = True,
    safe_update: bool = False,
    max_workers: int = DEFAULT_MAX_WORKERS,
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

    print(f"\nProcessing {len(to_process)} paper(s), up to {max_workers} in parallel...")
    items = [(f"{i}/{len(to_process)}", aid, verbosity, "") for i, (_, aid) in enumerate(to_process, start=1)]
    ok, failed, new_note_paths = _process_batch(
        items, model=model, extraction_model=extraction_model, vision_model=vision_model,
        api_key=api_key, max_workers=max_workers,
    )

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
    extraction_model: str = EXTRACTION_MODEL,
    vision_model: str = VISION_MODEL,
    openrouter_api_key: str = "",
    verbosity: int = 2,
    update_topics: bool = True,
    safe_update: bool = False,
    max_workers: int = DEFAULT_MAX_WORKERS,
):
    """Parse a batch of arXiv papers and auto-discover topics from them."""
    api_key = openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("No API key: pass --openrouter_api_key or set $OPENROUTER_API_KEY")
    if not arxiv_ids:
        raise ValueError("Pass at least one arXiv ID or URL.")

    index = load_paper_index()
    failed = skipped = 0
    items = []

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
        items.append((f"{i}/{len(arxiv_ids)}", aid, verbosity, ""))

    print(f"\nProcessing {len(items)} paper(s), up to {max_workers} in parallel...")
    ok, batch_failed, new_note_paths = _process_batch(
        items, model=model, extraction_model=extraction_model, vision_model=vision_model,
        api_key=api_key, max_workers=max_workers,
    )
    failed += batch_failed

    print(f"\nDone — {ok} new, {skipped} skipped, {failed} failed.")

    if update_topics and new_note_paths:
        _batch_update_topics(new_note_paths, model=model, openrouter_api_key=api_key,
                             safe_update=safe_update)


def reparse_all(
    *arxiv_ids: str,
    model: str = DEFAULT_MODEL,
    extraction_model: str = EXTRACTION_MODEL,
    vision_model: str = VISION_MODEL,
    openrouter_api_key: str = "",
    max_workers: int = DEFAULT_MAX_WORKERS,
    update_topics: bool = False,
    safe_update: bool = False,
):
    """Re-run the full pipeline on papers already in the vault (e.g. after a prompt change),
    preserving each note's hand-written '## My Notes' thoughts and its existing verbosity level.

    With no arxiv_ids, reparses the whole vault. Pass one or more IDs to limit the run —
    useful to sanity-check a few papers before committing to a full-vault rerun.

    update_topics defaults to False: running an already-registered paper through
    topic_manager.update() is designed for integrating a *new* paper and would append a
    spurious changelog entry for what is really just a content refresh. Run
    `uv run scripts/topic_manager.py init_all` afterward if you also want topic surveys
    regenerated from the refreshed notes.
    """
    api_key = openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("No API key: pass --openrouter_api_key or set $OPENROUTER_API_KEY")

    index = load_paper_index()
    if not index:
        print("Index is empty — nothing to reparse.")
        return

    targets = list(arxiv_ids) if arxiv_ids else list(index.keys())
    items: list[tuple[str, str, int, str]] = []
    skipped = 0

    for i, aid in enumerate(targets, start=1):
        info = index.get(aid)
        if not info or not info.get("file"):
            print(f"[{i}] {aid} not found in vault — skipping.")
            skipped += 1
            continue
        note_path = os.path.join(RESEARCH_PATH, info["file"])
        meta = note_meta(info["file"])
        verbosity = meta.get("verbosity") or 2
        preserved_thoughts = ""
        if os.path.exists(note_path):
            with open(note_path, encoding="utf-8") as f:
                preserved_thoughts = extract_my_notes(f.read())
        items.append((f"{i}/{len(targets)}", aid, verbosity, preserved_thoughts))

    if not items:
        print("Nothing to reparse.")
        return

    print(f"\nReparsing {len(items)} paper(s), up to {max_workers} in parallel...")
    ok, failed, new_note_paths = _process_batch(
        items, model=model, extraction_model=extraction_model, vision_model=vision_model,
        api_key=api_key, max_workers=max_workers,
    )

    print(f"\nReparse complete — {ok} succeeded, {failed} failed, {skipped} skipped.")

    if update_topics and new_note_paths:
        _batch_update_topics(new_note_paths, model=model, openrouter_api_key=api_key,
                             safe_update=safe_update)
    elif new_note_paths:
        print(
            "Skipped topic updates (default for reparse_all). Run "
            "'uv run scripts/topic_manager.py init_all' to regenerate topic surveys from the "
            "refreshed notes."
        )


if __name__ == "__main__":
    fire.Fire({
        "parse":        parse,
        "parse_many":   parse_many,
        "sync":         sync,
        "reparse_all":  reparse_all,
        "backlink_all": backlink_all,
    })
