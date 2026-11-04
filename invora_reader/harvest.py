"""Harvest Arabic item names for the master from scans that are already linked to Softec entries.

For every scan line paired unambiguously with a Softec line (same qty, close price) we know the Softec
code AND the Arabic name the supplier prints. Collected per code, they give a first draft of the
'الاسم العربي' / 'أسماء بديلة' columns (to be reviewed by a person: OCR noise is possible).
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Dict, List, Tuple

from . import link

_AR = re.compile(r"[ء-ي]")


def clean_arabic_name(text: str) -> str:
    """Keep Arabic words (>=2 letters), digits and a few unit tokens; drop OCR junk."""
    text = (text or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    text = re.sub(r"[ً-ْـ‎‏]", "", text)
    words = []
    for w in re.findall(r"[ء-ي]+|\d+(?:\.\d+)?|[+/*-]", text):
        if _AR.match(w) and len(w) < 2:
            continue
        words.append(w)
    return " ".join(words).strip(" -+/*")


def good_name(name: str) -> bool:
    ar_words = [w for w in name.split() if _AR.match(w) and len(w) >= 3]
    return len(ar_words) >= 1 and len(name) >= 5


def harvest(invoices: List[Dict], system_by_doc: Dict[str, Dict], tol_po: float = 0.035,
            tol_ibn: float = 0.025) -> Dict[str, Counter]:
    """code -> Counter(cleaned Arabic name). Uses only unambiguous (qty, price) pairings."""
    names: Dict[str, Counter] = defaultdict(Counter)
    for inv in invoices:
        cands = inv.get("system_candidates") or []
        if not cands or cands[0]["doc"] not in system_by_doc:
            continue
        sysinv = system_by_doc[cands[0]["doc"]]
        tol = tol_po if inv["template"] == "po" else tol_ibn
        for r in inv.get("rows", []):
            l = r["line"]
            ep = link._eff_price(l)
            if not ep or not l.get("qty"):
                continue
            m = [x for x in sysinv["items"] if abs(x["qty"] - l["qty"]) < 1e-6 and x["price"]
                 and abs(ep - x["price"]) / x["price"] <= tol]
            codes = {x["code"] for x in m}
            if len(codes) != 1:
                continue
            nm = clean_arabic_name(l.get("name_ar", ""))
            if good_name(nm):
                names[m[0]["code"]][nm] += 1
    return names


def to_rows(names: Dict[str, Counter], master) -> List[Dict]:
    rows = []
    for code, cnt in sorted(names.items(), key=lambda kv: -sum(kv[1].values())):
        ranked = [n for n, _ in cnt.most_common()]
        i = master.by_code.get(code)
        rows.append({"الكود": code, "الصنف": master.name[i] if i is not None else "",
                     "الاسم العربي": ranked[0], "أسماء بديلة": " ; ".join(ranked[1:4]),
                     "مرات الظهور": sum(cnt.values())})
    return rows
