"""Parser for the Softec 'سجل الحركة التفصيلي' HTML export (purchases from supplier)."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Dict, List, Optional

_DATE = re.compile(r"(\d{4})/(\d\d)/(\d\d)")


def _num(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", "").strip())
    except ValueError:
        return None


class _P(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: List[List[str]] = []
        self._cur = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._cur = []
        elif tag in ("td", "th") and self._cur is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._cur.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._cur is not None:
            self.rows.append(self._cur)
            self._cur = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse(path: str) -> List[Dict]:
    """Return invoices: {doc, supplier, supplier_type, amount, bonus, paid, user, datetime, date, items[]}.

    items: {code, name, qty, price, total}. Bonus lines have price 0.
    """
    p = _P()
    with open(path, encoding="utf-8-sig") as f:
        p.feed(f.read())
    out: List[Dict] = []
    cur = None
    for r in p.rows:
        c = [x for x in r if x != ""]
        if len(c) == 8 and _num(c[0]) is not None and _DATE.search(c[-1]):
            m = _DATE.search(c[-1])
            cur = dict(paid=_num(c[0]), amount=_num(c[1]), bonus=_num(c[2]), supplier_type=c[3],
                       supplier=c[4], doc=c[5], user=c[6], datetime=c[7],
                       date=f"{m.group(1)}-{m.group(2)}-{m.group(3)}", items=[])
            out.append(cur)
        elif (cur is not None and len(c) == 5 and _num(c[0]) is not None
              and _num(c[2]) is not None and re.fullmatch(r"\d+", c[4])):
            cur["items"].append(dict(total=_num(c[0]), price=_num(c[1]), qty=_num(c[2]), name=c[3], code=c[4]))
    return out
