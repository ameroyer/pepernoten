# Prompt builders for paper synthesis (two-stage: extraction then writing) and topic file generation

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
# Shared style rules — hardcoded, not exposed in the YAML (formatting rules, not semantics).
# Applied to every prompt that produces prose read by a human.
# ──────────────────────────────────────────────────────────────────────────────

_HUMAN_STYLE_RULES = (
    "Write like a sharp, direct human researcher explaining this to a colleague, not like an AI "
    "assistant summarizing a document. Concretely:\n"
    "- Never use an em dash (—). Use a period, comma, or parentheses instead.\n"
    "- Avoid AI-cliche filler: never write \"delve into\", \"it's worth noting that\", \"in the "
    "realm of\", \"plays a crucial/pivotal role\", \"underscores\", \"a testament to\", "
    "\"furthermore\", \"moreover\", \"boasts\", \"navigate the complexities of\", or similar "
    "hedging transitions.\n"
    "- No throat-clearing, no restating the question, no summary-of-what-I-am-about-to-say. "
    "Start with the substance.\n"
    "- Be opinionated and concrete: name the specific number, the specific method, the specific "
    "failure. A shorter, sharper sentence beats a longer, vaguer one."
)

# ──────────────────────────────────────────────────────────────────────────────
# Stage 1 — extraction prompts (small/fast model, bounded JSON facts only)
# ──────────────────────────────────────────────────────────────────────────────

_RESULTS_TABLE_SPEC = (
    '"results_table": an object with two keys:\n'
    '  "headers": list of strings, e.g. ["Method", "Benchmark1 Metric1", ...],\n'
    '  "rows": list of lists, each inner list is [method_name, value1, ...]. '
    'Proposed method first, then 3-5 strongest baselines. Use "—" for missing values.\n'
    '  Return {"headers": [], "rows": []} if no results table can be reliably extracted.'
)

_TAGS_SPEC = (
    '"tags": list of 3-5 short lowercase hyphenated keywords describing the research content, '
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


def _extraction_system_prompt(tags_context: str) -> str:
    return (
        "You are extracting structured factual data from an academic paper, for a downstream "
        "assistant that will write human-readable notes from your extraction.\n\n"
        "Extract facts only. Do not analyze, interpret, or editorialize. Be literal and precise: "
        "exact numbers, exact model and method names, exact benchmark results, exact "
        "hyperparameters. If something is not stated in the paper, omit it or leave the value "
        "empty. Never invent or guess a fact.\n\n"
        "Return ONLY a valid JSON object with exactly these fields:\n\n"
        '"problem_statement": 1-3 sentences stating the problem this paper addresses and the '
        'specific failure mode of prior work, as stated or clearly implied by the paper.\n\n'
        '"method_facts": list of 4-8 strings, each one factual detail of the proposed method '
        '(a module name, an attention/architecture variant, a critical hyperparameter, a novel '
        'loss term). One fact per string, no interpretation.\n\n'
        f'{_RESULTS_TABLE_SPEC}\n\n'
        '"key_result_facts": list of 3-6 strings, each a literal number comparison, e.g. '
        '"Method achieves 71.2 on StreamingBench vs 64.8 for BaselineX (Table 2)". Cite the table '
        'if the paper names one.\n\n'
        '"ablation_facts": list of 2-5 strings, each: component removed or varied, the resulting '
        'delta, and which table/section it comes from.\n\n'
        '"benchmarks": list of objects {"name": string, "what_it_tests": one short clause}, for '
        'every benchmark the paper evaluates on.\n\n'
        '"limitations_facts": list of 3-5 strings, each a literal statement of what the paper '
        'itself leaves untested, missing, or constrained. No comparison to other papers here.\n\n'
        '"gaps_facts": list of 3-5 strings, each: a competing method plus year plus the exact '
        'metric or number where this paper is untested or weaker.\n\n'
        '"oddities_facts": list of 0-3 strings: inconsistent numbers, unsupported claims, or '
        'notable omissions. Empty list if none stand out.\n\n'
        '"related_work": list of 5-10 objects, each with exactly these fields: "name" (full paper '
        'title), "authors" ("First Author et al."), "year" (string), "arxiv_id" (provide if '
        'confident, e.g. "2301.12345", leave "" if unsure, it will be looked up), "gap_link" (one '
        'technical sentence connecting this related paper to the paper being processed).\n\n'
        + _TAGS_SPEC + '\n\n'
        '"concepts_glossary": list of 3-6 objects {"term": string, "plain_definition": one '
        'sentence in plain English}, for the key technical terms a newcomer to this subfield '
        'would need defined.'
        + tags_context
    )


def _extraction_user_message(figures_context: str, text_source: str, full_text: str) -> str:
    parts = []
    if figures_context:
        parts.append(f"<figures>\n{figures_context}\n</figures>")
    parts.append(f'<paper source="{text_source}">\n{full_text[:40000]}\n</paper>')
    return "\n\n".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Stage 2 — writer prompts (big model, plain markdown text, not JSON)
# ──────────────────────────────────────────────────────────────────────────────

def _writer_section_specs(verbosity: int) -> str:
    if verbosity == 1:
        return """===TLDR===
One tight sentence (at most 25 words) naming the contribution and the key metric gain. Add an emoji.

===PROBLEM===
1 paragraph. Problem statement and precise failure mode of prior work. Assume an expert reader: no motivation, no background.

===METHODOLOGY===
1-2 paragraphs. Names, variants, hyperparameters only, every module, attention variant, critical value. Skip explanations of what components do. Include loss equations only if the form is novel.

===RESULTS===
2 paragraphs.
Para 1: benchmarks and metrics, one line each.
Para 2: the 3 most important numbers vs. the closest baselines, cited exactly. Add one analytical sentence only if the result is genuinely unexpected.

===ABLATION===
1-2 paragraphs. Component name, delta, table citation. Skip a concluding sentence unless it's non-obvious.

===GAPS===
2-3 bullets (start each line with "- "). Each: competing method plus year plus the exact metric where the claim is untested or weaker, plus one analytical sentence.

===LIMITATIONS===
2-3 bullets. Absolute gaps in the paper itself: missing experiments, untested conditions, architectural constraints, scope restrictions. One sentence each.

===ODDITIES===
0-2 bullets. Minor flaws, suspicious claims, inconsistent numbers. Omit this section entirely if nothing stands out."""
    elif verbosity == 2:
        return """===TLDR===
Two sentences with an emoji. Sentence 1 (at most 20 words): what the paper does. Sentence 2 (at most 20 words): why it matters, what changes if this works, or what assumption it overturns. Do not restate sentence 1.

===PROBLEM===
2 paragraphs. Para 1: state the problem and the specific failure mode of prior work. Para 2: the precise gap this paper fills and why closing it is non-trivial.

===METHODOLOGY===
2-3 paragraphs. Adapt to what the paper actually presents:
- Core innovation and key architecture: name every module, attention variant, critical hyperparameter.
- Training objective only if the paper explicitly defines loss terms; write each as a display equation.
- Implementation details only if notable.
Bold the most important terms. No filler.

===RESULTS===
3 paragraphs.
Para 1: one sentence on evaluation scope, then one line per benchmark/metric.
Para 2: key numbers as "X on Benchmark (vs Y for Baseline)", cited. 2-3 most important comparisons.
Para 3: the most surprising result, stated, with a conclusion: what prior assumption does it challenge?

===ABLATION===
3 paragraphs.
Para 1: ablation scope, components and benchmark (one line).
Para 2: per component, name, delta, table citation.
Para 3: which design choice has the largest impact, why the authors claim it does, and whether the numbers support that claim.

===GAPS===
3-4 bullets. Each: (1) concrete missing evidence (method, year, benchmark, number), then (2) one analytical sentence on what this means. Vague gaps are unacceptable.

===LIMITATIONS===
3-4 bullets. Each: (1) what is missing or constrained, (2) why it matters for validity or generalization.

===ODDITIES===
0-2 bullets. Inconsistent numbers, overstated claims, surprising omissions, one sentence with a precise pointer. Omit this section entirely if nothing stands out."""
    elif verbosity == 3:
        return """===TLDR===
Two sentences with an emoji, then a third sentence (at most 20 words) explaining what makes the approach non-obvious for someone entering this subfield.

===PROBLEM===
3 paragraphs.
Para 1: one orienting sentence for a subfield newcomer.
Para 2: what current approaches do and specifically why they fail technically.
Para 3: the precise gap and why it is technically hard to close.

===METHODOLOGY===
3 paragraphs. Cover the core innovation with intuition for each key design choice (not just names, briefly say why each was chosen). Include equations with a one-line explanation of what each term encourages. Cover implementation details if notable. Bold key terms.

===RESULTS===
3 paragraphs.
Para 1: evaluation scope sentence, then one line per benchmark followed by a brief explanation of the metric.
Para 2: key numbers and comparisons, cited.
Para 3: the most surprising result, a conclusion, and what it reveals about why the method works.

===ABLATION===
3 paragraphs.
Para 1: ablation scope.
Para 2: deltas per component with table citation.
Para 3: which design choice matters most, explained conceptually rather than just empirically.

===GAPS===
3-5 bullets. Each: (1) specific missing evidence, (2) analytical conclusion, (3) one sentence on why this gap matters beyond this paper.

===LIMITATIONS===
3-4 bullets. Each: (1) what is missing or constrained, (2) why it matters, (3) whether it's a fundamental constraint or just an oversight.

===ODDITIES===
0-3 bullets. Precise pointer (section/table/figure). Omit this section entirely if nothing stands out."""
    else:  # verbosity == 4
        return """===TLDR===
Three sentences with an emoji. Sentence 1: what problem this solves, in plain English. Sentence 2: what the key idea is. Sentence 3: why it matters for the field.

===PROBLEM===
3 paragraphs.
Para 1: introduce the field and why this problem matters, written for someone with general ML knowledge but no subfield experience.
Para 2: what approaches currently exist and why they fall short, defining every technical term you introduce.
Para 3: what gap this paper closes and why it was challenging.

===METHODOLOGY===
3-4 paragraphs. Explain from first principles. Define every technical term on first use. For equations, explain every symbol in plain English. Use analogies where they clarify. Cover: core innovation, architecture, training objective if present, implementation details if relevant.

===RESULTS===
3 paragraphs.
Para 1: explain the evaluation context (why these benchmarks, what they test), then list benchmarks with plain-English metric explanations.
Para 2: key results in plain language with numbers.
Para 3: why the result is significant and what it changes for the field.

===ABLATION===
3 paragraphs.
Para 1: briefly explain what ablation means here, state what is being tested.
Para 2: per component, what it does, what happens when removed (delta plus table), why that matters.
Para 3: the most important design choice, explained accessibly.

===GAPS===
3-4 bullets. Explain the missing evidence accessibly and why it matters for someone deciding to trust or build on this work.

===LIMITATIONS===
3-4 bullets. What assumptions, datasets, or conditions the paper did not test, and why a reader should keep this in mind.

===ODDITIES===
0-2 bullets. Plain language: something that seems off or glossed over. Omit this section entirely if nothing stands out.

===CONCEPTS===
2-4 lines, each formatted exactly as "**Term**: definition" (1-2 sentences in plain English). Only concepts not already explained in the methodology section."""


def _build_writer_tones() -> dict[int, str]:
    _default_tones = {
        1: "Precision and brevity are paramount. Assume an expert reader throughout: no motivation, no background.",
        2: "Precision and appropriate depth. Target reader: an ML researcher familiar with the broader field but not this specific subfield.",
        3: "Target reader: someone with solid ML/AI background entering this subfield for the first time. Explain subfield-specific concepts and design choices, but not general ML basics.",
        4: "Target reader: a motivated newcomer to ML/AI. Define all jargon, use analogies, prioritise clarity over brevity.",
    }
    return {k: _cfg_get(f"paper_synthesis.tones.{k}", v) for k, v in _default_tones.items()}


def _writer_system_prompt(verbosity: int) -> str:
    tones = _build_writer_tones()
    analyst_role = _cfg_get(
        "paper_synthesis.analyst_role",
        "You are a sharp, critical research analyst writing notes for a PhD researcher",
    )
    return (
        f"{analyst_role}. "
        f"{tones.get(verbosity, tones[2])}\n\n"
        "You have been given a set of facts extracted from a paper by an assistant model, plus the "
        "paper's own full text. Use the facts as your primary source, but they may be incomplete: "
        "cross-check the paper text if something seems missing, ambiguous, or off before you write.\n\n"
        "Output plain text with one section per marker below, in this exact order. Each marker line "
        "(e.g. ===TLDR===) must appear alone on its own line, followed by that section's content. "
        "No JSON, no markdown code fences around the response, no preamble or explanation outside "
        "the markers.\n\n"
        "Formatting rules for section content:\n"
        "- Paragraphs separated by a blank line. No bullet points except where a section explicitly "
        "asks for them (GAPS, LIMITATIONS, ODDITIES, CONCEPTS).\n"
        "- Be surgical: every sentence must carry information. Cut padding ruthlessly.\n"
        "- Bold (**like this**) the 2-4 most important technical terms or claims per section.\n"
        "- LaTeX: $...$ for inline math (single variables, short symbols). "
        "$$...$$ ONLY for full standalone equations that deserve their own line. "
        "Never $$...$$ for something that reads naturally inline. "
        "Inside math: \\mathcal{X} for script letters, \\text{} for roman words, "
        "\\operatorname{} for named operators, braces for multi-char subscripts "
        "($x_{ij}$ not $x_ij$). No bare English words inside math mode.\n"
        "- Flag suspicious claims, weak baselines, and inconsistent numbers: you are not writing a "
        "press release.\n\n"
        + _HUMAN_STYLE_RULES + "\n\n"
        "Sections to produce:\n\n"
        + _writer_section_specs(verbosity)
    )


def _writer_user_message(facts: dict, figures_context: str, text_source: str, full_text: str) -> str:
    def _bullets(key: str) -> str:
        items = facts.get(key) or []
        return "\n".join(f"- {i}" for i in items) if items else ""

    blocks = ['<extracted_facts source="assistant model extraction, may be incomplete">']
    if facts.get("problem_statement"):
        blocks.append(f"<problem_statement>{facts['problem_statement']}</problem_statement>")
    if facts.get("method_facts"):
        blocks.append(f"<method_facts>\n{_bullets('method_facts')}\n</method_facts>")

    rt = facts.get("results_table") or {}
    headers, rows = rt.get("headers") or [], rt.get("rows") or []
    if headers and rows:
        table_txt = " | ".join(headers) + "\n" + "\n".join(" | ".join(str(c) for c in row) for row in rows)
        blocks.append(f"<results_table>\n{table_txt}\n</results_table>")

    if facts.get("key_result_facts"):
        blocks.append(f"<key_result_facts>\n{_bullets('key_result_facts')}\n</key_result_facts>")
    if facts.get("ablation_facts"):
        blocks.append(f"<ablation_facts>\n{_bullets('ablation_facts')}\n</ablation_facts>")

    benchmarks = facts.get("benchmarks") or []
    if benchmarks:
        bench_txt = "\n".join(f"- {b.get('name', '')}: {b.get('what_it_tests', '')}" for b in benchmarks)
        blocks.append(f"<benchmarks>\n{bench_txt}\n</benchmarks>")

    if facts.get("limitations_facts"):
        blocks.append(f"<limitations_facts>\n{_bullets('limitations_facts')}\n</limitations_facts>")
    if facts.get("gaps_facts"):
        blocks.append(f"<gaps_facts>\n{_bullets('gaps_facts')}\n</gaps_facts>")
    if facts.get("oddities_facts"):
        blocks.append(f"<oddities_facts>\n{_bullets('oddities_facts')}\n</oddities_facts>")

    concepts = facts.get("concepts_glossary") or []
    if concepts:
        concepts_txt = "\n".join(f"- {c.get('term', '')}: {c.get('plain_definition', '')}" for c in concepts)
        blocks.append(f"<concepts_glossary>\n{concepts_txt}\n</concepts_glossary>")
    blocks.append("</extracted_facts>")

    if figures_context:
        blocks.append(f"<figures>\n{figures_context}\n</figures>")
    blocks.append(f'<paper source="{text_source}">\n{full_text[:40000]}\n</paper>')
    return "\n\n".join(blocks)


# ──────────────────────────────────────────────────────────────────────────────
# Topic file prompts (used by topic_manager.py)
# ──────────────────────────────────────────────────────────────────────────────

def _init_system_prompt() -> str:
    _default = (
        "You are a senior researcher writing a living review document, a structured, "
        "continuously-updated synthesis of a specific research topic. "
        "Your output will be read by PhD researchers who want deep, accurate insight into the field. "
        "Write like a survey paper: synthesize across papers, identify paradigms, draw conclusions. "
        "Never just enumerate papers or copy summaries. Every sentence should carry insight."
    )
    role = _cfg_get("topics.init_role", _default)
    return (
        f"{role} "
        "Bold (**like this**) the most important technical terms and claims. "
        "Use $...$ for inline math and $$...$$ for display equations.\n\n"
        + _HUMAN_STYLE_RULES
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

3-4 paragraphs. Motivate the problem: why does {topic_name} matter, what makes it hard, and what \
does success look like? Then sketch the research landscape: what broad approaches exist, what \
are the key trade-offs, how has the field evolved? Write for a researcher who knows ML but is \
entering this subfield. Reference specific papers only to exemplify broader points, not as a \
bullet list.

---

## Benchmarks

List all benchmarks these papers evaluate on, sorted by frequency (most papers first). Format each as:

**BenchmarkName** ({{}}/{{total}} papers): what it tests and what makes it distinctive or \
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
- Include benchmark columns for every benchmark appearing in 2 or more papers. \
  Use "—" for untested combinations. Do not add benchmarks not present in any paper.
- **Key Innovation**: one tight noun phrase, not a sentence (e.g. "streaming KV eviction", \
  "linear-sparse hybrid attention", "codec-based token reuse").
- Sort: proposed methods chronologically (newest first), then baselines.
- Do not merge rows, every distinct model variant gets its own row.

---

## Techniques & Tricks

Structured grouped list of techniques used across papers. Identify the main categories \
(e.g. KV-cache management, token compression, temporal modeling, attention efficiency, \
memory management, training strategies; adapt to what actually appears).

For each technique:
**Technique Name**: one-sentence description of the core idea.
  - *Variant or paper-specific name* (Paper Short Name, year): what this paper does specifically.

Only include techniques that are meaningfully presented and discussed, not generic boilerplate \
like "we use AdamW" or "standard visual encoder".

---

## Architecture Overview

4-6 paragraphs synthesizing the main architectural paradigms across these papers. Structure:

- **Para 1**: What is the fundamental design space? What are the key axes of variation \
  (e.g. attention mechanism, memory architecture, token strategy)?
- **Para 2 through N**: One paragraph per major architectural family. Name the paradigm, describe \
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
already addressed by another paper in this set, or if so, note it explicitly.

---

Return ONLY the markdown content starting from `## Introduction`. No preamble, no explanation, \
no frontmatter."""


def _update_system_prompt() -> str:
    _default = (
        "You are maintaining a living review document for a research topic. "
        "A new paper has arrived. Your job is to integrate it surgically, "
        "adding new information where it belongs, updating counts and tables, "
        "and refining the narrative where the new paper changes the picture. "
        "Do not rewrite sections that don't need updating. "
        "The document should remain coherent and read like a unified review, not a patchwork."
    )
    role = _cfg_get("topics.update_role", _default)
    return (
        f"{role} "
        "Bold important technical terms. Use $...$ for inline math.\n\n"
        + _HUMAN_STYLE_RULES
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
Update only if the new paper meaningfully shifts the field's direction (introduces a new \
paradigm, achieves a breakthrough result, or reframes the core problem). \
Otherwise preserve existing text exactly.

**Benchmarks**
- Add any benchmark this paper evaluates on that is not yet listed.
- For existing benchmarks, increment the paper count and add the new paper's short name \
  to the parenthetical (e.g. "(3 papers: A, B, C)" becomes "(4 papers: A, B, C, NewPaper)").
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
- Preserve the review-paper tone throughout, do not append a standalone summary of the paper.

**Open Problems & Gaps**
- If this paper closes or partially addresses a listed problem: add \
  "partially addressed by [Paper short name]" at the end of that bullet.
- If its limitations/gaps reveal problems not yet listed: add them.

After the last section, output exactly this separator on its own line:
---CHANGELOG---
Then list 3-8 bullet points summarising what specifically changed in this update. \
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
        "Focus on small topics (at most 4 papers) with heavily overlapping tags, benchmarks, or near-synonym names. "
        "Be conservative: only propose merges you are confident about."
    )
    role = _cfg_get("topics.discover_role", _default)
    return (
        f"{role} "
        "Output valid JSON only, no markdown, no explanation.\n\n"
        + _HUMAN_STYLE_RULES
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
- Topics with at most 4 papers each that cover the same or nearly identical subfield
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
- Do NOT merge topics just because they are broadly related, only merge near-duplicates.
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
        "Return only the updated markdown, no preamble, no explanation.\n\n"
        + _HUMAN_STYLE_RULES
    )


def _remove_user_prompt(topic_name: str, content: str, paper_title: str, paper_aid: str) -> str:
    return f"""Topic survey: **{topic_name}**

Remove all references to this paper:
- Title: {paper_title}
- arXiv ID: {paper_aid}

Current survey:

{content}

Return the updated survey with this paper fully excised."""
