# Current State — 2026-04-29

Live handoff doc. Read this at the start of any session continuing pipeline work. The durable architecture/rules live in `CLAUDE.md` — this file is just "where we left off."

## What works end-to-end

- **module_01 (render)** — PDF → 200dpi PNGs. Now stores `rotation` (0/90/180/270) per page.
- **module_02 (layout)** — surya 0.6.0 layout detection; `_column_aware_sort` reading order. Rotated pages (90°/270°) skip surya entirely and get a single full-page Figure region.
- **module_03 (extract)** — word-level PyMuPDF extraction with center-point filtering; junk filter (frequency + pattern + position). Formula crops now extend to full column/page width to prevent cut-offs.
- **module_04 (epub)** — `<figure class="visual">` with float:none CSS. `_find_document_title()` limited to page 1 only, min 15 chars.
- **pipeline.py** — accepts a path OR a bare book name. `--force` reprocesses.

## This session's additions (2026-04-29, later)

1. **Sideways page handling** — module_01 stores `page.rotation` in manifest; module_02 detects 90°/270° rotated pages and replaces surya output with a single full-page Figure region (clean, no coordinate mess).
2. **EPUB title fixed** — `_find_document_title` now only searches page 1 and requires ≥15 chars. Fallback uses raw `book_name`. Kills the "shows as chapter" bug.
3. **Left/right margin filter added** — `JUNK_LEFT_FRAC = 0.05`, `JUNK_RIGHT_FRAC = 0.05` added. Short text AND small images (< 200×200px) in the outer 5% margins on any side are dropped. Catches CrossMark emblems, publisher logos, corner badges.
4. **"Check for updates" / CrossMark patterns** — added `check\s+for\s+updates` and `crossmark` to JUNK_PATTERNS.
5. **Formula crop extension** — Formula regions now extend to full column width (or full page width if formula > 45% page width). Prevents equations being cut off at column boundaries in math-heavy papers.

6. **Interactive footnotes/references** — module_04 now detects text regions that start with `[N]` as reference definitions, collects them into a dedicated `footnotes.xhtml` chapter (shown as "References" in TOC), and replaces every `[N]` in body text with an `epub:type="noteref"` link. Each footnote entry has an `↑` back-link to the first occurrence in the body. CSS added for `.fn-ref` (superscript blue), `.fn-entry` (left-border aside), `.fn-back`. Works on Kobo; Kindle renders cross-file footnotes as regular navigation links (pop-ups require same-file footnotes, an acceptable trade-off for end-of-book reference lists).

## New test corpus

- `2510.22216v1.pdf` — math-heavy arXiv preprint added to `books/`. Not yet run through pipeline; this session's fixes target its known issues.

## Open issues — punch list

**High value, probably quick:**
1. **Module_02 loads surya before checking cache** — still present. If all layout pages are already cached (version matches), it still calls `_load_surya()` and would crash if surya's API drifted. Should short-circuit before loading the model.
2. **Manual junk-image deletion mechanism** — for recurring crap the automatic filter misses. Proposed: `data/<book>/excluded_regions.json` + small CLI helper.
3. **Run 2510.22216v1 through the full pipeline** — verify formula handling, sideways pages, margin filter. Check EPUB output quality.
4. **Verify Smellie paper** — hasn't been run since the geometry sort fix.

**Bigger features:**
5. **Footnotes** — clickable markers in body, footnote text pulled to chapter end with back-links.
6. **Cross-references** — "see Fig. 3" → anchor links to the figure.

## Known unknowns

- `2510.22216v1.pdf`: exact page with sideways content unknown. If rotation stored in manifest but layout was already cached from a prior run, use `--force` to re-detect.
- For papers already in the cache: rotation fix only applies to newly-detected pages. Use `--force` or clear the layout cache to trigger re-detection on previously processed papers.
- Page numbers rendered as formula images: if they're small AND in the bottom margin, the new margin-image filter should drop them. If they fall just inside the margin threshold, they'd still appear as images — acceptable for now.

## Sanity-check commands

```bash
# Run full pipeline on math paper (first time)
PYTHONIOENCODING=utf-8 python pipeline.py books/2510.22216v1.pdf

# Run modules 03+04 only (layout already cached, surya not needed)
PYTHONIOENCODING=utf-8 python module_03_extractor.py LeatandRiley2021Petrology31b
PYTHONIOENCODING=utf-8 python module_04_epub.py LeatandRiley2021Petrology31b

# Full pipeline (use direct PDF path to avoid → unicode crash on Windows)
PYTHONIOENCODING=utf-8 python pipeline.py books/LeatandRiley2021Petrology31b.pdf

# Force re-detect layout (e.g. after a sideways-page fix)
PYTHONIOENCODING=utf-8 python pipeline.py books/LeatandRiley2021Petrology31b.pdf --force

# Nuclear cache clear (extraction only — layout is fine)
del data\LeatandRiley2021Petrology31b\extraction\*.json
del data\LeatandRiley2021Petrology31b\extraction_manifest.json

# Nuclear clear everything
rmdir /s /q data\LeatandRiley2021Petrology31b\layout
del data\LeatandRiley2021Petrology31b\layout_manifest.json
del data\LeatandRiley2021Petrology31b\extraction_manifest.json
rmdir /s /q data\LeatandRiley2021Petrology31b\extraction
del output\LeatandRiley2021Petrology31b.epub
```
