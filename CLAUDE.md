# PDF to EPUB Converter

## Status
**Phase:** Core converter hardened and battle-tested across 25 PDFs.
**Current:** 22 PASS, 3 WARN (missing author metadata only), 0 FAIL across all 25 test PDFs. Line break issue fully resolved.
**Next:** Web portal (FastAPI-based upload/convert/download interface).

## Roadmap
- [x] PDF triage engine (scanned vs text vs complex)
- [x] Layout-aware text extraction via PyMuPDF
- [x] Heading detection (font-size-based, with drop-cap and title-page filtering)
- [x] Running header/footer detection and removal (ALL CAPS positional filter)
- [x] Inline image extraction (with background overlay filtering)
- [x] Cover generation (extract from PDF, styled cover for text-only pages, or render page 0)
- [x] EPUB assembly via pandoc with metadata
- [x] "This page intentionally left blank" filtering
- [x] Title/author extraction (title-page "by" pattern -> metadata -> filename fallback)
- [x] Batch conversion with summary report
- [x] Ligature decomposition (NFKC for Unicode, regex-based Th-ligature fix for ACaslonPro)
- [x] Printed TOC linking (paragraphs matching chapter headings become clickable links)
- [x] Styled cover generation with 6 color palettes and geometric design
- [x] 1x1 pixel placeholder image rejection (cover and inline)
- [x] Paragraph break fixing (within-page merging, cross-page merging, HTML post-processing)
- [x] Footnote buffering (multi-span footnotes merged into single paragraphs)
- [x] EPUB quality audit script
- [x] Battle-tested on 25 diverse PDFs (22 PASS, 3 WARN, 0 FAIL)
- [ ] Web portal for upload/convert/download

## File Map
- `PDF to EPUB converter.py` — Main converter script. Run with: `python3 "PDF to EPUB converter.py" <dir-or-file> [-y] [-o output_dir]`
- `audit_epubs.py` — EPUB quality audit script. Checks: title, author, cover, TOC, headings, images, mid-sentence breaks, page numbers, ligatures, paragraph quality
- `books to convert/` — Source PDFs, intermediate HTML, extracted images, and generated EPUBs
- `books to convert/batch_converter_master.py` — Copy of main converter (kept in sync)
- `Processed PDFs to EPUB/` — Final verified EPUB output

## Setup & Run
1. Requires: Python 3.x with PyMuPDF (`pip install pymupdf`), pandoc
2. Virtualenv at: `books to convert/pdf_env/` (also at `/Users/apple/Downloads/books to convert/pdf_env/`)
3. Run: `books to convert/pdf_env/bin/python3 "PDF to EPUB converter.py" "books to convert" -y -o "Processed PDFs to EPUB"`
4. Audit: `books to convert/pdf_env/bin/python3 audit_epubs.py "Processed PDFs to EPUB"`

## Known Quirks
- Title extraction priority: page-0 "Title by Author" pattern -> PDF metadata (never filename)
- Spaced-out text in PDFs (e.g., "O N E" for "ONE") appears as-is in TOC entries
- "Discon nected" in Chuck Palahniuk — PDF itself stores the word with a space; not a ligature issue
- Scanned PDFs are detected and skipped (no OCR engine integrated)
- The `fitz` module import name is deprecated; script handles both `pymupdf` and `fitz`
- Paragraph merging uses three layers: within-page (indentation+gap), cross-page (sentence-ender check), HTML post-processing (mid-sentence merge across image blocks)
- Footnotes are buffered per-block to avoid word-per-paragraph splitting
- Audit excludes footnote paragraphs from mid-sentence break counting
