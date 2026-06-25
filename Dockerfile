# syntax=docker/dockerfile:1
#
# PDF -> EPUB pipeline (surya-ocr 0.6.0, CPU-only)
# Model weights are NOT baked in; surya downloads them from Hugging Face on the
# first run and caches them under $HF_HOME. Mount a volume there to persist them
# across runs (see docker-compose.yml).

FROM python:3.11-slim

# --- System libraries -------------------------------------------------------
# opencv-python (pulled in by surya) needs libGL + glib at runtime, otherwise
# `import cv2` fails with "ImportError: libGL.so.1: cannot open shared object".
# libgomp1 is OpenMP runtime used by torch / numpy.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Python dependencies ----------------------------------------------------
# Install torch from the CPU wheel index FIRST so the rest of the resolve sees
# it as already satisfied and never pulls the multi-GB CUDA build from PyPI.
RUN pip install --no-cache-dir torch==2.11.0 \
        --index-url https://download.pytorch.org/whl/cpu

# Then the fully-pinned runtime set (torch is already satisfied above).
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt

# --- Application code --------------------------------------------------------
COPY config.py pipeline.py \
     module_01_renderer.py module_02_layout.py \
     module_03_extractor.py module_04_epub.py \
     ./

# Mount points for input/output (also work if nothing is mounted).
RUN mkdir -p books data output

# Persist Hugging Face / surya model weights here. Mount a volume to avoid
# re-downloading ~1-2 GB on every run.
ENV HF_HOME=/models
RUN mkdir -p /models

# `python pipeline.py <target>` — target is a PDF path, a folder, or a bare
# book name (resolved against books/). Default: process everything in books/.
ENTRYPOINT ["python", "pipeline.py"]
CMD ["books/"]
