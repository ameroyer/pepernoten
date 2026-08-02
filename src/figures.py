# Figure extraction from HTML and PDF sources

import base64
import os
import re
import shutil
from pathlib import Path
from urllib.parse import urljoin

import fitz
import requests
from bs4 import BeautifulSoup

import llm
from arxiv_utils import arxiv_asset
from vault import IMAGES_PATH, THUMBNAIL_PATH, VAULT_PATH


def _figure_rel_path(arxiv_id: str, label: str) -> str:
    num    = re.search(r"\d+", label).group()
    prefix = "Table" if "Table" in label else "Figure"
    return f"Research/images/{prefix}_{arxiv_id}_{num}.png"


def extract_figures_from_html(
    arxiv_id: str, html: str, base_url: str
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    os.makedirs(IMAGES_PATH, exist_ok=True)
    figure_map: dict[str, str] = {}
    caption_map: dict[str, str] = {}
    needs_pdf: dict[str, str] = {}

    soup = BeautifulSoup(html, "html.parser")

    for fig_el in soup.find_all("figure"):
        caption_el = fig_el.find("figcaption")
        if not caption_el:
            continue
        caption_text = caption_el.get_text(" ", strip=True)
        m = re.match(r"((?:Figure|Fig\.?\s*|Table)\s*\d+)[.:]?\s*(.*)", caption_text, re.IGNORECASE)
        if not m:
            continue
        label = re.sub(r"(?i)\bFig\b\.?\s*", "Figure ", m.group(1)).strip()
        label = re.sub(r"\s+", " ", label)
        if label in figure_map or label in needs_pdf:
            continue
        caption_map[label] = m.group(2)[:200].strip()
        rel_path  = _figure_rel_path(arxiv_id, label)
        full_path = os.path.join(VAULT_PATH, rel_path)

        raster_url: str | None = None
        svg_url: str | None = None

        for src_el in fig_el.find_all("source"):
            src = (src_el.get("srcset") or src_el.get("src", "")).split()[0]
            if not src:
                continue
            mime = src_el.get("type", "")
            if mime in ("image/png", "image/jpeg", "image/webp"):
                raster_url = urljoin(base_url, src)
                break
            elif mime == "image/svg+xml" and not svg_url:
                svg_url = urljoin(base_url, src)

        for img_el in fig_el.find_all("img"):
            src = img_el.get("src") or img_el.get("data-src", "")
            if not src:
                continue
            try:
                if int(img_el.get("width", 999)) < 50 or int(img_el.get("height", 999)) < 50:
                    continue
            except (ValueError, TypeError):
                pass
            ext = Path(src.split("?")[0]).suffix.lower()
            if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                if not raster_url:
                    raster_url = urljoin(base_url, src)
            elif ext == ".svg" and not svg_url:
                svg_url = urljoin(base_url, src)

        if raster_url:
            try:
                arxiv_asset.wait()
                resp = requests.get(raster_url, timeout=20)
                if resp.status_code == 200:
                    with open(full_path, "wb") as fh:
                        fh.write(resp.content)
                    figure_map[label] = rel_path
                    continue
            except Exception:
                pass

        if svg_url:
            png_sibling = re.sub(r"\.svg(\?.*)?$", ".png", svg_url, flags=re.IGNORECASE)
            if png_sibling != svg_url:
                try:
                    arxiv_asset.wait()
                    resp = requests.get(png_sibling, timeout=20)
                    ct = resp.headers.get("content-type", "")
                    if resp.status_code == 200 and "image" in ct:
                        with open(full_path, "wb") as fh:
                            fh.write(resp.content)
                        figure_map[label] = rel_path
                        continue
                except Exception:
                    pass

        needs_pdf[label] = caption_map[label]

    return figure_map, caption_map, needs_pdf


def extract_leading_figure(
    doc: fitz.Document | None,
    arxiv_id: str,
    figure_map: dict[str, str] | None = None,
    preferred_label: str | None = None,
) -> str:
    os.makedirs(THUMBNAIL_PATH, exist_ok=True)
    rel = f"Research/Thumbnails/Fig_{arxiv_id}.png"
    path = os.path.join(VAULT_PATH, rel)

    if figure_map and preferred_label and preferred_label in figure_map:
        src = os.path.join(VAULT_PATH, figure_map[preferred_label])
        if os.path.exists(src):
            shutil.copy(src, path)
            return rel

    if figure_map:
        for candidate in ("Figure 1", "Figure 2"):
            if candidate in figure_map:
                src = os.path.join(VAULT_PATH, figure_map[candidate])
                if os.path.exists(src):
                    shutil.copy(src, path)
                    return rel

    if doc is not None:
        best_pix, best_area = None, 0
        for page_num in range(min(8, len(doc))):
            for img in doc[page_num].get_images(full=True):
                xref = img[0]
                try:
                    pix = fitz.Pixmap(doc, xref)
                    if pix.n > 4:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    area = pix.width * pix.height
                    if area > best_area and pix.width > 150 and pix.height > 150:
                        best_area = area
                        best_pix = pix
                except Exception:
                    continue
        if best_pix:
            best_pix.save(path)
        else:
            doc[0].get_pixmap(dpi=150).save(path)
    return rel


def _find_figure_page(doc: fitz.Document, label: str, caption: str) -> int:
    num = re.search(r"\d+", label).group()
    is_table = "Table" in label
    search_terms = [
        f"Table {num}" if is_table else f"Figure {num}",
        f"Fig. {num}",
        f"Fig {num}",
    ]
    cap_start = caption[:30].lower().strip()
    for pnum in range(len(doc)):
        text = doc[pnum].get_text().lower()
        for term in search_terms:
            if term.lower() in text and (not cap_start or cap_start in text):
                return pnum
    for pnum in range(len(doc)):
        text = doc[pnum].get_text().lower()
        for term in search_terms:
            if term.lower() in text:
                return pnum
    return -1


def _image_blocks_above_caption(page: fitz.Page, cap_y0: float) -> list[fitz.Rect]:
    pw, ph = page.rect.width, page.rect.height
    rects = []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 1:
            continue
        r = fitz.Rect(b["bbox"])
        if r.width < 80 or r.height < 80:
            continue
        if r.width * r.height > pw * ph * 0.85:
            continue
        if r.y0 < cap_y0 + 10:
            rects.append(r)
    return rects


def extract_figures_with_vision(
    doc: fitz.Document,
    arxiv_id: str,
    skip_labels: set[str] | None = None,
    targets: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    os.makedirs(IMAGES_PATH, exist_ok=True)
    figure_map: dict[str, str] = {}
    caption_map: dict[str, str] = {}
    _skip = skip_labels or set()

    if targets is not None:
        work: dict[str, str] = {lbl: cap for lbl, cap in targets.items() if lbl not in _skip}
    else:
        work = {}
        for page_num in range(len(doc)):
            for block in doc[page_num].get_text("dict")["blocks"]:
                if block.get("type") != 0:
                    continue
                raw = " ".join(
                    span["text"]
                    for line in block.get("lines", [])
                    for span in line.get("spans", [])
                ).strip()
                m = re.match(r"((?:Figure|Fig\.?\s*|Table)\s*\d+)[.:]?\s*(.*)", raw)
                if not m:
                    continue
                lbl = re.sub(r"\bFig\b\.?\s*", "Figure ", m.group(1)).strip()
                lbl = re.sub(r"\s+", " ", lbl)
                if lbl not in work and lbl not in _skip:
                    work[lbl] = m.group(2)[:200].strip()

    for label, caption in work.items():
        caption_map[label] = caption
        rel_path  = _figure_rel_path(arxiv_id, label)
        full_path = os.path.join(VAULT_PATH, rel_path)

        page_num = _find_figure_page(doc, label, caption)
        if page_num == -1:
            print(f"  {label}: not found in PDF, skipping.")
            continue

        print(f"  Extracting {label} from page {page_num + 1}...")

        cap_y0 = float("inf")
        num_pat = re.search(r"\d+", label).group()
        for b in doc[page_num].get_text("dict")["blocks"]:
            if b["type"] != 0:
                continue
            raw = " ".join(s["text"] for line in b["lines"] for s in line["spans"]).strip()
            if re.match(rf"(?:Table|Figure|Fig\.?\s*)\s*{num_pat}\b", raw, re.IGNORECASE):
                cap_y0 = fitz.Rect(b["bbox"]).y0
                break

        img_rects = _image_blocks_above_caption(doc[page_num], cap_y0)

        if not img_rects and page_num > 0 and cap_y0 < doc[page_num].rect.height * 0.25:
            prev_page = doc[page_num - 1]
            img_rects = _image_blocks_above_caption(prev_page, prev_page.rect.height)
            if img_rects:
                page_num = page_num - 1

        if not img_rects:
            print(f"    {label}: no raster image found (vector figure?), skipping.")
            continue

        x0 = min(r.x0 for r in img_rects)
        y0 = min(r.y0 for r in img_rects)
        x1 = max(r.x1 for r in img_rects)
        y1 = max(r.y1 for r in img_rects)
        doc[page_num].get_pixmap(matrix=fitz.Matrix(3, 3), clip=fitz.Rect(x0, y0, x1, y1)).save(full_path)
        figure_map[label] = rel_path

    return figure_map, caption_map


def _describe_figure(
    img_path: str, label: str, caption: str, client, model: str,
    tracker: llm.UsageTracker | None = None,
) -> str:
    ext = Path(img_path).suffix.lstrip(".").lower() or "png"
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "svg": "image/svg+xml"}.get(ext, "image/png")
    try:
        with open(img_path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": (
                    f"This is {label} from an academic paper. Caption: '{caption[:200]}'. "
                    "In one sentence, describe what this figure/table shows and its role "
                    "(e.g. 'Architecture diagram of the proposed model showing X', "
                    "'Results table comparing method Y against baselines on Z benchmark', "
                    "'Ablation study showing impact of component W')."
                )},
            ]}],
            max_tokens=120,
        )
        if tracker is not None:
            tracker.add(llm.usage_from_openai_response(resp, model))
        return resp.choices[0].message.content.strip()
    except Exception:
        return caption[:200]


def _pick_best_figure(
    figure_map: dict[str, str],
    caption_map: dict[str, str],
    client,
    vision_model: str,
    tracker: llm.UsageTracker | None = None,
) -> str | None:
    candidates = [(lbl, path) for lbl, path in figure_map.items() if "Table" not in lbl][:6]
    if not candidates:
        candidates = list(figure_map.items())[:6]
    if len(candidates) == 1:
        return candidates[0][0]

    content: list[dict] = []
    valid: list[tuple[int, str]] = []

    for i, (label, rel_path) in enumerate(candidates, start=1):
        full_path = os.path.join(VAULT_PATH, rel_path)
        if not os.path.exists(full_path):
            continue
        try:
            ext = Path(rel_path).suffix.lstrip(".").lower() or "png"
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext, "image/png")
            with open(full_path, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode()
            content.append({"type": "text",
                             "text": f"Image {i} — {label}: {caption_map.get(label, '')[:80]}"})
            content.append({"type": "image_url",
                             "image_url": {"url": f"data:{mime};base64,{b64}"}})
            valid.append((i, label))
        except Exception:
            continue

    if not valid:
        return None
    if len(valid) == 1:
        return valid[0][1]

    content.append({"type": "text", "text": (
        "These are figures from an academic paper. "
        "Pick the single best one for use as a visual cover/banner — "
        "prioritise architecture diagrams and qualitative results over text tables or loss curves. "
        "Reply with ONLY the image number, e.g. '3'."
    )})

    try:
        resp = client.chat.completions.create(
            model=vision_model,
            messages=[{"role": "user", "content": content}],
            max_tokens=10,
        )
        if tracker is not None:
            tracker.add(llm.usage_from_openai_response(resp, vision_model))
        m = re.search(r"\d+", resp.choices[0].message.content.strip())
        if m:
            idx = int(m.group())
            for num, label in valid:
                if num == idx:
                    return label
    except Exception as e:
        print(f"  Banner figure selection failed: {e}")
    return None
