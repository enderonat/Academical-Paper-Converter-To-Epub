# Current State — 2026-04-29

Live handoff doc. Read this at the start of any session continuing pipeline work. The durable architecture/rules live in `CLAUDE.md` — this file is just "where we left off."

## What works end-to-end

- **module_01 (render)** — PDF → 200dpi PNGs. Stores `rotation` (0/90/180/270) per page in manifest.
- **module_02 (layout)** — surya 0.6.0 layout detection; `_column_aware_sort` reading order. Rotated pages (90°/270°) skip surya entirely and get a single full-page Figure region.
- **module_03 (extract)** — word-level PyMuPDF extraction with center-point filtering. Junk filter: frequency + pattern (including "check for updates", "crossmark") + top/bottom/left/right margin filter for short text and small images (< 200×200 px). Formula crops extend to full column/page width to prevent cut-offs.
- **module_04 (epub)** — `<figure class="visual">` with float:none CSS. `_find_document_title()` page-1 only, min 15 chars, fallback = raw book_name. Duplicate image dedup: regions whose effective crop bbox is 65%+ inside a larger region's crop are dropped before rendering (fixes surya's formula fragmentation). Interactive footnotes: `[N]` in body text becomes `epub:type="noteref"` links; definitions detected at start of text regions, collected into `footnotes.xhtml` with `↑` back-links.
- **pipeline.py** — accepts a path OR a bare book name. `--force` reprocesses.

## This session's work (2026-04-29)

### Quality fixes
1. **Sideways page handling** — module_01 stores `page.rotation`; module_02 replaces surya output with a single full-page Figure for 90°/270° pages.
2. **EPUB title** — `_find_document_title` scans page 1 only, requires ≥15 chars. Fallback = raw `book_name` (no `.title()` mangling). Fixes "shows as chapter" bug.
3. **Outer margin filter** — left/right 5% added (`JUNK_LEFT_FRAC`, `JUNK_RIGHT_FRAC`). Short text and small images in any outer margin get dropped (CrossMark, publisher logos, corner badges).
4. **Junk patterns** — added `check\s+for\s+updates` and `crossmark` to JUNK_PATTERNS.
5. **Formula crop extension** — Formula regions extended to full column (or full page if > 45% width) to prevent cut-off equations. Fixes math-heavy papers like 2510.22216v1.
6. **Duplicate formula fix** — `_dedup_overlapping_images` + `_effective_crop_bbox` in module_04 drops image regions whose effective crop (after extension) is 65%+ inside a larger region. Confirmed: page 5 of 1-s2.0-S2950555025000485-main goes from 11 → 4 image regions.

### New feature
7. **Interactive footnotes/references** — `_detect_footnotes` scans all text regions for those starting with `[N]`; marks them as definition regions. Body text gets `[N]` → `<a epub:type="noteref">` links to `footnotes.xhtml#fn-N`. First occurrence per number gets `id="fnref-N"` for back-linking. References chapter appended to TOC with `<aside epub:type="footnote">` entries and `↑` back-links. Kobo shows as side panel; Kindle navigates (cross-file pop-ups not possible). CSS: `.fn-ref`, `.fn-entry`, `.fn-back`.

## Test corpus (books/)

- `LeatandRiley2021Petrology31b.pdf` — primary, 14-page two-column geology paper. Pipeline fully verified.
- `Smellie_et_al_2021_AntSci_KGI_Eocene_strat.pdf` — secondary. **Not re-run since geometry sort fix** — needs a verification pass.
- `2510.22216v1.pdf` — math-heavy arXiv preprint. **Not yet run** — tests formula extension + margin filter.
- `1-s2.0-S2950555025000485-main.pdf` — Elsevier paper used to diagnose duplicate formula bug. Layout cached, extraction needs re-run to pick up dedup fix.
- `1-s2.0-S259014861930007X-main.pdf` — second Elsevier paper. Layout cached, not fully tested.

## Open issues — punch list

**High value, probably quick:**
1. **Module_02 loads surya before checking cache** — if all pages are cached (version matches), still calls `_load_surya()` unnecessarily. Should short-circuit: check all pages cached first, skip model load if so.
2. **Manual junk exclusion** — for recurring junk the auto filter misses. Proposed: `data/<book>/excluded_regions.json` + small CLI helper that lists regions by page and lets you mark some as excluded.
3. **Run verification passes** — re-run Smellie and 1-s2.0-S2950555025000485-main through module_04 to verify all fixes look correct in EPUB.

**Bigger features:**
4. **Cross-references** — "see Fig. 3" / "Table 2" → anchor links to the figure/table.
5. **Footnote detection robustness** — current detector requires region to START with `[N]`. Papers that pack multiple references into a block like "1. Smith... 2. Jones..." (period-separated, not bracket) would be missed.

## Known caveats

- **Rotation fix + cached layout**: rotation handling in module_02 only applies on fresh layout detection. Papers already in cache with a rotated page: clear that page's layout JSON or use `--force`.
- **Footnote detection**: only catches `[N]`-format citations (bracket-number). Author-year citations (Smith, 2020) are not linked.
- **Formula dedup threshold (65%)**: might be slightly aggressive for FigureGroups that contain genuinely different sub-images at similar positions. Tune if needed.

## Sanity-check commands

```bash
# Re-run just EPUB assembly (no surya, no extraction — just re-builds the .epub)
PYTHONIOENCODING=utf-8 python module_04_epub.py LeatandRiley2021Petrology31b
PYTHONIOENCODING=utf-8 python module_04_epub.py 1-s2.0-S2950555025000485-main

# Re-run extraction + epub (layout cached, surya not needed)
PYTHONIOENCODING=utf-8 python module_03_extractor.py LeatandRiley2021Petrology31b
PYTHONIOENCODING=utf-8 python module_04_epub.py LeatandRiley2021Petrology31b

# Full pipeline on new papers (first time, surya runs)
PYTHONIOENCODING=utf-8 python pipeline.py books/2510.22216v1.pdf
PYTHONIOENCODING=utf-8 python pipeline.py books/1-s2.0-S2950555025000485-main.pdf

# Force re-detect layout (e.g. to apply rotation fix to a cached paper)
PYTHONIOENCODING=utf-8 python pipeline.py books/LeatandRiley2021Petrology31b.pdf --force

# Nuclear clear — extraction only (layout is fine, keep it)
del data\1-s2.0-S2950555025000485-main\extraction\*.json
del data\1-s2.0-S2950555025000485-main\extraction_manifest.json

# Nuclear clear — everything for a book
rmdir /s /q data\LeatandRiley2021Petrology31b\layout
rmdir /s /q data\LeatandRiley2021Petrology31b\extraction
del data\LeatandRiley2021Petrology31b\layout_manifest.json
del data\LeatandRiley2021Petrology31b\extraction_manifest.json
del output\LeatandRiley2021Petrology31b.epub
```
