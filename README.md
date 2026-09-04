# PDF to EPUB Converter

A robust, battle-tested Python tool that converts text-based PDFs to well-structured EPUB e-books. Handles the messy realities of PDF text extraction — paragraph merging, heading detection, footnotes, ligatures, running headers/footers, cover generation, and metadata extraction.

## Features

- **Smart paragraph reconstruction** — Three-layer merging system that correctly joins lines split by PDF layout without destroying intentional breaks (poetry, dialogue, lists)
- **Heading detection** — Font-size-based chapter detection with drop-cap and title-page filtering
- **Running header/footer removal** — Statistical detection across pages, removes repeated text
- **Cover generation** — Extracts cover from PDF, renders title page, or generates a styled cover with color palettes
- **Ligature decomposition** — Handles Unicode ligatures (NFKC) and font-specific ligatures (ACaslonPro Th-ligature)
- **Footnote handling** — Buffered footnote extraction preserves multi-word footnotes as single paragraphs
- **TOC linking** — Printed table of contents entries become clickable links to chapter headings
- **Batch conversion** — Process entire directories with summary report
- **Quality audit** — Included audit script checks mid-sentence breaks, metadata, structure, and more
- **Non-blocking metadata prompt** — After batch conversion, optionally fill in missing author names

## Requirements

- Python 3.x
- [PyMuPDF](https://pypi.org/project/PyMuPDF/) (`pip install pymupdf`)
- [Pandoc](https://pandoc.org/installing.html)

## Usage

### Convert a single PDF
```bash
python3 "PDF to EPUB converter.py" path/to/book.pdf
```

### Convert all PDFs in a directory
```bash
python3 "PDF to EPUB converter.py" path/to/pdf/directory -y -o output/directory
```

### Options
- `-y` / `--yes` — Skip confirmation pauses
- `-o` / `--output` — Output directory for EPUBs (default: same as input)

### Audit EPUB quality
```bash
python3 audit_epubs.py path/to/epub/directory
```

## How It Works

1. **PDF Triage** — Classifies PDF as text-based, scanned, complex, or mixed
2. **Text Extraction** — Layout-aware span extraction via PyMuPDF with sort ordering
3. **Header/Footer Detection** — Statistical analysis removes repeated page elements
4. **Heading Classification** — Font-size histogram identifies chapter headings
5. **Paragraph Merging** — Three layers (within-page, cross-page, HTML post-processing)
6. **Footnote Buffering** — Spans in bottom 15% of page grouped into footnote paragraphs
7. **HTML Assembly** — Structured HTML with sections, headings, images, footnotes
8. **TOC Linking** — Printed TOC entries linked to actual chapter headings
9. **Cover Generation** — Best available cover from PDF content or generated design
10. **EPUB Assembly** — Pandoc converts HTML to EPUB with metadata and cover

## Battle-Tested

Tested across 25 diverse PDFs including philosophy texts, business books, memoirs, political works, and technical manuals. Results: 22 PASS, 3 WARN (missing author metadata only), 0 FAIL.

See [ERRORS_AND_FIXES.md](ERRORS_AND_FIXES.md) for the complete debugging history — every major issue, what caused it, what didn't work, and what ultimately fixed it.

## Limitations

- **Scanned PDFs** — Detected and skipped (no OCR engine integrated)
- **Spaced-out text** — PDFs that store characters with spaces between them (e.g., "p s y c h o l o g y") are reproduced as-is
- **Complex layouts** — Multi-column text, tables, and heavily formatted pages may not convert perfectly
- **Right-to-left languages** — Not tested or supported
