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

import sys
from pathlib import Path

from ebooklib import epub

from config import DATA_DIR, EPUB_CSS, LABEL_TO_HTML, OUTPUT_DIR
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
    fallback_title = book_name.replace("_", " ").replace("-", " ").title()
    book.set_title(detected_title or fallback_title)
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
    chapters_data = _split_into_chapters(all_regions, book_name)

    print(f"  Chapters  : {len(chapters_data)}")

    # Add image files to epub, build a lookup: filename → epub item
    image_items = _add_images(book, all_regions)

    # Build epub chapter objects
    epub_chapters: list[epub.EpubHtml] = []

    for chap_index, chap in enumerate(chapters_data):
        html_body = _render_chapter_html(chap["regions"], image_items)

        if not html_body.strip():
            continue

        chap_item = epub.EpubHtml(
            title    = chap["title"],
            file_name= f"chap_{chap_index:04d}.xhtml",
            lang     = "en",
        )
        chap_item.content = (
            f'<html><head><title>{_esc(chap["title"])}</title></head>'
            f'<body>{html_body}</body></html>'
        )
        chap_item.add_item(css_item)
        book.add_item(chap_item)
        epub_chapters.append(chap_item)

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
    """Flatten all pages → single ordered list of regions."""
    regions = []
    for page in manifest["pages"]:
        regions.extend(page["regions"])
    return regions


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
) -> str:
    """
    Convert a list of regions into an HTML string for one epub chapter.

    Consecutive List-item regions are automatically grouped into a <ul>.
    Empty text regions are silently skipped.
    """
    parts: list[str] = []
    pending_list_items: list[str] = []

    def flush_list() -> None:
        if pending_list_items:
            parts.append("<ul>")
            parts.extend(pending_list_items)
            parts.append("</ul>")
            pending_list_items.clear()

    for region in regions:
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

        text = _esc(content["text"])

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
    """Return the first non-empty 'Title' region's text, if any (else None)."""
    for page in manifest["pages"]:
        for region in page["regions"]:
            if region.get("label") != "Title":
                continue
            content = region.get("content", {})
            if content.get("type") == "text" and content.get("text"):
                return content["text"].strip()
    return None


# ── CLI entry-point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python module_04_epub.py <book_name>")
        sys.exit(1)

    build_epub(sys.argv[1])
