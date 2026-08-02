# Usage: uv run pepernoten_cli.py [--safe]

import os
import re
import shutil
import sys
import time as _time

VAULT    = os.path.dirname(os.path.abspath(__file__))
_SRC     = os.path.join(VAULT, "src")
_SCRIPTS = os.path.join(VAULT, "scripts")
for _p in (VAULT, _SCRIPTS, _SRC):   # insert(0) reverses order → src wins over scripts
    if _p not in sys.path:
        sys.path.insert(0, _p)

from vault import (
    RESEARCH_PATH, TOPICS_PATH,
    load_paper_index, load_topic_index, note_meta, short_authors, delete_paper,
)

from rich.console import Console, Group
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.rule import Rule
from rich.align import Align
from rich import box
import questionary
from questionary import Style as QStyle

console = Console()

# Green = primary, orange = secondary
Q_STYLE = QStyle([
    ("qmark",       "fg:ansigreen bold"),
    ("question",    "bold"),
    ("answer",      "fg:ansigreen bold"),
    ("pointer",     "fg:ansigreen bold"),
    ("highlighted", "fg:ansigreen bold"),
    ("selected",    "fg:ansigreen"),
    ("separator",   "fg:ansiyellow"),
    ("instruction", "fg:ansigray italic"),
    ("disabled",    "fg:ansigray italic"),
])

G  = "bold green"       # primary accent
O  = "dark_orange"      # secondary accent
DIM = "dim"

COMMANDS = [
    ("parse",            "paste arXiv URLs / IDs to parse and add to vault"),
    ("inbox",            "fetch Scholar Inbox digest and pick papers to add"),
    ("topics",           "detailed list of topics and their papers"),
    ("list",             "browse all papers; delete with [d]"),
    ("update_knowledge", "update topic files — add or remove papers from topics"),
    ("bibtex",           "generate BibTeX for a paper (checks if published)"),
    ("quit",             "exit"),
]

# Topic chip colours — deterministic per slug, cycles through this palette
_CHIP_COLORS = ["#4ecb8d", "#f0a429", "#6eb6ff", "#ff7eb3", "#b0b7ff", "#ffd3a5"]

def _topic_color(slug: str) -> str:
    return _CHIP_COLORS[abs(hash(slug)) % len(_CHIP_COLORS)]


_KEY_COLOR  = "#c47a1a"   # darker amber — for keyboard shortcut labels in hint lines

def _hint_line(pairs: list) -> list:
    """Return formatted-text tuples for a hint bar: keys in amber, descriptions in dim gray.

    pairs: list of (key_str, desc_str) tuples, e.g. [("←/→", "verbosity"), ("enter", "confirm")]
    """
    out = []
    for i, (key, desc) in enumerate(pairs):
        out.append(("class:key", ("  " if i == 0 else "   ") + key))
        out.append(("class:hint", f" {desc}"))
    out.append(("class:hint", "\n\n"))
    return out




# ──────────────────────────────────────────────────────────────
# HOME SCREEN
# ──────────────────────────────────────────────────────────────

# Pepernoot mascot — small round Dutch spiced cookie ✨
_COOKIE = (
    "  ╭──────────╮  \n"
    " ╱  ✦  ·  ✦  ╲ \n"
    "│  ·  (◡‿◡)  ·  │\n"
    " ╲  ✦  ·  ✦  ╱ \n"
    "  ╰──────────╯  "
)

# Compact 3-line rounded logo — 40 chars wide, each letter 3 wide
# P=╭─╮/├─╯/│   E=╭──/├─ /╰── R=╭─╮/├┬╯/│╰─  N=╭ ╭/│╲│/╯ ╰  O=╭─╮/│ │/╰─╯  T=─┬─/ │ / ╵
_LOGO = (
    "╭─╮ ╭── ╭─╮ ╭── ╭─╮  ╭ ╭ ╭─╮ ─┬─ ╭── ╭ ╭\n"
    "├─╯ ├─  ├─╯ ├─  ├┬╯  │╲│ │ │  │  ├─  │╲│\n"
    "│   ╰── │   ╰── │╰─  ╯ ╰ ╰─╯  ╵  ╰── ╯ ╰"
)


def show_home():
    papers = load_paper_index()
    topics = load_topic_index()

    sw = max(console.width - 4, 20)
    sparkle = ("✦  ·  " * 30)[:sw]

    cookie_and_stats = Text.assemble(
        (_COOKIE + "\n", O),
        ("\n", ""),
        ("  ✨  arXiv → Obsidian  ✨  \n", DIM),
        ("\n", ""),
        (f"  ◆ {len(papers)} papers", f"bold {O}"),
        ("   ", ""),
        (f"◆ {len(topics)} topics  ", f"bold {G}"),
    )

    console.print()
    console.print(Panel(
        Group(
            Text(""),
            Align(Text(sparkle, style=O),        align="center"),
            Text(""),
            Align(Text(_LOGO, style=G),           align="center"),
            Text(""),
            Align(cookie_and_stats,               align="center"),
            Text(""),
            Align(Text(sparkle, style=O),        align="center"),
            Text(""),
        ),
        box=box.DOUBLE_EDGE,
        border_style="green",
        expand=True,
        padding=(0, 1),
    ))

    # ── Topic table ─────────────────────────────────────────────
    if topics:
        console.print()
        t = Table(box=box.SIMPLE, show_header=True, header_style=DIM,
                  padding=(0, 2), show_edge=False)
        t.add_column("Topic",        style="bold")
        t.add_column("Papers",       justify="right", style=O)
        t.add_column("Last updated", style=DIM)
        t.add_column("",             style=DIM)

        for slug, td in sorted(topics.items(), key=lambda x: -len(x[1].get("papers", []))):
            n       = len(td.get("papers", []))
            updated = td.get("last_updated") or "never"
            exists  = "✓" if os.path.exists(
                os.path.join(TOPICS_PATH, f"{slug}.md")) else "✗"
            t.add_row(td["name"], str(n), updated, exists)

        console.print(t)
    else:
        console.print(f"\n  [{DIM}]No topics yet — use parse or inbox to add papers.[/{DIM}]")

    # ── Command hint bar ────────────────────────────────────────
    hints = "  ✦  ".join(cmd for cmd, _ in COMMANDS)
    console.print()
    console.print(Align(Text(hints, style=DIM), align="center"))
    console.print()


# ──────────────────────────────────────────────────────────────
# PAPER SELECTOR (custom prompt_toolkit widget)
# ──────────────────────────────────────────────────────────────

def _paper_selector(items: list, toggleable: bool = True) -> list | None:
    """
    Interactive paper selector with inline verbosity control.

    items   — list of dicts: aid, title, score (str), disabled (bool)
    toggleable — True for inbox (space=select), False for parse (all pre-selected)

    Keys: ↑/↓ navigate  space toggle  ←/→ verbosity  enter confirm  q/ctrl-c cancel
    Returns [(aid, verbosity), …] or None.
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style as PTStyle

    BARS = ["■□□□", "■■□□", "■■■□", "■■■■"]
    LBLS = ["expert", "researcher", "newcomer", "beginner"]
    MIN_TITLE_W = 16   # never shrink the title column past this, even on tiny terminals

    rows = [
        {
            "aid":      it["aid"],
            "title":    it["title"],
            "score":    it.get("score", ""),
            "disabled": it.get("disabled", False),
            "selected": (not toggleable) and not it.get("disabled", False),
            "verbosity": 2,
        }
        for it in items
    ]
    navigable = [i for i, r in enumerate(rows) if not r["disabled"]]
    cur = [0]
    start_time = _time.time()
    needs_marquee = any(len(r["title"]) > 30 for r in rows)

    def crow():
        return rows[navigable[cur[0]]] if navigable else None

    def scroll_text(text: str, width: int, elapsed: float, speed: float = 10.0) -> str:
        """Slide `text` continuously through a `width`-wide window, pausing briefly at the loop start."""
        pause = 1.2
        sep = "   ·   "
        loop = text + sep
        cycle = len(loop) / speed + pause
        t = elapsed % cycle
        offset = 0 if t < pause else int((t - pause) * speed) % len(loop)
        return (loop + loop)[offset:offset + width]

    def render():
        lines = []
        if toggleable:
            lines.extend(_hint_line([("space", "select"), ("←/→", "verbosity"), ("↑/↓", "navigate"), ("enter", "confirm")]))
        else:
            lines.extend(_hint_line([("←/→", "verbosity"), ("↑/↓", "navigate"), ("enter", "parse")]))

        term_width = shutil.get_terminal_size(fallback=(100, 24)).columns
        elapsed = _time.time() - start_time

        for idx, row in enumerate(rows):
            active = bool(navigable) and navigable[cur[0]] == idx
            ptr = "❯" if active else " "

            if row["disabled"]:
                check, sty = "·", "class:dim"
            elif row["selected"] and active:
                check, sty = "✓", "class:sel_active"
            elif row["selected"]:
                check, sty = "✓", "class:sel"
            elif active:
                check, sty = " ", "class:active"
            else:
                check, sty = " ", "class:normal"

            v   = row["verbosity"]
            bar = BARS[v - 1]
            lbl = LBLS[v - 1]
            sc  = f"  [{row['score']}]" if row["score"] else ""

            # Title column shrinks to fit the terminal; verbosity bar/label always stay put.
            if toggleable:
                prefix, suffix = f"  {ptr} [{check}]  ", f"{sc}  {bar} {lbl}"
            else:
                prefix, suffix = f"  {ptr}  ", f"  {bar} {lbl}"
            avail = max(MIN_TITLE_W, term_width - len(prefix) - len(suffix) - 1)

            title = row["title"]
            if len(title) <= avail:
                ttl = f"{title:<{avail}}"
            elif active:
                ttl = scroll_text(title, avail, elapsed)
            else:
                ttl = title[:avail - 1] + "…"

            lines.append((sty, f"{prefix}{ttl}{suffix}\n"))

        return lines

    kb = KeyBindings()

    @kb.add("up")
    def _up(event): cur[0] = max(0, cur[0] - 1)

    @kb.add("down")
    def _dn(event): cur[0] = min(len(navigable) - 1, cur[0] + 1)

    @kb.add("space")
    def _tog(event):
        if toggleable and (r := crow()):
            r["selected"] = not r["selected"]

    @kb.add("right")
    def _vup(event):
        if r := crow(): r["verbosity"] = min(4, r["verbosity"] + 1)

    @kb.add("left")
    def _vdn(event):
        if r := crow(): r["verbosity"] = max(1, r["verbosity"] - 1)

    @kb.add("enter")
    def _confirm(event):
        result = [(r["aid"], r["verbosity"]) for r in rows if r["selected"]]
        event.app.exit(result=result or None)

    @kb.add("q")
    @kb.add("c-c")
    def _cancel(event): event.app.exit(result=None)

    pt_style = PTStyle.from_dict({
        "hint":       "#5c6480 italic",
        "key":        f"{_KEY_COLOR} bold",
        "active":     "bold #f0a429",
        "sel_active": "bold #4ecb8d",
        "sel":        "#4ecb8d",
        "normal":     "#7a8570",
        "dim":        "#404555",
    })

    return Application(
        layout=Layout(Window(
            FormattedTextControl(render, focusable=True),
            dont_extend_height=True,
        )),
        key_bindings=kb,
        style=pt_style,
        full_screen=False,
        mouse_support=False,
        refresh_interval=0.15 if needs_marquee else None,
    ).run()


# ──────────────────────────────────────────────────────────────
# TOPICS COMMAND
# ──────────────────────────────────────────────────────────────

def cmd_topics():
    papers = load_paper_index()
    topics = load_topic_index()

    if not topics:
        console.print(f"[{DIM}]No topics registered yet.[/{DIM}]")
        return

    meta_cache = {aid: note_meta(info["file"]) for aid, info in papers.items()}

    console.print()
    for slug, td in sorted(topics.items(), key=lambda x: -len(x[1].get("papers", []))):
        ids = td.get("papers", [])
        console.print(Rule(
            f"[{G}]{td['name']}[/{G}]  [{DIM}]({len(ids)} papers)[/{DIM}]",
            style="green",
        ))

        if not ids:
            console.print(f"  [{DIM}]No papers yet.[/{DIM}]\n")
            continue

        t = Table(box=box.SIMPLE, show_header=False, padding=(0, 2), show_edge=False)
        t.add_column("Title",   style="bold",   no_wrap=False, max_width=55)
        t.add_column("Date",    style=DIM,      no_wrap=True)
        t.add_column("Authors", style=DIM,      no_wrap=False, max_width=38)

        for aid in ids:
            m = meta_cache.get(aid, {})
            t.add_row(
                m.get("title") or aid,
                (m.get("date") or "")[:10],
                short_authors(m.get("authors") or ""),
            )

        console.print(t)
        console.print()


# ──────────────────────────────────────────────────────────────
# SCHOLAR INBOX COMMAND
# ──────────────────────────────────────────────────────────────

def cmd_inbox(api_key: str, model: str, safe_update: bool, extraction_model: str):
    import parse as ap
    from arxiv_utils import fetch_digest, arxiv_id_from_paper, paper_score

    console.print(f"\n[{G}]Fetching Scholar Inbox digest…[/{G}]")
    try:
        digest = fetch_digest()
    except SystemExit as e:
        console.print(f"[red]{e}[/red]")
        return
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        return

    if not digest:
        console.print(f"[{DIM}]Digest is empty.[/{DIM}]")
        return

    console.print(f"  [{DIM}]{len(digest)} paper(s) in digest.[/{DIM}]\n")

    existing = set(load_paper_index().keys())
    items = []
    for p in digest:
        aid = arxiv_id_from_paper(p)
        if not aid:
            continue
        score  = paper_score(p)
        items.append({
            "aid":      aid,
            "title":    p.get("title") or aid,
            "score":    f"{score:.2f}" if score else "",
            "disabled": aid in existing,
        })

    if not items:
        console.print(f"[{DIM}]No papers with arXiv IDs in digest.[/{DIM}]")
        return
    if all(it["disabled"] for it in items):
        console.print(f"[{DIM}]All papers are already in the vault.[/{DIM}]")
        return

    selection = _paper_selector(items, toggleable=True)

    if not selection:
        console.print(f"[{DIM}]Nothing selected.[/{DIM}]")
        return

    console.print()
    console.print(f"[{DIM}]Processing {len(selection)} paper(s), up to {ap.DEFAULT_MAX_WORKERS} in parallel…[/{DIM}]\n")
    items = [(f"{i}/{len(selection)}", aid, verbosity, "") for i, (aid, verbosity) in enumerate(selection, 1)]
    ok, failed, new_paths = ap._process_batch(
        items, model=model, extraction_model=extraction_model, vision_model=ap.VISION_MODEL, api_key=api_key,
        max_workers=ap.DEFAULT_MAX_WORKERS,
    )

    if new_paths:
        console.print()
        ap._batch_update_topics(new_paths, model=model, openrouter_api_key=api_key,
                                safe_update=safe_update)

    failed_str = f", {failed} failed" if failed else ""
    console.print(f"\n[{G}]Done — {len(new_paths)} paper(s) added{failed_str}.[/{G}]")


# ──────────────────────────────────────────────────────────────
# PARSE COMMAND
# ──────────────────────────────────────────────────────────────

def cmd_parse(api_key: str, model: str, safe_update: bool, extraction_model: str):
    import parse as ap
    from arxiv_utils import extract_arxiv_id, fetch_titles

    raw = questionary.text(
        "Paste arXiv URLs or IDs (space-separated):",
        style=Q_STYLE,
    ).ask()

    if not raw or not raw.strip():
        console.print(f"[{DIM}]Nothing entered.[/{DIM}]")
        return

    existing   = set(load_paper_index().keys())
    to_process = []
    for tok in re.split(r"[\s,]+", raw.strip()):
        if not tok:
            continue
        try:
            aid = extract_arxiv_id(tok)
        except ValueError:
            console.print(f"[red]  Cannot parse: {tok}[/red]")
            continue
        if aid in existing:
            console.print(f"  [{DIM}]{aid} already in vault — skipping.[/{DIM}]")
        else:
            to_process.append(aid)

    if not to_process:
        console.print(f"[{DIM}]Nothing new to process.[/{DIM}]")
        return

    # Batch title fetch — one Atom API request for all IDs
    console.print(f"\n  [{DIM}]Fetching titles…[/{DIM}]")
    titles = fetch_titles(to_process)
    items  = [{"aid": aid, "title": titles[aid], "score": ""} for aid in to_process]
    for it in items:
        console.print(f"  [{DIM}]{it['aid']}[/{DIM}]  {it['title'][:68]}")

    console.print()
    selection = _paper_selector(items, toggleable=False)
    if not selection:
        console.print(f"[{DIM}]Cancelled.[/{DIM}]")
        return

    console.print()
    console.print(f"[{DIM}]Processing {len(selection)} paper(s), up to {ap.DEFAULT_MAX_WORKERS} in parallel…[/{DIM}]\n")
    items = [(f"{i}/{len(selection)}", aid, verbosity, "") for i, (aid, verbosity) in enumerate(selection, 1)]
    ok, failed, new_paths = ap._process_batch(
        items, model=model, extraction_model=extraction_model, vision_model=ap.VISION_MODEL, api_key=api_key,
        max_workers=ap.DEFAULT_MAX_WORKERS,
    )

    if new_paths:
        console.print()
        ap._batch_update_topics(new_paths, model=model, openrouter_api_key=api_key,
                                safe_update=safe_update)

    failed_str = f", {failed} failed" if failed else ""
    console.print(f"\n[{G}]Done — {len(new_paths)} paper(s) added{failed_str}.[/{G}]")


def cmd_list():
    """Browse all papers in the vault; press [d] to delete the highlighted one."""
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style as PTStyle

    BARS = ["■□□□", "■■□□", "■■■□", "■■■■"]

    papers = load_paper_index()
    if not papers:
        console.print(f"[{DIM}]No papers in vault.[/{DIM}]")
        return

    # Build rows sorted by file mtime (most recent first)
    rows = []
    for aid, info in papers.items():
        meta = note_meta(info.get("file", ""))
        full = meta.get("full_path", "")
        try:
            mtime = os.stat(full).st_mtime if full and os.path.exists(full) else 0
        except OSError:
            mtime = 0
        rows.append({
            "aid":       aid,
            "title":     meta.get("title") or aid,
            "verbosity": meta.get("verbosity"),
            "mtime":     mtime,
            "file":      info.get("file", ""),
        })
    rows.sort(key=lambda r: -r["mtime"])

    cur = [0]

    def render():
        lines = _hint_line([("↑/↓", "navigate"), ("d", "delete"), ("enter/q", f"back   ({len(rows)} papers)")])
        for i, row in enumerate(rows):
            active = (i == cur[0])
            ptr  = "❯" if active else " "
            sty  = "class:active" if active else "class:normal"
            v    = row["verbosity"]
            vstr = f"{BARS[v-1]} {v}" if v else "?"
            date_str = _time.strftime("%Y-%m-%d", _time.localtime(row["mtime"])) if row["mtime"] else "?"
            title = row["title"][:62]
            lines.append((sty, f"  {ptr}  {title:<62}  {date_str}   {vstr}\n"))
        return lines

    kb = KeyBindings()

    @kb.add("up")
    def _up(event):
        if cur[0] > 0: cur[0] -= 1

    @kb.add("down")
    def _dn(event):
        if cur[0] < len(rows) - 1: cur[0] += 1

    @kb.add("d")
    def _delete(event):
        event.app.exit(result=("delete", rows[cur[0]]))

    @kb.add("enter")
    @kb.add("q")
    @kb.add("c-c")
    def _quit(event):
        event.app.exit(result=None)

    pt_style = PTStyle.from_dict({
        "hint":   "#5c6480 italic",
        "key":    f"{_KEY_COLOR} bold",
        "active": "bold #f0a429",
        "normal": "#7a8570",
    })

    while True:
        result = Application(
            layout=Layout(Window(
                FormattedTextControl(render, focusable=True),
                dont_extend_height=True,
            )),
            key_bindings=kb,
            style=pt_style,
            full_screen=False,
            mouse_support=False,
        ).run()

        if result is None:
            break

        if result[0] == "delete":
            row = result[1]
            console.print()
            confirmed = questionary.confirm(
                f"Delete \"{row['title'][:70]}\" ({row['aid']}) from vault?",
                default=False,
                style=Q_STYLE,
            ).ask()
            if confirmed:
                delete_paper(row["aid"])
                rows.remove(row)
                cur[0] = min(cur[0], max(0, len(rows) - 1))
                console.print(f"  [{G}]Deleted.[/{G}]\n")
            else:
                console.print(f"  [{DIM}]Cancelled.[/{DIM}]\n")
            if not rows:
                break


# ──────────────────────────────────────────────────────────────
# UPDATE KNOWLEDGE COMMAND
# ──────────────────────────────────────────────────────────────

def cmd_update_knowledge(api_key: str, model: str, safe_update: bool, extraction_model: str):
    """Mark papers to add (+) or remove (-) from topics, then run the LLM update."""
    import parse as ap
    import topic_manager as tm
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style as PTStyle

    papers    = load_paper_index()
    topic_idx = load_topic_index()

    if not papers:
        console.print(f"[{DIM}]No papers in vault.[/{DIM}]")
        return

    # Map aid → list of topic slugs it belongs to
    aid_topics: dict = {aid: [] for aid in papers}
    for slug, td in topic_idx.items():
        for aid in td.get("papers", []):
            if aid in aid_topics:
                aid_topics[aid].append(slug)

    # Build rows, sorted: papers with topics first (by topic count desc), then ungrouped
    rows = []
    for aid, info in papers.items():
        rows.append({
            "aid":    aid,
            "title":  info.get("title") or aid,
            "file":   info.get("file", ""),
            "topics": aid_topics.get(aid, []),
            "state":  "neutral",   # "neutral" | "add" | "remove"
        })
    rows.sort(key=lambda r: (-len(r["topics"]), r["title"].lower()))

    cur = [0]

    def render():
        lines = _hint_line([("→", "add"), ("←", "remove"), ("space", "clear"), ("a", "add all"), ("enter", "run"), ("q", "cancel")])

        for i, row in enumerate(rows):
            active = (i == cur[0])
            state  = row["state"]

            if state == "add":
                bracket = "[+]"
                if active:
                    sty = "class:add_active"
                else:
                    sty = "class:add"
            elif state == "remove":
                bracket = "[-]"
                if active:
                    sty = "class:rem_active"
                else:
                    sty = "class:rem"
            else:
                bracket = "[ ]"
                sty = "class:active" if active else "class:normal"

            ptr   = "❯" if active else " "
            title = row["title"][:55]
            lines.append((sty, f"  {ptr} {bracket}  {title:<55}  "))

            # Topic chips
            if row["topics"]:
                for slug in row["topics"]:
                    color = _topic_color(slug)
                    lines.append((f"fg:{color}", f"▸{slug[:14]}  "))
            else:
                lines.append(("class:dim", "—"))
            lines.append(("", "\n"))

        return lines

    kb = KeyBindings()

    @kb.add("up")
    def _up(event):
        if cur[0] > 0: cur[0] -= 1

    @kb.add("down")
    def _dn(event):
        if cur[0] < len(rows) - 1: cur[0] += 1

    @kb.add("right")
    def _add(event): rows[cur[0]]["state"] = "add"

    @kb.add("left")
    def _rem(event): rows[cur[0]]["state"] = "remove"

    @kb.add("space")
    def _clear(event): rows[cur[0]]["state"] = "neutral"

    @kb.add("a")
    def _add_all(event):
        for r in rows: r["state"] = "add"

    @kb.add("enter")
    def _confirm(event):
        event.app.exit(result=rows)

    @kb.add("q")
    @kb.add("c-c")
    def _cancel(event):
        event.app.exit(result=None)

    pt_style = PTStyle.from_dict({
        "hint":       "#5c6480 italic",
        "key":        f"{_KEY_COLOR} bold",
        "active":     "bold #f0a429",
        "add":        "#4ecb8d",
        "add_active": "bold #4ecb8d",
        "rem":        "#ff7eb3",
        "rem_active": "bold #ff7eb3",
        "normal":     "#7a8570",
        "dim":        "#404555",
    })

    result = Application(
        layout=Layout(Window(
            FormattedTextControl(render, focusable=True),
            dont_extend_height=True,
        )),
        key_bindings=kb,
        style=pt_style,
        full_screen=False,
        mouse_support=False,
    ).run()

    if not result:
        console.print(f"[{DIM}]Cancelled.[/{DIM}]")
        return

    to_add    = [r for r in result if r["state"] == "add"]
    to_remove = [r for r in result if r["state"] == "remove"]

    if not to_add and not to_remove:
        console.print(f"[{DIM}]Nothing marked.[/{DIM}]")
        return

    console.print()

    # ── Add papers to topics ──────────────────────────────────
    if to_add:
        console.print(Rule(f"[{G}]Adding {len(to_add)} paper(s) to topics[/{G}]", style="green"))
        note_paths = [
            os.path.join(RESEARCH_PATH, r["file"])
            for r in to_add if r["file"]
        ]
        ap._batch_update_topics(note_paths, model=model,
                                openrouter_api_key=api_key, safe_update=safe_update)

    # ── Remove papers from topics ─────────────────────────────
    if to_remove:
        console.print()
        console.print(Rule(f"[bold #ff7eb3]Removing {len(to_remove)} paper(s) from topics[/bold #ff7eb3]",
                           style="#ff7eb3"))
        for r in to_remove:
            if r["topics"]:
                tm.remove_from_topics(r["aid"], model=model, openrouter_api_key=api_key)
            else:
                console.print(f"  [{DIM}]{r['aid']} has no topics — nothing to remove.[/{DIM}]")

    console.print(f"\n[{G}]Knowledge base updated.[/{G}]")

    # ── Topic merge pass (only when vault is large enough) ────
    if len(papers) < 10:
        return

    console.print()
    console.print(Rule(f"[{DIM}]Checking for topic merge candidates…[/{DIM}]", style="dim"))
    try:
        candidates = tm.find_merge_candidates(extraction_model=extraction_model, openrouter_api_key=api_key)
    except Exception as e:
        console.print(f"[{DIM}]  Merge check failed: {e}[/{DIM}]")
        return

    if not candidates:
        console.print(f"  [{DIM}]No merge candidates found.[/{DIM}]")
        return

    console.print(f"\n  Found [bold]{len(candidates)}[/bold] merge proposal(s):\n")
    for i, m in enumerate(candidates, 1):
        parts = "  +  ".join(
            f"[bold]{name}[/bold] ({n} paper{'s' if n != 1 else ''})"
            for _, n, name in m["merge_slugs_info"]
        )
        console.print(f"  [{O}]{i}.[/{O}]  {parts}")
        console.print(f"      [{G}]→[/{G}]  {m['new_name']}")
        console.print(f"      [{DIM}]{m['reason']}[/{DIM}]\n")

    confirmed = questionary.confirm(
        "Execute these merges?",
        default=False,
        style=Q_STYLE,
    ).ask()

    if not confirmed:
        console.print(f"  [{DIM}]Merges skipped.[/{DIM}]")
        return

    console.print()
    for m in candidates:
        try:
            tm.execute_merge(m, model=model, openrouter_api_key=api_key)
        except Exception as e:
            console.print(f"[red]  Merge '{m['new_name']}' failed: {e}[/red]")

    console.print(f"\n[{G}]Merge complete.[/{G}]")


def cmd_bibtex():
    import bibtex as bib

    papers = load_paper_index()
    if not papers:
        console.print(f"[{DIM}]No papers in vault.[/{DIM}]")
        return

    # Build choice list: "Title (arXiv:XXXX)"
    items = sorted(papers.items(), key=lambda kv: kv[1].get("title", ""))
    choices = [
        questionary.Choice(
            title=f"{info.get('title', aid)[:70]}  ({aid})",
            value=aid,
        )
        for aid, info in items
    ]
    choices.append(questionary.Choice(title="── enter arXiv ID manually ──", value="__manual__"))

    selected = questionary.select(
        "Generate BibTeX for:",
        choices=choices,
        style=Q_STYLE,
    ).ask()

    if not selected:
        return

    if selected == "__manual__":
        selected = (questionary.text("arXiv ID:", style=Q_STYLE).ask() or "").strip()
        if not selected:
            return

    update_note = questionary.confirm(
        "Patch the note with a ## BibTeX section?",
        default=False,
        style=Q_STYLE,
    ).ask()

    console.print()
    try:
        bib.generate(selected, update_note=update_note, clipboard=True, verbose=True)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


# ──────────────────────────────────────────────────────────────
# MAIN REPL
# ──────────────────────────────────────────────────────────────

def main():
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        console.print(
            "[red bold]OPENROUTER_API_KEY not set.[/red bold]\n"
            "[dim]Export it first:  export OPENROUTER_API_KEY=sk-...[/dim]"
        )
        sys.exit(1)

    model            = os.environ.get("PEPERNOTEN_MODEL", "anthropic/claude-sonnet-4-5")
    extraction_model = os.environ.get("PEPERNOTEN_EXTRACTION_MODEL", "anthropic/claude-haiku-4.5")
    safe_update      = "--safe" in sys.argv or "--safe_update" in sys.argv

    # Plain text titles — questionary doesn't interpret Rich markup
    cmd_choices = [
        questionary.Choice(title=f"{cmd:<14}  {desc}", value=cmd)
        for cmd, desc in COMMANDS
    ]

    while True:
        show_home()

        command = questionary.select(
            "Command:",
            choices=cmd_choices,
            style=Q_STYLE,
            use_shortcuts=False,
        ).ask()

        if command is None or command == "quit":
            console.print(f"\n[{DIM}]Bye.[/{DIM}]\n")
            break

        console.print()

        if   command == "parse":            cmd_parse(api_key, model, safe_update, extraction_model)
        elif command == "inbox":            cmd_inbox(api_key, model, safe_update, extraction_model)
        elif command == "topics":           cmd_topics()
        elif command == "list":             cmd_list()
        elif command == "update_knowledge": cmd_update_knowledge(api_key, model, safe_update, extraction_model)
        elif command == "bibtex":           cmd_bibtex()

        console.print()
        input("  press Enter to continue…  ")
        console.clear()


if __name__ == "__main__":
    main()
