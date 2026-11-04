"""python -m invora_reader read scans/*.pdf --master master.xlsx [--system export.htm] --out out/"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

from . import export, link, pipeline, system_export
from .match import Master


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="invora_reader")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read", help="OCR scanned invoices and match items to the master")
    r.add_argument("pdfs", nargs="+")
    r.add_argument("--master", required=True, help="item master .xlsx (Sheet1: code, name, price)")
    r.add_argument("--system", nargs="+", help="Softec purchase export .htm, to link invoices and verify matches")
    r.add_argument("--out", default="out")
    r.add_argument("--dpi", type=int, default=300)
    r.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)

    os.makedirs(a.out, exist_ok=True)
    master = Master.from_xlsx(a.master)
    system = [i for f in (a.system or []) for i in system_export.parse(f)]
    invoices = []
    for pdf in a.pdfs:
        cache = os.path.join(a.out, os.path.basename(pdf)[:-4] + ".pages.json")
        pages = pipeline.read_pdf(pdf, dpi=a.dpi, workers=a.workers, cache=cache)
        invoices.extend(pipeline.group_invoices(pages))

    out_rows = []
    history = link.build_history(system) if system else None
    by_doc = {s["doc"]: s for s in system}
    for inv in invoices:
        cands = link.link_by_lines(inv, system) if system else []
        inv["system_candidates"] = cands
        # leave-one-out: the linked entry itself must not help to match its own lines
        rows = link.line_candidates(inv, master, history=history,
                                    exclude_doc=cands[0]["doc"] if cands else None)
        if cands:
            link.assign_to_system(rows, by_doc[cands[0]["doc"]])
        inv["rows"] = rows
        for r in rows:
            l, c = r["line"], r["cands"]
            sysl = r["system"]["line"] if r.get("system") else None
            out_rows.append({
                "file": inv["file"], "invoice_key": inv["key"], "template": inv["template"], "date": inv["date"],
                "name_ar": l.get("name_ar"), "qty": l.get("qty"), "bonus": l.get("bonus"),
                "public": l.get("public"), "disc_pct": l.get("disc_pct", l.get("sup_pct")),
                "ibn_pct": l.get("ibn_pct"), "total": l.get("total", l.get("pretax")),
                "top1_code": c[0]["code"] if c else None, "top1_name": c[0]["name"] if c else None,
                "top1_score": c[0]["score"] if c else None,
                "sys_doc": cands[0]["doc"] if cands else None,
                "sys_code": sysl["code"] if sysl else None, "sys_name": sysl["name"] if sysl else None,
                "top1_matches_system": (c[0]["code"] == sysl["code"]) if (c and sysl) else None,
                "flags": "; ".join(l.get("flags", [])),
            })
    upload = [row for inv in invoices for row in export.rows_for_invoice(inv)]
    if upload:
        with open(os.path.join(a.out, "upload.csv"), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(upload[0]))
            w.writeheader()
            w.writerows(upload)
        n_auto = sum(1 for r in upload if r["status"] == "auto")
        print(f"upload.csv: {len(upload)} lines, {n_auto} auto, {len(upload) - n_auto} review")
    with open(os.path.join(a.out, "invoices.json"), "w", encoding="utf-8") as f:
        json.dump(invoices, f, ensure_ascii=False, indent=1)
    if out_rows:
        with open(os.path.join(a.out, "lines.csv"), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out_rows[0]))
            w.writeheader()
            w.writerows(out_rows)
    print(f"{len(invoices)} invoices, {len(out_rows)} lines -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
