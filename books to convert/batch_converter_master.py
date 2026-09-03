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


def ensure_cover(doc, pdf_path, img_dir, img_dir_name):
    """Ensure there's a cover image. If not, render page 0 at 150 DPI."""
    covers = glob.glob(os.path.join(img_dir, "cover.*"))
    if covers:
        return covers[0]

    print("  Generating cover from PDF page 0...")
    page = doc[0]
    pix = page.get_pixmap(dpi=150)
    cover_path = os.path.join(img_dir, "cover.jpeg")
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
                    if page_num == 0 and cover_image_path is None:
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
                    for span in line.get("spans", []):
                        text = span.get("text", "")
                        if text.strip():
                            spans.append({
                                "is_image": False,
                                "text": text,
                                "size": round(span.get("size", 0), 1),
                                "y0": span["bbox"][1],
                                "y1": span["bbox"][3],
                                "page_height": h,
                                "new_block": first_span_in_block,
                            })
                            first_span_in_block = False

        pages_spans.append(spans)

    return pages_spans, cover_image_path, img_dir, img_dir_name


def detect_headers_footers(pages_spans, top_frac=0.07, bot_frac=0.07, min_repeat_frac=0.15):
    """Find text strings that repeat in the top/bottom margin across many pages."""
    top_lines, bot_lines, n_pages = Counter(), Counter(), len(pages_spans)
    for spans in pages_spans:
        if not spans:
            continue
        ph = spans[0]["page_height"]
        seen_top, seen_bot = set(), set()
        for s in spans:
            if s.get("is_image", False):
                continue
            norm = " ".join(s["text"].split()).strip()
            if not norm or len(norm) > 80:
                continue
            if s["y0"] < ph * top_frac:
                seen_top.add(norm)
            if s["y1"] > ph * (1 - bot_frac):
                seen_bot.add(norm)
        for t in seen_top:
            top_lines[t] += 1
        for t in seen_bot:
            bot_lines[t] += 1

    threshold = max(3, int(n_pages * min_repeat_frac))
    # Single characters are never running headers -- they're drop caps or margin marks
    return (
        {t for t, c in top_lines.items() if c >= threshold and len(t) > 1},
        {t for t, c in bot_lines.items() if c >= threshold and len(t) > 1},
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

    for spans in pages_spans:
        if not spans:
            continue
        ph = spans[0]["page_height"]
        cur_para = []

        for s in spans:
            if s.get("new_block", False) and cur_para:
                parts.append("<p>" + strip_marginal_markers(" ".join(cur_para)) + "</p>")
                cur_para = []

            if s.get("is_image", False):
                parts.append(
                    f'<div class="image-block"><img src="{html_module.escape(s["src"])}" '
                    f'style="max-width:100%;" alt="" /></div>'
                )
                continue

            norm = " ".join(s["text"].split()).strip()
            if not norm:
                continue

            escaped = html_module.escape(norm)
            is_heading = s["size"] in heading_sizes

            # Skip "this page intentionally left blank"
            if _BLANK_PAGE_RE.match(norm):
                continue

            # Filter running headers/footers — but NOT if the span is at a heading size
            if not is_heading:
                if norm in header_set or norm in footer_set:
                    continue
                if (s["y0"] < ph * 0.07 or s["y1"] > ph * 0.93) and len(norm) <= 60:
                    continue
                if _PAGE_NUM_RE_LAYOUT.match(norm):
                    continue

            # Footnotes: small text near bottom of page
            if not is_heading and s["size"] <= footnote_thresh and s["y1"] > ph * 0.85:
                footnotes.append(escaped)
                continue

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

    if footnotes:
        parts.append("<hr/><h2>Notes</h2>")
        for fn in footnotes:
            parts.append(f"<p class='footnote'>{fn}</p>")
    parts.append("</body></html>")
    return "\n".join(parts)


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


def extract_title_author(doc, pdf_path):
    """Try to extract title and author from PDF metadata or first pages."""
    meta = doc.metadata or {}
    title = meta.get("title", "").strip()
    author = meta.get("author", "").strip()

    basename = os.path.splitext(os.path.basename(pdf_path))[0]
    # Clean up libgen-style suffixes: {Author}{ID} etc.
    clean_basename = re.sub(r"\{[^}]*\}", "", basename).strip()
    # Clean up junk metadata (DjVu filenames, etc.)
    if title and (title.endswith((".djvu", ".pdf")) or len(title) < 3):
        title = ""
    if author and len(author) < 3:
        author = ""

    if not title:
        if " - " in clean_basename:
            # Split on first " - " only; use the rest as-is
            parts = clean_basename.split(" - ")
            if len(parts) == 2:
                left, right = parts[0].strip(), parts[1].strip()
            else:
                # Multiple separators: first part is likely author, rest is title
                left = parts[0].strip()
                right = " - ".join(parts[1:]).strip()

            left_is_name = _looks_like_person_name(left)
            right_is_name = _looks_like_person_name(right)
            if left_is_name and not right_is_name:
                author = author or left
                title = right
            elif right_is_name and not left_is_name:
                author = author or right
                title = left
            else:
                if len(right) >= len(left):
                    title = right
                    author = author or left
                else:
                    title = left
                    author = author or right
        else:
            title = clean_basename

    return title, author

# =============================================================================
# SECTION 4: MASTER ORCHESTRATOR & BATCH LOGGING
# =============================================================================

def process_file(pdf_path, output_dir=None):
    """Convert a single PDF to EPUB. Returns (filename, category, status, message)."""
    filename = os.path.basename(pdf_path)
    base_dir = os.path.dirname(pdf_path)
    base_name = os.path.splitext(filename)[0]

    if output_dir:
        epub_path = os.path.join(output_dir, base_name + ".epub")
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
            header_set, footer_set = detect_headers_footers(pages_spans)
            body_size, heading_map = classify_heading_sizes(pages_spans)
            print(f"  Body size: {body_size}, Headings: {heading_map}")
            print(f"  Filtered headers: {header_set}")
            html = build_html_layout(pages_spans, header_set, footer_set, heading_map, body_size - 1.0)
        else:
            html = build_html_scanned(doc)

        cover_image_path = ensure_cover(doc, pdf_path, img_dir, img_dir_name)
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
        return filename, category, "SUCCESS", ""

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
