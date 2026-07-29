---
cssclasses:
  - dashboard
banner: "logo.png"
banner_y: 0.5
---

# :LiBookOpenText: PeperNoten

> [!quote] To add new paper: run `uv run pepernoten_cli.py` and use the **parse** command, or `uv run scripts/parse.py parse https://arxiv.org/abs/XXXX.XXXXX`

> [!abstract] **Links**
> [Scholar Inbox](https://www.scholar-inbox.com/home) :LiDot: [arxiv | CV](https://arxiv.org/list/cs.CV/recent) :LiDot: [arxiv | AI](https://arxiv.org/list/cs.AI/recent) :LiDot: [Daily Papers](https://huggingface.co/papers/date/2026-03-06)

<div style="height:1px;background:linear-gradient(90deg,transparent,var(--interactive-accent),transparent);margin:2.5em 0;opacity:0.35"></div>

## 📚 Library

```dataviewjs
const allPages = dv.pages('"Research"')
    .where(p => p.file.name !== dv.current().file.name)
    .sort(p => p.date, "desc");

// Collect unique tags, excluding internal ones
const skipTags = new Set(["research", "ai-parsed"]);
const allTags = [...new Set(allPages.flatMap(p => Array.isArray(p.tags) ? p.tags : []))]
    .filter(t => !skipTags.has(t))
    .sort();

const root = this.container;

// ── Header bar ────────────────────────────────────────────────
const bar = root.createEl("div", { attr: { style:
    "display:flex; align-items:center; justify-content:space-between; margin-bottom:18px; flex-wrap:wrap; gap:10px;"
}});

bar.createEl("span", { text: `${allPages.length} papers`, attr: { style:
    "font-size:0.82em; opacity:0.45; font-variant-numeric:tabular-nums;"
}});

const right = bar.createEl("div", { attr: { style: "display:flex; align-items:center; gap:8px;" }});
right.createEl("span", { text: "tag", attr: { style: "font-size:0.8em; opacity:0.45;" }});

const sel = right.createEl("select", { attr: { style:
    "background:var(--background-secondary); color:var(--text-normal);" +
    "border:1px solid var(--background-modifier-border); border-radius:8px;" +
    "padding:5px 10px; font-size:0.82em; cursor:pointer; outline:none;"
}});
sel.createEl("option", { text: "All", value: "all" });
allTags.forEach(t => sel.createEl("option", { text: t, value: t }));

// ── Card grid ─────────────────────────────────────────────────
const grid = root.createEl("div", { attr: { style:
    "display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:14px;"
}});

function renderCards(tag) {
    grid.empty();
    const pages = tag === "all"
        ? allPages
        : allPages.where(p => (Array.isArray(p.tags) ? p.tags : []).includes(tag));

    if (pages.length === 0) {
        grid.createEl("p", { text: "No papers for this tag.", attr: { style: "opacity:0.4; font-style:italic;" }});
        return;
    }

    for (const page of pages) {
        const card = grid.createEl("div", { attr: { style:
            "background:var(--background-secondary); border-radius:12px; overflow:hidden;" +
            "border:1px solid var(--background-modifier-border); cursor:pointer;" +
            "transition:transform 0.15s ease, box-shadow 0.15s ease;"
        }});

        card.addEventListener("mouseenter", () => {
            card.style.transform = "translateY(-4px)";
            card.style.boxShadow = "0 10px 28px rgba(0,0,0,0.35)";
        });
        card.addEventListener("mouseleave", () => {
            card.style.transform = "";
            card.style.boxShadow = "";
        });
        card.addEventListener("click", () => app.workspace.openLinkText(page.file.path, "", false));

        // Thumbnail
        const imgBox = card.createEl("div", { attr: { style:
            "height:130px; background:var(--background-primary); display:flex;" +
            "align-items:center; justify-content:center; overflow:hidden;"
        }});
        if (page.image) {
            imgBox.createEl("img", { attr: {
                src: app.vault.adapter.getResourcePath(page.image),
                style: "width:100%; height:100%; object-fit:cover;"
            }});
        } else {
            imgBox.createEl("span", { text: "📄", attr: { style: "font-size:2.2em; opacity:0.25;" }});
        }

        // Body
        const body = card.createEl("div", { attr: { style: "padding:11px 13px 13px;" }});

        body.createEl("div", { text: page.title || page.file.name, attr: { style:
            "font-size:0.82em; font-weight:600; line-height:1.35; margin-bottom:6px;" +
            "display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;"
        }});

        if (page.summary) {
            body.createEl("div", { text: page.summary, attr: { style:
                "font-size:0.73em; opacity:0.55; line-height:1.45;" +
                "display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; margin-bottom:9px;"
            }});
        }

        // Tag pills
        const pills = body.createEl("div", { attr: { style: "display:flex; flex-wrap:wrap; gap:4px; margin-bottom:8px;" }});
        (Array.isArray(page.tags) ? page.tags : [])
            .filter(t => !skipTags.has(t))
            .slice(0, 3)
            .forEach(t => pills.createEl("span", { text: t, attr: { style:
                "background:var(--interactive-accent); color:var(--text-on-accent);" +
                "border-radius:5px; padding:2px 7px; font-size:0.62em; font-weight:500;"
            }}));

        if (page.date) {
            body.createEl("div", { text: String(page.date).substring(0, 10), attr: { style:
                "font-size:0.62em; opacity:0.3; text-align:right;"
            }});
        }
    }
}

renderCards("all");
sel.addEventListener("change", () => renderCards(sel.value));
```
