# PDF to EPUB Converter — Error Log & Debugging History

This document captures every major issue encountered during development, what caused it,
what we tried, and what ultimately fixed it. Use this as a reference for future iterations
so you don't re-discover the same traps.

---

## 1. Mid-Sentence Line Breaks (The Big One)

**Problem:** PDFs store text as positioned spans, not paragraphs. When extracted line-by-line,
every visual line break became a `<p>` tag, producing hundreds of mid-sentence paragraph breaks
in the EPUB. A book with 500 paragraphs might show 200+ breaks mid-sentence.

**Root cause:** PyMuPDF's `get_text("dict")` returns spans positioned on the page. Each span is
a text fragment at specific coordinates. The converter was treating each span or group of spans
as a separate paragraph without considering whether adjacent lines were part of the same paragraph.

**What didn't work:**
- Simple "join all lines" — destroyed intentional paragraph breaks (poetry, lists, dialogue)
- Heuristic based on line length — too fragile across different page layouts
- Single threshold for all PDFs — some PDFs (like Plato's Five Dialogues) have many short lines
  that are legitimately separate paragraphs

**What fixed it — Three-layer paragraph merging:**

1. **Within-page merging (Layer 1):** Check indentation gaps between consecutive lines on the
   same page. If the next line isn't indented (no new paragraph indent) and the vertical gap
   is small, merge. Gated by a "single-line-block ratio" — if >15% of blocks on the page are
   single-line (measured by `block_y1 - block_y0 < font_size * 2.0`), enable aggressive merging
   for that page.

2. **Cross-page merging (Layer 2):** At page boundaries, check if the last paragraph on
   page N ends mid-sentence (doesn't end with `.!?:;)` or quotes). If so, merge with the
   first paragraph of page N+1.

3. **HTML post-processing (Layer 3):** After all HTML is built, scan for `<p>` tags that end
   mid-sentence followed by `<p>` tags starting with a lowercase letter — merge them. This
   catches breaks across image blocks, footnotes, and other HTML structures that the earlier
   layers couldn't handle.

**Key insight — single-line-block detection:** The original approach counted "single-span blocks"
to decide whether to enable merging. This failed for PDFs like Plato where a single visual line
might contain multiple spans (e.g., different font runs for italics). Changed to measuring the
block's **vertical extent** (`y1 - y0 < font_size * 2.0`) which correctly identifies single-line
blocks regardless of how many spans they contain.

**Results:** Went from 4 PASS / 14 FAIL to 22 PASS / 0 FAIL across 25 test PDFs.

---

## 2. Footnote Word-Per-Paragraph Splitting

**Problem:** Books with footnotes (especially The Network State) produced one `<p>` tag per word
in footnote regions. The Network State showed 3,216 mid-sentence breaks, almost entirely from
footnotes.

**Root cause:** Each footnote span was being appended individually to the `footnotes[]` list.
A footnote like "See Chapter 3 for details" became five separate paragraphs: "See", "Chapter",
"3", "for", "details".

**What fixed it:** Added a `cur_footnote = []` buffer (parallel to `cur_para` for body text).
Footnote spans accumulate in the buffer and are flushed as a single joined string on block
boundaries or when a non-footnote span is encountered.

**Result:** Network State went from 3,216 breaks to 0.

---

## 3. Ligature Decomposition

**Problem:** Some PDFs use Unicode ligature characters (ff, fi, fl, ffi, ffl) or font-specific
ligature glyphs that don't decompose to readable ASCII in the EPUB.

**Two sub-problems:**

### 3a. Unicode Ligatures (U+FB00 through U+FB04)
**Fix:** Apply `unicodedata.normalize("NFKC", text)` to all extracted text. This decomposes
`ﬁ` → `fi`, `ﬂ` → `fl`, etc.

### 3b. ACaslonPro Th-Ligature
**Problem:** The font ACaslonPro uses a custom "Th" ligature that maps to a single glyph.
After NFKC normalization, it appears as a standalone character that doesn't correspond to any
standard Unicode ligature. Words like "The", "That", "This" became garbled.

**Detection:** Scan all spans for font names containing "ACaslonPro" and text containing the
specific ligature character. If found, flag the document for Th-ligature fixing.

**Fix:** Regex-based replacement: find the ligature character followed by common suffixes
(e, at, is, ey, an, em, en, ere, rough, ough, ing, ose, ese, eir, ree, ree) and replace
with "Th" + suffix. Falls back to replacing bare ligature char with "Th".

---

## 4. Running Headers/Footers Leaking Into Text

**Problem:** Many PDFs repeat the book title, chapter name, or page numbers at the top/bottom
of every page. These leaked into the EPUB as body text, appearing hundreds of times.

**What didn't work:**
- Position-only filtering (top/bottom 10% of page) — caught too much legitimate content
  on pages with high or low text placement
- Exact string matching — headers vary slightly page-to-page (odd/even pages differ)

**What fixed it:** Statistical detection across ALL pages first. Collect text that appears
in the top 15% or bottom 15% of multiple pages. If the same text (or very similar text via
normalization) appears on >30% of pages, it's a running header/footer. Build a set of these
strings, then filter them during extraction. Additional ALL CAPS check for short strings in
header/footer positions.

---

## 5. Cover Image Extraction

**Problem:** Many PDFs have no extractable cover image, or the first page is a text-only title
page. Some PDFs embed a 1x1 pixel placeholder image that Calibre/readers show as a blank cover.

**Three-tier approach:**
1. **Extract from PDF:** Look for images on page 0. If found and larger than 1x1 pixels, use it.
2. **Render page 0:** If no suitable image, render the entire first page as a PNG at high DPI.
   This works well for graphically designed covers.
3. **Generate styled cover:** If page 0 is a plain text title page, generate a cover with the
   title and author name using one of 6 color palettes with geometric design elements.

**1x1 pixel trap:** Some PDFs embed tiny placeholder images. Added explicit dimension check:
reject any cover image where width <= 1 or height <= 1.

---

## 6. Title/Author Extraction

**Problem:** Getting the correct title and author from a PDF is surprisingly hard. PDF metadata
is often wrong, missing, or contains the filename.

**Priority order (after many iterations):**
1. **Title page pattern:** Look at page 0 for text that matches "Title by Author" or
   "Title\nby\nAuthor" layout. The largest text on the first page is likely the title;
   text following "by" is likely the author.
2. **PDF metadata:** Fall back to `doc.metadata["title"]` and `doc.metadata["author"]`.
   But many PDFs have garbage here (scanner software names, "Microsoft Word", etc.).
3. **Never use filename:** Early versions fell back to parsing the filename. This was removed
   because filenames like `Aristotle_-_Nicomachean_Ethics_(Hackett)_.pdf` produce ugly titles
   with underscores and parenthetical publisher info.

**Author specifically:** If no author found anywhere, leave it blank rather than guessing.
The converter now offers to fill in missing authors at the end of a batch run (non-blocking prompt).

---

## 7. "This Page Intentionally Left Blank"

**Problem:** Academic and formal publications include literal pages that say "This page
intentionally left blank." These showed up as paragraphs in the EPUB.

**Fix:** Regex filter during text extraction. If a page's only text content (after stripping
whitespace) matches patterns like "this page intentionally left blank", "this page has been
intentionally left blank", skip the entire page.

---

## 8. Heading Detection — Drop Caps and Title Pages

**Problem:** The heading detector uses font size to identify chapter headings (text significantly
larger than body text = heading). But this produced false positives:
- **Drop caps:** First letter of a chapter rendered at 3x body size was detected as a heading
- **Title page text:** Book title on page 0 at large font size created phantom headings

**What fixed it:**
- **Drop cap filter:** If a "heading" is a single character followed immediately by body-size
  text, it's a drop cap, not a heading. Skip it.
- **Title page filter:** Don't classify text on page 0 as a heading — it's title page content,
  handled separately by the title/author extractor.

---

## 9. Printed TOC Linking

**Problem:** Many books have a printed table of contents with chapter names and page numbers.
In the PDF these are just text. In the EPUB, they should be clickable links to the actual
chapter headings.

**Approach:**
1. After building HTML, collect all `<h1>`/`<h2>` heading text and create URL-safe slugs
2. Scan all paragraphs for text that matches a heading (fuzzy match via slug comparison)
3. If a paragraph's text matches a chapter heading slug, wrap it in an `<a href="#slug">` link

**Gotcha:** Page numbers in the TOC text (like "Chapter One ..... 15") need to be stripped
before slug matching. The dot leaders and numbers are noise.

---

## 10. Spaced-Out Text in PDFs

**Problem:** Some PDFs (notably Worringer's "Abstraction and Empathy") store text with spaces
between every character: "p s y c h o l o g y" instead of "psychology". This is a PDF-level
encoding choice, not a converter bug.

**Impact:** Common short words like "of", "and", "the" appear as "o f", "a n d", "t h e" and
can match heading-size thresholds, creating phantom headings and section breaks.

**Status:** Identified as inherently unsolvable without OCR-level reconstruction. The converter
produces readable output, but these words remain spaced out. Despite this, the Worringer EPUB
passes the audit because the paragraph merging layers handle most of the structural issues.

---

## 11. Scanned PDFs

**Problem:** Some PDFs are scanned page images with no text layer. PyMuPDF extracts zero or
near-zero text.

**Detection:** The `analyze_pdf()` function checks the ratio of text characters to pages.
If below a threshold, the PDF is classified as "SCANNED".

**Current behavior:** Scanned PDFs are detected and skipped with a clear message. No OCR
engine is integrated. This is a deliberate scope boundary — OCR (via Tesseract or similar)
is a future enhancement.

---

## 12. PyMuPDF Import Name Deprecation

**Problem:** The PyMuPDF library historically used `import fitz`. Newer versions use
`import pymupdf`. The `fitz` name triggers deprecation warnings.

**Fix:** Try `import pymupdf as fitz` first, fall back to `import fitz`. This handles both
old and new installations without warnings.

---

## 13. Curly Quote Corruption in Code Editing

**Problem (meta — tooling issue):** During development, the AI code editor would sometimes
convert straight single quotes (`'`) in Python string literals to Unicode curly quotes
(`'` U+2018 and `'` U+2019). This caused `SyntaxError: invalid character` at runtime.

**Fix:** For string literals that need actual curly quotes as data (like the `_SENT_ENDERS` set),
use `chr()` calls instead of literal characters: `chr(0x201C)` for `"`, `chr(0x201D)` for `"`, etc.
After any edit, run a syntax check before committing.

**Prevention:** Always run `python3 -c "import py_compile; py_compile.compile('file.py', doraise=True)"`
after edits to catch this immediately.

---

## 14. Per-Character Font Switching (Span Splitting)

**Problem:** Some PDFs (notably Worringer "Abstraction and Empathy") encode text where each
character uses a slightly different font variant. PyMuPDF's `get_text("dict")` returns each
character as a separate span because the font ID changes per glyph. Our converter then joined
these spans with spaces, turning "progression" into "p r o g r e ssi o n".

**Key discovery:** `get_text("text")` returns clean text for these PDFs because it ignores
font boundaries. But `get_text("dict")` — which we need for layout analysis (font sizes,
positions, heading detection) — splits on every font change.

**Misdiagnosis:** Initially reported as "PDF source issue — unsolvable without OCR." This was
wrong. The text data was correct; our extraction method was splitting it. The visual audit
(CLIP scoring) didn't catch it because CLIP can't detect garbled text within otherwise
well-formatted pages. Only a text-level check would have found it.

**What fixed it:** In `extract_spans()`, merge adjacent spans on the same line when their
horizontal gap is less than 60% of the average font size. This joins character-level spans
back into word-level spans while preserving intentional word spacing.

```python
if gap < avg_sz * 0.6:
    prev["text"] += text  # merge into previous span
```

**Result:** Worringer went from 42 garbled paragraphs to 1 (an intentionally spaced heading).

---

## 15. Small-Caps Letter-Spacing in PDF Source

**Problem:** Some PDFs use CSS-style letter-spacing for small-caps text. The text stream
literally contains "N EW Y ORK T IMES" instead of "NEW YORK TIMES". Both `get_text("text")`
and `get_text("dict")` return the spaced version — it's genuinely encoded that way.

**Affected books:** Freakonomics (chapter epigraphs), Freud/Jung Letters (signature lines),
Aristotle (footnote references), Harry Lorayne (alphabet tables).

**What fixed it — `collapse_spaced_text()`:**
1. **Phase 1:** Collapse runs of 3+ single letters separated by spaces. "F R E U D" -> "FREUD".
   Excludes common single-letter words (I, a, A, O) to avoid false positives.
2. **Phase 2:** In all-caps regions (>50% words start uppercase), merge single uppercase
   letter + uppercase-starting fragment pairs. "N EW" -> "NEW", "T IMES" -> "TIMES".

**What can't be fixed:** Mixed-case character spacing like "o f" for "of" or "th e" for "the"
where the fragments look like valid English words. Would require a dictionary/spell-checker.
These are flagged as WARN in the audit.

---

## 16. Audit Gap — Garbled Text Not Detected

**Problem:** The original audit script (`audit_epubs.py`) only checked for mid-sentence
paragraph breaks, metadata, headings, and ligatures. It had no check for garbled/spaced-out
text within words. This meant 42 garbled paragraphs in Worringer went completely undetected,
and the audit reported "22 PASS" when it should have been "FAIL" for several books.

**Root cause:** The audit was designed around the problems we knew about (line breaks, metadata)
and never considered character-level text corruption.

**What fixed it:** Added a garbled text detector to the audit:
```python
singles = sum(1 for w in words if len(w) == 1 and w.isalpha())
if singles > len(words) * 0.3:
    garbled_paras += 1
```
Thresholds: >5 garbled paragraphs = FAIL, >1 = WARN.

**Lesson:** Always verify audit coverage against the actual failure modes, not just the ones
you've already fixed. An audit that only tests for known bugs gives false confidence.

---

## Summary of Audit Results (25 PDFs)

After all fixes, running `audit_epubs.py`:

| Status | Count | Notes |
|--------|-------|-------|
| PASS   | 19    | Clean conversion, correct structure |
| WARN   | 6     | Missing author (2), minor PDF-source spacing (3), low headings (1) |
| FAIL   | 0     | No structural failures |

WARN details:
- Freakonomics, Communist Manifesto: missing author metadata
- Aristotle, Freud/Jung, Harry Lorayne: residual PDF-source character spacing (2-3 paragraphs each)
- Steal Like An Artist: only 2 headings detected (visual book with minimal text structure)
