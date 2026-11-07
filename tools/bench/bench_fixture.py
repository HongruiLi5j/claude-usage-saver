"""token 基准用的固定小项目 minishop（纯标准库 + pytest），由代码确定性生成。

make_fixture(dst) 在 dst 里写出完整项目（源码、测试、大文件），然后由运行器 git init + commit。
所有"大文件"都用固定随机种子生成，每次一模一样，所以各轮结果可比。
改这个文件 = 换基准：要把 FIXTURE_VERSION 加 1，并在报告里写明，不同版本的结果不能直接比。
"""
import os, random, textwrap, json
import datetime as _dt

FIXTURE_VERSION = 1

FILES = {}

FILES["README.md"] = """# minishop

A tiny order / pricing / inventory library used as a fixed benchmark project.

Run tests: `python3 -m pytest -q`

CLI: `python3 -m minishop.cli report data/orders.csv`
"""

FILES[".gitignore"] = "__pycache__/\n.pytest_cache/\n*.pyc\n"

FILES["minishop/__init__.py"] = '"""minishop: tiny shop library."""\n__version__ = "1.0.0"\n'

FILES["minishop/config.py"] = '''"""Static configuration."""

# Sales tax rate by region.
TAX_RATES = {
    "CA": 0.0725,
    "NY": 0.04,
    "WA": 0.065,
    "OR": 0.0,
    "TX": 0.0625,
}

DEFAULT_REGION = "CA"

# (minimum quantity, discount percent), checked from the largest tier down.
BULK_TIERS = [(100, 15), (50, 10), (10, 5)]

# Orders with subtotal at or above this ship for free.
FREE_SHIPPING_THRESHOLD = 75.0

# (max weight in kg, flat cost) -- weight exactly on the limit uses that tier.
SHIPPING_TIERS = [(1.0, 4.99), (5.0, 9.99), (20.0, 19.99)]

# Per-kg surcharge above the heaviest tier.
HEAVY_PER_KG = 1.25
'''

FILES["minishop/money.py"] = '''"""Money helpers: always round half-up to cents."""
from decimal import Decimal, ROUND_HALF_UP


def round_money(x):
    """Round to 2 decimals, half-up (2.675 -> 2.68, 0.125 -> 0.13)."""
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def parse_money(s):
    """Parse strings like "$1,234.50", "-3.2", " 7 " into a float rounded to cents."""
    if not isinstance(s, str):
        raise TypeError("parse_money expects a string")
    t = s.strip().replace(",", "")
    neg = t.startswith("-")
    if neg:
        t = t[1:]
    if t.startswith("$"):
        t = t[1:]
    if not t:
        raise ValueError("empty money string")
    v = round_money(float(t))
    return -v if neg else v


def format_money(x):
    """Format as $1,234.50 (negative as -$3.20)."""
    v = round_money(x)
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"
'''

FILES["minishop/catalog.py"] = '''"""Product catalog loaded from CSV."""
import csv
import re
from dataclasses import dataclass


def _check_sku(sku):
    if not isinstance(sku, str) or not re.match(r"^[A-Z]{3}-\\d{4}$", sku):
        raise ValueError(f"bad sku: {sku!r}")
    return sku


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price: float
    category: str
    weight_kg: float

    def __post_init__(self):
        _check_sku(self.sku)
        if self.price < 0:
            raise ValueError("negative price")


def load_catalog(path):
    """Return {sku: Product}. Duplicate SKUs raise ValueError."""
    out = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            p = Product(row["sku"], row["name"], float(row["price"]), row["category"], float(row["weight_kg"]))
            if p.sku in out:
                raise ValueError(f"duplicate sku {p.sku}")
            out[p.sku] = p
    return out


def by_category(catalog, category):
    """Products in one category, sorted by sku."""
    return sorted((p for p in catalog.values() if p.category == category), key=lambda p: p.sku)
'''

FILES["minishop/inventory.py"] = '''"""In-memory stock with reservations."""
import re


class InsufficientStock(Exception):
    pass


def _valid_sku(sku):
    if not isinstance(sku, str) or re.match(r"^[A-Z]{3}-\\d{4}$", sku) is None:
        raise ValueError(f"invalid sku {sku!r}")


class Inventory:
    def __init__(self):
        self._stock = {}
        self._reserved = {}

    def add(self, sku, qty):
        _valid_sku(sku)
        if qty <= 0:
            raise ValueError("qty must be positive")
        self._stock[sku] = self._stock.get(sku, 0) + qty

    def on_hand(self, sku):
        return self._stock.get(sku, 0)

    def available(self, sku):
        return self._stock.get(sku, 0) - self._reserved.get(sku, 0)

    def reserve(self, sku, qty):
        _valid_sku(sku)
        if qty <= 0:
            raise ValueError("qty must be positive")
        if qty > self.available(sku):
            raise InsufficientStock(sku)
        self._reserved[sku] = self._reserved.get(sku, 0) + qty

    def release(self, sku, qty):
        r = self._reserved.get(sku, 0)
        if qty > r:
            raise ValueError("releasing more than reserved")
        self._reserved[sku] = r - qty

    def ship(self, sku, qty):
        """Ship reserved units: reduces both reservation and stock."""
        self.release(sku, qty)
        self._stock[sku] -= qty
'''

FILES["minishop/pricing.py"] = '''"""Line prices, bulk discounts and tax."""
from .config import BULK_TIERS, TAX_RATES
from .money import round_money


def apply_discount(price, pct):
    if not 0 <= pct <= 100:
        raise ValueError("pct out of range")
    return round_money(price * (1 - pct / 100))


def bulk_discount_pct(qty):
    """Discount percent for a quantity; tiers apply at or above their minimum."""
    for min_qty, pct in sorted(BULK_TIERS, reverse=True):
        if qty >= min_qty:
            return pct
    return 0


def line_price(unit_price, qty):
    if qty <= 0:
        raise ValueError("qty must be positive")
    return apply_discount(unit_price * qty, bulk_discount_pct(qty))


def tax(amount, region):
    try:
        rate = TAX_RATES[region]
    except KeyError:
        raise ValueError(f"unknown region {region!r}") from None
    return round_money(amount * rate)


def effective_rate(amount, region):
    """Tax as a fraction of amount, for display (4 decimals)."""
    if amount == 0:
        return 0.0
    return round(tax(amount, region) / amount, 4)
'''

FILES["minishop/shipping.py"] = '''"""Shipping cost by weight."""
from .config import FREE_SHIPPING_THRESHOLD, HEAVY_PER_KG, SHIPPING_TIERS


def shipping_cost(weight_kg, subtotal):
    if weight_kg < 0:
        raise ValueError("negative weight")
    if subtotal >= FREE_SHIPPING_THRESHOLD:
        return 0.0
    for max_kg, cost in SHIPPING_TIERS:
        if weight_kg <= max_kg:
            return cost
    extra = weight_kg - SHIPPING_TIERS[-1][0]
    return round(SHIPPING_TIERS[-1][1] + extra * HEAVY_PER_KG, 2)
'''

FILES["minishop/orders.py"] = '''"""Orders and totals."""
import re
from dataclasses import dataclass, field

from .money import round_money
from .pricing import line_price, tax
from .shipping import shipping_cost


def ensure_sku(sku):
    if not isinstance(sku, str) or not re.fullmatch(r"^[A-Z]{3}-\\d{4}$", sku):
        raise ValueError(f"sku format must be AAA-0000, got {sku!r}")
    return sku


@dataclass
class OrderLine:
    sku: str
    qty: int
    unit_price: float

    def __post_init__(self):
        ensure_sku(self.sku)
        if self.qty <= 0:
            raise ValueError("qty must be positive")

    def price(self):
        return line_price(self.unit_price, self.qty)


@dataclass
class Order:
    order_id: str
    region: str = "CA"
    lines: list = field(default_factory=list)
    weight_kg: float = 0.0

    def add_line(self, sku, qty, unit_price):
        self.lines.append(OrderLine(sku, qty, unit_price))
        return self

    def subtotal(self):
        return round_money(sum(l.price() for l in self.lines))


def calc_total(order):
    """subtotal + tax + shipping, rounded to cents."""
    sub = order.subtotal()
    return round_money(sub + tax(sub, order.region) + shipping_cost(order.weight_kg, sub))
'''

FILES["minishop/report.py"] = '''"""Sales reports from an orders CSV (order_id, sku, quantity, unit_price, region)."""
import csv
from collections import defaultdict

from .money import round_money


def load_orders_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def sales_by_sku(rows):
    out = defaultdict(float)
    for r in rows:
        out[r["sku"]] += int(r["quantity"]) * float(r["unit_price"])
    return {k: round_money(v) for k, v in out.items()}


def top_skus(rows, n=3):
    s = sales_by_sku(rows)
    return sorted(s.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def summary(rows):
    orders = {r["order_id"] for r in rows}
    revenue = round_money(sum(sales_by_sku(rows).values()))
    avg = round(revenue / len(orders), 2) if orders else 0.0
    return {"orders": len(orders), "revenue": revenue, "avg_order": avg}
'''

FILES["minishop/dates.py"] = '''"""Date helpers (business days = Mon..Fri)."""
import datetime as dt


def parse_iso(s):
    return dt.date.fromisoformat(s.strip())


def business_days_between(a, b):
    """Number of business days in [a, b) -- start included, end excluded."""
    if b < a:
        return -business_days_between(b, a)
    n = 0
    for i in range((b - a).days):
        if (a + dt.timedelta(days=i)).weekday() < 5:
            n += 1
    return n


def add_business_days(d, n):
    if n < 0:
        raise ValueError("n must be >= 0")
    while n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d
'''

FILES["minishop/textutil.py"] = '''"""Text helpers."""
import re


def slugify(s):
    """Lowercase; runs of non-alphanumerics become one '-'; no leading/trailing '-'."""
    s = re.sub(r"[^a-z0-9]+", "-", s.lower())
    return s.strip("-")


def truncate(s, n, suffix="..."):
    """Return s if len(s) <= n, else cut so the result (with suffix) has length n."""
    if n <= len(suffix):
        raise ValueError("n must be longer than suffix")
    if len(s) <= n:
        return s
    return s[: n - len(suffix)] + suffix
'''

FILES["minishop/cli.py"] = '''"""Command line: python3 -m minishop.cli report data/orders.csv"""
import argparse
import sys

from .money import format_money
from .report import load_orders_csv, summary, top_skus


def main(argv=None):
    ap = argparse.ArgumentParser(prog="minishop")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("csv")
    r.add_argument("--top", type=int, default=3)
    a = ap.parse_args(argv)
    if a.cmd == "report":
        rows = load_orders_csv(a.csv)
        s = summary(rows)
        print(f"orders: {s['orders']}")
        print(f"revenue: {format_money(s['revenue'])}")
        print(f"avg_order: {format_money(s['avg_order'])}")
        for sku, rev in top_skus(rows, a.top):
            print(f"  {sku}  {format_money(rev)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

FILES["tests/__init__.py"] = ""
FILES["tests/conftest.py"] = "import os, sys\nsys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"

FILES["tests/test_money.py"] = '''import pytest
from minishop.money import round_money, parse_money, format_money


@pytest.mark.parametrize("x,exp", [(2.675, 2.68), (0.125, 0.13), (1.005, 1.01), (2.5, 2.5), (-1.235, -1.24),
                                   (10.0, 10.0), (0.004, 0.0), (99.995, 100.0), (3.14159, 3.14), (7.385, 7.39)])
def test_round_money(x, exp):
    assert round_money(x) == exp


@pytest.mark.parametrize("s,exp", [("$1,234.50", 1234.5), ("-3.2", -3.2), (" 7 ", 7.0), ("$0.125", 0.13),
                                   ("-$5", -5.0), ("1,000,000", 1000000.0)])
def test_parse_money(s, exp):
    assert parse_money(s) == exp


@pytest.mark.parametrize("bad", ["", "  ", "$", "-"])
def test_parse_money_empty(bad):
    with pytest.raises(ValueError):
        parse_money(bad)


def test_parse_money_type():
    with pytest.raises(TypeError):
        parse_money(3)


@pytest.mark.parametrize("x,exp", [(1234.5, "$1,234.50"), (-3.2, "-$3.20"), (0, "$0.00"), (2.675, "$2.68")])
def test_format_money(x, exp):
    assert format_money(x) == exp
'''

FILES["tests/test_pricing.py"] = '''import pytest
from minishop.pricing import apply_discount, bulk_discount_pct, line_price, tax, effective_rate


@pytest.mark.parametrize("qty,pct", [(1, 0), (9, 0), (10, 5), (11, 5), (49, 5), (50, 10), (51, 10),
                                     (99, 10), (100, 15), (101, 15), (1000, 15)])
def test_bulk_discount_pct(qty, pct):
    assert bulk_discount_pct(qty) == pct


@pytest.mark.parametrize("price,pct,exp", [(100, 0, 100), (100, 5, 95), (19.99, 10, 17.99), (10, 100, 0), (0.1, 15, 0.09)])
def test_apply_discount(price, pct, exp):
    assert apply_discount(price, pct) == exp


@pytest.mark.parametrize("pct", [-1, 101, 250])
def test_apply_discount_range(pct):
    with pytest.raises(ValueError):
        apply_discount(10, pct)


@pytest.mark.parametrize("unit,qty,exp", [(2.0, 1, 2.0), (2.0, 10, 19.0), (2.0, 50, 90.0), (2.0, 100, 170.0),
                                          (3.33, 3, 9.99), (1.25, 12, 14.25)])
def test_line_price(unit, qty, exp):
    assert line_price(unit, qty) == exp


def test_line_price_qty():
    with pytest.raises(ValueError):
        line_price(1.0, 0)


@pytest.mark.parametrize("amount,region,exp", [(100, "CA", 7.25), (100, "NY", 4.0), (100, "OR", 0.0),
                                               (19.99, "WA", 1.3), (50, "TX", 3.13)])
def test_tax(amount, region, exp):
    assert tax(amount, region) == exp


def test_tax_unknown_region():
    with pytest.raises(ValueError):
        tax(10, "ZZ")


def test_effective_rate():
    assert effective_rate(100, "CA") == 0.0725
    assert effective_rate(0, "CA") == 0.0
'''

FILES["tests/test_shipping.py"] = '''import pytest
from minishop.shipping import shipping_cost


@pytest.mark.parametrize("w,exp", [(0.0, 4.99), (0.5, 4.99), (1.0, 4.99), (1.01, 9.99), (5.0, 9.99),
                                   (5.5, 19.99), (20.0, 19.99), (21.0, 21.24), (30.0, 32.49)])
def test_shipping_tiers(w, exp):
    assert shipping_cost(w, 10.0) == exp


@pytest.mark.parametrize("sub", [75.0, 75.01, 200.0])
def test_free_shipping(sub):
    assert shipping_cost(3.0, sub) == 0.0


def test_not_free_just_below():
    assert shipping_cost(3.0, 74.99) == 9.99


def test_negative_weight():
    with pytest.raises(ValueError):
        shipping_cost(-1, 10)
'''

FILES["tests/test_orders.py"] = '''import pytest
from minishop.orders import Order, OrderLine, calc_total


def make(region="CA", weight=2.0):
    o = Order("O-1", region=region, weight_kg=weight)
    o.add_line("ABC-0001", 2, 10.0).add_line("XYZ-0002", 10, 1.0)
    return o


def test_subtotal():
    assert make().subtotal() == 29.5


@pytest.mark.parametrize("region,exp", [("CA", 41.63), ("NY", 40.67), ("OR", 39.49)])
def test_calc_total(region, exp):
    assert calc_total(make(region)) == exp


def test_calc_total_free_shipping():
    o = Order("O-2", weight_kg=3.0).add_line("ABC-0001", 8, 10.0)
    assert calc_total(o) == 85.8


@pytest.mark.parametrize("sku", ["abc-0001", "AB-0001", "ABC-001", "ABC0001", "", None])
def test_bad_sku(sku):
    with pytest.raises(ValueError):
        OrderLine(sku, 1, 1.0)


def test_bad_qty():
    with pytest.raises(ValueError):
        OrderLine("ABC-0001", 0, 1.0)
'''

FILES["tests/test_inventory.py"] = '''import pytest
from minishop.inventory import Inventory, InsufficientStock


def test_add_and_available():
    inv = Inventory()
    inv.add("ABC-0001", 5)
    inv.add("ABC-0001", 3)
    assert inv.on_hand("ABC-0001") == 8
    assert inv.available("ABC-0001") == 8


def test_reserve_release_ship():
    inv = Inventory()
    inv.add("ABC-0001", 5)
    inv.reserve("ABC-0001", 4)
    assert inv.available("ABC-0001") == 1
    inv.release("ABC-0001", 1)
    assert inv.available("ABC-0001") == 2
    inv.ship("ABC-0001", 3)
    assert inv.on_hand("ABC-0001") == 2
    assert inv.available("ABC-0001") == 2


def test_insufficient():
    inv = Inventory()
    inv.add("ABC-0001", 2)
    with pytest.raises(InsufficientStock):
        inv.reserve("ABC-0001", 3)


@pytest.mark.parametrize("sku", ["abc-0001", "AB-0001", "ABCD-0001", "ABC-00011", 7])
def test_bad_sku(sku):
    with pytest.raises(ValueError):
        Inventory().add(sku, 1)


@pytest.mark.parametrize("qty", [0, -1])
def test_bad_qty(qty):
    with pytest.raises(ValueError):
        Inventory().add("ABC-0001", qty)


def test_over_release():
    inv = Inventory()
    inv.add("ABC-0001", 2)
    with pytest.raises(ValueError):
        inv.release("ABC-0001", 1)
'''

FILES["tests/test_catalog.py"] = '''import os
import pytest
from minishop.catalog import Product, load_catalog, by_category

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "catalog.csv")


def test_load_catalog():
    cat = load_catalog(DATA)
    assert len(cat) == 120
    assert all(isinstance(p, Product) for p in cat.values())


def test_by_category_sorted():
    cat = load_catalog(DATA)
    tools = by_category(cat, "tools")
    assert tools and [p.sku for p in tools] == sorted(p.sku for p in tools)


@pytest.mark.parametrize("sku", ["abc-0001", "ABC-01", "", "ABC-0001 "])
def test_bad_sku(sku):
    with pytest.raises(ValueError):
        Product(sku, "x", 1.0, "tools", 1.0)


def test_negative_price():
    with pytest.raises(ValueError):
        Product("ABC-0001", "x", -1.0, "tools", 1.0)
'''

FILES["tests/test_report.py"] = '''import os
from minishop.report import load_orders_csv, sales_by_sku, top_skus, summary

ROWS = [
    {"order_id": "1", "sku": "ABC-0001", "quantity": "2", "unit_price": "10.00", "region": "CA"},
    {"order_id": "1", "sku": "XYZ-0002", "quantity": "1", "unit_price": "5.50", "region": "CA"},
    {"order_id": "2", "sku": "ABC-0001", "quantity": "1", "unit_price": "10.00", "region": "NY"},
]


def test_sales_by_sku():
    assert sales_by_sku(ROWS) == {"ABC-0001": 30.0, "XYZ-0002": 5.5}


def test_top_skus():
    assert top_skus(ROWS, 1) == [("ABC-0001", 30.0)]


def test_summary():
    assert summary(ROWS) == {"orders": 2, "revenue": 35.5, "avg_order": 17.75}


def test_load_orders_csv():
    rows = load_orders_csv(os.path.join(os.path.dirname(__file__), "..", "data", "orders.csv"))
    assert len(rows) == 300
'''

FILES["tests/test_dates.py"] = '''import datetime as dt
import pytest
from minishop.dates import parse_iso, business_days_between, add_business_days

D = dt.date


@pytest.mark.parametrize("a,b,exp", [(D(2026, 11, 2), D(2026, 11, 9), 5), (D(2026, 11, 2), D(2026, 11, 2), 0),
                                     (D(2026, 11, 6), D(2026, 11, 9), 1), (D(2026, 11, 7), D(2026, 11, 9), 0),
                                     (D(2026, 11, 9), D(2026, 11, 2), -5), (D(2026, 11, 2), D(2026, 11, 30), 20)])
def test_business_days_between(a, b, exp):
    assert business_days_between(a, b) == exp


@pytest.mark.parametrize("d,n,exp", [(D(2026, 11, 6), 1, D(2026, 11, 9)), (D(2026, 11, 2), 0, D(2026, 11, 2)),
                                     (D(2026, 11, 2), 5, D(2026, 11, 9))])
def test_add_business_days(d, n, exp):
    assert add_business_days(d, n) == exp


def test_parse_iso():
    assert parse_iso(" 2026-11-06 ") == D(2026, 11, 6)
'''

FILES["tests/test_textutil.py"] = '''from minishop.textutil import slugify, truncate


def test_slugify_basic():
    assert slugify("Hello World") == "hello-world"


def test_truncate_basic():
    assert truncate("abcdefghij", 6) == "abc..."
'''


# ---------------------------------------------------------------- 大文件（确定性生成）
WORDS = ("cache shard ledger queue worker invoice token batch replica cursor schema index snapshot quota "
         "tenant gateway webhook retry backoff latency budget partition region failover audit rollout "
         "canary migration checksum lease heartbeat throttle buffer manifest pipeline").split()
VERBS = "reconciles flushes validates retries compacts rotates throttles drains replays signs batches".split()
ADJ = "idempotent eventual bounded stale durable lazy eager partial atomic versioned sharded".split()


def _sentence(rng):
    n = rng.randint(9, 16)
    w = []
    for i in range(n):
        r = rng.random()
        w.append(rng.choice(VERBS) if r < 0.15 else rng.choice(ADJ) if r < 0.35 else rng.choice(WORDS))
    s = " ".join(w)
    return s[0].upper() + s[1:] + "."


def _para(rng, k):
    return " ".join(_sentence(rng) for _ in range(k))


def design_doc():
    rng = random.Random(1106)
    out = ["# minishop platform design notes", ""]
    facts = {
        (3, 2): "Email notification retries: up to 4 attempts, linear backoff of 10 seconds, no cap.",
        (11, 3): ("Payment webhook retries: a failed payment webhook is retried up to 7 attempts in total, "
                  "with exponential backoff starting at 3 seconds and capped at 120 seconds per wait."),
        (6, 1): "Inventory sync retries: up to 2 attempts, fixed 30 second wait.",
    }
    for sec in range(1, 15):
        out.append(f"## {sec}. {rng.choice(WORDS).title()} {rng.choice(WORDS)} subsystem")
        out.append("")
        for sub in range(1, 5):
            out.append(f"### {sec}.{sub} {rng.choice(ADJ)} {rng.choice(WORDS)}")
            out.append("")
            for _ in range(3):
                out.extend(textwrap.wrap(_para(rng, 5), 100))
                out.append("")
            if (sec, sub) in facts:
                out.extend(textwrap.wrap(_para(rng, 2) + " " + facts[(sec, sub)] + " " + _para(rng, 2), 100))
                out.append("")
    return "\n".join(out) + "\n"


HANDBOOK_CODENAMES = ["MARIGOLD", "OBSIDIAN", "PELICAN", "JUNIPER", "TUNGSTEN",
                      "SAFFRON", "QUASAR", "BRAMBLE", "LANTERN", "VERMILION"]


def handbook_part(k):
    """第 k 部分（1..10），约 60KB；第 k 个里程碑代号埋在中间。"""
    rng = random.Random(500 + k)
    out = [f"# Operations handbook, part {k:02d}", ""]
    nsec = 40
    bury = rng.randint(14, 30)
    for sec in range(1, nsec + 1):
        out.append(f"## {k}.{sec} {rng.choice(ADJ).title()} {rng.choice(WORDS)} procedures")
        out.append("")
        for _ in range(3):
            out.extend(textwrap.wrap(_para(rng, 6), 110))
            out.append("")
        if sec == bury:
            out.extend(textwrap.wrap(_para(rng, 2) + f" The codename for milestone {k} is {HANDBOOK_CODENAMES[k-1]}. "
                                     + _para(rng, 2), 110))
            out.append("")
    return "\n".join(out) + "\n"


SERVICES = ["checkout", "payments", "catalog", "search", "auth", "shipping"]
LEVELS = ["INFO"] * 14 + ["WARN"] * 3 + ["ERROR"] * 2 + ["DEBUG"] * 3


def app_log():
    rng = random.Random(4242)
    lines = []
    t = 1793491200  # 2026-11-01T00:00:00Z
    msgs = ["request served", "cache miss", "upstream slow", "retrying", "token refreshed", "queue drained",
            "lease renewed", "timeout talking to upstream", "validation failed", "rate limited"]
    for i in range(6000):
        t += rng.randint(1, 9)
        lvl = rng.choice(LEVELS)
        svc = rng.choice(SERVICES)
        lat = int(rng.lognormvariate(4.0, 0.8))
        if lat > 3000:
            lat = 3000 - rng.randint(1, 500)
        rid = f"R{rng.randint(0, 999999):06d}"
        ts = _dt.datetime.fromtimestamp(t, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines.append(f"{ts} {lvl:5s} svc={svc} req={rid} latency_ms={lat} msg=\"{rng.choice(msgs)}\"")
    # 一个唯一的最大值
    j = 3817
    parts = lines[j].split(" ")
    lines[j] = " ".join(p if not p.startswith("latency_ms=") else "latency_ms=4981" for p in parts)
    lines[j] = lines[j].replace(lines[j].split("req=")[1].split(" ")[0], "R770431")
    return "\n".join(lines) + "\n"


def catalog_csv():
    rng = random.Random(77)
    cats = ["tools", "garden", "kitchen", "office", "toys"]
    rows = ["sku,name,price,category,weight_kg"]
    used = set()
    while len(rows) < 121:
        sku = "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(3)) + "-%04d" % rng.randint(0, 9999)
        if sku in used:
            continue
        used.add(sku)
        rows.append(f"{sku},{rng.choice(WORDS)} {rng.choice(WORDS)},{rng.randint(100, 9999)/100:.2f},"
                    f"{rng.choice(cats)},{rng.randint(1, 300)/10:.1f}")
    return "\n".join(rows) + "\n", sorted(used)


def orders_csv(skus):
    rng = random.Random(99)
    rows = ["order_id,sku,quantity,unit_price,region"]
    oid = 1000
    while len(rows) < 301:
        oid += 1
        for _ in range(rng.randint(1, 4)):
            if len(rows) >= 301:
                break
            rows.append(f"{oid},{rng.choice(skus[:40])},{rng.randint(1, 12)},{rng.randint(100, 5000)/100:.2f},"
                        f"{rng.choice(['CA', 'NY', 'WA', 'OR', 'TX'])}")
    return "\n".join(rows) + "\n"


def make_fixture(dst):
    files = dict(FILES)
    cat, skus = catalog_csv()
    files["data/catalog.csv"] = cat
    files["data/orders.csv"] = orders_csv(skus)
    files["docs/DESIGN.md"] = design_doc()
    files["logs/app.log"] = app_log()
    for k in range(1, 11):
        files[f"docs/handbook/part{k:02d}.md"] = handbook_part(k)
    for rel, text in files.items():
        p = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)
    return dst


if __name__ == "__main__":
    import sys
    make_fixture(sys.argv[1])
    print("ok", sys.argv[1])
