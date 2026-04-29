"""
module_04_epub.py — extraction manifest → .epub

Assembles the final epub from the extracted content:
  - Text regions   → HTML elements (h2, p, li, etc.) via LABEL_TO_HTML in config
  - Image regions  → embedded PNGs wrapped in a centred div
  - Chapter breaks → every Section-header starts a new epub chapter
  - TOC           → built automatically from chapter titles
  - List items    → consecutive List-item regions are grouped into a single <ul>

Usage (standalone):
    python module_04_epub.py mybook

Usage (as module):
    from module_04_epub import build_epub
    epub_path = build_epub("mybook")
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from ebooklib import epub

from config import EPUB_CSS, LABEL_TO_HTML, OUTPUT_DIR
from module_03_extractor import load_extraction_manifest


# ── Public API ─────────────────────────────────────────────────────────────────

def build_epub(book_name: str) -> Path:
    """
    Build an epub from the extraction manifest.

    Reads  : data/{book_name}/extraction_manifest.json
             data/{book_name}/regions/*.png
    Writes : output/{book_name}.epub

    Returns the path to the written epub file.
    """
    manifest   = load_extraction_manifest(book_name)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path   = OUTPUT_DIR / f"{book_name}.epub"

    print(f"\n[Module 04] Building epub for '{book_name}'")
    print(f"  Pages     : {manifest['page_count']}")
    print(f"  Output    : {out_path}")

    book = epub.EpubBook()
    book.set_identifier(f"pdf2epub_{book_name}")

    # Prefer a detected "Title" region for the EPUB title; fall back to filename
    detected_title = _find_document_title(manifest)
    book.set_title(detected_title or book_name)
    book.set_language("en")

    # Add shared CSS
    css_item = epub.EpubItem(
        uid="main_css",
        file_name="style/main.css",
        media_type="text/css",
        content=EPUB_CSS,
    )
    book.add_item(css_item)

    # Collect all regions across pages, in order, then split into chapters
    all_regions = _collect_regions(manifest)

    # Detect footnote/reference definitions before building chapters so we
    # can skip those regions during normal rendering and link refs correctly.
    fn_defs, fn_region_ids = _detect_footnotes(all_regions)
    fn_numbers = set(fn_defs.keys())
    # Populated during chapter rendering: {fn_num → first chap filename}
    fn_first_ref: dict[int, str] = {}

    chapters_data = _split_into_chapters(all_regions, book_name)

    print(f"  Chapters  : {len(chapters_data)}")
    if fn_defs:
        print(f"  Refs found: {len(fn_defs)}")

    # Add image files to epub, build a lookup: filename → epub item
    image_items = _add_images(book, all_regions)

    # Build epub chapter objects
    epub_chapters: list[epub.EpubHtml] = []

    for chap_index, chap in enumerate(chapters_data):
        chap_filename = f"chap_{chap_index:04d}.xhtml"
        html_body = _render_chapter_html(
            chap["regions"], image_items,
            fn_region_ids=fn_region_ids,
            fn_numbers=fn_numbers,
            fn_first_ref=fn_first_ref,
            chap_filename=chap_filename,
        )

        if not html_body.strip():
            continue

        chap_item = epub.EpubHtml(
            title    = chap["title"],
            file_name= chap_filename,
            lang     = "en",
        )
        chap_item.content = (
            f'<html xmlns:epub="http://www.idpf.org/2007/ops">'
            f'<head><title>{_esc(chap["title"])}</title></head>'
            f'<body>{html_body}</body></html>'
        )
        chap_item.add_item(css_item)
        book.add_item(chap_item)
        epub_chapters.append(chap_item)

    # Append footnotes/references chapter after all body chapters
    if fn_defs:
        fn_html  = _render_footnotes_html(fn_defs, fn_first_ref)
        fn_item  = epub.EpubHtml(title="References", file_name="footnotes.xhtml", lang="en")
        fn_item.content = (
            f'<html xmlns:epub="http://www.idpf.org/2007/ops">'
            f'<head><title>References</title></head>'
            f'<body>{fn_html}</body></html>'
        )
        fn_item.add_item(css_item)
        book.add_item(fn_item)
        epub_chapters.append(fn_item)

    if not epub_chapters:
        print("  WARNING: No content was generated. Check extraction results.")
        return out_path

    # Spine + TOC
    book.spine = ["nav"] + epub_chapters
    book.toc   = tuple(
        epub.Link(c.file_name, c.title, c.file_name.replace(".xhtml", ""))
        for c in epub_chapters
    )
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub.write_epub(str(out_path), book, {})
    print(f"  Done → {out_path}")
    return out_path


# ── Chapter splitting ──────────────────────────────────────────────────────────

def _collect_regions(manifest: dict) -> list[dict]:
    """Flatten all pages → single ordered list of regions, with per-page image dedup."""
    regions = []
    for page in manifest["pages"]:
        pw = page.get("width_px", 0)
        page_regions = _dedup_overlapping_images(page["regions"], pw)
        regions.extend(page_regions)
    return regions


def _dedup_overlapping_images(regions: list[dict], page_width_px: int) -> list[dict]:
    """
    Remove image regions whose effective crop bbox is substantially contained
    within a larger image region's effective crop bbox.

    Fixes the case where surya splits one equation into several overlapping
    Formula bboxes — our column-extension logic then produces near-identical
    crops for all of them.  We keep the one with the largest effective area.
    """
    img_idx = [i for i, r in enumerate(regions) if r.get("policy") == "image"]
    if len(img_idx) <= 1:
        return regions

    # Pre-compute effective crop bboxes (mirrors _extract_image extension logic)
    effective: dict[int, tuple] = {
        i: _effective_crop_bbox(regions[i], page_width_px) for i in img_idx
    }

    to_drop: set[int] = set()
    for i in img_idx:
        if i in to_drop:
            continue
        ex0, ey0, ex1, ey1 = effective[i]
        e_area = max(1, (ex1 - ex0) * (ey1 - ey0))

        for j in img_idx:
            if i == j or j in to_drop:
                continue
            jx0, jy0, jx1, jy1 = effective[j]
            j_area = (jx1 - jx0) * (jy1 - jy0)

            ix0 = max(ex0, jx0);  iy0 = max(ey0, jy0)
            ix1 = min(ex1, jx1);  iy1 = min(ey1, jy1)
            if ix1 <= ix0 or iy1 <= iy0:
                continue

            inter = (ix1 - ix0) * (iy1 - iy0)
            # Drop i if 65%+ of it is inside j AND j is at least as large
            if inter / e_area > 0.65 and j_area >= e_area:
                to_drop.add(i)
                break

    return [r for i, r in enumerate(regions) if i not in to_drop]


def _effective_crop_bbox(region: dict, page_width_px: int) -> tuple:
    """
    Return the actual crop bbox that _extract_image would use for this region,
    including the formula-extension logic (mirrors module_03_extractor).
    """
    x0, y0, x1, y1 = region["bbox_px"]
    if region.get("label") == "Formula" and page_width_px:
        formula_w = x1 - x0
        if formula_w >= page_width_px * 0.45:
            x0, x1 = 0, page_width_px
        else:
            mid_x = page_width_px // 2
            cx = (x0 + x1) / 2
            x0 = 0      if cx < mid_x else mid_x
            x1 = mid_x  if cx < mid_x else page_width_px
    return (x0, y0, x1, y1)


def _split_into_chapters(regions: list[dict], book_name: str) -> list[dict]:
    """
    Split the flat region list into chapters.

    A new chapter begins at every Section-header. Content before the first
    Section-header goes into a chapter named after the book.

    Returns a list of:
        {"title": str, "regions": [region, ...]}
    """
    chapters: list[dict] = []
    current: dict = {"title": book_name.replace("_", " ").title(), "regions": []}

    for region in regions:
        content = region.get("content", {})

        is_header = region["label"] in ("Section-header", "SectionHeader")

        if (
            is_header
            and content.get("type") == "text"
            and content.get("text")
        ):
            # Save current chapter if it has any actual content
            if _chapter_has_content(current):
                chapters.append(current)

            current = {
                "title":   _truncate(content["text"], 80),
                "regions": [region],   # include the header itself in its chapter
            }
        else:
            current["regions"].append(region)

    # Don't forget the last chapter
    if _chapter_has_content(current):
        chapters.append(current)

    return chapters


def _chapter_has_content(chapter: dict) -> bool:
    """True if the chapter has at least one non-empty, non-header region."""
    for r in chapter["regions"]:
        c = r.get("content", {})
        if c.get("type") == "image":
            return True
        if c.get("type") == "text" and not c.get("is_empty") and c.get("text"):
            return True
    return False


# ── Image registration ─────────────────────────────────────────────────────────

def _add_images(book: epub.EpubBook, regions: list[dict]) -> dict[str, epub.EpubItem]:
    """
    Read every image crop and register it with the epub book.
    Returns a dict mapping filename → EpubItem for use in HTML rendering.
    """
    items: dict[str, epub.EpubItem] = {}

    for region in regions:
        content = region.get("content", {})
        if content.get("type") != "image":
            continue

        filename = content["image_filename"]
        if filename in items:
            continue   # already added (shouldn't happen but be safe)

        img_path = Path(content["image_path"])
        if not img_path.exists():
            continue

        img_data = img_path.read_bytes()
        item = epub.EpubItem(
            uid        = f"img_{filename}",
            file_name  = f"images/{filename}",
            media_type = "image/png",
            content    = img_data,
        )
        book.add_item(item)
        items[filename] = item

    return items


# ── HTML rendering ─────────────────────────────────────────────────────────────

def _render_chapter_html(
    regions: list[dict],
    image_items: dict[str, epub.EpubItem],
    fn_region_ids: set[str] | None = None,
    fn_numbers: set[int] | None = None,
    fn_first_ref: dict[int, str] | None = None,
    chap_filename: str = "",
) -> str:
    """
    Convert a list of regions into an HTML string for one epub chapter.

    Consecutive List-item regions are automatically grouped into a <ul>.
    Empty text regions are silently skipped.
    Footnote definition regions (fn_region_ids) are skipped — they go into
    the dedicated footnotes chapter instead.
    [N] citation markers in body text are replaced with epub:type="noteref"
    links when fn_numbers / fn_first_ref are supplied.
    """
    fn_region_ids = fn_region_ids or set()
    fn_numbers    = fn_numbers    or set()

    parts: list[str] = []
    pending_list_items: list[str] = []

    def flush_list() -> None:
        if pending_list_items:
            parts.append("<ul>")
            parts.extend(pending_list_items)
            parts.append("</ul>")
            pending_list_items.clear()

    for region in regions:
        # Skip regions that are footnote definitions — they go into footnotes.xhtml
        if region.get("region_id") in fn_region_ids:
            continue

        label   = region["label"]
        content = region.get("content", {})

        # ── Image region ───────────────────────────────────────────────────────
        if content.get("type") == "image":
            flush_list()
            filename = content.get("image_filename", "")
            if filename and filename in image_items:
                alt = label.lower().replace("_", " ")
                parts.append(
                    f'<figure class="visual">'
                    f'<img src="images/{filename}" alt="{_esc(alt)}" />'
                    f'</figure>'
                )
            continue

        # ── Text region ────────────────────────────────────────────────────────
        if content.get("type") != "text":
            continue
        if content.get("is_empty") or not content.get("text"):
            continue

        raw = content["text"]
        # Inject interactive footnote links where [N] appears
        text = _inject_fn_refs(raw, fn_numbers, fn_first_ref, chap_filename)

        # List items: buffer them, flush when the run ends
        if label in ("List-item", "ListItem"):
            pending_list_items.append(f"  <li>{text}</li>")
            continue

        flush_list()

        # Section-header → h2 (handle both surya API formats)
        if label in ("Section-header", "SectionHeader"):
            parts.append(f"<h2>{text}</h2>")
            continue

        # All other text labels → look up tag in config
        tag = LABEL_TO_HTML.get(label, "p")
        parts.append(f"<{tag}>{text}</{_base_tag(tag)}>")

    flush_list()   # catch any trailing list items

    return "\n".join(parts)


# ── Utilities ──────────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    """Minimal HTML escaping — only what's strictly needed in element content."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _base_tag(tag: str) -> str:
    """Extract base tag name from something like 'p class=\\'footnote\\''."""
    return tag.split()[0]


def _truncate(text: str, max_len: int) -> str:
    """Truncate a string to max_len chars, adding ellipsis if needed."""
    return text if len(text) <= max_len else text[:max_len - 1] + "…"


def _find_document_title(manifest: dict) -> str | None:
    """
    Return the first non-empty 'Title' region's text from page 1 only.

    Limiting to page 1 avoids matching chapter headings deeper in the document.
    A minimum length of 15 chars rejects single-word false positives.
    """
    pages = manifest["pages"]
    for page in pages[:1]:  # page 1 only
        for region in page["regions"]:
            if region.get("label") != "Title":
                continue
            content = region.get("content", {})
            if content.get("type") == "text":
                text = (content.get("text") or "").strip()
                if len(text) >= 15:
                    return text
    return None


# ── Footnote / reference handling ─────────────────────────────────────────────

# Matches bare numeric citations in body text: [1], [2], [10], ...
_FN_REF_RE   = re.compile(r'\[(\d+)\]')
# A text region is a footnote definition if its content starts with [N]
_FN_DEF_START = re.compile(r'^\s*\[\d+\]')


def _detect_footnotes(
    all_regions: list[dict],
) -> tuple[dict[int, str], set[str]]:
    """
    Scan every text region and collect those whose content starts with [N].
    These are treated as reference/footnote definitions.

    Returns:
        fn_defs        — {number: definition_text}
        fn_region_ids  — region_ids of definition regions (skipped in body chapters)
    """
    fn_defs:       dict[int, str] = {}
    fn_region_ids: set[str]       = set()

    for region in all_regions:
        content = region.get("content", {})
        if content.get("type") != "text" or not content.get("text"):
            continue
        text = content["text"].strip()
        if not _FN_DEF_START.match(text):
            continue
        entries = _parse_fn_def_region(text)
        if entries:
            fn_region_ids.add(region["region_id"])
            for n, fn_text in entries:
                if n not in fn_defs:
                    fn_defs[n] = fn_text

    return fn_defs, fn_region_ids


def _parse_fn_def_region(text: str) -> list[tuple[int, str]]:
    """
    Split a text block that starts with [N] into (number, text) pairs.
    Handles regions that contain multiple consecutive definitions.
    """
    # Split at each [N] boundary, keeping the delimiter via lookahead
    parts = re.split(r'(?=\[\d+\]\s)', text.strip())
    result = []
    for part in parts:
        part = part.strip()
        m = re.match(r'^\[(\d+)\]\s*(.+)', part, re.DOTALL)
        if m:
            result.append((int(m.group(1)), m.group(2).strip()))
    return result


def _inject_fn_refs(
    text: str,
    fn_numbers: set[int],
    fn_first_ref: dict[int, str] | None,
    chap_filename: str,
) -> str:
    """
    Replace [N] citation markers with interactive epub:type="noteref" links.

    Only linkifies Ns that have a known definition (fn_numbers).
    The first occurrence of each [N] also gets an id="fnref-N" anchor so the
    footnotes chapter can link back to it.
    Handles HTML escaping for all non-[N] text segments.
    """
    if not fn_numbers:
        return _esc(text)

    parts: list[str] = []
    last = 0

    for m in _FN_REF_RE.finditer(text):
        n = int(m.group(1))
        # Escape everything before this match
        parts.append(_esc(text[last:m.start()]))
        last = m.end()

        if n not in fn_numbers:
            parts.append(_esc(m.group(0)))
            continue

        # Record first chapter where this ref appears (for back-links)
        if fn_first_ref is not None and n not in fn_first_ref:
            fn_first_ref[n] = chap_filename
            anchor = f' id="fnref-{n}"'
        else:
            anchor = ""

        parts.append(
            f'<a{anchor} class="fn-ref"'
            f' href="footnotes.xhtml#fn-{n}"'
            f' epub:type="noteref">[{n}]</a>'
        )

    parts.append(_esc(text[last:]))
    return "".join(parts)


def _render_footnotes_html(
    fn_defs: dict[int, str],
    fn_first_ref: dict[int, str],
) -> str:
    """Build the HTML body for the dedicated References chapter."""
    parts = ["<h2>References</h2>"]
    for n in sorted(fn_defs.keys()):
        ref_file = fn_first_ref.get(n, "")
        if ref_file:
            back = (
                f'<a class="fn-back" href="{ref_file}#fnref-{n}"'
                f' epub:type="backlink">&#8593;</a> '
            )
        else:
            back = ""
        entry_text = _esc(fn_defs[n])
        parts.append(
            f'<aside id="fn-{n}" class="fn-entry" epub:type="footnote">'
            f'<p>{back}<strong>[{n}]</strong> {entry_text}</p>'
            f'</aside>'
        )
    return "\n".join(parts)


# ── CLI entry-point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python module_04_epub.py <book_name>")
        sys.exit(1)

    build_epub(sys.argv[1])
