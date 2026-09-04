#!/usr/bin/env python3
"""Comprehensive EPUB quality audit."""

import os, re, sys, zipfile

EPUB_DIR = "Processed PDFs to EPUB"
SENTENCE_ENDERS = set('.!?\'":;)' + chr(0x201c) + chr(0x201d) + chr(0x2018) + chr(0x2019))

def audit_epub(epub_path):
    results = {"file": os.path.basename(epub_path), "issues": [], "warnings": [], "stats": {}}
    try:
        with zipfile.ZipFile(epub_path) as z:
            names = z.namelist()

            # 1. METADATA from OPF
            opf_files = [n for n in names if n.endswith('.opf')]
            title = author = None
            has_cover = False
            if opf_files:
                opf = z.read(opf_files[0]).decode('utf-8', errors='replace')
                tm = re.search(r'<dc:title[^>]*>([^<]+)</dc:title>', opf)
                if tm: title = tm.group(1).strip()
                am = re.search(r'<dc:creator[^>]*>([^<]+)</dc:creator>', opf)
                if am: author = am.group(1).strip()
                if re.search(r'cover', opf, re.IGNORECASE):
                    has_cover = True

            results["stats"]["title"] = title or "(none)"
            results["stats"]["author"] = author or "(none)"
            if not title: results["issues"].append("TITLE: No title in metadata")
            if not author: results["warnings"].append("AUTHOR: No author in metadata")
            if not has_cover: results["issues"].append("COVER: No cover reference in OPF")

            # 2. TOC from nav.xhtml
            nav_files = [n for n in names if 'nav' in n.lower() and n.endswith('.xhtml')]
            toc_count = 0
            if nav_files:
                nav = z.read(nav_files[0]).decode('utf-8', errors='replace')
                toc_count = len(re.findall(r'<a[^>]*href="[^"]*"', nav))
            if toc_count == 0:
                results["warnings"].append("TOC: No entries in navigation")
            results["stats"]["toc_entries"] = toc_count

            # 3. CONTENT ANALYSIS
            content_files = sorted([n for n in names if n.endswith('.xhtml') and 'ch' in os.path.basename(n)])
            if not content_files:
                content_files = sorted([n for n in names if n.endswith('.xhtml')])

            total_paras = 0
            mid_sentence_breaks = 0
            heading_count = 0
            image_count = 0
            page_num_leaks = 0
            ligature_issues = 0
            para_lengths = []

            for f in content_files:
                html = z.read(f).decode('utf-8', errors='replace')
                para_matches = re.findall(r'<p([^>]*)>(.*?)</p>', html, re.DOTALL)
                paras = [(attrs, content) for attrs, content in para_matches]
                for i, (attrs, p) in enumerate(paras):
                    text = re.sub(r'<[^>]+>', '', p).strip()
                    total_paras += 1
                    if not text: continue
                    para_lengths.append(len(text))

                    is_footnote = 'footnote' in attrs
                    if i < len(paras) - 1 and not is_footnote:
                        nxt_attrs, nxt_p = paras[i+1]
                        nxt = re.sub(r'<[^>]+>', '', nxt_p).strip()
                        if text and nxt and 'footnote' not in nxt_attrs:
                            lc = text[-1]
                            fw = nxt.split()[0] if nxt.split() else ''
                            if lc not in SENTENCE_ENDERS and fw and fw[0].islower():
                                mid_sentence_breaks += 1

                    if re.match(r'^\d{1,4}$', text): page_num_leaks += 1
                    for ch in ['ﬀ', 'ﬁ', 'ﬂ', 'ﬃ', 'ﬄ']:
                        if ch in text:
                            ligature_issues += 1
                            break

                heading_count += len(re.findall(r'<h[1-6]', html))
                image_count += len(re.findall(r'<img\b', html))

            results["stats"]["paragraphs"] = total_paras
            results["stats"]["headings"] = heading_count
            results["stats"]["images"] = image_count

            if mid_sentence_breaks > 0:
                pct = mid_sentence_breaks / max(total_paras, 1) * 100
                if pct > 5:
                    results["issues"].append(f"LINE_BREAKS: {mid_sentence_breaks} mid-sentence breaks ({pct:.1f}%)")
                elif pct > 1:
                    results["warnings"].append(f"LINE_BREAKS: {mid_sentence_breaks} mid-sentence breaks ({pct:.1f}%)")

            if page_num_leaks > 20:
                results["issues"].append(f"PAGE_NUMS: {page_num_leaks} leaked page numbers")
            elif page_num_leaks > 10:
                results["warnings"].append(f"PAGE_NUMS: {page_num_leaks} possible page number leaks")

            if ligature_issues > 0:
                results["warnings"].append(f"LIGATURES: {ligature_issues} undecoded ligatures")

            if heading_count == 0:
                results["issues"].append("CHAPTERS: No headings found")
            elif heading_count < 3:
                results["warnings"].append(f"CHAPTERS: Only {heading_count} headings")

            if para_lengths:
                avg = sum(para_lengths) / len(para_lengths)
                med = sorted(para_lengths)[len(para_lengths)//2]
                results["stats"]["avg_para"] = round(avg)
                results["stats"]["median_para"] = med

    except Exception as e:
        results["issues"].append(f"ERROR: {e}")
    return results

def main():
    epub_dir = sys.argv[1] if len(sys.argv) > 1 else EPUB_DIR
    epubs = sorted([f for f in os.listdir(epub_dir) if f.endswith('.epub')])
    if not epubs:
        print(f"No EPUBs in {epub_dir}")
        return

    print(f"{'='*100}")
    print(f"  EPUB QUALITY AUDIT - {len(epubs)} books")
    print(f"{'='*100}\n")

    all_r = []
    for ef in epubs:
        r = audit_epub(os.path.join(epub_dir, ef))
        all_r.append(r)
        status = "PASS" if not r["issues"] else "FAIL"
        if not r["issues"] and r["warnings"]: status = "WARN"
        icon = {"PASS": "[+]", "WARN": "[~]", "FAIL": "[X]"}[status]
        short = ef[:55] + "..." if len(ef) > 58 else ef
        s = r["stats"]
        print(f"{icon} {status:4s} | {short}")
        print(f"       Title: {s.get('title','?')[:60]} | Author: {s.get('author','?')[:35]}")
        print(f"       Paras: {s.get('paragraphs',0)} | H: {s.get('headings',0)} | Img: {s.get('images',0)} | TOC: {s.get('toc_entries',0)} | AvgP: {s.get('avg_para','?')} | MedP: {s.get('median_para','?')}")
        for i in r["issues"]: print(f"       ** {i}")
        for w in r["warnings"]: print(f"       *  {w}")
        print()

    passed = sum(1 for r in all_r if not r["issues"] and not r["warnings"])
    warned = sum(1 for r in all_r if not r["issues"] and r["warnings"])
    failed = sum(1 for r in all_r if r["issues"])
    issues = sum(len(r["issues"]) for r in all_r)
    warns = sum(len(r["warnings"]) for r in all_r)
    print(f"{'='*100}")
    print(f"  SUMMARY: {passed} PASS | {warned} WARN | {failed} FAIL | {issues} issues | {warns} warnings")
    print(f"{'='*100}")

if __name__ == "__main__":
    main()
