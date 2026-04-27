"""
pipeline.py — run the full PDF → EPUB pipeline

Orchestrates all four modules in sequence. Can process a single PDF or an
entire folder. Skips books whose epub already exists (use --force to override).

Usage:
    python pipeline.py books/mybook.pdf
    python pipeline.py books/                  # all PDFs in folder
    python pipeline.py books/ --force          # reprocess even if epub exists
    python pipeline.py books/ --batch-size 4   # lower if you get OOM errors
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

from config import BOOKS_DIR, OUTPUT_DIR


def run(pdf_path: Path, batch_size: int = 8, force: bool = False) -> bool:
    """
    Run the full pipeline for one PDF.
    Returns True on success, False on failure.
    """
    book_name = pdf_path.stem
    epub_path = OUTPUT_DIR / f"{book_name}.epub"

    if epub_path.exists() and not force:
        print(f"\n[pipeline] Skipping '{book_name}' — epub already exists.")
        print(f"           Use --force to reprocess.")
        return True

    print(f"\n{'='*60}")
    print(f"  Processing: {pdf_path.name}")
    print(f"{'='*60}")

    t0 = time.perf_counter()

    try:
        # ── Module 01: Render pages ────────────────────────────────────────
        from module_01_renderer import render_pdf
        render_pdf(pdf_path)

        # ── Module 02: Layout detection ────────────────────────────────────
        from module_02_layout import detect_layout
        detect_layout(book_name, batch_size=batch_size)

        # ── Module 03: Content extraction ─────────────────────────────────
        from module_03_extractor import extract_content
        extract_content(book_name)

        # ── Module 04: EPUB assembly ───────────────────────────────────────
        from module_04_epub import build_epub
        build_epub(book_name)

    except Exception:
        print(f"\n  ERROR processing '{book_name}':")
        traceback.print_exc()
        return False

    elapsed = time.perf_counter() - t0
    print(f"\n  ✓ '{book_name}' completed in {elapsed:.1f}s")
    print(f"    → {epub_path}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert PDF research papers and textbooks to EPUB."
    )
    parser.add_argument(
        "target",
        type=Path,
        help="Path to a PDF file or a folder of PDFs",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        metavar="N",
        help="Pages per surya batch (default: 8). Lower if you get out-of-memory errors.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess even if the epub already exists.",
    )
    args = parser.parse_args()

    target: Path = args.target.resolve()

    # Forgiving lookup: if the target doesn't exist as given, try treating it
    # as a bare book name and look in BOOKS_DIR for `<name>.pdf`.
    if not target.exists():
        candidate = (BOOKS_DIR / args.target.name).with_suffix(".pdf")
        if candidate.exists():
            target = candidate.resolve()
            print(f"[pipeline] Resolved bare name → {target}")
        else:
            print(f"Error: '{target}' does not exist.")
            print(f"       Also tried: {candidate}")
            sys.exit(1)

    if target.is_dir():
        pdfs = sorted(target.glob("*.pdf"))
        if not pdfs:
            print(f"No PDF files found in '{target}'.")
            sys.exit(1)
        print(f"Found {len(pdfs)} PDF(s) in '{target}'")
    elif target.suffix.lower() == ".pdf":
        pdfs = [target]
    else:
        print(f"Expected a .pdf file or a folder. Got: {target}")
        sys.exit(1)

    results = {}
    overall_start = time.perf_counter()

    for pdf in pdfs:
        success = run(pdf, batch_size=args.batch_size, force=args.force)
        results[pdf.name] = "✓" if success else "✗ FAILED"

    total_elapsed = time.perf_counter() - overall_start

    print(f"\n{'='*60}")
    print(f"  Summary ({len(pdfs)} file(s) in {total_elapsed:.1f}s)")
    print(f"{'='*60}")
    for name, status in results.items():
        print(f"  {status}  {name}")


if __name__ == "__main__":
    main()
