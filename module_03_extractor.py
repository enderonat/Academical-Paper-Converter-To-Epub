"""
module_03_extractor.py — regions → content

For every region detected by module 02:
  - policy "text"  → open the original PDF, extract clean text within the bbox
  - policy "image" → crop the rendered PNG, save the crop as a PNG file

This is the module that directly implements the "math/graphs as images" guarantee.
Formulas, Figures, Tables never touch text extraction — they go straight to PNG crop.

Usage (standalone):
    python module_03_extractor.py mybook

Usage (as module):
    from module_03_extractor import extract_content
    extraction_manifest = extract_content("mybook")
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import fitz
from PIL import Image

from config import DATA_DIR, REGIONS_SUBDIR
from module_02_layout import LAYOUT_VERSION, load_layout_manifest

# Padding (px) added around image crops so edges aren't clipped
IMAGE_CROP_PADDING = 4

# ── Junk-filter knobs ─────────────────────────────────────────────────────────
# Text appearing on this many pages or more is considered a running header/footer
JUNK_REPEAT_THRESHOLD = 3
# Top/bottom/left/right margin fractions in which short text is considered chrome
JUNK_TOP_FRAC    = 0.08
JUNK_BOTTOM_FRAC = 0.05
JUNK_LEFT_FRAC   = 0.05
JUNK_RIGHT_FRAC  = 0.05
# Length under which margin text is considered chrome (page numbers, headers)
JUNK_SHORT_MAX_CHARS = 60
# Images smaller than this area (px²) in the outer margin are publisher decorations
JUNK_IMG_MAX_AREA = 200 * 200
# Substring patterns that always mark a region as junk regardless of position
JUNK_PATTERNS = re.compile(
    r"(downloaded\s+from|by\s+guest\s+on|ip\s+address|"
    r"all\s+rights\s+reserved|©\s*\d{4}|"
    r"https?://\S*lyellcollection|"
    r"www\.geolsoc\.org\.uk|"
    r"publishing\s+disclaimer|"
    r"check\s+for\s+updates|"
    r"crossmark)",
    re.IGNORECASE,
)


# ── Public API ─────────────────────────────────────────────────────────────────

def extract_content(book_name: str) -> dict:
    """
    Extract content from every region in the layout manifest.

    Reads  : data/{book_name}/layout_manifest.json   (written by module 02)
             data/{book_name}/pages/page_NNNN.png     (for image crops)
             original PDF                             (for text extraction)
    Writes : data/{book_name}/regions/*.png           (cropped image regions)
             data/{book_name}/extraction/page_NNNN.json
             data/{book_name}/extraction_manifest.json

    Returns the extraction_manifest dict.
    """
    layout_manifest = load_layout_manifest(book_name)

    # Recover the original PDF path from the render manifest
    from module_01_renderer import load_manifest as load_render_manifest
    render_manifest = load_render_manifest(book_name)
    pdf_path        = Path(render_manifest["pdf_path"])

    regions_dir    = DATA_DIR / book_name / REGIONS_SUBDIR
    extraction_dir = DATA_DIR / book_name / "extraction"
    regions_dir.mkdir(parents=True, exist_ok=True)
    extraction_dir.mkdir(parents=True, exist_ok=True)

    pages = layout_manifest["pages"]
    total = len(pages)

    print(f"\n[Module 03] Content extraction for '{book_name}'")
    print(f"  Pages     : {total}")
    print(f"  PDF       : {pdf_path}")
    print(f"  Regions   : {regions_dir}")

    doc = fitz.open(str(pdf_path))

    page_extraction_records: list[dict] = []
    t0 = time.perf_counter()

    for page_meta in pages:
        page_num   = page_meta["page_number"]
        page_index = page_meta["page_index"]

        out_path = extraction_dir / f"page_{page_num:04d}.json"

        # Resume support — skip pages already extracted from the same layout version
        if out_path.exists():
            record = json.loads(out_path.read_text(encoding="utf-8"))
            if record.get("source_layout_version") == LAYOUT_VERSION:
                record.setdefault("height_px", page_meta.get("height_px"))
                record.setdefault("width_px",  page_meta.get("width_px"))
                page_extraction_records.append(record)
                _progress(page_num, total)
                continue

        pdf_page    = doc[page_index]
        page_img    = Image.open(page_meta["image_path"]).convert("RGB")
        extracted_regions = []

        for region in page_meta["regions"]:
            result = _extract_region(
                region, pdf_page, page_img,
                page_meta["width_px"], page_meta["height_px"],
                regions_dir,
            )
            extracted_regions.append(result)

        page_img.close()

        record = {
            "source_layout_version": LAYOUT_VERSION,
            "page_index":  page_index,
            "page_number": page_num,
            "width_px":    page_meta["width_px"],
            "height_px":   page_meta["height_px"],
            "regions":     extracted_regions,
        }
        out_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        page_extraction_records.append(record)
        _progress(page_num, total)

    doc.close()

    elapsed = time.perf_counter() - t0
    print(f"\n\n  Extraction done — {total} pages in {elapsed:.1f}s")

    # ── Junk filter pass ──────────────────────────────────────────────────
    # Removes running headers/footers, watermarks, and short margin chrome.
    # Runs across all pages so frequency analysis sees the whole book.
    clean_stats = _filter_junk(page_extraction_records)
    print(f"  Junk removed   : {sum(clean_stats.values())} regions")
    print(f"    repeating    : {clean_stats['repeating']}  (running headers/footers)")
    print(f"    pattern      : {clean_stats['pattern']}  (watermarks)")
    print(f"    margin text  : {clean_stats['position']}  (short margin text)")
    print(f"    margin img   : {clean_stats.get('margin_img', 0)}  (small margin images)")

    # Re-write per-page JSONs so the cleaned data is what resume picks up
    for record in page_extraction_records:
        out_path = extraction_dir / f"page_{record['page_number']:04d}.json"
        out_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    # Final counts after cleaning
    text_count = image_count = empty_count = 0
    for record in page_extraction_records:
        tc, ic, ec = _count_content(record["regions"])
        text_count  += tc
        image_count += ic
        empty_count += ec

    print(f"  Text regions   : {text_count}")
    print(f"  Image regions  : {image_count}")
    print(f"  Empty (skipped): {empty_count}")

    extraction_manifest = {
        "book_name":    book_name,
        "page_count":   total,
        "text_regions": text_count,
        "img_regions":  image_count,
        "pages":        page_extraction_records,
    }

    em_path = DATA_DIR / book_name / "extraction_manifest.json"
    em_path.write_text(json.dumps(extraction_manifest, indent=2), encoding="utf-8")
    print(f"  Manifest  : {em_path}")

    return extraction_manifest


def load_extraction_manifest(book_name: str) -> dict:
    """Load the extraction manifest written by extract_content()."""
    path = DATA_DIR / book_name / "extraction_manifest.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No extraction manifest for '{book_name}'. Run module_03_extractor first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


# ── Region extraction ──────────────────────────────────────────────────────────

def _extract_region(
    region: dict,
    pdf_page,
    page_img: Image.Image,
    width_px: int,
    height_px: int,
    regions_dir: Path,
) -> dict:
    """
    Dispatch to text or image extraction based on region policy.
    Returns the region dict extended with a 'content' key.
    """
    result = dict(region)  # shallow copy — don't mutate the layout manifest data

    if region["policy"] == "text":
        result["content"] = _extract_text(region, pdf_page)
    else:
        result["content"] = _extract_image(
            region, page_img, width_px, height_px, regions_dir
        )

    return result


def _extract_text(region: dict, pdf_page) -> dict:
    """
    Extract clean text from a PDF page within the region's bbox_pt.

    Uses word-level extraction with overlap filtering rather than hard
    clipping. Hard clipping slices words that START before the clip
    boundary — common in multi-column PDFs — producing "ula" instead
    of "Peninsula". Word-level filtering keeps or drops each word as a
    whole unit based on how much of it sits inside the region bbox.

    Returns:
        {"type": "text", "text": "...", "is_empty": bool}
    """
    x0, y0, x1, y1 = region["bbox_pt"]
    region_rect = fitz.Rect(x0, y0, x1, y1)

    try:
        # Each word tuple: (x0, y0, x1, y1, word, block_no, line_no, word_no)
        words = pdf_page.get_text("words")
    except Exception:
        words = []

    kept: list[tuple] = []
    for w in words:
        wx0, wy0, wx1, wy1 = w[0], w[1], w[2], w[3]
        word_rect   = fitz.Rect(wx0, wy0, wx1, wy1)
        intersection = region_rect & word_rect

        if intersection.is_empty:
            continue

        # Keep words whose center point is inside the region bbox.
        # Center-point check is more stable than overlap-ratio for
        # narrow columns where words can straddle the boundary.
        cx = (wx0 + wx1) / 2
        cy = (wy0 + wy1) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            kept.append(w)

    if not kept:
        return {"type": "text", "text": "", "is_empty": True}

    # Re-assemble into lines using the block/line numbers from PyMuPDF
    # sort by (block, line, word position within line)
    kept.sort(key=lambda w: (w[5], w[6], w[0]))

    lines: list[str] = []
    current_line_words: list[str] = []
    prev_block = prev_line = None

    for w in kept:
        block_no, line_no = w[5], w[6]
        if (block_no, line_no) != (prev_block, prev_line):
            if current_line_words:
                lines.append(" ".join(current_line_words))
            current_line_words = [w[4]]
            prev_block, prev_line = block_no, line_no
        else:
            current_line_words.append(w[4])

    if current_line_words:
        lines.append(" ".join(current_line_words))

    raw = "\n".join(lines)
    cleaned = _clean_text(raw)

    return {
        "type":     "text",
        "text":     cleaned,
        "is_empty": len(cleaned) == 0,
    }


def _extract_image(
    region: dict,
    page_img: Image.Image,
    width_px: int,
    height_px: int,
    regions_dir: Path,
) -> dict:
    """
    Crop the region from the page PNG and save it.

    Returns:
        {"type": "image", "image_path": "...", "image_filename": "..."}
    """
    x0, y0, x1, y1 = region["bbox_px"]

    # For Formula regions: extend crop to the full column (or page) width so
    # equations that run to the column edge aren't cut off.
    if region.get("label") == "Formula":
        formula_w = x1 - x0
        if formula_w >= width_px * 0.45:
            # Wide formula (single-column paper or full-width equation) → full page
            x0, x1 = 0, width_px
        else:
            # Narrow formula in a two-column layout → extend to containing column
            mid_x = width_px // 2
            cx = (x0 + x1) / 2
            x0 = 0      if cx < mid_x else mid_x
            x1 = mid_x  if cx < mid_x else width_px

    # Add padding, clamped to page bounds
    x0 = max(0,         x0 - IMAGE_CROP_PADDING)
    y0 = max(0,         y0 - IMAGE_CROP_PADDING)
    x1 = min(width_px,  x1 + IMAGE_CROP_PADDING)
    y1 = min(height_px, y1 + IMAGE_CROP_PADDING)

    crop = page_img.crop((x0, y0, x1, y1))

    filename = f"{region['region_id']}.png"
    out_path = regions_dir / filename
    crop.save(str(out_path), format="PNG")
    crop.close()

    return {
        "type":           "image",
        "image_path":     str(out_path),
        "image_filename": filename,
    }


# ── Text cleaning ──────────────────────────────────────────────────────────────

# Matches a word ending with a hyphen at end-of-line: "hyphen-\n" → "hyphen"
_HYPHEN_BREAK = re.compile(r"-\n\s*")
# Collapse remaining newlines and excess whitespace
_WHITESPACE   = re.compile(r"\s+")
# Common PDF artefacts: page markers left by some publishers
_PAGE_MARKER  = re.compile(r"Downloaded from https?://\S+", re.IGNORECASE)
_IP_LINE      = re.compile(r"IP address:.*", re.IGNORECASE)


def _clean_text(raw: str) -> str:
    if not raw:
        return ""
    text = _PAGE_MARKER.sub("", raw)
    text = _IP_LINE.sub("", text)
    text = _HYPHEN_BREAK.sub("", text)        # re-join hyphenated line breaks
    text = _WHITESPACE.sub(" ", text)         # collapse all whitespace
    return text.strip()


# ── Junk filter ────────────────────────────────────────────────────────────────

def _filter_junk(page_records: list[dict]) -> dict:
    """
    Mutate page_records in place, dropping regions that are clearly chrome:

      1. Repeating text — same normalized text on >= JUNK_REPEAT_THRESHOLD
         pages (catches running headers like "P. T. Leat and T. R. Riley"
         and journal title bars on alternating pages)
      2. Watermark patterns — substring match against JUNK_PATTERNS regardless
         of word order (catches "Downloaded from..." reordered by PyMuPDF)
      3. Short text in page margins — text under JUNK_SHORT_MAX_CHARS sitting
         in the top JUNK_TOP_FRAC or bottom JUNK_BOTTOM_FRAC of the page
         (catches page numbers and small chrome surya missed)

    Image regions are never dropped here.

    Returns a stats dict {repeating, pattern, position}.
    """
    # Pass 1: build text→pages frequency map
    text_pages: dict[str, set[int]] = defaultdict(set)
    for page in page_records:
        page_num = page["page_number"]
        for region in page["regions"]:
            content = region.get("content", {})
            if content.get("type") != "text":
                continue
            text = (content.get("text") or "").strip()
            if not text:
                continue
            normalized = re.sub(r"\s+", " ", text.lower())
            text_pages[normalized].add(page_num)

    repeating = {
        text for text, pages in text_pages.items()
        if len(pages) >= JUNK_REPEAT_THRESHOLD
    }

    # Pass 2: filter
    stats = {"repeating": 0, "pattern": 0, "position": 0}

    for page in page_records:
        height_px = page.get("height_px")
        width_px  = page.get("width_px")
        kept: list[dict] = []
        for region in page["regions"]:
            content = region.get("content", {})

            # Image regions: drop small images sitting in the outer margins
            # (publisher logos, CrossMark emblems, "Check for Updates" badges).
            if content.get("type") == "image":
                bbox_px = region.get("bbox_px")
                if bbox_px and height_px and width_px:
                    ix0, iy0, ix1, iy1 = bbox_px
                    iw, ih = ix1 - ix0, iy1 - iy0
                    in_margin = (
                        ix1 < width_px  * JUNK_LEFT_FRAC  or
                        ix0 > width_px  * (1 - JUNK_RIGHT_FRAC) or
                        iy1 < height_px * JUNK_TOP_FRAC or
                        iy0 > height_px * (1 - JUNK_BOTTOM_FRAC)
                    )
                    if in_margin and iw * ih < JUNK_IMG_MAX_AREA:
                        stats["margin_img"] = stats.get("margin_img", 0) + 1
                        continue
                kept.append(region)
                continue

            text = (content.get("text") or "").strip()
            if not text:
                # Empty text region — keep so module_04 can skip it consistently
                kept.append(region)
                continue

            # 2a. Watermark substring match
            if JUNK_PATTERNS.search(text):
                stats["pattern"] += 1
                continue

            # 2b. Repeating text
            normalized = re.sub(r"\s+", " ", text.lower())
            if normalized in repeating:
                stats["repeating"] += 1
                continue

            # 2c. Short text in margins (top/bottom/left/right)
            if len(text) < JUNK_SHORT_MAX_CHARS and height_px:
                bbox_px = region.get("bbox_px")
                if bbox_px and len(bbox_px) == 4:
                    x_left, y_top, x_right, y_bot = bbox_px
                    in_top   = y_bot   < height_px * JUNK_TOP_FRAC
                    in_bot   = y_top   > height_px * (1 - JUNK_BOTTOM_FRAC)
                    in_left  = width_px and x_right < width_px * JUNK_LEFT_FRAC
                    in_right = width_px and x_left  > width_px * (1 - JUNK_RIGHT_FRAC)
                    if in_top or in_bot or in_left or in_right:
                        stats["position"] += 1
                        continue

            kept.append(region)

        page["regions"] = kept

    return stats


# ── Helpers ────────────────────────────────────────────────────────────────────

def _count_content(regions: list[dict]) -> tuple[int, int, int]:
    """Return (text_count, image_count, empty_count) for a page's regions."""
    tc = ic = ec = 0
    for r in regions:
        t = r.get("content", {}).get("type")
        if t == "image":
            ic += 1
        elif t == "text":
            if r["content"].get("is_empty"):
                ec += 1
            else:
                tc += 1
    return tc, ic, ec


def _progress(current: int, total: int) -> None:
    bar_len = 30
    filled  = int(bar_len * current / total)
    bar     = "█" * filled + "░" * (bar_len - filled)
    print(f"\r  [{bar}] {current}/{total}", end="", flush=True)


# ── CLI entry-point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python module_03_extractor.py <book_name>")
        sys.exit(1)

    extract_content(sys.argv[1])
