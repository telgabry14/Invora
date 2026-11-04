"""Match an invoice line (Arabic name + optional public price) to the Softec item master."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from rapidfuzz import fuzz, process

from .translit import arabic_norm, numbers, skeleton_words


@dataclass
class Candidate:
    code: str
    name: str
    master_price: Optional[float]
    score: float
    parts: dict


class Master:
    def __init__(self, rows):
        """rows: iterable of (code, name, price) or (code, name, price, [extra names]).

        Extra names (Arabic name, supplier aliases) are indexed as additional rows of the same
        code, so an Arabic scan name is compared Arabic-to-Arabic as well as phonetically.
        """
        self.code, self.name, self.price, self.sk, self.nums, self.joined, self.arn = [], [], [], [], [], [], []
        self.by_code = {}
        self.ar_rows = {}
        for row in rows:
            code, name, price = row[0], row[1], row[2]
            extras = list(row[3]) if len(row) > 3 and row[3] else []
            if code is None or not name:
                continue
            price = float(price) if price not in (None, "") else None
            first = True
            for nm in [name] + extras:
                if not nm:
                    continue
                self._add(str(code), str(nm), price)
                if first:
                    self.by_code[str(code)] = len(self.code) - 1
                    first = False

    def _add(self, code: str, name: str, price) -> None:
        self.code.append(code)
        self.name.append(name)
        self.price.append(price)
        sw = skeleton_words(name)
        self.sk.append(" ".join(sw))
        self.joined.append("".join(sw))
        self.nums.append(numbers(name))
        self.arn.append(arabic_norm(name))
        self.has_arabic = getattr(self, "has_arabic", False) or bool(self.arn[-1])
        if self.arn[-1]:
            self.ar_rows.setdefault(code, []).append(len(self.code) - 1)

    @classmethod
    def from_xlsx(cls, path: str, sheet: str = "Sheet1") -> "Master":
        """Columns by header: code (الكود), name (الصنف), price (السعر); optional Arabic name
        (الاسم العربي / اسم عربي / arabic_name) and aliases (أسماء بديلة / aliases, split on ; | ،)."""
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True)
        ws = wb[sheet] if sheet in wb.sheetnames else wb.worksheets[0]
        it = ws.iter_rows(values_only=True)
        head = [str(h or "").strip() for h in next(it)]

        def col(*names, default=None):
            for n in names:
                if n in head:
                    return head.index(n)
            return default

        ic, inm, ip = col("الكود", "code", default=0), col("الصنف", "name", default=1), col("السعر", "price", default=2)
        iar = col("الاسم العربي", "اسم عربي", "arabic_name", "الاسم بالعربي")
        ial = col("أسماء بديلة", "اسم بديل", "aliases", "الأسماء البديلة")

        def rows():
            for r in it:
                extras = []
                if iar is not None and iar < len(r) and r[iar]:
                    extras.append(str(r[iar]))
                if ial is not None and ial < len(r) and r[ial]:
                    extras += [x.strip() for x in re.split(r"[;|،]", str(r[ial])) if x.strip()]
                yield (r[ic], r[inm], r[ip], extras)
        return cls(rows())

    def __len__(self):
        return len(self.code)

    def match(self, name_ar: str, public_price: Optional[float] = None, k: int = 5,
              price_tol: float = 0.06, boost=None, force=None) -> List[Candidate]:
        q_sk = " ".join(skeleton_words(name_ar))
        q_nums = numbers(name_ar)
        q_join = q_sk.replace(" ", "")
        q_ar = arabic_norm(name_ar)
        if not q_sk and not boost:
            return []
        pool = process.extract(q_sk, self.sk, scorer=fuzz.token_set_ratio, limit=150) if q_sk else []
        idx = {i for _, _, i in pool}
        if q_ar and self.has_arabic:  # Arabic master names / aliases: compare Arabic to Arabic
            for _, sc, i in process.extract(q_ar, self.arn, scorer=fuzz.token_set_ratio, limit=60):
                if sc >= 60 and i not in idx:
                    pool.append((None, 0.0, i))
                    idx.add(i)
        if public_price:  # also rank inside the price window so cheap/common names are not crowded out
            win = [i for i, mp in enumerate(self.price)
                   if mp and abs(mp - public_price) / public_price <= price_tol]
            if win:
                sub = process.extract(q_sk, [self.sk[i] for i in win], scorer=fuzz.token_set_ratio, limit=60)
                for _, sc, j in sub:
                    if win[j] not in idx:
                        pool.append((None, sc, win[j]))
                        idx.add(win[j])
        if force:  # learned aliases are always candidates, with a bonus
            for code in force:
                i = self.by_code.get(code)
                if i is not None and i not in idx:
                    pool.append((None, fuzz.token_set_ratio(q_sk, self.sk[i]) if q_sk else 0, i))
                    idx.add(i)
        out: List[Candidate] = []
        for _, name_score, i in pool:
            if q_ar and self.code[i] in self.ar_rows:
                # the item has an Arabic name/alias: Arabic-to-Arabic (strict) decides, phonetics only supports
                ar = max(fuzz.token_sort_ratio(q_ar, self.arn[j]) for j in self.ar_rows[self.code[i]])
                name_score = 0.3 * name_score + 0.7 * ar
            n_score = self._num_score(q_nums, self.nums[i])
            p_score = self._price_score(public_price, self.price[i], price_tol)
            brand = self._brand(q_sk, self.sk[i])
            if q_join and self.joined[i]:   # 'DALACIN-C' vs 'DALACINC', split/merged words
                brand = max(brand, fuzz.partial_ratio(q_join[:8], self.joined[i][:12]) if len(q_join) >= 4 else 0)
            # price is a strong filter when we have it; names carry the rest
            score = 0.40 * name_score + 0.20 * brand + 0.25 * n_score * 100 + 0.15 * p_score * 100
            if boost:
                score += boost(self.code[i])
            if force and self.code[i] in force:
                score += force[self.code[i]]
            out.append(Candidate(self.code[i], self.name[i], self.price[i], round(score, 1),
                                 {"name": round(name_score), "brand": round(brand),
                                  "nums": round(n_score, 2), "price": round(p_score, 2)}))
        out.sort(key=lambda c: -c.score)
        seen, best = set(), []
        for c in out:            # one result per item code (an item can have several indexed names)
            if c.code not in seen:
                seen.add(c.code)
                best.append(c)
        return best[:k]

    @staticmethod
    def _brand(q: str, m: str) -> float:
        """Similarity of the most distinctive (longest) words; stamps add short junk words."""
        a = sorted(q.split(" "), key=len, reverse=True)[:2]
        b = sorted(m.split(" "), key=len, reverse=True)[:2]
        if not a or not b or not a[0] or not b[0]:
            return 0.0
        first = fuzz.ratio(q.split(" ")[0], m.split(" ")[0])
        return max([first] + [fuzz.ratio(x, y) for x in a for y in b])

    @staticmethod
    def _num_score(a: set, b: set) -> float:
        if not a and not b:
            return 0.5
        return len(a & b) / max(len(a | b), 1)

    @staticmethod
    def _price_score(p: Optional[float], mp: Optional[float], tol: float) -> float:
        if p is None or not mp:
            return 0.5
        d = abs(p - mp) / mp
        return 1.0 if d <= 0.02 else (0.7 if d <= tol else (0.2 if d <= 0.25 else 0.0))
