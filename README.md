# PDF → EPUB Pipeline

Convert academic research papers — multi-column, math- and figure-heavy — into clean EPUBs that read well on Kindle, Kobo, and other e-ink devices.

Most PDF-to-EPUB converters choke on academic papers: they linearize two-column layouts into nonsense, mangle equations into garbled text, and flatten figures. This pipeline takes a different approach. It detects the layout of each page, reads columns in the correct order, extracts body text as real reflowable text, and treats every figure, table, equation, and plot as an **image** — cropped straight from the page, never parsed. The result is an EPUB that actually reflows on a small screen while keeping the math and figures intact.

## What it does

- **Layout-aware**: detects regions on each page and reconstructs proper reading order for one- and two-column papers.
- **Math & figures stay perfect**: any figure, table, equation, or plot is cropped to an image rather than (badly) converted to text.
- **Reflowable body text**: actual paragraphs are extracted as text, so font size and line spacing adapt to the reader's device.
- **Clean output**: strips running headers, footers, page numbers, and repeated journal boilerplate.
- **Resumable**: every stage skips work that's already done, so interrupted runs pick up where they left off.

## How it works

A four-stage pipeline, each stage writing its output to disk so it can be inspected or resumed independently:

```
PDF
 │
 ├─ 1. Render      PDF pages → 200 dpi PNG images
 ├─ 2. Layout      detect regions, sort into reading order
 ├─ 3. Extract     text regions → words; figures/tables/math → cropped PNGs
 └─ 4. Assemble    stitch text + images into a structured EPUB
 │
EPUB
```

## Quick start (with Docker)

You don't need Python, the right library versions, or any setup on your machine — Docker handles all of it. If you've never used Docker, just follow these steps.

**1. Install Docker Desktop** (one time): download from [docker.com](https://www.docker.com/products/docker-desktop/), install it, and launch it. Wait until its whale icon says "running."

**2. Get this project and open a terminal in its folder.** On Windows, open the folder in File Explorer, type `cmd` in the address bar, and press Enter.

**3. Build the image** (one time, takes a few minutes the first time):

```bash
docker compose build
```

This reads the `Dockerfile` and assembles a self-contained environment with the exact, tested versions of every dependency.

**4. Convert a PDF.** Put your PDF in the `books/` folder, then:

```bash
# Convert one paper (use the filename without .pdf)
docker compose run --rm pdf2epub mypaper

# Or convert every PDF sitting in books/
docker compose run --rm pdf2epub
```

The finished `.epub` appears in the `output/` folder. Copy it to your e-reader and you're done.

> **First run note:** the very first conversion downloads the layout-detection model (~1–2 GB) from Hugging Face. That's a one-time download — it's cached and reused on every run after.

### Handy variations

```bash
# Reprocess a paper even if its EPUB already exists
docker compose run --rm pdf2epub mypaper --force

# Lower memory use if a run gets killed (fewer pages processed at once)
docker compose run --rm pdf2epub mypaper --batch-size 4
```

### A few Docker terms, in plain English

- **Image** — a frozen snapshot of the app and everything it needs to run. `docker compose build` creates it.
- **Container** — a running instance of that image. `docker compose run` starts one, does the conversion, and `--rm` cleans it up afterward.
- **Volume / mount** — a shared folder between your computer and the container. That's how your PDFs in `books/` get *in* and the EPUBs in `output/` come *out*.

## Folder layout

```
books/          ← drop input PDFs here
output/         ← finished EPUBs land here
data/           ← intermediate files (page images, layout, crops)
config.py       ← all settings in one place
pipeline.py     ← orchestrates the four modules
module_01..04   ← the pipeline stages
Dockerfile      ← build recipe for the container
docker-compose.yml
```

## Running without Docker (advanced)

If you'd rather run it natively, you'll need Python 3.11 and the pinned dependencies:

```bash
pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.lock.txt
python pipeline.py mypaper
```

Note that one dependency (`surya-ocr`) is intentionally pinned to `0.6.0`; newer releases change the API the pipeline is built around.

## Tech stack

Python · [surya-ocr](https://github.com/datalab-to/surya) (layout detection) · PyMuPDF (rendering & text extraction) · ebooklib (EPUB assembly) · PyTorch (CPU) · Docker.

## Support

If this saved you some eye strain, you can buy me a coffee:

<a href="https://ko-fi.com/enderonat" target="_blank"><img src="https://ko-fi.com/img/githubbutton_sm.svg" alt="Support me on Ko-fi" ></a>

## License

MIT — see [LICENSE](LICENSE). Free to use, modify, and build on.
