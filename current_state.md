# Current State — 2026-04-27

Live handoff doc. Read this at the start of any session continuing pipeline work. The durable architecture/rules live in `CLAUDE.md` — this file is just "where we left off."

## What works end-to-end

- **module_01 (render)** — PDF → 200dpi PNGs. Resume reads PNG dims from disk (no re-render).
- **module_02 (layout)** — surya 0.6.0 layout detection works; reading order is pure geometry (`_column_aware_sort`), surya ordering model dropped entirely. `LAYOUT_VERSION = 2` auto-invalidates stale caches.
- **module_03 (extract)** — word-level PyMuPDF extraction with center-point filtering; junk filter (frequency + pattern + position). Now stamps `source_layout_version` on each extraction page JSON — stale extractions auto-invalidate when layout version bumps.
- **module_04 (epub)** — `<figure class="visual">` with float:none CSS. `_find_document_title()` scans first non-empty Title region for book name.
- **pipeline.py** — accepts a path OR a bare book name. `--force` reprocesses.

## Last session's fixes (2026-04-27, evening)

1. **Extraction cache staleness bug fixed** — module_03 was reusing stale per-page extraction JSONs from before the geometry sort fix. The junk-filter re-write pass was touching the timestamps (making them look fresh) while leaving old `reading_order` values baked in. Fixed by adding `source_layout_version` to each extraction page record; resume now invalidates if it doesn't match `LAYOUT_VERSION`.
2. **Confirmed correct reading order in EPUB** — after deleting stale extraction cache and re-running modules 03+04, page 2 now correctly outputs: image → figure caption ("Fig. 1.") → left col body → right col body.

## Open issues — punch list

**High value, probably quick:**
1. **Book name shows as "chapter"** — `_find_document_title()` in module_04 is matching the wrong region. Need to inspect what Title regions surya is producing and why the wrong one wins.
2. **Module_02 loads surya before checking cache** — if all layout pages are already cached (version matches), it still calls `_load_surya()` and crashes if surya's API has drifted. Should short-circuit: check cache first, skip model load if everything is already done.
3. **Manual junk-image deletion mechanism** — for recurring crap like "Check for Updates" badges. Proposed: `data/<book>/excluded_regions.json` + small CLI helper that lists regions, lets you mark some as excluded, persists, and is honored by module_03.

**Bigger features:**
4. **Footnotes** — clickable markers in body, footnote text pulled to chapter end with back-links.
5. **Cross-references** — "see Fig. 3" → anchor links to the figure.
6. **Parenthesis-to-footnote** (deferred, opt-in only) — long parentheticals (>100 char) optionally promoted to footnotes.

## Known unknowns

- Has the second test paper (`Smellie_et_al_2021_AntSci_KGI_Eocene_strat.pdf`) been run end-to-end since the recent fixes? Verify before assuming the pipeline generalizes.
- Does the geometry sort handle the rare case of a sidebar/callout box embedded in body text? Not exercised by current corpus.
- Page 7 has a figure spanning 65% width (just over the 60% full-width threshold) with a right-column caption inside its y-range. The ordering is technically correct (left col then right col) but may feel jarring if the right-column caption relates to the figure. Worth checking in the EPUB.

## Sanity-check commands

```bash
# Run modules 03+04 only (layout already cached, surya not needed)
PYTHONIOENCODING=utf-8 python module_03_extractor.py LeatandRiley2021Petrology31b
PYTHONIOENCODING=utf-8 python module_04_epub.py LeatandRiley2021Petrology31b

# Full pipeline (note: use direct PDF path to avoid the → unicode crash on Windows)
PYTHONIOENCODING=utf-8 python pipeline.py books/LeatandRiley2021Petrology31b.pdf

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
