# PDF to EPUB Converter

## Status
**Phase:** Core converter hardened and battle-tested across 25 PDFs.
**Current:** All 25 sample PDFs convert successfully with correct chapter detection, covers, inline images, and metadata.
**Next:** Web portal (FastAPI-based upload/convert/download interface).

## Roadmap
- [x] PDF triage engine (scanned vs text vs complex)
- [x] Layout-aware text extraction via PyMuPDF
- [x] Heading detection (font-size-based, with drop-cap and title-page filtering)
- [x] Running header/footer detection and removal
- [x] Inline image extraction (with background overlay filtering)
- [x] Cover generation (extract from PDF or render page 0)
- [x] EPUB assembly via pandoc with metadata
- [x] "This page intentionally left blank" filtering
- [x] Title/author extraction (metadata + smart filename parsing)
- [x] Batch conversion with summary report
- [x] Battle-tested on 25 diverse PDFs
- [ ] Web portal for upload/convert/download

## File Map
- `PDF to EPUB converter.py` — Main converter script. Run with: `python3 "PDF to EPUB converter.py" <dir-or-file> [-y] [-o output_dir]`
- `books to convert/` — Source PDFs, intermediate HTML, extracted images, and generated EPUBs
- `books to convert/batch_converter_master.py` — Copy of main converter (kept in sync)
- `Processed PDFs to EPUB/` — Final verified EPUB output

## Setup & Run
1. Requires: Python 3.x with PyMuPDF (`pip install pymupdf`), pandoc
2. Existing virtualenv at: `/Users/apple/Downloads/books to convert/pdf_env/`
3. Run: `/Users/apple/Downloads/books\ to\ convert/pdf_env/bin/python3 "PDF to EPUB converter.py" "books to convert" -y -o "Processed PDFs to EPUB"`

## Known Quirks
- PDFs with junk metadata (DjVu conversions, etc.) fall back to filename parsing for title/author
- Spaced-out text in PDFs (e.g., "O N E" for "ONE") appears as-is in TOC entries
- Scanned PDFs are detected and skipped (no OCR engine integrated)
- The `fitz` module import name is deprecated; script handles both `pymupdf` and `fitz`
