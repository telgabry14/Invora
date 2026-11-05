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

from .translit import skeleton_words


def alias_key(template: str, line: Dict) -> Optional[str]:
    if template == "ibn" and line.get("code_sup") and "margin" in line:
        return f"ibn|code|{line['code_sup']}"
    sk = "".join(skeleton_words(line.get("name_ar", "") or line.get("name_on_scan", "")))
    return f"{template}|name|{sk}" if len(sk) >= 5 else None


def load(path: Optional[str]) -> Dict[str, Dict[str, int]]:
    if path and os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    return {}


def save(store: Dict, path: str) -> None:
    json.dump(store, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)


def lookup(store: Dict, template: str, line: Dict):
    """-> (code, count) of the majority learned code for this line, or (None, 0)."""
    k = alias_key(template, line)
    if not k or k not in store:
        return None, 0
    code, n = Counter(store[k]).most_common(1)[0]
    return code, n


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
