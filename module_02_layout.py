"""
module_02_layout.py — page images → detected regions (with reading order)
 
Uses surya to run layout detection on each rendered page image. Surya detects
whole semantic regions — a graph is one bbox, an equation is one bbox, a table
is one bbox — and assigns a correct reading order even for multi-column pages.
 
First run: surya will download its models (~1GB) into a local cache. This only
happens once. Subsequent runs load from cache instantly.
 
Usage (standalone):
    python module_02_layout.py mybook
    python module_02_layout.py mybook --batch-size 4
 
Usage (as module):
    from module_02_layout import detect_layout
    layout_manifest = detect_layout("mybook")
"""
 
from __future__ import annotations
 
import json
import sys
import time
from pathlib import Path
 
from PIL import Image
 
from config import (
    DATA_DIR,
    LABEL_POLICY,
    LAYOUT_SUBDIR,
    MIN_REGION_PX,
    RENDER_DPI,
)
# LABEL_POLICY is used in _parse_regions for the debug printout — already imported above
from module_01_renderer import load_manifest
 
# Default pages per GPU/CPU batch. Lower this if you get out-of-memory errors.
DEFAULT_BATCH_SIZE = 8

# Bump this whenever the layout JSON format or ordering algorithm changes.
# Cached per-page JSONs without a matching version stamp get regenerated.
LAYOUT_VERSION = 2
 
 
# ── Public API ─────────────────────────────────────────────────────────────────
 
def detect_layout(book_name: str, batch_size: int = DEFAULT_BATCH_SIZE) -> dict:
    """
    Run surya layout detection on every page of *book_name*.
 
    Reads  : data/{book_name}/manifest.json        (written by module 01)
    Writes : data/{book_name}/layout/page_NNNN.json (one per page)
             data/{book_name}/layout_manifest.json
 
    Returns the layout_manifest dict.
    """
    manifest   = load_manifest(book_name)
    layout_dir = DATA_DIR / book_name / LAYOUT_SUBDIR
    layout_dir.mkdir(parents=True, exist_ok=True)
 
    pages      = manifest["pages"]
    total      = len(pages)
    scale      = 72.0 / RENDER_DPI   # px → PDF points
 
    print(f"\n[Module 02] Layout detection for '{book_name}'")
    print(f"  Pages     : {total}")
    print(f"  Batch size: {batch_size}")
    print(f"  Output    : {layout_dir}")
    print(f"\n  Loading surya models (downloads on first run)...")
 
    engine, mode = _load_surya()
    print("  Layout model ready. Reading order: column-aware geometry sort.\n")
 
    page_layout_records: list[dict] = []
    t0 = time.perf_counter()
 
    # Process in batches to avoid OOM on long documents
    for batch_start in range(0, total, batch_size):
        batch_pages = pages[batch_start : batch_start + batch_size]
 
        # Load PIL images for the batch
        images = [Image.open(p["image_path"]).convert("RGB") for p in batch_pages]
 
        # Run surya — returns one LayoutResult per image
        results = _run_surya(images, engine, mode)
 
        # First parse all batch results into our region format
        batch_records: list[tuple[dict, list[dict], "Image.Image"]] = []
        for page_meta, result, img in zip(batch_pages, results, images):
            page_num = page_meta["page_number"]
            out_path = layout_dir / f"page_{page_num:04d}.json"

            # Resume support — skip if cached file matches current LAYOUT_VERSION.
            # Older caches (pre-geometry-sort) are silently regenerated.
            if out_path.exists():
                cached = json.loads(out_path.read_text(encoding="utf-8"))
                if cached.get("layout_version") == LAYOUT_VERSION:
                    page_layout_records.append(cached)
                    _progress(page_num, total)
                    continue

            regions = _parse_regions(result, page_meta, scale)
            # Sideways pages: skip surya's (likely garbage) regions and treat
            # the whole page as one image.  90/270 are the common cases; 180
            # is upside-down but still readable so we leave it to surya.
            if page_meta.get("rotation", 0) in (90, 270):
                regions = _full_page_image_region(page_meta)
            batch_records.append((page_meta, regions, img))

        # Reading-order pass — surya's layout returns label-grouped boxes,
        # not reading-flow order. Apply column-aware geometry sort.
        for page_meta, regions, _img in batch_records:
            _column_aware_sort(regions, page_meta["width_px"])

        # Now write each page record to disk
        for page_meta, regions, _img in batch_records:
            page_num = page_meta["page_number"]
            out_path = layout_dir / f"page_{page_num:04d}.json"

            record = {
                "layout_version": LAYOUT_VERSION,
                "page_index":   page_meta["page_index"],
                "page_number":  page_num,
                "image_path":   page_meta["image_path"],
                "width_px":     page_meta["width_px"],
                "height_px":    page_meta["height_px"],
                "width_pt":     page_meta["width_pt"],
                "height_pt":    page_meta["height_pt"],
                "regions":      regions,
            }

            out_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
            page_layout_records.append(record)
            _progress(page_num, total)
 
        # Free PIL images from this batch
        for img in images:
            img.close()
 
    elapsed = time.perf_counter() - t0
    total_regions = sum(len(p["regions"]) for p in page_layout_records)
 
    print(f"\n  Done — {total} pages in {elapsed:.1f}s")
    print(f"  Total regions detected: {total_regions}")
    _print_label_summary(page_layout_records)
 
    layout_manifest = {
        "book_name":     book_name,
        "page_count":    total,
        "total_regions": total_regions,
        "pages":         page_layout_records,
    }
 
    lm_path = DATA_DIR / book_name / "layout_manifest.json"
    lm_path.write_text(json.dumps(layout_manifest, indent=2), encoding="utf-8")
    print(f"  Manifest  : {lm_path}")
 
    return layout_manifest
 
 
def load_layout_manifest(book_name: str) -> dict:
    """Load the layout manifest written by detect_layout()."""
    path = DATA_DIR / book_name / "layout_manifest.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No layout manifest for '{book_name}'. Run module_02_layout first."
        )
    return json.loads(path.read_text(encoding="utf-8"))
 
 
# ── Surya wrappers ─────────────────────────────────────────────────────────────
 
def _load_surya():
    """
    Load surya layout model.

    Tries APIs in order, newest first:
      - surya >= 0.7:  surya.layout.LayoutPredictor()  (callable on images)
      - surya 0.6.x:   surya.model.detection.segformer.{load_model,load_processor}
                       + surya.layout.batch_layout_detection
    Returns (engine, mode) where mode is "predictor" or "legacy".
    """
    try:
        import surya  # noqa: F401
    except ImportError:
        print("\n  ERROR: surya-ocr is not installed.")
        print("  Run:   pip install surya-ocr")
        sys.exit(1)

    errors = []

    # ── surya 0.6.0 split layout: model.py + processor.py ─────────────────
    try:
        from surya.model.detection.model import load_model, load_processor
        from surya.settings import settings

        checkpoint = settings.LAYOUT_MODEL_CHECKPOINT

        def _try_load(fn, ckpt):
            # Some 0.6.x variants use checkpoint=, others positional, others none
            for call in (
                lambda: fn(checkpoint=ckpt),
                lambda: fn(ckpt),
                lambda: fn(),
            ):
                try:
                    return call()
                except TypeError:
                    continue
            return fn()  # last-ditch

        model     = _try_load(load_model, checkpoint)
        processor = _try_load(load_processor, checkpoint)
        print(f"  surya API: 0.6.0 split (model.py + processor.py, checkpoint={checkpoint})")
        return (model, processor), "legacy"
    except Exception as e:
        errors.append(f"  Attempt 1 (model.py + processor.py): {e}")

    # ── surya 0.7+: LayoutPredictor ───────────────────────────────────────
    try:
        from surya.layout import LayoutPredictor
        predictor = LayoutPredictor()
        print("  surya API: LayoutPredictor (>=0.7)")
        return predictor, "predictor"
    except Exception as e:
        errors.append(f"  Attempt 2 (LayoutPredictor): {e}")

    # ── surya 0.6.x bundled: segformer.py ─────────────────────────────────
    try:
        from surya.model.detection import segformer
        from surya.settings import settings

        checkpoint = settings.LAYOUT_MODEL_CHECKPOINT
        model      = segformer.load_model(checkpoint=checkpoint)
        processor  = segformer.load_processor(checkpoint=checkpoint)
        print(f"  surya API: 0.6.x bundled (segformer.py, checkpoint={checkpoint})")
        return (model, processor), "legacy"
    except Exception as e:
        errors.append(f"  Attempt 3 (segformer.py): {e}")

    # ── Inspection for diagnostics ────────────────────────────────────────
    try:
        import importlib
        layout_mod = importlib.import_module("surya.layout")
        errors.append(f"  surya.layout: {sorted(x for x in dir(layout_mod) if not x.startswith('_'))}")
    except Exception as e:
        errors.append(f"  Could not inspect surya.layout: {e}")

    print("\n  ERROR: could not load any surya layout API.")
    for err in errors:
        print(err)
    sys.exit(1)


def _run_surya(images: list, engine, mode: str) -> list:
    """Run layout detection. Dispatches based on the mode from _load_surya()."""
    if mode == "predictor":
        # LayoutPredictor is callable: predictor(images) -> List[LayoutResult]
        return engine(images)
    # Legacy 0.6.x path
    from surya.layout import batch_layout_detection
    model, processor = engine
    return batch_layout_detection(images, model, processor)


def _column_aware_sort(regions: list[dict], page_width_px: int) -> None:
    """
    Pure-geometry reading-order pass. Handles 1- and 2-column layouts:
      - Regions wider than 60% of the page are "full-width" separators
        (titles, abstracts, full-page figures, footnote rules).
      - Within each horizontal band between full-width regions, emit
        left-column top-to-bottom, then right-column top-to-bottom.

    `page_width_px` MUST be the actual page width from page_meta. Earlier
    versions estimated this from region bboxes, which broke when no region
    extended to the right margin (mid_x ended up well left of true center).

    Mutates `regions` in place: re-orders and assigns `reading_order` 0..N-1.
    """
    if not regions:
        return

    mid_x          = page_width_px / 2
    full_threshold = page_width_px * 0.6

    def classify(r: dict) -> str:
        x0, _y0, x1, _y1 = r["bbox_px"]
        if (x1 - x0) > full_threshold:
            return "full"
        return "left" if (x0 + x1) / 2 < mid_x else "right"

    # Walk top-to-bottom, splitting into bands at every full-width region.
    by_y = sorted(regions, key=lambda r: r["bbox_px"][1])

    ordered: list[dict] = []
    band_left:  list[dict] = []
    band_right: list[dict] = []

    def flush_band() -> None:
        ordered.extend(band_left)
        ordered.extend(band_right)
        band_left.clear()
        band_right.clear()

    for r in by_y:
        kind = classify(r)
        if kind == "full":
            flush_band()
            ordered.append(r)
        elif kind == "left":
            band_left.append(r)
        else:
            band_right.append(r)
    flush_band()

    regions.clear()
    regions.extend(ordered)
    for i, r in enumerate(regions):
        r["reading_order"] = i
 
 
# ── Region parsing ─────────────────────────────────────────────────────────────

def _full_page_image_region(page_meta: dict) -> list[dict]:
    """Return a single Figure region covering the entire page (for rotated pages)."""
    w_px = page_meta["width_px"]
    h_px = page_meta["height_px"]
    n    = page_meta["page_number"]
    scale = 72.0 / RENDER_DPI
    return [{
        "region_id":     f"p{n}_r0",
        "label":         "Figure",
        "policy":        "image",
        "reading_order": 0,
        "bbox_px":       [0, 0, w_px, h_px],
        "bbox_pt":       [0.0, 0.0, round(w_px * scale, 3), round(h_px * scale, 3)],
    }]


def _parse_regions(result, page_meta: dict, scale: float) -> list[dict]:
    """
    Convert surya LayoutResult → our clean region list.
 
    Each region dict:
    {
        "region_id":     "p3_r7",
        "label":         "Formula",
        "policy":        "image",
        "reading_order": 2,
        "bbox_px":       [x0,y0,x1,y1],
        "bbox_pt":       [x0,y0,x1,y1],
    }
 
    Regions with policy="skip" or below MIN_REGION_PX are excluded.
    """
    page_num  = page_meta["page_number"]
    width_px  = page_meta["width_px"]
    height_px = page_meta["height_px"]
 
    # ── Discover bbox container (handles API version differences) ─────────
    # Old surya: result.bboxes
    # New surya: result might be the list directly, or have .layout_boxes etc.
    if hasattr(result, "bboxes"):
        raw_bboxes = result.bboxes
    elif hasattr(result, "layout_boxes"):
        raw_bboxes = result.layout_boxes
    elif isinstance(result, list):
        raw_bboxes = result
    else:
        # Last resort: try iterating the result directly
        try:
            raw_bboxes = list(result)
        except TypeError:
            raw_bboxes = []
 
    if not raw_bboxes:
        return []
 
    # ── Reading order ─────────────────────────────────────────────────────
    # New surya LayoutBox has neither .position nor .order — boxes come out
    # already sorted by reading order from LayoutPredictor, so use enumerate.
    # For legacy .position/.order we still respect it explicitly.
    def _explicit_order(b):
        if hasattr(b, "position"): return b.position
        if hasattr(b, "order"):    return b.order
        return None

    if any(_explicit_order(b) is not None for b in raw_bboxes):
        raw_bboxes = sorted(raw_bboxes, key=lambda b: _explicit_order(b) or 0)
 
    # ── Log actual label names on page 1 for debugging ───────────────────
    if page_num == 1 and raw_bboxes:
        seen_labels = sorted(set(
            getattr(b, "label", "?") for b in raw_bboxes
        ))
        print(f"\n  [DEBUG p1] Surya labels detected: {seen_labels}")
        print(f"  [DEBUG p1] Known LABEL_POLICY keys: {sorted(LABEL_POLICY.keys())}")
        missing = [l for l in seen_labels if l not in LABEL_POLICY]
        if missing:
            print(f"  [DEBUG p1] ⚠ Unlisted labels (will be SKIPPED): {missing}")
            print(f"             → Add them to LABEL_POLICY in config.py")
 
    regions = []
    region_index = 0

    for read_idx, bbox_obj in enumerate(raw_bboxes):
        label  = getattr(bbox_obj, "label", "Text")
        policy = LABEL_POLICY.get(label, "skip")

        if policy == "skip":
            continue

        # New API: .polygon = [[x,y], [x,y], [x,y], [x,y]]
        # Old API: .bbox    = [x0, y0, x1, y1]
        raw_bbox = getattr(bbox_obj, "bbox", None)
        if raw_bbox is None:
            polygon = getattr(bbox_obj, "polygon", None)
            if not polygon:
                continue
            xs = [pt[0] for pt in polygon]
            ys = [pt[1] for pt in polygon]
            raw_bbox = [min(xs), min(ys), max(xs), max(ys)]
        x0, y0, x1, y1 = raw_bbox

        # Resolve reading order: explicit attr if present, else position in list
        explicit = _explicit_order(bbox_obj)
        reading_order = explicit if explicit is not None else read_idx
 
        # Clamp to page bounds
        x0 = max(0, min(x0, width_px))
        y0 = max(0, min(y0, height_px))
        x1 = max(0, min(x1, width_px))
        y1 = max(0, min(y1, height_px))
 
        if (x1 - x0) < MIN_REGION_PX or (y1 - y0) < MIN_REGION_PX:
            continue
 
        bbox_pt = [
            round(x0 * scale, 3),
            round(y0 * scale, 3),
            round(x1 * scale, 3),
            round(y1 * scale, 3),
        ]
 
        regions.append({
            "region_id":     f"p{page_num}_r{region_index}",
            "label":         label,
            "policy":        policy,
            "reading_order": reading_order,
            "bbox_px":       [x0, y0, x1, y1],
            "bbox_pt":       bbox_pt,
        })
        region_index += 1
 
    return regions
 
 
# ── Reporting helpers ──────────────────────────────────────────────────────────
 
def _print_label_summary(page_records: list[dict]) -> None:
    counts: dict[str, int] = {}
    for page in page_records:
        for region in page["regions"]:
            label = region["label"]
            counts[label] = counts.get(label, 0) + 1
 
    print("\n  Region breakdown:")
    for label, count in sorted(counts.items(), key=lambda x: -x[1]):
        policy = LABEL_POLICY.get(label, "skip")
        print(f"    {label:<20} {count:>5}   [{policy}]")
 
 
def _progress(current: int, total: int) -> None:
    bar_len = 30
    filled  = int(bar_len * current / total)
    bar     = "█" * filled + "░" * (bar_len - filled)
    print(f"\r  [{bar}] {current}/{total}", end="", flush=True)
 
 
# ── CLI entry-point ────────────────────────────────────────────────────────────
 
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python module_02_layout.py <book_name> [--batch-size N]")
        print("       book_name is the PDF filename without extension")
        sys.exit(1)
 
    _book = sys.argv[1]
    _bs   = DEFAULT_BATCH_SIZE
 
    if "--batch-size" in sys.argv:
        idx = sys.argv.index("--batch-size")
        try:
            _bs = int(sys.argv[idx + 1])
        except (IndexError, ValueError):
            print("Invalid --batch-size value")
            sys.exit(1)
 
    detect_layout(_book, batch_size=_bs)
 
