"""Match an invoice line (Arabic name + optional public price) to the Softec item master."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from rapidfuzz import fuzz, process

from .translit import numbers, skeleton_words


@dataclass
class Candidate:
    code: str
    name: str
    master_price: Optional[float]
    score: float
    parts: dict


class Master:
    def __init__(self, rows):
        """rows: iterable of (code, name, price)."""
        self.code, self.name, self.price, self.sk, self.nums = [], [], [], [], []
        for code, name, price in rows:
            if code is None or not name:
                continue
            self.code.append(str(code))
            self.name.append(str(name))
            self.price.append(float(price) if price not in (None, "") else None)
            self.sk.append(" ".join(skeleton_words(str(name))))
            self.nums.append(numbers(str(name)))
        self.by_code = {c: i for i, c in enumerate(self.code)}

    @classmethod
    def from_xlsx(cls, path: str, sheet: str = "Sheet1") -> "Master":
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True)
        ws = wb[sheet]
        rows = ((r[0], r[1], r[2]) for r in ws.iter_rows(min_row=2, values_only=True))
        return cls(rows)

    def __len__(self):
        return len(self.code)

    def match(self, name_ar: str, public_price: Optional[float] = None, k: int = 5,
              price_tol: float = 0.06) -> List[Candidate]:
        q_sk = " ".join(skeleton_words(name_ar))
        q_nums = numbers(name_ar)
        if not q_sk:
            return []
        pool = process.extract(q_sk, self.sk, scorer=fuzz.token_set_ratio, limit=150)
        idx = {i for _, _, i in pool}
        if public_price:  # also rank inside the price window so cheap/common names are not crowded out
            win = [i for i, mp in enumerate(self.price)
                   if mp and abs(mp - public_price) / public_price <= price_tol]
            if win:
                sub = process.extract(q_sk, [self.sk[i] for i in win], scorer=fuzz.token_set_ratio, limit=60)
                for _, sc, j in sub:
                    if win[j] not in idx:
                        pool.append((None, sc, win[j]))
                        idx.add(win[j])
        out: List[Candidate] = []
        for _, name_score, i in pool:
            n_score = self._num_score(q_nums, self.nums[i])
            p_score = self._price_score(public_price, self.price[i], price_tol)
            brand = self._brand(q_sk, self.sk[i])
            # price is a strong filter when we have it; names carry the rest
            score = 0.40 * name_score + 0.20 * brand + 0.25 * n_score * 100 + 0.15 * p_score * 100
            out.append(Candidate(self.code[i], self.name[i], self.price[i], round(score, 1),
                                 {"name": round(name_score), "brand": round(brand),
                                  "nums": round(n_score, 2), "price": round(p_score, 2)}))
        out.sort(key=lambda c: -c.score)
        return out[:k]

    @staticmethod
    def _brand(q: str, m: str) -> float:
        a, b = q.split(" ")[:1], m.split(" ")[:1]
        return fuzz.ratio(a[0], b[0]) if a and b else 0.0

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
