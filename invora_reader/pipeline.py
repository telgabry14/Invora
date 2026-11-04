"""End to end: PDF -> pages -> invoices -> master matches -> Softec entry links."""
from __future__ import annotations

import os as _os

# Tesseract spawns its own OpenMP threads; with a process pool that oversubscribes the CPU.
_os.environ.setdefault("OMP_THREAD_LIMIT", "1")

import json
import os
import re
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime
from typing import Dict, List, Optional

import numpy as np

from . import ibn2_template, ibn_template, po_template
from .ocr import arabic_text, numeric_tokens
from .prep import clean_page, render_pages

_PO_DATE = re.compile(r"(20\d\d)-(\d\d)-(\d\d)")
_IBN_DATE = re.compile(r"(\d\d)-(\d\d)-(20\d\d)")
_PAGE_PO = re.compile(r"Page\s*(\d+)\s*of\s*(\d+)", re.I)
_PAGE_IBN = re.compile(r"(\d{9,})\s*_\s*(\d+)\s*/\s*(\d+)")


def header_info(clean: np.ndarray) -> Dict:
    """Template, date, invoice key and page index from the top of the page."""
    H, W = clean.shape
    top = clean[: int(0.30 * H)]
    text = arabic_text(top, psm=6)
    low = text.lower()
    nums = [t.text for t in numeric_tokens(top, min_conf=60)]
    info: Dict = {"template": None, "date": None, "key": None, "page": None, "pages": None}
    if "ibnsina" in low or "statement" in low or "cash" in low:
        info["template"] = "ibn"
    elif "overseas" in low or "مسعر" in text or "page" in low:
        info["template"] = "po"
    if info["template"] == "ibn":
        iso = next((t for t in nums if _PO_DATE.fullmatch(t)), None) or (
            _PO_DATE.search(text).group(0) if _PO_DATE.search(text) else None)
        if iso:
            info["layout"], info["date"] = "B", iso
        for t in nums:
            if info["date"]:
                break
            m = _IBN_DATE.fullmatch(t)
            if m:
                info["date"] = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"  # printed dd-mm-yyyy
                break
        if not info["date"]:
            m = _IBN_DATE.search(text)
            if m:
                info["date"] = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        m = _PAGE_IBN.search(text)
        if m:
            info["key"], info["page"], info["pages"] = m.group(1), int(m.group(2)), int(m.group(3))
        else:
            ids = [t for t in nums if re.fullmatch(r"20\d{11,13}", t)]
            info["key"] = ids[0] if ids else None
    elif info["template"] == "po":
        for t in nums:
            m = _PO_DATE.fullmatch(t)
            if m:
                info["date"] = t
                break
        if not info["date"]:
            m = _PO_DATE.search(text)
            if m:
                info["date"] = m.group(0)
        m = _PAGE_PO.search(text)
        if m:
            info["page"], info["pages"] = int(m.group(1)), int(m.group(2))
        order = [t for t in nums if re.fullmatch(r"10\d{8}", t)]
        bars = [t for t in nums if re.fullmatch(r"88\d{8}", t)]
        info["key"] = (order or bars or [None])[0]
    info["header_text"] = text[:200]
    return info


def read_one_page(args) -> Dict:
    path, pno, dpi = args
    g = None
    for n, img in render_pages(path, dpi=dpi):
        if n == pno:
            g = img
            break
    clean, diag = clean_page(g)
    hdr = header_info(clean)
    res: Dict = {"file": os.path.basename(path), "page": pno, "diag": diag, "header": hdr, "lines": [], "totals": {}}
    try:
        if hdr["template"] == "po":
            r = po_template.read_page(clean)
        elif hdr["template"] == "ibn":
            r = (ibn2_template if hdr.get("layout") == "B" else ibn_template).read_page(clean)
        else:
            r = {"lines": [], "warnings": ["unknown template (not an invoice page?)"]}
    except Exception as e:  # keep batch running; surface the page for review
        r = {"lines": [], "warnings": [f"error: {type(e).__name__}: {e}"]}
    res["lines"] = r.get("lines", [])
    res["totals"] = r.get("totals", {})
    res["warnings"] = r.get("warnings", [])
    return res


def count_pages(path: str) -> int:
    import pymupdf
    with pymupdf.open(path) as d:
        return len(d)


def read_pdf(path: str, dpi: int = 300, workers: int = 4, pages: Optional[List[int]] = None,
             cache: Optional[str] = None) -> List[Dict]:
    """Per-page results, cached as JSON (re-running only recomputes missing pages)."""
    n = count_pages(path)
    todo = pages or list(range(1, n + 1))
    done: Dict[int, Dict] = {}
    if cache and os.path.exists(cache):
        done = {r["page"]: r for r in json.load(open(cache, encoding="utf-8"))}
    need = [p for p in todo if p not in done]
    if need:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for r in ex.map(read_one_page, [(path, p, dpi) for p in need]):
                done[r["page"]] = r
        if cache:
            json.dump([done[k] for k in sorted(done)], open(cache, "w", encoding="utf-8"), ensure_ascii=False)
    return [done[p] for p in todo]


def group_invoices(pages: List[Dict]) -> List[Dict]:
    """Merge consecutive pages into invoices (page index 1 starts a new invoice)."""
    invs: List[Dict] = []
    for pg in pages:
        h = pg["header"]
        if h["template"] is None and not pg["lines"]:
            continue
        starts = h["page"] in (None, 1) or not invs or invs[-1]["template"] != h["template"]
        if h["key"] and invs and invs[-1]["key"] == h["key"]:
            starts = False
        if starts:
            invs.append({"template": h["template"], "key": h["key"], "date": h["date"], "file": pg["file"],
                         "pages": [], "lines": [], "totals": {}, "warnings": []})
        inv = invs[-1]
        inv["pages"].append(pg["page"])
        inv["lines"].extend(pg["lines"])
        inv["warnings"].extend(f"p{pg['page']}: {w}" for w in pg.get("warnings", []))
        if pg["totals"]:
            last = h["pages"] is None or h["page"] is None or h["page"] == h["pages"]
            if last or not inv["totals"]:
                inv["totals"] = pg["totals"]
        inv["date"] = inv["date"] or h["date"]
    return invs
