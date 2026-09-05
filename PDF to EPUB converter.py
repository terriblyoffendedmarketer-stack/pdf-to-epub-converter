#!/usr/bin/env python3
"""
PDF to EPUB converter — Layout-aware PDF to EPUB batch converter.
Uses PyMuPDF's native block ordering to preserve reading order.

Usage: python3 "PDF to EPUB converter.py" <directory-or-file> [-y]
Requires: PyMuPDF (pymupdf), pandoc

Gotchas:
- Running headers that share text with chapter titles (e.g. "PHAEDO" as both
  header and chapter title) must be exempted from header filtering when the
  span's font size maps to a heading. Otherwise the chapter title gets stripped.
- Image blocks covering >60% of the page are background/decorative overlays
  and should be skipped (not saved as inline images). Only page-0 full-bleed
  images become the cover.
- Pandoc needs --resource-path set to the directory containing image folders,
  and image src paths must be relative to that.
- The `fitz` import name is deprecated; `pymupdf` is the new name but we
  fall back to `fitz` for older installs.
"""

import sys
import argparse
import subprocess
import os
import re
import glob
import html as html_module
import unicodedata
from collections import Counter

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        sys.path.append("/Users/apple/Downloads/books to convert/pdf_env/lib/python3.14/site-packages")
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz

# =============================================================================
# SECTION 1: PDF TRIAGE ENGINE
# =============================================================================

def analyze_pdf(path):
    """Classify a PDF as SCANNED, MIXED, COMPLEX, or TEXT_BASED."""
    doc = fitz.open(path)
    n_pages = len(doc)
    sample_pages = list(range(n_pages)) if n_pages <= 30 else \
        sorted(set([0, n_pages - 1] + list(range(0, n_pages, max(1, n_pages // 20)))))

    total_chars = 0
    total_image_area_ratio = 0.0
    pages_with_text = 0
    pages_checked = 0
    font_sizes = set()

    for i in sample_pages:
        page = doc[i]
        text = page.get_text("text")
        chars = len(text.strip())
        total_chars += chars
        if chars > 50:
            pages_with_text += 1
        pages_checked += 1

        page_area = page.rect.width * page.rect.height
        img_area = 0.0
        for img in page.get_images(full=True):
            try:
                bbox_list = page.get_image_rects(img[0])
                for r in bbox_list:
                    img_area += r.width * r.height
            except Exception:
                pass
        if page_area > 0:
            total_image_area_ratio += min(img_area / page_area, 1.0)

        try:
            d = page.get_text("dict")
            for block in d.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        font_sizes.add(round(span.get("size", 0), 1))
        except Exception:
            pass

    avg_chars_per_page = total_chars / max(pages_checked, 1)
    avg_image_ratio = total_image_area_ratio / max(pages_checked, 1)
    text_page_fraction = pages_with_text / max(pages_checked, 1)
    font_variance = len(font_sizes)

    if avg_chars_per_page < 30 or text_page_fraction < 0.3:
        category, note = "SCANNED", "Little to no extractable text — likely scanned images."
    elif avg_image_ratio > 0.35:
        category, note = "MIXED", "Real text layer present, but significant image content."
    elif font_variance > 12:
        category, note = "COMPLEX", "High font-size variance suggests complex layout."
    else:
        category, note = "TEXT_BASED", "Clean text layer, simple layout signal."

    doc.close()
    return category, note

# =============================================================================
# SECTION 2: SCANNED / OCR FALLBACK ENGINE
# =============================================================================

ROMAN_HEADING_RE = re.compile(r"^[IVXLCDM]+\.?\s+.{2,40}$")
ALLCAPS_LINE_RE = re.compile(r"^[^a-z]{3,60}$")
BARE_NUMBER_RE_SCANNED = re.compile(r"^\s*\d+\s*$")


def clean_page_lines(raw_text):
    return [l for l in [l.strip() for l in raw_text.split("\n")] if l != ""]


def is_heading_shaped(line):
    if ROMAN_HEADING_RE.match(line):
        return True
    if ALLCAPS_LINE_RE.match(line) and len(line.split()) <= 6:
        return True
    return False


def find_running_heads(all_pages_lines, min_repeat_frac=0.02):
    candidates = Counter()
    n_pages = len(all_pages_lines)
    for lines in all_pages_lines:
        for line in lines[:2]:
            if BARE_NUMBER_RE_SCANNED.match(line) or is_heading_shaped(line):
                candidates[line] += 1
    return {t for t, c in candidates.items() if c >= max(3, int(n_pages * min_repeat_frac))}


def build_html_scanned(doc):
    all_pages_lines = [clean_page_lines(p.get_text("text")) for p in doc]
    running_heads = find_running_heads(all_pages_lines)
    parts, cur_para = ["<html><body>"], []

    for lines in all_pages_lines:
        dropped = 0
        while lines and dropped < 2:
            first = lines[0]
            if BARE_NUMBER_RE_SCANNED.match(first) or first in running_heads:
                lines.pop(0)
                dropped += 1
            else:
                break

        for line in lines:
            if is_heading_shaped(line) and line not in running_heads:
                if cur_para:
                    parts.append("<p>" + " ".join(cur_para) + "</p>")
                    cur_para = []
                parts.append(f"<h2>{html_module.escape(line)}</h2>")
            else:
                cur_para.append(html_module.escape(line))
                if len(line) < 45 and line.endswith((".", "?", "!", "”", '"')):
                    parts.append("<p>" + " ".join(cur_para) + "</p>")
                    cur_para = []
    if cur_para:
        parts.append("<p>" + " ".join(cur_para) + "</p>")
    parts.append("</body></html>")
    return "\n".join(parts)


_COVER_PALETTES = [
    {"bg": (0.10, 0.12, 0.18), "accent": (0.90, 0.35, 0.25), "stripe": (0.15, 0.18, 0.25)},
    {"bg": (0.06, 0.20, 0.28), "accent": (0.95, 0.75, 0.20), "stripe": (0.08, 0.26, 0.36)},
    {"bg": (0.22, 0.10, 0.18), "accent": (0.85, 0.55, 0.35), "stripe": (0.30, 0.14, 0.24)},
    {"bg": (0.08, 0.16, 0.12), "accent": (0.55, 0.85, 0.55), "stripe": (0.12, 0.22, 0.18)},
    {"bg": (0.18, 0.14, 0.24), "accent": (0.70, 0.50, 0.90), "stripe": (0.24, 0.20, 0.32)},
    {"bg": (0.14, 0.14, 0.14), "accent": (0.95, 0.60, 0.10), "stripe": (0.20, 0.20, 0.20)},
]


def _generate_styled_cover(title, author, cover_path):
    """Generate a visually interesting cover with geometric elements."""
    palette = _COVER_PALETTES[sum(ord(c) for c in title) % len(_COVER_PALETTES)]
    bg, accent, stripe = palette["bg"], palette["accent"], palette["stripe"]

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    shape = page.new_shape()

    # Main background
    shape.draw_rect(page.rect)
    shape.finish(color=None, fill=bg)

    # Diagonal stripe band (top-right to mid-left)
    shape.draw_quad(fitz.Quad(
        fitz.Point(350, 0), fitz.Point(612, 0),
        fitz.Point(612, 260), fitz.Point(180, 260)
    ))
    shape.finish(color=None, fill=stripe)

    # Accent bar at bottom
    shape.draw_rect(fitz.Rect(0, 720, 612, 792))
    shape.finish(color=None, fill=accent)

    # Thin accent line above title area
    shape.draw_rect(fitz.Rect(55, 310, 280, 314))
    shape.finish(color=None, fill=accent)

    # Decorative circles (top-right corner)
    for cx, cy, r in [(540, 70, 45), (480, 130, 25), (555, 160, 15)]:
        shape.draw_circle(fitz.Point(cx, cy), r)
        shape.finish(color=accent, fill=None, width=1.5)

    shape.commit()

    # Title text
    title_rect = fitz.Rect(55, 340, 557, 560)
    title_fontsize = 38 if len(title) < 25 else 30 if len(title) < 45 else 24
    page.insert_textbox(title_rect, title, fontsize=title_fontsize,
                        fontname="helv", color=(1, 1, 1), align=fitz.TEXT_ALIGN_LEFT)

    # Author text
    if author:
        author_rect = fitz.Rect(55, 580, 557, 650)
        page.insert_textbox(author_rect, author, fontsize=16,
                            fontname="helv", color=(0.7, 0.7, 0.7), align=fitz.TEXT_ALIGN_LEFT)

    # Large decorative initial letter (faded, behind text area)
    if title:
        letter = title[0].upper()
        letter_rect = fitz.Rect(320, 400, 600, 700)
        r, g, b = stripe
        page.insert_textbox(letter_rect, letter, fontsize=200,
                            fontname="helv", color=(r + 0.06, g + 0.06, b + 0.06),
                            align=fitz.TEXT_ALIGN_RIGHT)

    pix = page.get_pixmap(dpi=150)
    pix.save(cover_path)
    doc.close()


def ensure_cover(doc, pdf_path, img_dir, img_dir_name, title="", author=""):
    """Ensure there's a cover image. Uses extracted cover, styled cover, or page-0 render."""
    covers = glob.glob(os.path.join(img_dir, "cover.*"))
    if covers:
        return covers[0]

    cover_path = os.path.join(img_dir, "cover.jpeg")

    # Check if page 0 has a substantial image (real cover art)
    page = doc[0]
    page_area = page.rect.width * page.rect.height
    has_cover_art = False
    for img in page.get_images(full=True):
        try:
            xref = img[0]
            img_info = doc.extract_image(xref)
            pw, ph = img_info.get("width", 0), img_info.get("height", 0)
            if pw < 50 or ph < 50:
                continue
            for r in page.get_image_rects(xref):
                if (r.width * r.height) / page_area > 0.3:
                    has_cover_art = True
        except Exception:
            pass

    if has_cover_art:
        print("  Generating cover from PDF page 0...")
        pix = page.get_pixmap(dpi=150)
        pix.save(cover_path)
    elif title:
        print("  Generating styled cover (text-only title page)...")
        _generate_styled_cover(title, author, cover_path)
    else:
        print("  Generating cover from PDF page 0...")
        pix = page.get_pixmap(dpi=150)
        pix.save(cover_path)

    return cover_path

# =============================================================================
# SECTION 3: LAYOUT-AWARE ENGINE
# =============================================================================

_PAGE_NUM_RE_LAYOUT = re.compile(
    r"^[ivxlcdm]+$|^\d{1,4}$|^[ivxlcdm]+\s*[-–]\s*\d+$|^page\s+\d+$",
    re.IGNORECASE
)
_BLANK_PAGE_RE = re.compile(
    r"^this page (?:intentionally |)(?:left |)blank\.?$",
    re.IGNORECASE
)

# Th-ligature: some fonts (ACaslonPro, etc.) map the "Th" ligature glyph to just
# "T", dropping the "h". Only affects capital T; lowercase "th" is fine.
# This regex matches word-initial "T" followed by patterns that are NOT real English
# words but WOULD be if "h" were inserted after the "T".
_TH_LIGATURE_RE = re.compile(
    r"\bT(e\b|eir|em\b|ey\b|ey'|ere\b|ere[bdf]|ese\b|ose\b"
    r"|is\b|is[,.]|at\b|at'|us\b"
    r"|eme|esis|erap|eor[eiy]"
    r"|irte|ird\b|irst|ink|ings?\b|ieve"
    r"|umb|under|ought|ousand|orough"
    r"|reat(?!ed?\b|ing\b|ment\b)"
    r"|ree(?='s\b)"  # Tree's → Three's
    r"|ree\b(?=\s+(?:times|years|days|months|people|hundred|thousand|million|cups?|more)))"
)
# Post-fix for "Tree" in ordinal/counting contexts (Act Three, Number Three, etc.)
_TH_CONTEXT_RE = re.compile(
    r"\b(Act|Number|Chapter|Part|Grade|Step)\s+Tree\b"
)


def _detect_th_ligature_issue(pages_spans):
    """Check if a document has the Th ligature problem by counting telltale patterns."""
    te_count = 0
    for spans in pages_spans:
        for s in spans:
            if s.get("is_image"):
                continue
            text = s.get("text", "")
            if re.search(r"\bTe\b", text):
                te_count += 1
    return te_count >= 5


def _fix_th_ligature(text):
    """Insert missing 'h' after capital T in known Th-ligature patterns."""
    text = _TH_LIGATURE_RE.sub(lambda m: "Th" + m.group(1), text)
    text = _TH_CONTEXT_RE.sub(lambda m: m.group(1) + " Three", text)
    return text


def normalize_ligatures(text, fix_th=False):
    """Decompose Unicode ligatures (ﬀ→ff, ﬁ→fi, etc.) and optionally fix Th ligature."""
    text = unicodedata.normalize("NFKC", text)
    if fix_th:
        text = _fix_th_ligature(text)
    return text


_SPACING_SAFE = frozenset({'I', 'a', 'A', 'O'})

def collapse_spaced_text(text):
    """Collapse letter-spaced text from PDF small-caps or character spacing.

    Handles two patterns:
    1. Pure single-letter runs: 'F R E U D' -> 'FREUD'
    2. Small-caps fragments: 'N EW Y ORK' -> 'NEW YORK'
       (single uppercase + lowercase tail, only in ALL-CAPS context)
    """
    # Phase 1: collapse pure single-letter runs (3+)
    def _collapse_singles(m):
        chars = m.group(0).split()
        if all(c in _SPACING_SAFE for c in chars):
            return m.group(0)
        return ''.join(chars)
    text = re.sub(r'(?<!\w)([a-zA-Z] ){2,}[a-zA-Z](?!\w)', _collapse_singles, text)

    # Phase 2: small-caps fragment merging — only in ALL-CAPS spans
    # 'N EW Y ORK T IMES' -> 'NEW YORK TIMES'
    # Pattern: single uppercase letter + uppercase-starting fragment
    # We only do this in regions that are predominantly uppercase
    words = text.split()
    if len(words) < 3:
        return text

    upper_ratio = sum(1 for w in words if w and w[0].isupper()) / len(words)
    if upper_ratio < 0.5:
        return text

    # Merge: single uppercase letter followed by uppercase-start word
    # 'N' + 'EW' -> 'NEW', 'T' + 'IMES' -> 'TIMES', 'M' + 'AGAZINE' -> 'MAGAZINE'
    merged = []
    i = 0
    while i < len(words):
        w = words[i]
        if (len(w) == 1 and w.isupper() and w not in _SPACING_SAFE
                and i + 1 < len(words)
                and len(words[i + 1]) >= 2
                and words[i + 1][0].isupper()):
            merged.append(w + words[i + 1])
            i += 2
        else:
            merged.append(w)
            i += 1
    return ' '.join(merged)


def extract_spans(doc, pdf_path):
    """Extract text spans and images from every page, preserving block order."""
    pages_spans = []
    cover_image_path = None

    base_dir = os.path.dirname(pdf_path)
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    img_dir_name = base_name.replace(" ", "_") + "_images"
    img_dir = os.path.join(base_dir, img_dir_name)

    if not os.path.exists(img_dir):
        os.makedirs(img_dir)

    img_counter = 0

    for page_num in range(len(doc)):
        page = doc[page_num]
        h, w = page.rect.height, page.rect.width
        page_area = w * h
        d = page.get_text("dict", sort=True)
        spans = []

        for block in d.get("blocks", []):
            if block.get("type", 0) == 1:
                bbox = block.get("bbox", [0, 0, 0, 0])
                img_w = bbox[2] - bbox[0]
                img_h = bbox[3] - bbox[1]
                area_ratio = (img_w * img_h) / page_area if page_area > 0 else 0

                if area_ratio > 0.6:
                    # Full-bleed image: cover candidate on page 0, background overlay otherwise
                    # Reject tiny pixel images (e.g. 1x1 transparent placeholders)
                    real_w = block.get("width", 0)
                    real_h = block.get("height", 0)
                    if page_num == 0 and cover_image_path is None and real_w > 50 and real_h > 50:
                        ext = block.get("ext", "png")
                        img_path = os.path.join(img_dir, f"cover.{ext}")
                        with open(img_path, "wb") as f:
                            f.write(block["image"])
                        cover_image_path = img_path
                    # Skip background overlays on other pages
                elif area_ratio > 0.005:
                    # Real inline image — save it
                    ext = block.get("ext", "png")
                    img_path = os.path.join(img_dir, f"img_{page_num}_{img_counter}.{ext}")
                    with open(img_path, "wb") as f:
                        f.write(block["image"])
                    img_counter += 1

                    spans.append({
                        "is_image": True,
                        "src": os.path.join(img_dir_name, os.path.basename(img_path)),
                        "y0": bbox[1],
                        "y1": bbox[3],
                        "page_height": h,
                        "new_block": True,
                    })
                # else: tiny image (< 0.5% of page), skip as artifact
            else:
                first_span_in_block = True
                for line in block.get("lines", []):
                    # Merge adjacent spans on the same line to avoid
                    # char-level splitting (PDFs with per-char font changes)
                    merged_line_spans = []
                    for span in line.get("spans", []):
                        text = span.get("text", "")
                        if not text.strip():
                            continue
                        bbox = span["bbox"]
                        sz = round(span.get("size", 0), 1)
                        if merged_line_spans:
                            prev = merged_line_spans[-1]
                            gap = bbox[0] - prev["x1"]
                            avg_sz = (prev["size"] + sz) / 2
                            if gap < avg_sz * 0.6:
                                prev["text"] += text
                                prev["x1"] = bbox[2]
                                prev["y1"] = max(prev["y1"], bbox[3])
                                continue
                        merged_line_spans.append({
                            "text": text,
                            "size": sz,
                            "x0": bbox[0],
                            "x1": bbox[2],
                            "y0": bbox[1],
                            "y1": bbox[3],
                        })
                    for ms in merged_line_spans:
                        spans.append({
                            "is_image": False,
                            "text": ms["text"],
                            "size": ms["size"],
                            "x0": ms["x0"],
                            "y0": ms["y0"],
                            "y1": ms["y1"],
                            "page_height": h,
                            "new_block": first_span_in_block,
                        })
                        first_span_in_block = False

        pages_spans.append(spans)

    return pages_spans, cover_image_path, img_dir, img_dir_name


def detect_headers_footers(pages_spans, top_frac=0.10, bot_frac=0.10, min_repeat_frac=0.08):
    """Find text strings that repeat in the top/bottom margin across many pages."""
    top_lines, bot_lines, n_pages = Counter(), Counter(), len(pages_spans)
    for spans in pages_spans:
        if not spans:
            continue
        ph = spans[0]["page_height"]
        seen_top, seen_bot = set(), set()
        top_by_y, bot_by_y = {}, {}
        for s in spans:
            if s.get("is_image", False):
                continue
            norm = " ".join(s["text"].split()).strip()
            if not norm or len(norm) > 80:
                continue
            if s["y0"] < ph * top_frac:
                seen_top.add(norm)
                y_key = round(s["y0"])
                top_by_y.setdefault(y_key, []).append(norm)
            if s["y1"] > ph * (1 - bot_frac):
                seen_bot.add(norm)
                y_key = round(s["y1"])
                bot_by_y.setdefault(y_key, []).append(norm)
        for t in seen_top:
            top_lines[t] += 1
        for t in seen_bot:
            bot_lines[t] += 1
        for words in top_by_y.values():
            if len(words) > 1:
                full_line = " ".join(words)
                if len(full_line) <= 80:
                    top_lines[full_line] += 1
        for words in bot_by_y.values():
            if len(words) > 1:
                full_line = " ".join(words)
                if len(full_line) <= 80:
                    bot_lines[full_line] += 1

    threshold = max(3, int(n_pages * min_repeat_frac))
    # Short strings (<=2 chars) are ligature artifacts or drop caps, not headers
    return (
        {t for t, c in top_lines.items() if c >= threshold and len(t) > 2},
        {t for t, c in bot_lines.items() if c >= threshold and len(t) > 2},
    )


def classify_heading_sizes(pages_spans):
    """Determine body font size and build a map of heading sizes -> h1/h2/h3."""
    sizes = Counter()
    for spans in pages_spans:
        for s in spans:
            if not s.get("is_image", False):
                sizes[s["size"]] += len(s["text"])
    body_size = sizes.most_common(1)[0][0] if sizes else 11.0

    total_text_chars = sum(sizes.values())
    h_sizes = Counter()
    h_chars = Counter()
    h_pages = {}  # track which pages each heading size appears on
    for page_idx, spans in enumerate(pages_spans):
        for s in spans:
            if s.get("is_image", False):
                continue
            text = s.get("text", "").strip()
            if not text:
                continue
            # Skip single-character spans at large sizes (drop caps)
            if len(text) == 1 and s["size"] > body_size + 5:
                continue
            if s["size"] > body_size + 1.5:
                h_sizes[s["size"]] += 1
                h_chars[s["size"]] += len(text)
                h_pages.setdefault(s["size"], set()).add(page_idx)

    # A heading size should:
    # 1. Use less total text than body (max 10%)
    # 2. Appear on more than 2 pages (not just the title page)
    char_cutoff = max(100, int(total_text_chars * 0.10))
    candidates = {
        size: c for size, c in h_sizes.items()
        if h_chars[size] <= char_cutoff and len(h_pages.get(size, set())) >= 3
    }

    # If no candidates pass the page-spread filter, fall back to any that pass char cutoff
    if not candidates:
        candidates = {size: c for size, c in h_sizes.items() if h_chars[size] <= char_cutoff}

    heading_map = {
        size: f"h{i + 1}"
        for i, size in enumerate(sorted(candidates.keys(), reverse=True)[:3])
    }
    return body_size, heading_map


_PAGE_NUM_RE_FIX = re.compile(r"^\d{1,4}$")

def _fix_paragraph_breaks(pages_spans, heading_sizes, header_set=None, footer_set=None):
    """Merge single-line PDF blocks into proper paragraphs using indentation and gap signals."""
    if header_set is None:
        header_set = set()
    if footer_set is None:
        footer_set = set()
    # Global single-line-block ratio (block whose vertical extent < 2x font size)
    total_body_blocks = 0
    single_line_blocks = 0
    for spans in pages_spans:
        block_y0, block_y1, block_size = None, None, None
        for s in spans:
            if s.get("is_image"):
                if block_y0 is not None:
                    total_body_blocks += 1
                    if (block_y1 - block_y0) < block_size * 2.0:
                        single_line_blocks += 1
                block_y0, block_y1, block_size = None, None, None
                continue
            if s.get("new_block"):
                if block_y0 is not None:
                    total_body_blocks += 1
                    if (block_y1 - block_y0) < block_size * 2.0:
                        single_line_blocks += 1
                block_y0 = s.get("y0", 0)
                block_y1 = s.get("y1", 0)
                block_size = s.get("size", 12)
            else:
                block_y1 = s.get("y1", block_y1 or 0)
        if block_y0 is not None:
            total_body_blocks += 1
            if (block_y1 - block_y0) < block_size * 2.0:
                single_line_blocks += 1
    global_apply = total_body_blocks > 0 and single_line_blocks / total_body_blocks >= 0.15

    # Within-page merging: apply if global threshold met OR per-page threshold met
    for spans in pages_spans:
        if not spans:
            continue
        if not global_apply:
            page_blocks = 0
            page_single = 0
            b_y0, b_y1, b_sz = None, None, None
            for s in spans:
                if s.get("is_image"):
                    if b_y0 is not None:
                        page_blocks += 1
                        if (b_y1 - b_y0) < b_sz * 2.0:
                            page_single += 1
                    b_y0, b_y1, b_sz = None, None, None
                    continue
                if s.get("new_block"):
                    if b_y0 is not None:
                        page_blocks += 1
                        if (b_y1 - b_y0) < b_sz * 2.0:
                            page_single += 1
                    b_y0 = s.get("y0", 0)
                    b_y1 = s.get("y1", 0)
                    b_sz = s.get("size", 12)
                else:
                    b_y1 = s.get("y1", b_y1 or 0)
            if b_y0 is not None:
                page_blocks += 1
                if (b_y1 - b_y0) < b_sz * 2.0:
                    page_single += 1
            if page_blocks < 3 or page_single / page_blocks < 0.15:
                continue
        body_x0s = Counter()
        body_gaps = []
        prev_y1 = None
        for s in spans:
            if s.get("is_image") or s.get("size", 0) in heading_sizes:
                prev_y1 = None
                continue
            body_x0s[round(s.get("x0", 0))] += 1
            if prev_y1 is not None:
                gap = s["y0"] - prev_y1
                if gap > 0:
                    body_gaps.append(gap)
            prev_y1 = s["y1"]
        if not body_x0s:
            continue
        dominant_x0 = body_x0s.most_common(1)[0][0]
        indent_threshold = max(5.0, abs(dominant_x0) * 0.08)
        if body_gaps:
            body_gaps_sorted = sorted(body_gaps)
            typical_gap = body_gaps_sorted[len(body_gaps_sorted) // 2]
        else:
            typical_gap = 0
        gap_threshold = typical_gap * 1.8 if typical_gap > 0 else 999
        prev_y1 = None
        after_heading_or_image = True
        for s in spans:
            if s.get("is_image"):
                after_heading_or_image = True
                prev_y1 = None
                continue
            if s.get("size", 0) in heading_sizes:
                after_heading_or_image = True
                prev_y1 = s["y1"]
                continue
            if s.get("new_block") and not after_heading_or_image and prev_y1 is not None:
                x0 = round(s.get("x0", 0))
                gap = s["y0"] - prev_y1
                is_indented = x0 > dominant_x0 + indent_threshold
                has_large_gap = gap > gap_threshold
                if not is_indented and not has_large_gap:
                    s["new_block"] = False
            after_heading_or_image = False
            prev_y1 = s["y1"]

    # Cross-page merging
    def _is_marginal(s, ph):
        text = s.get("text", "").strip()
        if not text:
            return True
        if _PAGE_NUM_RE_FIX.match(text):
            return True
        if text in header_set or text in footer_set:
            return True
        if (s["y0"] < ph * 0.07 or s["y1"] > ph * 0.93) and len(text) <= 60:
            return True
        # ALL CAPS short text in top/bottom 10% is a running header
        if (s["y0"] < ph * 0.10 or s["y1"] > ph * 0.90) and len(text) <= 60:
            alpha = [c for c in text if c.isalpha()]
            if alpha and all(c.isupper() for c in alpha):
                return True
        return False

    SENTENCE_ENDERS = set('.!?:;\'")“”''')

    for pi in range(len(pages_spans) - 1):
        cur_page = pages_spans[pi]
        nxt_page = pages_spans[pi + 1]
        if not cur_page or not nxt_page:
            continue
        ph_cur = cur_page[0]["page_height"] if cur_page else 0
        ph_nxt = nxt_page[0]["page_height"] if nxt_page else 0
        last_body = None
        for s in reversed(cur_page):
            if s.get("is_image") or s.get("size", 0) in heading_sizes:
                continue
            if _is_marginal(s, ph_cur):
                continue
            if s.get("text", "").strip():
                last_body = s
                break
        if last_body is None:
            continue
        last_text = last_body["text"].strip()
        if not last_text:
            continue
        last_char = last_text[-1]
        first_body = None
        for s in nxt_page:
            if s.get("is_image") or s.get("size", 0) in heading_sizes:
                continue
            if _is_marginal(s, ph_nxt):
                continue
            if s.get("text", "").strip():
                first_body = s
                break
        if first_body is None:
            continue
        first_text = first_body["text"].strip()
        if not first_text:
            continue
        first_char = first_text[0]
        # Merge if mid-sentence (not ending with punctuation + next starts lowercase)
        # or if last word is hyphenated (word split across pages)
        if last_char not in SENTENCE_ENDERS and first_char.islower():
            first_body["new_block"] = False
        elif last_char == "-" and last_text[-2:] != " -":
            first_body["new_block"] = False




def _slugify(text):
    """Convert heading text to pandoc-style heading ID (lowercase, hyphens)."""
    text = re.sub(r"<[^>]+>", "", text)  # strip any HTML tags
    text = html_module.unescape(text)
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s.\-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug


def _linkify_toc_page(html_text, heading_slugs):
    """Convert printed TOC paragraphs into hyperlinks to actual chapter headings."""
    lines = html_text.split("\n")
    result = []
    for line in lines:
        m = re.match(r"^<p>(.+)</p>$", line)
        if m:
            text = m.group(1)
            plain = html_module.unescape(text)
            slug = _slugify(plain)
            if slug in heading_slugs:
                line = f'<p><a href="#{slug}">{text}</a></p>'
        result.append(line)
    return "\n".join(result)


def strip_marginal_markers(text):
    text = re.sub(r"\|\s*[\dlLoOuU<>]{1,4}\s*\|", " ", text)
    text = re.sub(
        r"\b1(\d{2})1\b",
        lambda m: " " if int(m.group(1)) % 5 == 0 else m.group(0),
        text,
    )
    return re.sub(r"\s{2,}", " ", text).strip()


def build_html_layout(pages_spans, header_set, footer_set, heading_map, footnote_thresh):
    """Build semantic HTML from extracted spans, with proper heading hierarchy."""
    heading_sizes = set(heading_map.keys())
    parts, footnotes = [], []
    parts.append("<html><body>")
    cur_para = []
    cur_footnote = []

    for spans in pages_spans:
        if not spans:
            continue
        ph = spans[0]["page_height"]

        for s in spans:
            if s.get("is_image", False):
                if cur_para:
                    parts.append("<p>" + strip_marginal_markers(" ".join(cur_para)) + "</p>")
                    cur_para = []
                parts.append(
                    f'<div class="image-block"><img src="{html_module.escape(s["src"])}" '
                    f'style="max-width:100%;" alt="" /></div>'
                )
                continue

            norm = " ".join(s["text"].split()).strip()
            if not norm:
                continue

            is_heading = s["size"] in heading_sizes

            # Skip "this page intentionally left blank"
            if _BLANK_PAGE_RE.match(norm):
                continue

            # Filter running headers/footers — but NOT if the span is at a heading size
            # Filter BEFORE flushing cur_para so page numbers don't split paragraphs
            if not is_heading:
                if norm in header_set or norm in footer_set:
                    continue
                if (s["y0"] < ph * 0.07 or s["y1"] > ph * 0.93) and len(norm) <= 60:
                    continue
                if _PAGE_NUM_RE_LAYOUT.match(norm):
                    continue
                # ALL CAPS short text in top/bottom 10% is likely a running header
                if (s["y0"] < ph * 0.10 or s["y1"] > ph * 0.90) and len(norm) <= 60:
                    alpha = [c for c in norm if c.isalpha()]
                    if alpha and all(c.isupper() for c in alpha):
                        continue

            escaped = html_module.escape(norm)

            if s.get("new_block", False) and cur_para:
                parts.append("<p>" + strip_marginal_markers(" ".join(cur_para)) + "</p>")
                cur_para = []
            if s.get("new_block", False) and cur_footnote:
                footnotes.append(" ".join(cur_footnote))
                cur_footnote = []

            # Footnotes: small text near bottom of page
            if not is_heading and s["size"] <= footnote_thresh and s["y1"] > ph * 0.85:
                cur_footnote.append(escaped)
                continue

            # If we were collecting footnotes and hit non-footnote content, flush
            if cur_footnote:
                footnotes.append(" ".join(cur_footnote))
                cur_footnote = []

            if is_heading:
                # Single characters at heading sizes are drop caps, not headings
                if len(norm) == 1:
                    cur_para.append(escaped)
                    continue

                if cur_para:
                    parts.append("<p>" + strip_marginal_markers(" ".join(cur_para)) + "</p>")
                    cur_para = []

                tag = heading_map[s["size"]]
                closing_tag = f"</{tag}>"
                # Merge consecutive spans at the same heading size into one heading
                if parts and parts[-1].endswith(closing_tag):
                    parts[-1] = parts[-1][: -len(closing_tag)] + " " + escaped + closing_tag
                else:
                    parts.append(f"<{tag}>{escaped}{closing_tag}")
            else:
                # Hyphenation merge: only when previous chunk ends with a real hyphen
                if cur_para and cur_para[-1].endswith("-") and not cur_para[-1].endswith(" -"):
                    cur_para[-1] = cur_para[-1][:-1] + escaped
                else:
                    cur_para.append(escaped)

    if cur_para:
        parts.append("<p>" + strip_marginal_markers(" ".join(cur_para)) + "</p>")
    if cur_footnote:
        footnotes.append(" ".join(cur_footnote))

    if footnotes:
        parts.append("<hr/><h2>Notes</h2>")
        for fn in footnotes:
            parts.append(f"<p class='footnote'>{fn}</p>")
    parts.append("</body></html>")
    html_text = "\n".join(parts)
    # Post-process: merge paragraphs split at hyphens (word-wrap artifacts)
    html_text = re.sub(
        r"(\w)[\-­]\s*</p>\n<p>([a-z])",
        r"\1\2",
        html_text,
    )
    # Post-process: merge paragraphs split mid-sentence
    # If a <p> ends without sentence-ending punctuation and the next <p> starts lowercase, join them
    # Also handles image blocks between split paragraphs
    _SENT_ENDERS = set(".!?:;)" + chr(0x201C) + chr(0x201D) + chr(0x2018) + chr(0x2019) + chr(34) + chr(39))
    _P_RE = re.compile(r'^(<p[^>]*>)(.*)</p>$')
    changed = True
    while changed:
        lines = html_text.split('\n')
        changed = False
        merged = []
        i = 0
        while i < len(lines):
            m_cur = _P_RE.match(lines[i])
            if m_cur:
                cur_tag, cur_content = m_cur.group(1), m_cur.group(2).rstrip()
                if cur_content and cur_content[-1] not in _SENT_ENDERS:
                    j = i + 1
                    between = []
                    while j < len(lines) and not _P_RE.match(lines[j]):
                        between.append(lines[j])
                        j += 1
                    m_nxt = _P_RE.match(lines[j]) if j < len(lines) else None
                    if m_nxt:
                        nxt_content = m_nxt.group(2)
                        if nxt_content and nxt_content[0].islower():
                            merged.append(f'{cur_tag}{cur_content} {nxt_content}</p>')
                            merged.extend(between)
                            i = j + 1
                            changed = True
                            continue
            merged.append(lines[i])
            i += 1
        html_text = '\n'.join(merged)
    return html_text


def _looks_like_person_name(text):
    """Heuristic: does this text look like a person's name?"""
    text = text.strip()
    # Remove parenthetical content for analysis
    clean = re.sub(r"\([^)]*\)", "", text).strip()
    words = clean.split()
    if len(words) < 1 or len(words) > 5:
        return False
    # Titles often start with articles or contain prepositions/conjunctions
    title_words = {"the", "a", "an", "on", "in", "of", "and", "or", "for", "to", "with",
                   "how", "why", "what", "when", "where", "every", "all", "no", "my", "your"}
    lower_words = {w.lower() for w in words}
    if lower_words & title_words:
        return False
    # Parenthetical content suggests a title, not a name
    if "(" in text:
        return False
    # Single well-known author names
    if len(words) == 1 and words[0][0].isupper():
        return True
    # 2-4 capitalized words with no function words = likely a name
    if len(words) >= 2:
        capitalized = sum(1 for w in words if w[0].isupper())
        return capitalized >= len(words) * 0.7
    return False


def _extract_title_from_titlepage(doc):
    """Try to get title/author from a clear 'Title by Author' pattern on early pages."""
    for page_num in range(min(3, len(doc))):
        page = doc[page_num]
        d = page.get_text("dict", sort=True)

        spans = []
        for block in d.get("blocks", []):
            if block.get("type", 0) != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if text and len(text) > 1:
                        spans.append((round(span["size"], 1), text))

        if len(spans) < 2:
            continue

        sizes = sorted(set(s[0] for s in spans), reverse=True)
        max_size = sizes[0]
        if max_size < sizes[-1] * 1.3:
            continue

        title_spans = [s[1] for s in spans if s[0] == max_size]
        full = " ".join(title_spans)

        by_match = re.search(r"\s+by\s+(.+)$", full, re.IGNORECASE)
        if by_match:
            candidate_title = full[:by_match.start()].strip()
            if candidate_title.lower() not in ("copyright", "published", "edited", "translated",
                                                "printed", "distributed", "produced"):
                return candidate_title, by_match.group(1).strip()

        # Check for "by" as a separate span at the same size
        _NON_TITLES = {"copyright", "published", "edited", "translated",
                       "printed", "distributed", "produced"}
        for i, ts in enumerate(title_spans):
            if ts.lower().strip() == "by":
                before = " ".join(title_spans[:i]).strip()
                if before.lower() in _NON_TITLES:
                    break
                after_same = " ".join(title_spans[i + 1:]).strip()
                if not after_same:
                    remaining = [s[1] for s in spans if s[0] < max_size]
                    after_same = remaining[0] if remaining else ""
                if before:
                    return before, after_same
                break

    return "", ""


def extract_title_author(doc, pdf_path):
    """Extract title and author from metadata, title-page text, or filename."""
    meta = doc.metadata or {}
    meta_title = meta.get("title", "").strip()
    meta_author = meta.get("author", "").strip()

    if meta_title and (meta_title.endswith((".djvu", ".pdf")) or len(meta_title) < 3):
        meta_title = ""
    if meta_author and len(meta_author) < 3:
        meta_author = ""

    # Check for clear "Title by Author" pattern on title pages
    page_title, page_author = _extract_title_from_titlepage(doc)
    if page_title:
        title = page_title
        author = page_author or meta_author
        return title, author

    # Fall back to metadata
    if meta_title:
        return meta_title, meta_author

    # Last resort: parse filename
    basename = os.path.splitext(os.path.basename(pdf_path))[0]
    clean_basename = re.sub(r"\{[^}]*\}", "", basename).strip()

    if " - " in clean_basename:
        parts = clean_basename.split(" - ")
        if len(parts) == 2:
            left, right = parts[0].strip(), parts[1].strip()
        else:
            left = parts[0].strip()
            right = " - ".join(parts[1:]).strip()

        left_is_name = _looks_like_person_name(left)
        right_is_name = _looks_like_person_name(right)
        if left_is_name and not right_is_name:
            return right, left
        elif right_is_name and not left_is_name:
            return left, right
        else:
            if len(right) >= len(left):
                return right, left
            return left, right

    return clean_basename, meta_author

# =============================================================================
# SECTION 4: MASTER ORCHESTRATOR & BATCH LOGGING
# =============================================================================

def process_file(pdf_path, output_dir=None):
    """Convert a single PDF to EPUB. Returns (filename, category, status, message)."""
    filename = os.path.basename(pdf_path)
    base_dir = os.path.dirname(pdf_path)
    base_name = os.path.splitext(filename)[0]

    if output_dir:
        epub_path = os.path.abspath(os.path.join(output_dir, base_name + ".epub"))
    else:
        epub_path = os.path.join(base_dir, base_name + ".epub")

    print(f"\n--- PROCESSING: {filename} ---")

    try:
        category, note = analyze_pdf(pdf_path)
        print(f"  [{category}] {note}")

        if category == "SCANNED":
            print(f"  Skipping — scanned PDF without usable text layer.")
            return filename, category, "SKIPPED", note

        doc = fitz.open(pdf_path)
        title, author = extract_title_author(doc, pdf_path)
        print(f"  Title: {title}")
        if author:
            print(f"  Author: {author}")

        img_dir_name = base_name.replace(" ", "_") + "_images"
        img_dir = os.path.join(base_dir, img_dir_name)
        if not os.path.exists(img_dir):
            os.makedirs(img_dir)

        if category in ["TEXT_BASED", "COMPLEX", "MIXED"]:
            pages_spans, cover_image_path, img_dir, img_dir_name = extract_spans(doc, pdf_path)

            # Normalize ligatures in all text spans
            fix_th = _detect_th_ligature_issue(pages_spans)
            if fix_th:
                print("  Detected Th-ligature issue — applying fix")
            for page_spans in pages_spans:
                for s in page_spans:
                    if not s.get("is_image") and "text" in s:
                        s["text"] = normalize_ligatures(s["text"], fix_th=fix_th)
                        s["text"] = collapse_spaced_text(s["text"])

            header_set, footer_set = detect_headers_footers(pages_spans)
            body_size, heading_map = classify_heading_sizes(pages_spans)
            _fix_paragraph_breaks(pages_spans, set(heading_map.keys()), header_set, footer_set)
            print(f"  Body size: {body_size}, Headings: {heading_map}")
            print(f"  Filtered headers: {header_set}")
            html = build_html_layout(pages_spans, header_set, footer_set, heading_map, body_size - 1.0)

            # Linkify printed TOC pages: match paragraphs to actual chapter headings
            heading_slugs = {_slugify(m.group(1)) for m in re.finditer(r"<h[12]>([^<]+)</h[12]>", html)}
            if heading_slugs:
                html = _linkify_toc_page(html, heading_slugs)
        else:
            html = build_html_scanned(doc)

        cover_image_path = ensure_cover(doc, pdf_path, img_dir, img_dir_name, title=title, author=author)
        doc.close()

        html_path = os.path.join(base_dir, base_name + ".html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)

        # Build pandoc command with proper metadata
        cmd = [
            "pandoc", os.path.basename(html_path),
            "-o", epub_path,
            "--metadata", f"title={title}",
            f"--resource-path={base_dir}",
        ]
        if author:
            cmd.extend(["--metadata", f"creator={author}"])
        if cover_image_path and os.path.exists(cover_image_path):
            rel_cover = os.path.relpath(cover_image_path, base_dir)
            cmd.append(f"--epub-cover-image={rel_cover}")

        print(f"  Running pandoc...")
        result = subprocess.run(cmd, cwd=base_dir, capture_output=True, text=True)
        if result.returncode != 0:
            msg = result.stderr.strip()
            print(f"  Pandoc Error: {msg}", file=sys.stderr)
            return filename, category, "FAILED", msg

        print(f"  -> {epub_path}")
        return filename, category, "SUCCESS", "", title, author, epub_path

    except Exception as e:
        msg = str(e)
        print(f"  Error on {filename}: {msg}")
        return filename, "ERROR", "FAILED", msg


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PDF to EPUB Converter")
    parser.add_argument("target", help="Path to a directory containing PDFs, or a single PDF file")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip pauses")
    parser.add_argument("-o", "--output", help="Output directory for EPUBs (default: same as input)")
    args = parser.parse_args()

    if os.path.isdir(args.target):
        files = sorted([
            os.path.join(args.target, f)
            for f in os.listdir(args.target)
            if f.lower().endswith(".pdf")
        ])
    else:
        files = [args.target]

    if not files:
        print("No PDF files found.")
        sys.exit(1)

    output_dir = args.output
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)

    print(f"Starting batch conversion for {len(files)} file(s)...\n")

    results = []
    for f in files:
        results.append(process_file(f, output_dir))

    print("\n" + "=" * 80)
    print(" " * 27 + "BATCH CONVERSION SUMMARY")
    print("=" * 80)
    print(f"{'STATUS':<10} | {'CATEGORY':<12} | {'FILE'}")
    print("-" * 80)
    for filename, category, status, *_ in results:
        icon = {"SUCCESS": "+", "FAILED": "X", "SKIPPED": "-"}.get(status, "?")
        print(f"[{icon}] {status:<8} | {category:<12} | {filename}")
    print("=" * 80 + "\n")

    # Non-blocking author metadata prompt
    missing_author = []
    for r in results:
        if len(r) >= 7:
            filename, category, status, msg, title, author, epub_path = r
            if status == "SUCCESS" and not author:
                missing_author.append((title, epub_path))

    if missing_author:
        print(f"{len(missing_author)} book(s) have no author metadata:")
        for i, (t, ep) in enumerate(missing_author, 1):
            print(f"  {i}. {t}")
        print()
        try:
            ans = input("Enter author names? (y to fill in, Enter to skip): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans == "y":
            for i, (t, ep) in enumerate(missing_author, 1):
                try:
                    name = input(f"  Author for '{t}': ").strip()
                except (EOFError, KeyboardInterrupt):
                    name = ""
                if name:
                    html_path = ep.replace(".epub", ".html")
                    base_dir = os.path.dirname(html_path)
                    cmd = [
                        "pandoc", os.path.basename(html_path),
                        "-o", ep,
                        "--metadata", f"title={t}",
                        "--metadata", f"creator={name}",
                        f"--resource-path={base_dir}",
                    ]
                    cover_candidates = [
                        os.path.join(base_dir, os.path.splitext(os.path.basename(ep))[0] + "_images", "cover.png"),
                        os.path.join(base_dir, os.path.splitext(os.path.basename(ep))[0] + "_images", "cover.jpg"),
                    ]
                    for cc in cover_candidates:
                        if os.path.exists(cc):
                            cmd.append(f"--epub-cover-image={os.path.relpath(cc, base_dir)}")
                            break
                    result = subprocess.run(cmd, cwd=base_dir, capture_output=True, text=True)
                    if result.returncode == 0:
                        print(f"    Updated: {os.path.basename(ep)}")
                    else:
                        print(f"    Failed to update: {result.stderr.strip()}")
            print()
