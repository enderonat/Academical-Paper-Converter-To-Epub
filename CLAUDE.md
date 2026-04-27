# Thesis: PDF → EPUB Pipeline

Convert academic research papers (multi-column, math/figure-heavy) into clean EPUBs readable on Kindle/Kobo e-ink devices.

## Hard rules — do not relitigate

- **Math, figures, tables = images. Always.** Never extract them as text. Six months in, this is locked. Any region with label `Picture | Figure | Table | Formula | FigureGroup | TableOfContents | Form` is cropped to PNG, never parsed.
- **surya-ocr is pinned to 0.6.0.** Do not upgrade. 0.7+ changes the API and 0.17.1 detects only `Text` (broken). The pipeline is built around 0.6.0's split-model API.
- **Resume support is mandatory.** Every module skips already-processed pages by checking output existence. Any new code must preserve this.

## Architecture (4-module pipeline)

```
PDF
 ↓ module_01_renderer.py    PDF → 200dpi PNGs + manifest.json   (PyMuPDF/fitz)
 ↓ module_02_layout.py      pages → region JSONs                 (surya 0.6.0 + geometry sort)
 ↓ module_03_extractor.py   text regions → words; image regions → PNG crops
 ↓ module_04_epub.py        assemble EPUB                        (ebooklib)
EPUB
```

Single source of truth: `config.py` (LABEL_POLICY, LABEL_TO_HTML, EPUB_CSS, JUNK_PATTERNS, paths).
Orchestrator: `pipeline.py` (accepts a path *or* a bare book name; looks up `books/<name>.pdf`).

### Module responsibilities — keep them clean

- **module_03** = content extraction + cleanup (junk filter lives here).
- **module_04** = pure assembly. Do NOT add cleanup logic in module_04.

## File layout

```
project/
  books/{book}.pdf                 ← input
  data/{book}/
    manifest.json                  ← module 01
    pages/page_NNNN.png            ← module 01
    layout/page_NNNN.json          ← module 02
    layout_manifest.json           ← module 02
    extraction/page_NNNN.json      ← module 03
    regions/*.png                  ← module 03 (image crops)
  output/{book}.epub               ← module 04
```

## surya 0.6.0 — API gotchas

```python
# Model + processor BOTH live in model.py (not processor.py)
from surya.model.detection.model import load_model, load_processor
from surya.settings import settings

checkpoint = settings.LAYOUT_MODEL_CHECKPOINT
model      = load_model(checkpoint=checkpoint)
processor  = load_processor(checkpoint=checkpoint)

# Inference
from surya.layout import batch_layout_detection
results = batch_layout_detection(images, model, processor)
```

`LayoutPredictor` does NOT exist in 0.6.0 — that's 0.7+. `module_02_layout._load_surya()` already handles the version dance defensively; touch with care.

### `LayoutBox` attribute fallbacks

API varies between minor versions, so `_parse_regions` defensively reads:
- `result.bboxes` | `result.layout_boxes` | `list(result)`
- `bbox_obj.bbox = [x0,y0,x1,y1]` (old) or `bbox_obj.polygon = [[x,y]×4]` (new)
- `bbox_obj.position` | `bbox_obj.order` | enumerate-index

## Reading order: pure geometry, not ML

We dropped surya's ordering model entirely (sdpa load errors + 200MB extra weight). `module_02_layout._column_aware_sort` handles 1- and 2-column academic layouts:

- Regions wider than 60% of page width → "full-width" separators.
- Within each band between full-width regions: emit left column top→bottom, then right column top→bottom.
- Takes `page_width_px` from `page_meta` — **never** estimate page width from region bboxes (was the original bug).

## LAYOUT_VERSION — auto-invalidating cache

Every cached `data/{book}/layout/page_NNNN.json` carries a `layout_version` stamp. Resume only reuses caches matching the current `LAYOUT_VERSION` constant in `module_02_layout.py`. **When the layout JSON format or ordering algorithm changes, bump `LAYOUT_VERSION`** so old caches regenerate automatically. No manual `rm -rf` needed.

The same pattern is good to apply if module_03's extraction format ever changes (currently no version stamp there).

## Two coordinate systems per region

Each region carries:
- `bbox_px` — pixel coords on the rendered PNG (module 03 uses for image cropping)
- `bbox_pt` — PDF point coords (module 03 uses for PyMuPDF text extraction)

Conversion: `scale = 72.0 / RENDER_DPI`, applied in `_parse_regions`.

## Junk filter (module_03)

Removes recurring header/footer junk that surya doesn't always catch as `Page-header`:
- **Frequency-based** — string repeating ≥3 pages → drop (kills running author-name headers).
- **Pattern-based** (substring, NOT anchored regex — PyMuPDF reorders rotated margin text) — `JUNK_PATTERNS` in config.py: "Downloaded from", "by guest on", copyright, lyellcollection URLs, etc.
- **Position-based** — top/bottom margin trim.

## Module 03 word-extraction gotcha

`page.get_text("text", clip=...)` truncates words crossing the bbox boundary ("Peninsula" → "ula"). Always use word-level extraction with **center-point filtering**:

```python
words = pdf_page.get_text("words")  # (x0,y0,x1,y1,word,block_no,line_no,word_no)
kept = [w for w in words
        if x0 <= (w[0]+w[2])/2 <= x1 and y0 <= (w[1]+w[3])/2 <= y1]
kept.sort(key=lambda w: (w[5], w[6], w[0]))  # block, line, x
```

## Module 04 figure rendering

`<figure class="visual">` (NOT `<div>`) with CSS `display:block; float:none; clear:both` to prevent Kindle's older renderers from floating images and wrapping text alongside.

## Workflow

```bash
# Full pipeline on one paper
python pipeline.py LeatandRiley2021Petrology31b
python pipeline.py books/LeatandRiley2021Petrology31b.pdf   # also works

# Force reprocess
python pipeline.py LeatandRiley2021Petrology31b --force

# Single module (debugging)
python module_02_layout.py LeatandRiley2021Petrology31b
python module_03_extractor.py LeatandRiley2021Petrology31b
python module_04_epub.py LeatandRiley2021Petrology31b

# Nuclear cache clear (if --force isn't enough)
rmdir /s /q data\<book>\layout
rmdir /s /q data\<book>\extraction
del data\<book>\layout_manifest.json
del data\<book>\extraction_manifest.json
del output\<book>.epub
```

## Test corpus (in `books/`)

- `LeatandRiley2021Petrology31b.pdf` — primary test case, 14 pages, two-column geology paper with figures and geochemical plots.
- `Smellie_et_al_2021_AntSci_KGI_Eocene_strat.pdf` — secondary, needs verification run.

## How I work

- Minimalist, chill, no corporate fluff. Keep responses direct and practical.
- Clean, modern, modular Python. No unnecessary abstraction.
- I brainstorm with cheaper models, escalate to Opus for hard problems. If something is genuinely tricky, say so rather than papering over it.
- For this thesis: focus on the most efficient path forward, not the most "robust" overengineered one.

## Open issues / what's next

See `current_state.md` for the live punch list (open issues, last session's fixes, near-term priorities). Read it at the start of any session that's continuing pipeline work.
