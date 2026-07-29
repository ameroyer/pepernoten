# arXiv and Scholar Inbox utilities — rate limiting, HTML fetching, ID extraction

import json
import re
import subprocess
import threading
import time
from difflib import SequenceMatcher

import arxiv
import requests
from bs4 import BeautifulSoup

# ──────────────────────────────────────────────────────────────────────────────
# Rate limiting
# ──────────────────────────────────────────────────────────────────────────────

class _RateLimiter:
    def __init__(self, min_interval: float):
        self._min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            gap = self._min_interval - (time.monotonic() - self._last)
            if gap > 0:
                time.sleep(gap)
            self._last = time.monotonic()


# arXiv asks for ≥3 s between automated requests. One global limiter covers
# metadata API, HTML pages, PDF downloads, and arXiv title lookups.
# Static figure image downloads use a lighter limit.
arxiv_api   = _RateLimiter(4.0)
arxiv_asset = _RateLimiter(0.3)


# ──────────────────────────────────────────────────────────────────────────────
# arXiv ID extraction
# ──────────────────────────────────────────────────────────────────────────────

def extract_arxiv_id(url: str) -> str:
    # fire parses bare IDs like 2405.12345 as floats — coerce back
    match = re.search(r"(\d{4}\.\d{4,5})(v\d+)?", str(url))
    if not match:
        raise ValueError(f"Could not extract arxiv ID from: {url}")
    return match.group(1)


def fetch_titles(arxiv_ids: list) -> dict:
    """Batch title lookup via one direct Atom API call — no rate-limit wait needed.

    Returns {arxiv_id: title}; missing IDs fall back to the bare ID string.
    """
    fallback = {aid: aid for aid in arxiv_ids}
    if not arxiv_ids:
        return fallback
    try:
        resp = requests.get(
            "https://export.arxiv.org/api/query",
            params={"id_list": ",".join(arxiv_ids), "max_results": len(arxiv_ids)},
            timeout=10,
            headers={"User-Agent": "PeperNoten/2.0"},
        )
        if resp.status_code != 200:
            return fallback
        out = dict(fallback)
        for m in re.finditer(
            r"<entry>.*?<id>[^<]*/abs/(\d{4}\.\d{4,5})[^<]*</id>.*?<title>(.*?)</title>",
            resp.text, re.DOTALL,
        ):
            aid = m.group(1)
            if aid in out:
                out[aid] = re.sub(r"\s+", " ", m.group(2)).strip()
        return out
    except Exception:
        return fallback


# ──────────────────────────────────────────────────────────────────────────────
# HTML fetching
# ──────────────────────────────────────────────────────────────────────────────

def fetch_arxiv_html(arxiv_id: str) -> tuple[str, str] | tuple[None, None]:
    """Fetch the arXiv HTML version, falling back to ar5iv."""
    for url in [
        f"https://arxiv.org/html/{arxiv_id}",
        f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}",
    ]:
        try:
            arxiv_api.wait()
            resp = requests.get(url, timeout=20, headers={"User-Agent": "PeperNoten/2.0"})
            if resp.status_code == 200:
                base = resp.url if resp.url.endswith("/") else resp.url + "/"
                return resp.text, base
        except Exception:
            continue
    return None, None


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    for math_el in soup.find_all("math"):
        alttext = math_el.get("alttext", "")
        if alttext:
            math_el.replace_with(f"${alttext}$")
    main = (
        soup.find("article")
        or soup.find("div", class_="ltx_page_main")
        or soup.body
        or soup
    )
    return main.get_text(separator="\n", strip=True)


# ──────────────────────────────────────────────────────────────────────────────
# arXiv title search
# ──────────────────────────────────────────────────────────────────────────────

def _arxiv_title_score(candidate: str, expected: str) -> float:
    c, e = candidate.lower().strip(), expected.lower().strip()
    seq = SequenceMatcher(None, c, e).ratio()
    ct, et = set(c.split()), set(e.split())
    union = ct | et
    jaccard = len(ct & et) / len(union) if union else 0.0
    return max(seq, jaccard)


def lookup_arxiv_id(title: str) -> str | None:
    if not title.strip():
        return None
    try:
        arxiv_api.wait()
        results = list(arxiv.Client().results(
            arxiv.Search(query=f'ti:"{title}"', max_results=8)
        ))
        for r in results:
            if _arxiv_title_score(r.title, title) >= 0.80:
                return r.entry_id.rsplit("/", 1)[-1].split("v")[0]
    except Exception:
        pass
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Scholar Inbox
# ──────────────────────────────────────────────────────────────────────────────

def fetch_digest(debug: bool = False) -> list[dict]:
    """Run scholarinboxcli digest and return a list of paper dicts."""
    try:
        result = subprocess.run(
            ["scholarinboxcli", "digest"],
            capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError:
        raise SystemExit(
            "scholarinboxcli not found.\n"
            "Install:      pip install scholarinboxcli\n"
            "Authenticate: scholarinboxcli auth"
        )

    if result.returncode != 0:
        err = (result.stderr or result.stdout).strip()
        raise SystemExit(f"scholarinboxcli failed (exit {result.returncode}):\n{err}")

    raw = result.stdout.strip()
    if debug:
        print(f"[debug] raw digest ({len(raw)} chars):\n{raw[:600]}\n")

    if not raw:
        raise SystemExit("scholarinboxcli digest returned empty output. Are you authenticated?")

    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("digest_df", "papers", "items", "results", "digest", "data"):
                if isinstance(data.get(key), list):
                    return data[key]
    except json.JSONDecodeError:
        pass

    raise SystemExit(
        f"Could not parse scholarinboxcli output as JSON.\n"
        f"First 300 chars received:\n{raw[:300]}\n"
        "Run with debug=True to see full output."
    )


def arxiv_id_from_paper(paper: dict) -> str | None:
    """Extract an arXiv ID from a paper dict returned by scholarinboxcli."""
    for field in ["arxiv_id", "arxiv", "id"]:
        val = str(paper.get(field, ""))
        m = re.search(r"(\d{4}\.\d{4,5})", val)
        if m:
            return m.group(1)
    for field in ["url", "link", "pdf_url", "html_url", "paper_url", "href"]:
        val = str(paper.get(field, ""))
        m = re.search(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})", val)
        if m:
            return m.group(1)
    for val in paper.values():
        if isinstance(val, str):
            m = re.search(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})", val)
            if m:
                return m.group(1)
    return None


def paper_score(paper: dict) -> float:
    """Extract a numeric relevance score from a paper dict."""
    for field in ["score", "relevance_score", "relevance", "similarity", "rank"]:
        val = paper.get(field)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
    return 0.0
