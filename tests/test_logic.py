"""Unit tests for the arithmetic validation, phonetic matching and system parser.

Run: python -m pytest tests -q   (no OCR / PDF needed)
Numbers come from real scanned invoices checked against the Softec export.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from invora_reader import ibn_template, po_template  # noqa: E402
from invora_reader.match import Master  # noqa: E402
from invora_reader.translit import numbers, skeleton_words  # noqa: E402


def test_skeleton_arabic_vs_latin():
    assert skeleton_words("ليبيتور 40 مجم 14 قرص") == skeleton_words("LIPITOR - 40MG - 14TAB") == ["lbtr"]
    assert skeleton_words("نستوجين 3 لبن 200 جرام")[0] == skeleton_words("NESTOGEN 3 200G MILK")[0]
    assert numbers("لينجالابس 25-5-1000 مجم 30") == numbers("LINGALABS 25/5/1000 30")


def test_master_ranks_known_items_first():
    m = Master([
        ("117718", "LIPITOR - 40MG - 14TAB", 226),
        ("83231", "LIPITOR - 80MG - 14TAB", 236),
        ("103158", "LIPITOR - 20MG - 30TAB (XX)", 240),
        ("111447", "NESTOGEN 3 200G MILK", 140),
    ])
    assert m.match("ليبيتور 40 مجم. 14 قرص", 226)[0].code == "117718"
    assert m.match("ليبيتور 80 مجم. 14 قرص", 236)[0].code == "83231"
    # public price on the invoice (135) is 3.6% below the master (140): still matched
    assert m.match("نستوجين 3 لبن 200 جرام", 135)[0].code == "111447"


def test_po_repairs_misread_unit_price_from_public_and_discount():
    # invoice 7876: OCR read the pharmacist price as 493.85, true 123.85; 10 x 123.85 = 1238.5
    ln = po_template.PoLine(y=0, public=135.0, tax_unit=0.0, disc_pct=8.26, pharm_price=493.85, total=1238.5)
    po_template._validate(ln)
    assert ln.ok and ln.qty == 10 and abs(ln.pharm_price - 123.85) < 0.01


def test_po_taxable_row_qty_from_total():
    # row 1 of the 2026-10-29 invoice: 4 x (175.25 + 24.54) = 799.16
    ln = po_template.PoLine(y=0, public=250.0, tax_unit=24.54, disc_pct=20.0, pharm_price=175.25, total=799.16)
    po_template._validate(ln)
    assert ln.ok and ln.qty == 4


def test_ibn_pretax_derived_from_vat_and_discounts():
    # invoice 9082 line 1: qty 12 x 24.93 = 299.16; OCR of the pre-VAT cell was garbage (216.0)
    ln = ibn_template.IbnLine(y=0, price=24.93, pretax=216.0, vat=39.81, vat_rate=0.14,
                              sup_pct=0.03, ibn_pct=0.02, tot_qty=12.0)
    ibn_template._validate(ln)
    assert ln.ok and ln.qty == 12 and abs(ln.pretax - 299.16) < 0.01 and ln.bonus == 0.0


def test_ibn_totals_identity_repairs_dropped_digit():
    from invora_reader.ocr import Tok
    lines = [ibn_template.IbnLine(y=100)]
    # grand, vat, ibn(117.72 read as 17.72), sup, price_sum  -- laid out left to right
    xs = [("6575.43", 100), ("807.54", 200), ("17.72", 300), ("28.07", 400), ("5913.68", 500)]
    toks = [Tok(x, 900, 80, 25, t, 96.0) for t, x in xs]
    out = ibn_template._totals(toks, lines, 1748)
    assert out["ibn_disc"] == 117.72 and out["consistent"]


def test_po_ignores_noise_tax_cell():
    # invoice 7876: '0.000' read as 0.0002 used to give pharm=0.001 and qty=1,032,083
    ln = po_template.PoLine(y=0, public=135.0, tax_unit=0.0002, disc_pct=8.26, pharm_price=493.85, total=1238.5)
    po_template._validate(ln)
    assert ln.ok and ln.qty == 10


def test_learned_alias_overrides_name_mismatch(tmp_path):
    from invora_reader import learned, link, export
    m = Master([("1", "BIOTIN 10MG 30CAP (GLOBAL)", 400), ("2", "VEE FERRO 30 CAP", 230)])
    inv = {"template": "ibn", "lines": [{"name_ar": "HAIR AND NAILS 60 PIECES", "public": 231.0, "qty": 1,
                                       "code_sup": "679934", "margin": 0, "ok": True}]}
    assert link.line_candidates(inv, m)[0]["cands"][0]["code"] == "2"      # unknown alias: wrong guess
    csv_path = tmp_path / "reviewed.csv"
    csv_path.write_text("template,layout,supplier_item_code,name_on_scan,item_code\n"
                        "ibn,B,679934,HAIR AND NAILS 60 PIECES,1\n"
                        "ibn,B,679934,HAIR AND NAILS 60,1\n", encoding="utf-8")
    store = {}
    assert learned.learn_csv(store, str(csv_path)) == 2
    row = link.line_candidates(inv, m, learned=store)[0]
    assert row["cands"][0]["code"] == "1" and row["learned"]["count"] == 2
    assert export.status_for("ibn", inv["lines"][0], row["cands"], row["learned"]) == "auto"


def test_printed_total_check_detects_missing_line():
    from invora_reader import export
    lines = [dict(ok=True, total=799.16, tax_unit=24.54, tax_total=98.16),
             dict(ok=True, total=177.6, tax_unit=0.0, tax_total=0.0),
             dict(ok=True, total=2516.4, tax_unit=0.0, tax_total=0.0)]
    inv = {"template": "po", "lines": lines, "totals": {"printed_values": [701.0, 2694.0, 98.16]}}
    assert export.printed_total_check(inv) == "ok"          # 177.6 + 2516.4 = 2694.0 ; 799.16 - 98.16 = 701.0
    inv["lines"] = lines[:2]                                  # a line got lost
    assert export.printed_total_check(inv) == "mismatch"


def test_arabic_name_column_disambiguates_similar_brands(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["الكود", "الصنف", "السعر", "الاسم العربي", "أسماء بديلة"])
    ws.append([1, "LIMITLESS MAN MULTIGUMMIES 90 GUMMIES", 345, "ليمتلس مان مالتي جاميز 90 قطعه", None])
    ws.append([2, "LIMITLESS WOMAN MULTIGUMMIES 90 GUMMIES", 345, "ليمتلس وومان مالتي جاميز 90 قطعه", None])
    ws.append([3, "BIOTIN 10MG 30CAP (GLOBAL)", 250, None, "هير اند نيلز 10 مجم; HAIR AND NAILS 10MG"])
    path = tmp_path / "master.xlsx"
    wb.save(path)
    m = Master.from_xlsx(str(path))
    assert m.match("ليمتلس وومان مالتى جاميز 90 قطعه", 345)[0].code == "2"
    assert m.match("ليمتلس مان مالتى جاميز 90 قطعه", 345)[0].code == "1"
    # a supplier's alias maps straight to the Softec item
    assert m.match("هير اند نيلز 10 مجم", 250)[0].code == "3"
    assert m.match("HAIR AND NAILS 10MG 30 TABLETS", 256)[0].code == "3"
    assert len({c.code for c in m.match("ليمتلس مان مالتى جاميز 90", 345, k=5)}) == len(m.match("ليمتلس مان مالتى جاميز 90", 345, k=5))


def test_review_workbook_round_trip_and_learning(tmp_path):
    """Build a workbook (no crops), simulate a human review, read it back and learn from it."""
    import openpyxl
    from invora_reader import learned, review_book
    base = dict(supplier="ابن سينا", invoice_key="K1", scan_date="2026-10-31", page=1, y=500, template="ibn", layout="B",
                supplier_item_code="679934", file="x.pdf", item_name="VEE FERRO 30 CAP", match_score=80, status="review",
                name_on_scan="HAIR AND NAILS 60 PIECES", item_code="2", alt_2="", alt_3="", qty=1)
    auto = dict(base, item_code="9", status="auto", supplier_item_code="111111", name_on_scan="XATRAL XL 10MG")
    path = tmp_path / "review.xlsx"
    info = review_book.build([base, auto], str(path), sample_auto=5, crops={})
    assert info["review"] == 1 and info["sample"] == 1
    wb = openpyxl.load_workbook(path)
    ws = wb[review_book.SHEET_REVIEW]
    head = [c.value for c in ws[1]]
    ws.cell(row=2, column=head.index("الكود المؤكد") + 1, value="1")      # the human fixes the code ...
    ws.cell(row=2, column=head.index("تمت المراجعة") + 1, value="✘")      # ... and marks it
    ws2 = wb[review_book.SHEET_SAMPLE]
    ws2.cell(row=2, column=head.index("تمت المراجعة") + 1, value="✔")      # auto line confirmed correct
    wb.save(path)
    rows = review_book.read(str(path))
    d = review_book.decisions(rows)
    assert d["corrected"] == 1 and d["confirmed"] == 1 and d["auto_checked"] == 1 and d["auto_correct"] == 1
    store = {}
    assert learned.learn_rows(store, rows) == 2
    assert learned.lookup(store, "ibn", {"code_sup": "679934", "margin": 0, "name_ar": ""})[0] == "1"
    # untouched rows teach nothing
    wb = openpyxl.load_workbook(path)
    wb[review_book.SHEET_REVIEW].cell(row=2, column=head.index("تمت المراجعة") + 1).value = None   # cell(value=None) is a no-op
    wb.save(path)
    assert learned.learn_rows({}, review_book.read(str(path))) == 1
