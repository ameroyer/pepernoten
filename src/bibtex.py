# BibTeX generation library — imported by pepernoten_cli.py and scripts/bibtex.py
# VAULT_PATH resolved as two levels up (src/ → project root)

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from pathlib import Path

import requests

from vault import INDEX_PATH, RESEARCH_PATH, VAULT_PATH, fm_field, frontmatter

CACHE_PATH = os.path.join(VAULT_PATH, ".bibtex_cache.json")

_EMAIL = "pepernoten@localhost"

_STOP = {"a", "an", "the", "of", "in", "on", "for", "with", "and", "are",
          "is", "via", "from", "to", "at", "by", "as", "its", "our", "we",
          "do", "not", "can", "be"}

_VENUE_BOOKTITLE = {
    "neurips":       "Advances in Neural Information Processing Systems",
    "nips":          "Advances in Neural Information Processing Systems",
    "icml":          "Proceedings of the International Conference on Machine Learning",
    "iclr":          "Proceedings of the International Conference on Learning Representations",
    "cvpr":          "Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition",
    "eccv":          "Proceedings of the European Conference on Computer Vision",
    "iccv":          "Proceedings of the IEEE/CVF International Conference on Computer Vision",
    "aaai":          "Proceedings of the AAAI Conference on Artificial Intelligence",
    "acl":           "Proceedings of the Annual Meeting of the Association for Computational Linguistics",
    "emnlp":         "Proceedings of the Conference on Empirical Methods in Natural Language Processing",
    "naacl":         "Proceedings of the Conference of the North American Chapter of the ACL",
    "acm mm":        "Proceedings of the ACM International Conference on Multimedia",
    "ijcai":         "Proceedings of the International Joint Conference on Artificial Intelligence",
    "kdd":           "Proceedings of the ACM SIGKDD Conference on Knowledge Discovery and Data Mining",
    "wacv":          "Proceedings of the IEEE/CVF Winter Conference on Applications of Computer Vision",
    "bmvc":          "Proceedings of the British Machine Vision Conference",
    "interspeech":   "Proceedings of Interspeech",
    "acl findings":  "Findings of the Association for Computational Linguistics",
    "emnlp findings": "Findings of the Association for Computational Linguistics: EMNLP",
}

def _canonical_booktitle(raw: str) -> str:
    lo = raw.lower()
    for key, full in _VENUE_BOOKTITLE.items():
        if key in lo:
            return full
    return raw


# ──────────────────────────────────────────────────────────────────────────────
# Cache
# ──────────────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    if os.path.exists(CACHE_PATH):
        try:
            return json.load(open(CACHE_PATH))
        except Exception:
            pass
    return {}


def _save_cache(cache: dict):
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# HTTP helper
# ──────────────────────────────────────────────────────────────────────────────

def _get(url: str, params: dict | None = None, headers: dict | None = None,
         timeout: int = 12, retries: int = 2) -> requests.Response | None:
    base_headers = {"User-Agent": f"PeperNoten/2.0 (mailto:{_EMAIL})"}
    if headers:
        base_headers.update(headers)
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=base_headers,
                             timeout=timeout, allow_redirects=True)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 5 * (attempt + 1)))
                print(f"    rate-limited → waiting {wait}s …", flush=True)
                time.sleep(wait)
                continue
            return r
        except requests.RequestException as exc:
            if attempt == retries:
                print(f"    request failed: {exc}", flush=True)
            else:
                time.sleep(1.5 ** attempt)
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Source 1: Papers With Code
# ──────────────────────────────────────────────────────────────────────────────

def _pwc_parse(p: dict) -> dict:
    proceeding = (p.get("proceeding") or "").strip()
    year = None
    ym = re.search(r"\b(20\d{2})\b", proceeding)
    if ym:
        year = int(ym.group(1))
    return {
        "title":      (p.get("title") or "").strip(),
        "authors":    p.get("authors", []),
        "proceeding": proceeding or None,
        "year":       year,
    }


def _papers_with_code(arxiv_id: str, title_hint: str = "") -> dict | None:
    base = "https://paperswithcode.com/api/v1/papers/"

    def _try(params) -> list:
        r = _get(base, params=params)
        if not r or r.status_code != 200 or not r.text.strip():
            return []
        try:
            return r.json().get("results", [])
        except Exception:
            return []

    results = _try({"arxiv_id": arxiv_id})
    if results:
        return _pwc_parse(results[0])

    results = _try({"q": arxiv_id})
    if results:
        return _pwc_parse(results[0])

    if title_hint:
        results = _try({"q": title_hint[:120]})
        title_lo = title_hint.lower()
        for p in results[:5]:
            sim = SequenceMatcher(None, title_lo, (p.get("title") or "").lower()).ratio()
            if sim >= 0.82:
                return _pwc_parse(p)

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Source 2: CrossRef
# ──────────────────────────────────────────────────────────────────────────────

def _crossref(title: str, year: int | None = None) -> dict | None:
    r = _get(
        "https://api.crossref.org/works",
        params={
            "query.title": title,
            "rows":         5,
            "select":       "title,author,container-title,published,DOI,type,event",
            "mailto":       _EMAIL,
        },
    )
    if not r or r.status_code != 200 or not r.text.strip():
        return None
    try:
        items = r.json().get("message", {}).get("items", [])
    except Exception:
        return None

    title_lo = title.lower()
    for item in items:
        item_titles = item.get("title") or []
        item_title  = item_titles[0] if item_titles else ""
        sim = SequenceMatcher(None, title_lo, item_title.lower()).ratio()
        if sim < 0.85:
            continue

        pub_parts = (item.get("published") or {}).get("date-parts", [[]])
        item_year = pub_parts[0][0] if pub_parts and pub_parts[0] else None
        if year and item_year and abs(item_year - year) > 1:
            continue

        event     = item.get("event") or {}
        container = item.get("container-title") or []
        venue = event.get("name") or (container[0] if container else "")
        if not venue or "arxiv" in venue.lower():
            continue

        doi       = (item.get("DOI") or "").strip()
        item_type = item.get("type", "")
        is_journal = item_type == "journal-article"

        authors = []
        for a in (item.get("author") or []):
            given  = (a.get("given") or "").strip()
            family = (a.get("family") or "").strip()
            if family:
                authors.append(f"{given} {family}".strip() if given else family)

        return {
            "title":      item_title,
            "authors":    authors,
            "year":       item_year or year,
            "doi":        doi,
            "venue":      venue,
            "is_journal": is_journal,
        }

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Source 3: Semantic Scholar  (set SEMANTIC_SCHOLAR_API_KEY for higher quota)
# ──────────────────────────────────────────────────────────────────────────────

def _semantic_scholar(arxiv_id: str) -> dict | None:
    api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")
    headers = {"x-api-key": api_key} if api_key else {}
    if not api_key:
        time.sleep(1.5)
    r = _get(
        f"https://api.semanticscholar.org/graph/v1/paper/arXiv:{arxiv_id}",
        params={"fields": "title,authors,year,venue,externalIds,publicationVenue,journal,publicationTypes"},
        headers=headers,
    )
    if not r or r.status_code != 200:
        return None
    try:
        d = r.json()
    except Exception:
        return None
    if not d.get("title"):
        return None
    pub_venue  = d.get("publicationVenue") or {}
    venue_name = pub_venue.get("name") or d.get("venue") or ""
    pub_types  = d.get("publicationTypes") or []
    journal    = d.get("journal") or {}
    doi        = ((d.get("externalIds") or {}).get("DOI") or "").strip()
    authors    = [a["name"] for a in (d.get("authors") or [])]
    return {
        "title":      d.get("title", "").strip(),
        "authors":    authors,
        "year":       d.get("year"),
        "doi":        doi,
        "venue":      venue_name,
        "is_journal": "JournalArticle" in pub_types or bool(journal.get("name")),
        "journal":    journal,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Source 4: arXiv Atom API  (always works — for @misc fallback + primaryClass)
# ──────────────────────────────────────────────────────────────────────────────

def _arxiv_metadata(arxiv_id: str) -> dict:
    r = _get("https://export.arxiv.org/api/query", params={"id_list": arxiv_id})
    if not r or r.status_code != 200:
        return {}
    ns = {
        "atom":  "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    try:
        root  = ET.fromstring(r.text)
        entry = root.find("atom:entry", ns)
        if entry is None:
            return {}
        title  = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
        authors = [
            (a.findtext("atom:name", "", ns) or "").strip()
            for a in entry.findall("atom:author", ns)
        ]
        published = (entry.findtext("atom:published", "", ns) or "")[:4]
        primary   = entry.find("arxiv:primary_category", ns)
        category  = primary.get("term", "cs.LG") if primary is not None else "cs.LG"
        return {
            "title":    title,
            "authors":  authors,
            "year":     int(published) if published.isdigit() else 2024,
            "category": category,
        }
    except ET.ParseError:
        return {}


# ──────────────────────────────────────────────────────────────────────────────
# DBLP  (BibTeX formatter — only called once we have a confirmed venue)
# ──────────────────────────────────────────────────────────────────────────────

def _dblp_bibtex_by_doi(doi: str) -> str | None:
    r = _get("https://dblp.org/search/publ/api",
             params={"q": f"doi:{doi}", "format": "json", "h": 3})
    if not r or r.status_code != 200 or not r.text.strip():
        return None
    try:
        hits = r.json().get("result", {}).get("hits", {}).get("hit", [])
    except Exception:
        return None
    key  = (hits[0].get("info", {}).get("key") or "") if hits else ""
    return _dblp_fetch(key) if key else None


def _dblp_bibtex_by_title(title: str, year: int | None = None,
                           venue_hint: str = "") -> str | None:
    q = title if not year else f"{title} {year}"
    r = _get("https://dblp.org/search/publ/api",
             params={"q": q, "format": "json", "h": 10})
    if not r or r.status_code != 200 or not r.text.strip():
        return None
    try:
        hits = r.json().get("result", {}).get("hits", {}).get("hit", [])
    except Exception:
        return None
    if not hits:
        return None

    title_lo = title.lower()
    best_key, best_score = None, 0.0
    for hit in hits:
        info      = hit.get("info", {})
        key       = info.get("key", "")
        if key.startswith("journals/corr"):
            continue
        hit_title = (info.get("title") or "").lower()
        score     = SequenceMatcher(None, title_lo, hit_title).ratio()
        hit_year  = int(info.get("year") or 0)
        if year and hit_year and abs(hit_year - year) > 1:
            score *= 0.6
        if score > best_score:
            best_score, best_key = score, key

    if best_score < 0.85 or not best_key:
        return None
    return _dblp_fetch(best_key)


def _dblp_fetch(key: str) -> str | None:
    r = _get(f"https://dblp.org/rec/{key}.bib", params={"param": "1"})
    return r.text.strip() if r and r.status_code == 200 else None


# ──────────────────────────────────────────────────────────────────────────────
# BibTeX helpers
# ──────────────────────────────────────────────────────────────────────────────

def _bibtex_key(authors: list[str], year: int, title: str) -> str:
    last   = (authors[0] if authors else "Unknown").split()[-1]
    words  = [w for w in re.sub(r"[^a-zA-Z\s]", "", title).split()
               if w.lower() not in _STOP]
    suffix = words[0].capitalize() if words else "Paper"
    return f"{last}{year}{suffix}"


def _fmt(entry_type: str, key: str, fields: dict) -> str:
    longest = max((len(k) for k in fields), default=1)
    lines   = [f"@{entry_type}{{{key},"]
    items   = [(k, v) for k, v in fields.items() if v]
    for i, (k, v) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        lines.append(f"  {k:{longest}} = {{{v}}}{comma}")
    lines.append("}")
    return "\n".join(lines)


def _build_inproceedings(title, authors, year, venue, doi, arxiv_id) -> str:
    key    = _bibtex_key(authors, year, title)
    austr  = " and ".join(authors) if authors else "Unknown"
    fields = {
        "title":     title,
        "author":    austr,
        "booktitle": _canonical_booktitle(venue),
        "year":      str(year),
        **({"doi": doi} if doi else {}),
        "eprint":    arxiv_id,
        "archivePrefix": "arXiv",
    }
    return _fmt("inproceedings", key, fields)


def _build_article(title, authors, year, journal, doi, arxiv_id,
                   volume="", pages="") -> str:
    key    = _bibtex_key(authors, year, title)
    austr  = " and ".join(authors) if authors else "Unknown"
    fields = {
        "title":   title,
        "author":  austr,
        "journal": journal,
        "year":    str(year),
        **({"volume": volume} if volume else {}),
        **({"pages":  pages}  if pages  else {}),
        **({"doi":    doi}    if doi    else {}),
        "eprint":  arxiv_id,
        "archivePrefix": "arXiv",
    }
    return _fmt("article", key, fields)


def build_arxiv_misc(arxiv_id, title, authors, year, category) -> str:
    key    = _bibtex_key(authors, year, title)
    austr  = " and ".join(authors) if authors else "Unknown"
    fields = {
        "title":         title,
        "author":        austr,
        "year":          str(year),
        "eprint":        arxiv_id,
        "archivePrefix": "arXiv",
        "primaryClass":  category,
        "url":           f"https://arxiv.org/abs/{arxiv_id}",
    }
    return _fmt("misc", key, fields)


# ──────────────────────────────────────────────────────────────────────────────
# Note helpers
# ──────────────────────────────────────────────────────────────────────────────

def _find_note(arxiv_id: str) -> str | None:
    if not os.path.exists(INDEX_PATH):
        return None
    with open(INDEX_PATH) as f:
        idx = json.load(f)
    info = idx.get(arxiv_id)
    return os.path.join(RESEARCH_PATH, info["file"]) if info else None


def _read_note_meta(note_path: str) -> dict:
    fm = frontmatter(Path(note_path).read_text("utf-8", errors="ignore"))
    return {
        "title":   fm_field(fm, "title"),
        "authors": [a.strip() for a in fm_field(fm, "authors").split(",") if a.strip()],
        "year":    fm_field(fm, "date")[:4],
    }


def _patch_note(note_path: str, bibtex: str):
    """Add or replace a ## BibTeX section at the end of the note."""
    text    = Path(note_path).read_text("utf-8", errors="ignore")
    section = f"\n## BibTeX\n\n```bibtex\n{bibtex}\n```\n"
    new     = re.sub(r"\n## BibTeX\n.*?(?=\n## |\Z)", section, text, flags=re.DOTALL)
    if new == text:
        new = text.rstrip() + "\n" + section
    Path(note_path).write_text(new, encoding="utf-8")
    print(f"  Note updated: {note_path}")


def _clipboard(text: str):
    try:
        import subprocess
        subprocess.run(["pbcopy"], input=text.encode(), check=True, capture_output=True)
        print("  Copied to clipboard.")
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def generate(
    arxiv_id:    str,
    update_note: bool = False,
    bib_file:    str  = "",
    clipboard:   bool = True,
    verbose:     bool = True,
) -> str:
    """Generate a BibTeX entry for an arXiv paper.

    Lookup chain: Papers With Code → CrossRef → Semantic Scholar → DBLP (formatter) → @misc fallback.
    Results cached in .bibtex_cache.json.
    """
    def log(msg):
        if verbose:
            print(msg, flush=True)

    # fire parses bare IDs like 2405.12345 as floats — coerce back
    m = re.search(r"(\d{4}\.\d{4,5}(?:v\d+)?)", str(arxiv_id))
    arxiv_id = m.group(1).split("v")[0] if m else str(arxiv_id).strip()

    log(f"\nGenerating BibTeX for arXiv:{arxiv_id}")
    log("─" * 52)

    cache = _load_cache()
    if arxiv_id in cache:
        log("  ✓ Loaded from cache")
        bibtex = cache[arxiv_id]
        print(f"\n{bibtex}\n")
        if clipboard:
            _clipboard(bibtex)
        return bibtex

    note_path = _find_note(arxiv_id)
    note_meta = _read_note_meta(note_path) if note_path else {}

    log("  [0] Fetching arXiv metadata …")
    ax = _arxiv_metadata(arxiv_id)
    ax_category = ax.get("category", "cs.LG")

    title   = note_meta.get("title")   or ax.get("title", "")
    authors = note_meta.get("authors") or ax.get("authors", [])
    year    = int(note_meta.get("year") or ax.get("year") or 2024)

    venue, doi, is_journal = "", "", False
    journal_meta: dict = {}
    bibtex: str | None = None

    log("  [1/3] Papers With Code …")
    pwc = _papers_with_code(arxiv_id, title_hint=title)
    if pwc:
        if pwc.get("title"):   title   = pwc["title"]
        if pwc.get("authors"): authors = pwc["authors"]
        if pwc.get("year"):    year    = pwc["year"]
        if pwc.get("proceeding"):
            venue = pwc["proceeding"]
            log(f"       ✓ proceeding: {venue!r}")
        else:
            log("       found but no proceeding yet")
    else:
        log("       not found")

    if not venue and title:
        log("  [2/3] CrossRef …")
        cr = _crossref(title, year)
        if cr:
            if cr.get("authors"): authors = cr["authors"]
            if cr.get("year"):    year    = cr["year"]
            if not doi and cr.get("doi"): doi = cr["doi"]
            venue      = cr["venue"]
            is_journal = cr.get("is_journal", False)
            log(f"       ✓ venue: {venue!r}  doi: {doi or '—'}")
        else:
            log("       no match")

    if not venue:
        ss_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")
        log(f"  [3/3] Semantic Scholar {'(API key ✓)' if ss_key else '(no key — slow)'}…")
        ss = _semantic_scholar(arxiv_id)
        if ss:
            if not title   and ss.get("title"):   title   = ss["title"]
            if not authors and ss.get("authors"): authors = ss["authors"]
            if not year    and ss.get("year"):    year    = ss["year"]
            if not doi     and ss.get("doi"):     doi     = ss["doi"]
            if ss.get("venue"):
                venue        = ss["venue"]
                is_journal   = ss.get("is_journal", False)
                journal_meta = ss.get("journal", {})
                log(f"       ✓ venue: {venue!r}")
            else:
                log("       no venue — paper still a preprint")
        else:
            log("       not found")

    if venue:
        log("  [+] DBLP lookup for clean BibTeX …")
        if doi:
            bibtex = _dblp_bibtex_by_doi(doi)
            if bibtex:
                log("       ✓ DBLP match via DOI")
        if not bibtex:
            bibtex = _dblp_bibtex_by_title(title, year, venue_hint=venue)
            if bibtex:
                log("       ✓ DBLP match via title")
            else:
                log("       DBLP has no match yet — building from metadata")

    if not bibtex and venue:
        if is_journal:
            bibtex = _build_article(
                title, authors, year,
                journal=journal_meta.get("name") or venue,
                doi=doi, arxiv_id=arxiv_id,
                volume=journal_meta.get("volume", ""),
                pages=journal_meta.get("pages", ""),
            )
        else:
            bibtex = _build_inproceedings(title, authors, year, venue, doi, arxiv_id)
        log(f"       built @{'article' if is_journal else 'inproceedings'}")

    if not bibtex:
        log("  [+] @misc fallback (preprint, not yet published)")
        if not title:   title   = ax.get("title", arxiv_id)
        if not authors: authors = ax.get("authors", [])
        bibtex = build_arxiv_misc(arxiv_id, title, authors, year, ax_category)
        log(f"       @misc ({ax_category})")

    log("─" * 52)
    print(f"\n{bibtex}\n")

    cache[arxiv_id] = bibtex
    _save_cache(cache)

    if clipboard:
        _clipboard(bibtex)

    if update_note and note_path:
        _patch_note(note_path, bibtex)
    elif update_note:
        print(f"  Note not found for arXiv:{arxiv_id} — skipping note patch.")

    if bib_file:
        with open(bib_file, "a", encoding="utf-8") as f:
            f.write(f"\n{bibtex}\n")
        print(f"  Appended to {bib_file}")

    return bibtex


def batch(
    *arxiv_ids:   str,
    bib_file:     str  = "refs.bib",
    update_notes: bool = False,
    verbose:      bool = True,
):
    """Generate BibTeX for multiple arXiv papers and write them to a .bib file."""
    results = {}
    for aid in arxiv_ids:
        try:
            results[aid] = generate(
                aid,
                update_note=update_notes,
                bib_file=bib_file,
                clipboard=False,
                verbose=verbose,
            )
        except Exception as e:
            print(f"  ERROR {aid}: {e}")
            results[aid] = None
    ok = sum(1 for v in results.values() if v)
    print(f"\nDone — {ok}/{len(arxiv_ids)} entries written to {bib_file}")
    return results


def clear_cache(arxiv_id: str = ""):
    """Clear the BibTeX cache (specific ID, or all if no ID given)."""
    if not os.path.exists(CACHE_PATH):
        print("Cache is empty.")
        return
    cache = _load_cache()
    if arxiv_id:
        m = re.search(r"(\d{4}\.\d{4,5})", str(arxiv_id))
        key = m.group(1) if m else str(arxiv_id)
        removed = cache.pop(key, None)
        print(f"  {'Removed' if removed else 'Not found'}: {key}")
    else:
        n = len(cache)
        cache.clear()
        print(f"  Cleared {n} cached entries.")
    _save_cache(cache)
