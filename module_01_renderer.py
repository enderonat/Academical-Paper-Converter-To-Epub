"""
module_01_renderer.py — PDF → page images

Takes a PDF, renders every page as a high-resolution PNG, and writes a
manifest.json so downstream modules know what was produced without having
to scan the filesystem themselves.

Usage (standalone):
    python module_01_renderer.py path/to/book.pdf

Usage (as module):
    from module_01_renderer import render_pdf
    manifest = render_pdf(Path("books/mybook.pdf"))
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

from config import DATA_DIR, PAGES_SUBDIR, RENDER_DPI


# ── Public API ─────────────────────────────────────────────────────────────────

def render_pdf(pdf_path: Path) -> dict:
    """
    Render every page of *pdf_path* to PNG images.

    Returns a manifest dict (also written to disk):
    {
        "book_name":  "mybook",
        "pdf_path":   "/abs/path/to/mybook.pdf",
        "page_count": 42,
        "dpi":        200,
        "pages": [
            {
                "page_index": 0,           # 0-based
                "page_number": 1,          # 1-based (human-friendly)
                "image_path": "/abs/...",  # absolute path to the PNG
                "width_px": 1654,
                "height_px": 2339,
                "width_pt": 595.2,         # original PDF points
                "height_pt": 841.9,
            },
            ...
        ]
    }
    """
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    book_name  = pdf_path.stem
    pages_dir  = DATA_DIR / book_name / PAGES_SUBDIR
    pages_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[Module 01] Rendering '{book_name}'")
    print(f"  DPI       : {RENDER_DPI}")
    print(f"  Output    : {pages_dir}")

    doc   = fitz.open(pdf_path)
    total = len(doc)
    matrix = fitz.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)  # 72 pt = 1 inch

    page_records: list[dict] = []
    t0 = time.perf_counter()

    for page_index in range(total):
        page   = doc[page_index]
        page_number = page_index + 1

        # Skip-render if the file already exists (allows resuming interrupted runs)
        out_path = pages_dir / f"page_{page_number:04d}.png"
        if not out_path.exists():
            pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB)
            pix.save(str(out_path))
            width_px, height_px = pix.width, pix.height
        else:
            # Read dims from the existing PNG header — way faster than re-rendering
            with Image.open(out_path) as im:
                width_px, height_px = im.size

        page_records.append({
            "page_index":  page_index,
            "page_number": page_number,
            "image_path":  str(out_path),
            "width_px":    width_px,
            "height_px":   height_px,
            "width_pt":    round(page.rect.width,  2),
            "height_pt":   round(page.rect.height, 2),
            "rotation":    page.rotation,  # 0, 90, 180, or 270
        })

        _progress(page_number, total)

    doc.close()

    elapsed = time.perf_counter() - t0
    print(f"\n  Done — {total} pages in {elapsed:.1f}s "
          f"({elapsed/total:.2f}s/page)")

    manifest = {
        "book_name":  book_name,
        "pdf_path":   str(pdf_path),
        "page_count": total,
        "dpi":        RENDER_DPI,
        "pages":      page_records,
    }

    manifest_path = DATA_DIR / book_name / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"  Manifest  : {manifest_path}")

    return manifest


def load_manifest(book_name: str) -> dict:
    """Load the manifest written by render_pdf() for a previously processed book."""
    path = DATA_DIR / book_name / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No manifest found for '{book_name}'. Run module_01_renderer first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _progress(current: int, total: int) -> None:
    bar_len  = 30
    filled   = int(bar_len * current / total)
    bar      = "█" * filled + "░" * (bar_len - filled)
    print(f"\r  [{bar}] {current}/{total}", end="", flush=True)


# ── CLI entry-point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python module_01_renderer.py path/to/book.pdf")
        print("       python module_01_renderer.py books/  (renders all PDFs in folder)")
        sys.exit(1)

    target = Path(sys.argv[1])

    if target.is_dir():
        pdfs = sorted(target.glob("*.pdf"))
        if not pdfs:
            print(f"No PDF files found in '{target}'")
            sys.exit(1)
        print(f"Found {len(pdfs)} PDF(s) in '{target}'")
        for pdf in pdfs:
            render_pdf(pdf)
    elif target.suffix.lower() == ".pdf":
        render_pdf(target)
    else:
        print(f"Expected a .pdf file or a directory, got: {target}")
        sys.exit(1)
