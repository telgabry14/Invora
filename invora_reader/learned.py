"""Alias memory: confirmed (supplier item) -> Softec code, learned from reviewed upload files.

Key: ibnsina layout B -> supplier item code; everything else -> phonetic skeleton of the scan name.
A supplier's own name for an item often differs from Softec's ('HAIR AND NAILS' vs 'BIOTIN'), so these
cannot be guessed from names; they are learned once from a reviewed sheet and reused afterwards.
"""
from __future__ import annotations

import csv
import json
import os
from collections import Counter
from typing import Dict, Optional

import re

from rapidfuzz import fuzz, process

from .translit import arabic_norm


def _norm_text(name: str) -> str:
    """Specific, order-insensitive text key: Arabic letters normalised (alef/ya/ta-marbuta) or Latin upper-case,
    digits kept. NOT the phonetic skeleton: 'وومان' (WOMAN) and 'مان' (MAN) share a skeleton and must not collide."""
    ar = arabic_norm(name or "")
    if ar:
        return ar
    return " ".join(re.findall(r"[A-Z]+|\d+", (name or "").upper()))


def alias_key(template: str, line: Dict) -> Optional[str]:
    if template == "ibn" and line.get("code_sup") and "margin" in line:
        return f"ibn|code|{line['code_sup']}"
    t = _norm_text(line.get("name_ar", "") or line.get("name_on_scan", ""))
    return f"{template}|name|{t}" if len(t.replace(" ", "")) >= 6 else None


def load(path: Optional[str]) -> Dict[str, Dict[str, int]]:
    if path and os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    return {}


def save(store: Dict, path: str) -> None:
    json.dump(store, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)


def _major(entry: Dict[str, int]):
    """(code, count) of the majority code, or (None, 0) when two codes compete (>=40% each)."""
    ranked = Counter(entry).most_common()
    total = sum(entry.values())
    if len(ranked) > 1 and ranked[1][1] / total >= 0.4:
        return None, 0
    return ranked[0]


def lookup(store: Dict, template: str, line: Dict, fuzzy: int = 101):
    """-> (code, count) learned for this line, or (None, 0).

    Exact key first; for name keys also a near-identical OCR reading (similarity >= ``fuzzy``) whose
    neighbours all agree on one code. Ambiguity returns nothing: a wrong boost is worse than none."""
    k = alias_key(template, line)
    if not k:
        return None, 0
    if k in store:
        return _major(store[k])
    if "|name|" not in k:
        return None, 0
    prefix = k.split("|name|")[0] + "|name|"
    keys = [x for x in store if x.startswith(prefix)]
    if not keys:
        return None, 0
    q = k[len(prefix):]
    qn = set(re.findall(r"\d+", q))
    near = [x for x, sc, _ in process.extract(k, keys, scorer=fuzz.token_sort_ratio, limit=5)
            if sc >= fuzzy and set(re.findall(r"\d+", x[len(prefix):])) == qn]   # strengths/counts must be identical
    codes = {_major(store[x])[0] for x in near}
    if len(near) and len(codes) == 1 and None not in codes:
        return _major(store[near[0]])
    return None, 0


def learn_csv(store: Dict, path: str) -> int:
    """Add every row of a reviewed upload.csv (item_code = the confirmed Softec code)."""
    n = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            code = (r.get("item_code") or "").strip()
            if not code:
                continue
            line = {"name_ar": r.get("name_on_scan", ""), "code_sup": r.get("supplier_item_code") or None}
            if r.get("layout") == "B":
                line["margin"] = 0
            k = alias_key(r.get("template", ""), line)
            if not k:
                continue
            store.setdefault(k, {})
            store[k][code] = store[k].get(code, 0) + 1
            n += 1
    return n


def learn_rows(store: Dict, rows) -> int:
    """Learn from reviewed workbook rows: only rows a person marked (✔/✘) with a confirmed code."""
    n = 0
    for r in rows:
        if not (r.get("reviewed") or "").strip():
            continue
        code = str(r.get("confirmed") or "").strip()
        if code.endswith(".0"):
            code = code[:-2]
        if not code:
            continue
        line = {"name_ar": r.get("name_on_scan") or "", "code_sup": (str(r["supplier_item_code"]) if r.get("supplier_item_code") else None)}
        if r.get("layout") == "B":
            line["margin"] = 0
        k = alias_key(r.get("template") or "", line)
        if not k:
            continue
        store.setdefault(k, {})
        store[k][code] = store[k].get(code, 0) + 1
        n += 1
    return n
