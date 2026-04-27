"""
config.py — single source of truth for all pipeline settings.
Every module imports from here. Change things in one place.
"""

from pathlib import Path

# ── Directory Layout ───────────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).parent
BOOKS_DIR   = BASE_DIR / "books"       # drop your input PDFs here
DATA_DIR    = BASE_DIR / "data"        # intermediate outputs per book
OUTPUT_DIR  = BASE_DIR / "output"      # final .epub files land here

# Per-book data sub-folders (relative to DATA_DIR / book_name)
PAGES_SUBDIR   = "pages"     # rendered PNG images  (module 01)
LAYOUT_SUBDIR  = "layout"    # surya region JSON    (module 02)
REGIONS_SUBDIR = "regions"   # cropped region images (module 03)

# ── Rendering (module 01) ──────────────────────────────────────────────────────

RENDER_DPI = 200  # surya works well at 150-200. Higher = slower but more accurate.

# ── Layout Detection (module 02) ───────────────────────────────────────────────

# How to handle each surya label in the epub
# "image"  → crop the region from the page, embed as PNG
# "text"   → extract text from PDF within the bbox, emit as HTML
# "skip"   → discard entirely (running headers, page numbers, etc.)

LABEL_POLICY: dict[str, str] = {
    # ── Visual regions → always crop as PNG, never touch text extraction ──
    "Picture":          "image",
    "Figure":           "image",
    "Table":            "image",
    "Formula":          "image",
    "FigureGroup":      "image",
    "TableOfContents":  "image",
    "Form":             "image",

    # ── Text regions → extract text from PDF ─────────────────────────────
    "Text":             "text",
    "TextInlineMath":   "text",
    "Title":            "text",

    # Surya old API style (hyphenated)
    "Section-header":   "text",
    "Caption":          "text",
    "List-item":        "text",
    "Footnote":         "text",

    # Surya new API style (CamelCase) — same labels, different format
    "SectionHeader":    "text",
    "ListItem":         "text",

    # ── Skip — running headers/footers add no reading value ──────────────
    "Page-header":      "skip",
    "Page-footer":      "skip",
    "PageHeader":       "skip",
    "PageFooter":       "skip",
}

# Minimum region size in pixels — regions smaller than this are discarded
MIN_REGION_PX = 10

# ── EPUB Assembly (module 04) ──────────────────────────────────────────────────

# Surya label → HTML tag for text regions
LABEL_TO_HTML: dict[str, str] = {
    # Old surya API (hyphenated)
    "Section-header": "h2",
    "Caption":        "p class='caption'",
    "Footnote":       "p class='footnote'",
    "List-item":      "li",
    "Text":           "p",
    "TextInlineMath": "p",

    # New surya API (CamelCase)
    "SectionHeader":  "h2",
    "ListItem":       "li",

    # Document title (papers, articles) — render as top-level heading
    "Title":          "h1",
}

EPUB_CSS = """
body              { font-family: Georgia, serif; line-height: 1.7; margin: 1em 2em; }
h1                { font-size: 1.6em; margin-top: 0.5em; margin-bottom: 0.8em;
                    text-align: center; clear: both; page-break-before: always; }
h2                { font-size: 1.3em; margin-top: 1.8em; margin-bottom: 0.4em;
                    border-bottom: 1px solid #ccc; padding-bottom: 4px;
                    clear: both; page-break-after: avoid; }
p                 { margin: 0.5em 0; text-align: justify; }
p.caption         { font-size: 0.85em; color: #444; text-align: center;
                    margin-top: 0.2em; font-style: italic; clear: both; }
p.footnote        { font-size: 0.8em; color: #666; border-top: 1px solid #eee;
                    padding-top: 0.3em; margin-top: 1em; clear: both; }
li                { margin: 0.3em 0; }

/* Images: force block layout, no float, no wrap. Some e-readers (especially
   Kindle's older renderers) default <img> to float:left when narrower than
   the column — explicit float:none + display:block + clear:both prevents
   text from wrapping next to figures. page-break-inside avoids splitting
   a figure across pages. */
figure.visual     { display: block; margin: 1.2em 0; text-align: center;
                    clear: both; float: none; page-break-inside: avoid; }
figure.visual img { display: block; max-width: 100%; height: auto;
                    margin: 0 auto; float: none; }

/* Backward compat for any old chapter HTML still using <div class="visual">. */
div.visual        { display: block; margin: 1.2em 0; text-align: center;
                    clear: both; float: none; page-break-inside: avoid; }
div.visual img    { display: block; max-width: 100%; height: auto;
                    margin: 0 auto; float: none; }
"""
