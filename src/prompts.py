# Prompt builders for paper synthesis and topic file generation

import os
from collections import defaultdict

import yaml  # pyyaml

from notes import note_xml

# ──────────────────────────────────────────────────────────────────────────────
# User-configurable prompt config (pepernoten_prompts.yaml)
# ──────────────────────────────────────────────────────────────────────────────

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pepernoten_prompts.yaml")


def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


_cfg = _load_config()


def _cfg_get(path: str, default: str) -> str:
    """Dot-path lookup into _cfg with fallback to default."""
    parts = path.split(".")
    node = _cfg
    for p in parts:
        if not isinstance(node, dict):
            return default
        node = node.get(p, ...)
        if node is ...:
            return default
    return str(node) if node is not None else default

# ──────────────────────────────────────────────────────────────────────────────
# Paper synthesis prompts (used by paper.py / parse.py)
# ──────────────────────────────────────────────────────────────────────────────

_RESULTS_TABLE_SPEC = (
    '"results_table": an object with two keys:\n'
    '  "headers": list of strings — ["Method", "Benchmark1 Metric1", ...],\n'
    '  "rows": list of lists — each inner list is [method_name, value1, ...]. '
    'Proposed method first, then 3-5 strongest baselines. Use "—" for missing values.\n'
    '  Return {"headers": [], "rows": []} if no results table can be reliably extracted.'
)

_TAGS_SPEC = (
    '"tags": list of 3-5 short lowercase hyphenated keywords describing the research content — '
    'the area, technique, or domain. Rules:\n'
    '  - Must reflect what the paper is ABOUT, not how it was processed. Never use meta/process '
    'tags like "research", "ai-parsed", "paper", "arxiv", "preprint".\n'
    '  - Broad enough to reuse across many papers, specific enough to be meaningful. '
    'Bad (too specific): "question-guided-compression". '
    'Bad (too vague): "deep-learning", "neural-network". '
    'Good: "kv-cache", "video-llm", "ssm", "object-detection", "rl-from-human-feedback".\n'
    '  - Strongly prefer existing tags from the vocabulary in <existing_tags>; only introduce a new one '
    'if no existing tag fits AND the concept applies to future papers too.'
)


def _field_specs(verbosity: int) -> str:
    rt = _RESULTS_TABLE_SPEC
    if verbosity == 1:
        return f"""
"tldr": One tight sentence (≤25 words) naming the contribution and the key metric gain. Add an emoji.

"problem": 1 paragraph. Problem statement and precise failure mode of prior work. Assume expert reader — no motivation, no background.

"methodology": 1-2 paragraphs. Names, variants, hyperparameters only — every module, attention variant, critical value. Skip explanations of what components do. Include loss equations only if the form is novel.

"results": 2 paragraphs.
  Para 1 — Benchmarks and metrics, one line each.
  Para 2 — 3 most important numbers vs. closest baselines. Cite table. Add one analytical sentence only if the result is genuinely unexpected.

{rt}

"ablation": 1-2 paragraphs. Component name, delta, table citation. Skip concluding sentence unless non-obvious.

"gaps": list of 2-3 strings. Each: competing method + year + exact metric/number where the claim is untested or weaker, plus one analytical sentence.

"limitations": list of 2-3 strings. Absolute gaps in the paper itself — missing experiments, untested conditions, architectural constraints, or scope restrictions. No comparison to prior work; only what this paper fails to establish on its own terms. One sentence each.

"oddities": list of 0-2 strings. Minor flaws, suspicious claims, inconsistent numbers, or anything that would make a careful reader raise an eyebrow. Empty list if nothing stands out.

"related_work": list of 3-4 objects. Direct competitors only. Each "gap_link": one technical sentence connecting this paper to a specific gap above.
"""
    elif verbosity == 2:
        return f"""
"tldr": Two sentences with an emoji. Sentence 1 (≤20 words): what the paper does. Sentence 2 (≤20 words): why it matters — what changes if this works, or what assumption it overturns. Do not restate sentence 1.

"problem": 2 paragraphs. Para 1: state the problem and the specific failure mode of prior work. Para 2: the precise gap this paper fills and why closing it is non-trivial.

"methodology": 2-3 paragraphs. Adapt to what the paper actually presents:
  - Core innovation and key architecture: name every module, attention variant, critical hyperparameter.
  - Training objective ONLY if the paper explicitly defines loss terms; write each as a display equation.
  - Implementation details ONLY if notable.
  Bold the most important terms. No filler.

"results": 3 paragraphs.
  Para 1 — Benchmarks & metrics: one sentence on evaluation scope, then one line per benchmark/metric.
  Para 2 — Key numbers: "X on Benchmark (vs Y for Baseline)". Cite table. 2-3 most important comparisons.
  Para 3 — Most surprising result: state it and draw a conclusion — what prior assumption does it challenge?

{rt}

"ablation": 3 paragraphs.
  Para 1 — Ablation scope: components and benchmark (one line).
  Para 2 — Per component: name, delta, table citation.
  Para 3 — Which design choice has the largest impact, why the authors claim it does, and whether the numbers support that claim.

"gaps": list of 3-4 strings. Each: (1) concrete missing evidence (method + year + benchmark + number), then (2) one analytical sentence on what this means. Vague gaps are unacceptable.

"limitations": list of 3-4 strings. Each: (1) what is missing or constrained, (2) why it matters for validity or generalization.

"oddities": list of 0-2 strings. Inconsistent numbers, overstated claims, surprising omissions — one sentence with a precise pointer. Empty list if nothing stands out.

"related_work": list of 4-5 objects. Direct competitors and papers that enrich understanding. Each "gap_link": one technical sentence per paper.
"""
    elif verbosity == 3:
        return f"""
"tldr": Two sentences with an emoji, then a third sentence (≤20 words) explaining what makes the approach non-obvious for someone entering this subfield.

"problem": 3 paragraphs.
  Para 1: one orienting sentence for a subfield newcomer.
  Para 2: what current approaches do and specifically why they fail technically.
  Para 3: the precise gap and why it is technically hard to close.

"methodology": 3 paragraphs. Cover core innovation with intuition for each key design choice (not just names — briefly say why each was chosen). Include equations with a one-line explanation of what each term encourages. Cover implementation details if notable. Bold key terms.

"results": 3 paragraphs.
  Para 1 — Evaluation scope sentence, then one line per benchmark followed by a brief explanation of the metric.
  Para 2 — Key numbers and comparisons. Cite table.
  Para 3 — Most surprising result, conclusion, and what it reveals about why the method works.

{rt}

"ablation": 3 paragraphs.
  Para 1 — Ablation scope.
  Para 2 — Deltas per component with table citation.
  Para 3 — Which design choice matters most and explain conceptually why it helps, not just empirically.

"gaps": list of 3-5 strings. Each: (1) specific missing evidence, (2) analytical conclusion, (3) one sentence on why this gap matters beyond this paper.

"limitations": list of 3-4 strings. Each: (1) what is missing or constrained, (2) why it matters, (3) whether it is a fundamental constraint or just an oversight.

"oddities": list of 0-3 strings. Precise pointer (section/table/figure). Empty list if nothing stands out.

"related_work": list of 5-7 objects. Direct competitors AND papers providing important context for the subfield.
"""
    else:  # verbosity == 4
        return f"""
"tldr": Three sentences with an emoji. Sentence 1: what problem this solves in plain English (no jargon). Sentence 2: what the key idea is. Sentence 3: why it matters for the field.

"problem": 3 paragraphs.
  Para 1: introduce the field and why this problem matters — write for someone with general ML knowledge but no subfield experience.
  Para 2: what approaches currently exist and why they fall short — define every technical term you introduce.
  Para 3: what gap this paper closes and why it was challenging.

"methodology": 3-4 paragraphs. Explain from first principles. Define every technical term on first use. For equations, explain every symbol in plain English. Use analogies where they clarify. Cover: core innovation, architecture, training objective if present, implementation details if relevant.

"results": 3 paragraphs.
  Para 1 — Explain evaluation context (why these benchmarks, what they test), then list benchmarks with plain-English metric explanations.
  Para 2 — Key results in plain language with numbers.
  Para 3 — Why the result is significant and what it changes for the field.

{rt}

"ablation": 3 paragraphs.
  Para 1 — Briefly explain what ablation means here, state what is being tested.
  Para 2 — Per component: what it does, what happens when removed (delta + table), why that matters.
  Para 3 — The most important design choice with an accessible explanation of why it works.

"gaps": list of 3-4 strings. Explain the missing evidence accessibly and why it matters for someone deciding to trust or build on this work.

"limitations": list of 3-4 strings. What assumptions, datasets, or conditions the paper did not test and why a reader should keep this in mind.

"oddities": list of 0-2 strings. Plain language — something that seems off or glossed over. Empty list if nothing stands out.

"related_work": list of 7-10 objects. Competitors AND foundational papers a newcomer should read.

"concepts": list of 2-4 objects, each with "term" (string) and "definition" (1-2 sentences in plain English). Only concepts not already explained in methodology.
"""


def _build_system_prompt(verbosity: int, tags_context: str) -> str:
    _default_tones = {
        1: "Precision and brevity are paramount. Assume an expert reader throughout — no motivation, no background.",
        2: "Precision and appropriate depth. Target reader: an ML researcher familiar with the broader field but not this specific subfield.",
        3: "Target reader: someone with solid ML/AI background entering this subfield for the first time. Explain subfield-specific concepts and design choices, but not general ML basics.",
        4: "Target reader: a motivated newcomer to ML/AI. Define all jargon, use analogies, prioritise clarity over brevity.",
    }
    tones = {
        k: _cfg_get(f"paper_synthesis.tones.{k}", v)
        for k, v in _default_tones.items()
    }
    analyst_role = _cfg_get(
        "paper_synthesis.analyst_role",
        "You are a sharp, critical research analyst writing notes for a PhD researcher",
    )
    return (
        f"{analyst_role}. "
        f"{tones.get(verbosity, tones[2])}\n\n"
        "Return ONLY a valid JSON object. Rules for all prose fields:\n"
        "- Paragraphs separated by \\n\\n. No bullet points in prose fields.\n"
        "- Be surgical: every sentence must carry information. Cut padding ruthlessly.\n"
        "- Bold (**like this**) the 2-4 most important technical terms or claims per section.\n"
        "- LaTeX: $...$ for inline math (single variables, short symbols). "
        "$$...$$ ONLY for full standalone equations that deserve their own line. "
        "Never $$...$$ for something that reads naturally inline. "
        "Inside math: \\mathcal{X} for script letters, \\text{} for roman words, "
        "\\operatorname{} for named operators, braces for multi-char subscripts "
        "($x_{ij}$ not $x_ij$). No bare English words inside math mode.\n"
        "- Flag suspicious claims, weak baselines, and inconsistent numbers — "
        "you are not writing a press release.\n\n"
        "Field specifications:\n"
        + _field_specs(verbosity)
        + '\nAll "related_work" objects must have exactly these fields: '
        '"name" (full title), "authors" ("First Author et al."), "year" (string), '
        '"arxiv_id" (provide if confident, e.g. "2301.12345"; leave "" if unsure — it will be looked up), '
        '"gap_link" (as specified above).\n\n'
        + _TAGS_SPEC
        + tags_context
    )


def _build_user_message(figures_context: str, text_source: str, full_text: str) -> str:
    parts = []
    if figures_context:
        parts.append(f"<figures>\n{figures_context}\n</figures>")
    parts.append(f'<paper source="{text_source}">\n{full_text[:40000]}\n</paper>')
    return "\n\n".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Topic file prompts (used by topic_manager.py)
# ──────────────────────────────────────────────────────────────────────────────

def _init_system_prompt() -> str:
    _default = (
        "You are a senior researcher writing a living review document — a structured, "
        "continuously-updated synthesis of a specific research topic. "
        "Your output will be read by PhD researchers who want deep, accurate insight into the field. "
        "Write like a survey paper: synthesize across papers, identify paradigms, draw conclusions. "
        "Never just enumerate papers or copy summaries. Every sentence should carry insight."
    )
    role = _cfg_get("topics.init_role", _default)
    return (
        f"{role} "
        "Bold (**like this**) the most important technical terms and claims. "
        "Use $...$ for inline math and $$...$$ for display equations."
    )


def _init_user_prompt(topic_name: str, notes: list[dict], citation_block: str = "") -> str:
    papers_xml = "\n\n".join(note_xml(d, i + 1) for i, d in enumerate(notes))
    ref_list   = "\n".join(
        f"  {i+1}. **{d['title']}** ({d['date'][:4]}, arXiv:{d['arxiv_id']})"
        for i, d in enumerate(notes)
    )
    bench_freq: dict[str, int] = defaultdict(int)
    for d in notes:
        for b in d["benchmarks"]:
            bench_freq[b] += 1

    citation_section = f"\n\n{citation_block}" if citation_block else ""

    return f"""Write the initial version of a living review document about: **{topic_name}**

You have {len(notes)} papers to synthesize:
{ref_list}

<papers>
{papers_xml}
</papers>{citation_section}

Produce the complete markdown document with exactly these sections, in this order:

---

## Introduction

3–4 paragraphs. Motivate the problem: why does {topic_name} matter, what makes it hard, and what \
does success look like? Then sketch the research landscape: what broad approaches exist, what \
are the key trade-offs, how has the field evolved? Write for a researcher who knows ML but is \
entering this subfield. Reference specific papers only to exemplify broader points — not as a \
bullet list.

---

## Benchmarks

List all benchmarks these papers evaluate on, sorted by frequency (most papers first). Format each as:

**BenchmarkName** ({{}}/{{total}} papers) — what it tests and what makes it distinctive or \
challenging. If multiple benchmarks are closely related (e.g. VideoMME short/long), group them \
with a brief note on differences.

Only include benchmarks that at least one paper explicitly evaluates on. Do not invent benchmarks.

---

## Methods & Baselines

A complete markdown table comparing ALL proposed methods and ALL major baselines mentioned in the \
papers. Required columns:

| Model | Year | Size | [benchmark columns] | Key Innovation |

Rules:
- **Size** is MANDATORY. Extract from each paper's methodology section. \
  Common formats: "7B", "13B", "72B params", "ViT-L". Use "?" only if truly absent after careful reading.
- Include benchmark columns for every benchmark appearing in ≥2 papers. \
  Use "—" for untested combinations. Do not add benchmarks not present in any paper.
- **Key Innovation**: one tight noun phrase, not a sentence (e.g. "streaming KV eviction", \
  "linear-sparse hybrid attention", "codec-based token reuse").
- Sort: proposed methods chronologically (newest first), then baselines.
- Do not merge rows — every distinct model variant gets its own row.

---

## Techniques & Tricks

Structured grouped list of techniques used across papers. Identify the main categories \
(e.g. KV-cache management, token compression, temporal modeling, attention efficiency, \
memory management, training strategies — adapt to what actually appears).

For each technique:
**Technique Name** — one-sentence description of the core idea.
  - *Variant or paper-specific name* (Paper Short Name, year): what this paper does specifically.

Only include techniques that are meaningfully presented and discussed — not generic boilerplate \
like "we use AdamW" or "standard visual encoder".

---

## Architecture Overview

4–6 paragraphs synthesizing the main architectural paradigms across these papers. Structure:

- **Para 1**: What is the fundamental design space? What are the key axes of variation \
  (e.g. attention mechanism, memory architecture, token strategy)?
- **Para 2–N**: One paragraph per major architectural family. Name the paradigm, describe \
  its core design choices, list representative papers, and note key advantages and failure modes.
- **Final para**: Emerging convergences, open architectural questions, and where the field \
  seems to be heading.

Bold important technical terms. Use display equations only for core formulas that are \
central to understanding an approach.

---

## Open Problems & Gaps

Bulleted list synthesized from gaps and limitations across all papers. For each problem:

**Problem name**: precise description. Why it matters. Which papers surface it.

Order from broadest/most fundamental to most specific. Do not include problems that are \
already addressed by another paper in this set — or if so, note it explicitly.

---

Return ONLY the markdown content starting from `## Introduction`. No preamble, no explanation, \
no frontmatter."""


def _update_system_prompt() -> str:
    _default = (
        "You are maintaining a living review document for a research topic. "
        "A new paper has arrived. Your job is to integrate it surgically — "
        "adding new information where it belongs, updating counts and tables, "
        "and refining the narrative where the new paper changes the picture. "
        "Do not rewrite sections that don't need updating. "
        "The document should remain coherent and read like a unified review, not a patchwork."
    )
    role = _cfg_get("topics.update_role", _default)
    return (
        f"{role} "
        "Bold important technical terms. Use $...$ for inline math."
    )


def _update_user_prompt(topic_name: str, existing_content: str, note: dict, citation_block: str = "") -> str:
    citation_section = f"\n\n{citation_block}" if citation_block else ""
    return f"""Integrate a new paper into the living review document about **{topic_name}**.

<existing_document>
{existing_content}
</existing_document>

<new_paper title="{note['title']}" arxiv_id="{note['arxiv_id']}" date="{note['date']}">
{note_xml(note)}
</new_paper>{citation_section}

Produce the COMPLETE updated document (all sections, from ## Introduction to ## Open Problems & Gaps). \
Apply these rules per section:

**Introduction**
Update only if the new paper meaningfully shifts the field's direction — introduces a new \
paradigm, achieves a breakthrough result, or reframes the core problem. \
Otherwise preserve existing text exactly.

**Benchmarks**
- Add any benchmark this paper evaluates on that is not yet listed.
- For existing benchmarks, increment the paper count and add the new paper's short name \
  to the parenthetical (e.g. "(3 papers: A, B, C)" → "(4 papers: A, B, C, NewPaper)").
- Keep sorted by frequency (re-sort if a benchmark moves up).

**Methods & Baselines table**
- Add one row for the new paper's proposed method. Extract model size from its methodology \
  section (use "?" only if truly absent). Fill benchmark values from its results; use "—" for \
  benchmarks not evaluated.
- Add rows for any new baselines the paper introduces that are not already in the table.
- Re-sort: proposed methods by year descending, baselines below.
- Do not modify existing rows unless a value was clearly wrong.

**Techniques & Tricks**
- If the paper introduces a genuinely novel technique: add it under the appropriate category \
  (or create a new category if no existing one fits).
- If it uses a variant of an existing technique: add a sub-bullet under that technique.
- Skip anything that is standard background or already well-represented.

**Architecture Overview**
- If this paper fits an existing paradigm: add it as an example in the appropriate paragraph \
  (one sentence or clause, not a full paragraph unless it meaningfully deepens the description).
- If it introduces a new architectural pattern with no existing paragraph: add a new paragraph.
- If it challenges or refines a claim in the current text: update that sentence/paragraph.
- Preserve the review-paper tone throughout — do not append a standalone summary of the paper.

**Open Problems & Gaps**
- If this paper closes or partially addresses a listed problem: add \
  "→ partially addressed by [Paper short name]" at the end of that bullet.
- If its limitations/gaps reveal problems not yet listed: add them.

After the last section, output exactly this separator on its own line:
---CHANGELOG---
Then list 3–8 bullet points summarising what specifically changed in this update. \
Be concrete: name the model, benchmark, technique, or paragraph that was touched. \
Format each bullet as:
- [Section]: what was added / changed / updated
Example: "- [Methods table]: added ViCoStream (7B); StreamingBench 71.2, OvO-Bench 58.4"

Return the updated markdown starting from `## Introduction`, then the separator, then the changelog. \
No preamble, no explanation."""


# ──────────────────────────────────────────────────────────────────────────────
# Topic merge prompts
# ──────────────────────────────────────────────────────────────────────────────

def _merge_system_prompt() -> str:
    _default = (
        "You analyze a personal research vault's topic structure to find merge opportunities. "
        "Topics should be merged when they cover essentially the same research subfield and "
        "would benefit from a single unified survey. "
        "Focus on small topics (≤4 papers) with heavily overlapping tags, benchmarks, or near-synonym names. "
        "Be conservative: only propose merges you are confident about."
    )
    role = _cfg_get("topics.discover_role", _default)
    return (
        f"{role} "
        "Output valid JSON only — no markdown, no explanation."
    )


def _merge_user_prompt(topics: list) -> str:
    lines = "\n".join(
        f"  slug={t['slug']!r:40s}  name={t['name']!r:45s}  "
        f"papers={t['paper_count']}  "
        f"tags={t['fingerprint_tags']}  "
        f"benchmarks={t['fingerprint_benchmarks']}"
        for t in topics
    )
    return f"""Topics currently in this research vault:

{lines}

Identify pairs (or small groups) of topics that should be merged into one.
Merge criteria:
- Topics with ≤4 papers each that cover the same or nearly identical subfield
- Topics with heavily overlapping fingerprint_tags or fingerprint_benchmarks
- Topics whose names are near-synonyms or one is a strict subset of the other

Return JSON:
{{
  "merges": [
    {{
      "merge_slugs": ["slug-a", "slug-b"],
      "new_name": "Specific Combined Name (3-6 words)",
      "reason": "one sentence"
    }}
  ]
}}

Rules:
- Each slug may appear in at most one merge group.
- Do NOT merge topics just because they are broadly related — only merge near-duplicates.
- If nothing should be merged, return {{"merges": []}}."""


# ──────────────────────────────────────────────────────────────────────────────
# Topic removal prompts
# ──────────────────────────────────────────────────────────────────────────────

def _remove_system_prompt() -> str:
    return (
        "You are a research knowledge base editor. "
        "Your task is to update a living topic survey by removing all content and references "
        "related to a specific paper that has been removed from the vault. "
        "Keep all other content fully intact. "
        "If the paper was the sole entry in a section or table row, remove that section/row entirely. "
        "Return only the updated markdown — no preamble, no explanation."
    )


def _remove_user_prompt(topic_name: str, content: str, paper_title: str, paper_aid: str) -> str:
    return f"""Topic survey: **{topic_name}**

Remove all references to this paper:
- Title: {paper_title}
- arXiv ID: {paper_aid}

Current survey:

{content}

Return the updated survey with this paper fully excised."""
