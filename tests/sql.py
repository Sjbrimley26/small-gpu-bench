#!/usr/bin/env python3
"""SQL-SYN: text-to-SQL on an invented hardware-store database (50 questions, executed and compared).

The lab's original SQL50 ran against a real purchasing database, so it cannot be published. This is a rebuild
with the same *shape* on invented data: one SQLite schema, a prompt of system rules + schema DDL + two sample
rows per table + a hint + the question, and a gold SQL for every question that is verified by executing it
(a gold that errors or returns no rows is rejected, as in the lab). Everything is generated from a seed in
code (`--gen` rewrites tests/fixtures/sql/questions.json; the SQLite database itself is built in memory at run
time, so no binary is committed). Nothing here comes from any real catalog.

Traps built in, because they are what made the lab set discriminate between small models:
  * column names with spaces and punctuation that must be double-quoted ("Unit Price / Rate", "Reorder?");
  * near-duplicate names (Unit Price / Rate vs Unit Cost vs line_cost; Part No vs part_no; vendor in three tables);
  * a join the model should NOT make (the finish table lists only some maker/kind pairs, so using it to answer
    "which makers supply this kind" drops rows; the kind table answers it alone);
  * NULL handling (missing cost, unflagged reorder column, unreceived orders; `<>` silently drops NULLs);
  * ties (the most expensive item is shared; "the kind with the most items" is asked without LIMIT 1);
  * date-ish text columns (MM/DD/YYYY text that does not sort or compare as a date; ISO text that does).

Scoring, per question: run the model's SQL and the gold SQL on the same database and compare rows (order-
insensitive unless the question asks for an order). Failures are classed: syntax, hallucinated column,
hallucinated table, other SQL error, wrong rows; plus empty reply, and a transport-error bucket (a request
that failed is not an answer: more than a quarter of rows in it makes the run `invalid`, exit 1).

Two ways to get the SQL out of the reply are scored side by side:
  lenient  cut at the first code fence / prose / blank line / semicolon, the way the lab harness did
  strict   the raw reply is the query (one optional trailing semicolon, nothing else)
A model that writes the right query and then explains it passes lenient and fails strict. That gap is a result.

  python3 tests/sql.py --selftest
  python3 tests/sql.py --gen
  BASE_URL=... MODEL=... python3 tests/sql.py [--limit N] [--max-tokens 700]
"""
import argparse
import json
import os
import random
import re
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

SEED = 20261115
FIXTURE = common.fixture_path("sql", "questions.json")

# ---------------------------------------------------------------- invented data (all made up)
AISLES = [("A1", 1, "Fasteners"), ("A2", 1, "Fasteners"), ("B1", 1, "Hardware"), ("B2", 2, "Hardware"),
          ("C1", 2, "Doors"), ("C2", 2, "Doors")]
KINDS = [("bolt", "threaded bolts and studs", "A1"), ("screw", "wood and machine screws", "A1"),
         ("washer", "flat and lock washers", "A2"), ("anchor", "wall and masonry anchors", "A2"),
         ("hinge", "door and cabinet hinges", "B1"), ("latch", "gate and cabinet latches", "B1"),
         ("bracket", "shelf and corner brackets", "B2"), ("clamp", "pipe and bar clamps", "B2"),
         ("knob", "door and drawer knobs", "C1"), ("caster", "swivel and fixed casters", "C2")]
MAKERS = [("Vantrell", "Canada", 1974), ("Korbin", "Germany", 1951), ("Tamsey", "USA", 1988), ("Brandel", "USA", 1962),
          ("Mosquill", "Taiwan", 1999), ("Dralund", "Sweden", 1947), ("Fulmont", "USA", 2004), ("Zandor", "Italy", 1979),
          ("Perwick", "Canada", 1993), ("Lorvane", "Germany", 1968), ("Hesketh", "USA", 1985), ("Yarrow", "Taiwan", 2011)]
VENDORS = [("Northline Supply", "Tucson", "net 30"), ("Cobalt Trade", "Mesa", "net 45"), ("Ridgeway Parts", "Reno", "net 30"),
           ("Summit Wholesale", "Boise", "net 15"), ("Delmar Brothers", "Tucson", "net 60"), ("Ironwood Direct", "Denver", "net 30"),
           ("Harbor Fasteners", "Tacoma", "net 45"), ("Plainview Sales", "Omaha", "net 30")]
FINISHES = ["zinc", "brass", "black", "nickel", "stainless", "bronze"]
ITEM_WORDS = {"bolt": ["Hex Bolt", "Carriage Bolt", "Eye Bolt"], "screw": ["Wood Screw", "Machine Screw", "Deck Screw"],
              "washer": ["Flat Washer", "Lock Washer"], "anchor": ["Wall Anchor", "Sleeve Anchor"],
              "hinge": ["Butt Hinge", "Strap Hinge", "Cabinet Hinge"], "latch": ["Gate Latch", "Hook Latch"],
              "bracket": ["Shelf Bracket", "Corner Bracket"], "clamp": ["Pipe Clamp", "Bar Clamp"],
              "knob": ["Door Knob", "Drawer Knob"], "caster": ["Swivel Caster", "Fixed Caster"]}
SIZES = ['1/4"', '3/8"', '1/2"', '5/8"', '3/4"', '1"', '2"']

DDL = """CREATE TABLE aisles (aisle TEXT PRIMARY KEY, floor INTEGER, zone TEXT);
CREATE TABLE kinds (kind TEXT PRIMARY KEY, description TEXT, aisle TEXT REFERENCES aisles(aisle));
CREATE TABLE makers (maker TEXT PRIMARY KEY, country TEXT, founded INTEGER);
CREATE TABLE maker_kinds (maker TEXT, kind TEXT);
CREATE TABLE maker_finishes (maker TEXT, kind TEXT, finish TEXT);
CREATE TABLE vendors (vendor TEXT PRIMARY KEY, city TEXT, terms TEXT);
CREATE TABLE vendor_makers (vendor TEXT, maker TEXT);
CREATE TABLE stock_import ("Part No" TEXT, "Item Name" TEXT, "Kind" TEXT, "Maker" TEXT, "Unit Price / Rate" REAL, "Unit Cost" REAL, "Qty On Hand" INTEGER, "Reorder?" TEXT, "Last Counted" TEXT);
CREATE TABLE orders (order_id INTEGER PRIMARY KEY, vendor TEXT, ordered TEXT, received TEXT, status TEXT);
CREATE TABLE order_lines (order_id INTEGER, part_no TEXT, qty INTEGER, line_cost REAL);"""

HINT = """NOTES ABOUT THIS SCHEMA:
- maker_kinds(maker, kind) lists which KINDS a maker supplies. It has NO finish column.
- maker_finishes(maker, kind, finish) lists finishes, but only for some maker/kind pairs. If a question mentions finishes, use maker_finishes.
- vendor_makers(vendor, maker) links vendors to makers.
- kinds(kind, description, aisle): aisle joins to aisles(aisle).
- stock_import has one row per part. Its column names contain spaces and punctuation, so you MUST double-quote them in SQL,
  e.g. SELECT "Unit Price / Rate" FROM stock_import WHERE "Part No" = 'x'.
- "Unit Price / Rate" is the selling price; "Unit Cost" is what the store pays (it can be NULL). order_lines.line_cost is the cost of a whole order line.
- "Last Counted" is text in MM/DD/YYYY form. orders.ordered and orders.received are ISO text (YYYY-MM-DD); received is NULL until the order arrives.
"""


def build_db(seed=SEED):
    """Deterministic invented database -> (sqlite connection in memory, picks used by the question builder)."""
    rng = random.Random(seed)
    con = sqlite3.connect(":memory:")
    con.executescript(DDL)
    con.executemany("INSERT INTO aisles VALUES (?,?,?)", AISLES)
    con.executemany("INSERT INTO kinds VALUES (?,?,?)", KINDS)
    con.executemany("INSERT INTO makers VALUES (?,?,?)", MAKERS)
    con.executemany("INSERT INTO vendors VALUES (?,?,?)", VENDORS)
    kind_names = [k[0] for k in KINDS]
    mk = {}
    for m, _c, _f in MAKERS:
        mk[m] = sorted(rng.sample(kind_names, rng.randint(2, 4)))
    mk["Yarrow"] = []                                      # a maker with no kinds and no stock (for the NOT IN question)
    for m, ks in mk.items():
        for k in ks:
            con.execute("INSERT INTO maker_kinds VALUES (?,?)", (m, k))
            if rng.random() < 0.55:                        # finishes only for some pairs: the join trap
                for f in rng.sample(FINISHES, rng.randint(1, 3)):
                    con.execute("INSERT INTO maker_finishes VALUES (?,?,?)", (m, k, f))
    vm = {}
    for v, _c, _t in VENDORS:
        vm[v] = sorted(rng.sample([m for m in mk if m != "Yarrow"], rng.randint(2, 4)))
        for m in vm[v]:
            con.execute("INSERT INTO vendor_makers VALUES (?,?)", (v, m))
    # parts
    parts, n = [], 0
    stocked = [m for m in mk if mk[m]]
    for i in range(120):
        m = stocked[i % len(stocked)] if i < 60 else rng.choice(stocked)
        k = rng.choice(mk[m])
        fin = rng.choice(FINISHES).capitalize()
        name = "%s %s %s" % (fin, rng.choice(ITEM_WORDS[k]), rng.choice(SIZES))
        price = round(rng.choice([0.45, 0.85, 1.25, 1.95, 2.4, 3.1, 4.75, 6.5, 8.9, 12.0, 15.5, 22.0, 31.0]) * rng.choice([1, 1, 1, 2]), 2)
        cost = None if rng.random() < 0.15 else round(price * rng.uniform(0.45, 0.8), 2)
        qty = rng.choice([0, 0, 3, 8, 12, 20, 35, 60, 90, 150, 240])
        flag = rng.choice(["Y", "N", "N", None])
        m_, d_ = rng.randint(1, 12), rng.randint(1, 28)
        counted = "%02d/%02d/%d" % (m_, d_, rng.choice([2025, 2026, 2026]))
        n += 1
        parts.append(("PT-%04d" % (1000 + n * 7), name, k, m, price, cost, qty, flag, counted))
    top = max(p[4] for p in parts)
    low = min(p[4] for p in parts)
    for j in (3, 41):                                      # engineered ties: the most expensive and the cheapest are shared
        p = list(parts[j]); p[4] = top; parts[j] = tuple(p)
    for j in (17, 88):
        p = list(parts[j]); p[4] = low; parts[j] = tuple(p)
    con.executemany("INSERT INTO stock_import VALUES (?,?,?,?,?,?,?,?,?)", parts)
    # orders (a vendor with none: the NOT IN question)
    ordering = [v for v, _c, _t in VENDORS if v != "Plainview Sales"]
    oid = 5000
    for i in range(40):
        oid += rng.randint(1, 9)
        v = ordering[i % len(ordering)] if i < 28 else rng.choice(ordering)
        od = "2026-%02d-%02d" % (rng.randint(1, 10), rng.randint(1, 28))
        open_ = rng.random() < 0.25
        if open_:
            rc, st = None, "open"
        else:
            y, mo, d = map(int, od.split("-"))
            d2 = d + rng.randint(3, 16)
            mo2, d2 = (mo + 1, d2 - 28) if d2 > 28 else (mo, d2)
            rng.choice(["received", "received", "short"])   # drawn and discarded: keeps the seeded stream as first generated
            rc, st = "2026-%02d-%02d" % (min(mo2, 12), d2), "received"   # status agrees with received-date by construction
        con.execute("INSERT INTO orders VALUES (?,?,?,?,?)", (oid, v, od, rc, st))
        pool = [p for p in parts if p[3] in vm[v]] or parts
        for p in rng.sample(pool, rng.randint(1, 4)):
            q = rng.choice([10, 20, 25, 50, 100, 200])
            con.execute("INSERT INTO order_lines VALUES (?,?,?,?)", (oid, p[0], q, round(q * (p[5] or p[4] * 0.6), 2)))
    con.commit()
    return con


def schema_ddl(con):
    return "\n".join(r[0] for r in con.execute("select sql from sqlite_master where type='table' and sql is not null order by name"))


def sample_rows(con, n=2):
    out = []
    for (t,) in con.execute("select name from sqlite_master where type='table' order by name").fetchall():
        cols = [d[1] for d in con.execute('pragma table_info("%s")' % t)]
        rows = con.execute('select * from "%s" limit %d' % (t, n)).fetchall()
        out.append("%s: cols=%s" % (t, cols))
        for r in rows:
            out.append("   " + " | ".join("" if v is None else str(v)[:38] for v in r))
    return "\n".join(out)


# ---------------------------------------------------------------- questions: (kind, question, gold SQL, ordered)
def questions(con):
    q = lambda sql: con.execute(sql).fetchall()   # noqa: E731
    # picks derived from the data (deterministic), so every question has a non-empty gold
    maker_multi = q("select maker from maker_kinds group by maker having count(*)>=3 order by maker")[0][0]
    maker_fin = q("select maker, kind from maker_finishes group by maker, kind order by maker, kind")[0]
    kind_hot = q('select "Kind" from stock_import group by "Kind" order by count(*) desc, "Kind"')[0][0]
    kind_b = q("select kind from kinds where aisle='B1' order by kind")[0][0]
    vend_a, vend_b = VENDORS[0][0], VENDORS[4][0]
    mk_v = con.execute("select maker from vendor_makers where vendor=? order by maker", (vend_a,)).fetchone()[0]
    fin_m, fin_k = maker_fin
    fin_f = con.execute("select finish from maker_finishes where maker=? and kind=? order by finish", maker_fin).fetchone()[0]
    mk_stock = q('select "Maker" from stock_import group by "Maker" order by count(*) desc, "Maker"')[0][0]
    ord_id = q("select order_id from order_lines group by order_id having count(*)>=3 order by order_id")[0][0]
    P = '"Unit Price / Rate"'
    Q = []

    def add(kind, text, sql, ordered=False):
        Q.append({"kind": kind, "question": text, "gold": sql, "ordered": ordered})

    # --- count
    add("count", "How many parts are in stock_import?", "SELECT COUNT(*) FROM stock_import")
    add("count", "How many vendors are in Tucson?", "SELECT COUNT(*) FROM vendors WHERE city = 'Tucson'")
    add("count", "How many kinds are shelved in aisle B2?", "SELECT COUNT(*) FROM kinds WHERE aisle = 'B2'")
    # --- filter / quoting
    add("quoting", "List the Part No of every part with a selling price over 20.", 'SELECT "Part No" FROM stock_import WHERE %s > 20' % P)
    add("quoting", "List the Item Name of every %s part with fewer than 20 on hand." % kind_hot,
        'SELECT "Item Name" FROM stock_import WHERE "Kind" = \'%s\' AND "Qty On Hand" < 20' % kind_hot)
    add("quoting", "List the Part No of parts flagged for reorder.", 'SELECT "Part No" FROM stock_import WHERE "Reorder?" = \'Y\'')
    add("quoting", "List the Part No and Item Name of every part made by %s." % mk_stock,
        'SELECT "Part No", "Item Name" FROM stock_import WHERE "Maker" = \'%s\'' % mk_stock)
    add("quoting", "How many parts have nothing on hand?", 'SELECT COUNT(*) FROM stock_import WHERE "Qty On Hand" = 0')
    add("quoting", "List the Item Name of parts whose name contains 'Brass'.", "SELECT \"Item Name\" FROM stock_import WHERE \"Item Name\" LIKE '%Brass%'")
    add("quoting", "List the Part No of parts that sell for more than they cost.", 'SELECT "Part No" FROM stock_import WHERE %s > "Unit Cost"' % P)
    # --- NULL handling
    add("nulls", "How many parts have no cost recorded?", 'SELECT COUNT(*) FROM stock_import WHERE "Unit Cost" IS NULL')
    add("nulls", "How many parts have never been flagged either way in the reorder column?", 'SELECT COUNT(*) FROM stock_import WHERE "Reorder?" IS NULL')
    add("nulls", "What is the average unit cost across parts that have a cost recorded?", 'SELECT AVG("Unit Cost") FROM stock_import')
    add("nulls", "How many orders have not been received yet?", "SELECT COUNT(*) FROM orders WHERE received IS NULL")
    add("nulls", "How many parts are not flagged Y for reorder, counting parts with no flag at all?",
        'SELECT COUNT(*) FROM stock_import WHERE "Reorder?" IS NULL OR "Reorder?" <> \'Y\'')
    # --- aggregates
    add("aggregate", "What is the total quantity on hand across all parts?", 'SELECT SUM("Qty On Hand") FROM stock_import')
    add("aggregate", "What is the average selling price of %s parts?" % kind_hot, "SELECT AVG(%s) FROM stock_import WHERE \"Kind\" = '%s'" % (P, kind_hot))
    add("aggregate", "What is the lowest selling price in stock_import?", "SELECT MIN(%s) FROM stock_import" % P)
    add("aggregate", "What is the total value on hand at selling price (price times quantity)?", 'SELECT SUM(%s * "Qty On Hand") FROM stock_import' % P)
    add("aggregate", "What is the total line cost of order %d?" % ord_id, "SELECT SUM(line_cost) FROM order_lines WHERE order_id = %d" % ord_id)
    # --- group by / having
    add("groupby", "How many parts are there of each kind? Return kind and count.", 'SELECT "Kind", COUNT(*) FROM stock_import GROUP BY "Kind"')
    add("groupby", "What is the average selling price per kind? Return kind and average.", 'SELECT "Kind", AVG(%s) FROM stock_import GROUP BY "Kind"' % P)
    add("groupby", "What is the total quantity on hand per maker? Return maker and total.", 'SELECT "Maker", SUM("Qty On Hand") FROM stock_import GROUP BY "Maker"')
    add("groupby", "How many orders has each vendor placed? Return vendor and count.", "SELECT vendor, COUNT(*) FROM orders GROUP BY vendor")
    add("groupby", "Which kinds have more than 12 parts? Return the kind.", 'SELECT "Kind" FROM stock_import GROUP BY "Kind" HAVING COUNT(*) > 12')
    add("groupby", "Which vendors have placed at least 5 orders? Return the vendor.", "SELECT vendor FROM orders GROUP BY vendor HAVING COUNT(*) >= 5")
    # --- joins
    add("join", "List the vendors that supply the maker %s." % mk_v, "SELECT vendor FROM vendor_makers WHERE maker = '%s'" % mk_v)
    add("join", "List the distinct cities of vendors that supply %s." % mk_v,
        "SELECT DISTINCT v.city FROM vendors v JOIN vendor_makers vm ON vm.vendor = v.vendor WHERE vm.maker = '%s'" % mk_v)
    add("join", "On which floor is the %s kind shelved?" % kind_b, "SELECT a.floor FROM kinds k JOIN aisles a ON a.aisle = k.aisle WHERE k.kind = '%s'" % kind_b)
    add("join", "List the kinds shelved on floor 2.", "SELECT k.kind FROM kinds k JOIN aisles a ON a.aisle = k.aisle WHERE a.floor = 2")
    add("join", "List the Part No of parts whose kind is shelved in the Doors zone.",
        'SELECT s."Part No" FROM stock_import s JOIN kinds k ON k.kind = s."Kind" JOIN aisles a ON a.aisle = k.aisle WHERE a.zone = \'Doors\'')
    add("join", "List the countries of the makers that %s buys from (distinct)." % vend_b,
        "SELECT DISTINCT m.country FROM vendor_makers vm JOIN makers m ON m.maker = vm.maker WHERE vm.vendor = '%s'" % vend_b)
    # --- the join the model should not make
    add("nojoin", "Which kinds does %s supply? Return the kind." % maker_multi, "SELECT kind FROM maker_kinds WHERE maker = '%s'" % maker_multi)
    add("nojoin", "Which makers supply the %s kind? Return the maker." % kind_hot, "SELECT maker FROM maker_kinds WHERE kind = '%s'" % kind_hot)
    add("nojoin", "Which finishes does %s offer for %s? Return the finish." % (fin_m, fin_k),
        "SELECT finish FROM maker_finishes WHERE maker = '%s' AND kind = '%s'" % (fin_m, fin_k))
    add("nojoin", "Which makers offer a %s finish for %s? Return the maker." % (fin_f, fin_k),
        "SELECT maker FROM maker_finishes WHERE kind = '%s' AND finish = '%s'" % (fin_k, fin_f))
    # --- ties
    add("ties", "List the Part No of the most expensive part or parts by selling price.",
        'SELECT "Part No" FROM stock_import WHERE %s = (SELECT MAX(%s) FROM stock_import)' % (P, P))
    add("ties", "List the Part No of the cheapest part or parts by selling price.",
        'SELECT "Part No" FROM stock_import WHERE %s = (SELECT MIN(%s) FROM stock_import)' % (P, P))
    add("ties", "Which kind or kinds have the most parts? Return the kind.",
        'SELECT "Kind" FROM stock_import GROUP BY "Kind" HAVING COUNT(*) = '
        '(SELECT MAX(c) FROM (SELECT COUNT(*) AS c FROM stock_import GROUP BY "Kind"))')
    # --- subqueries / negation
    add("subquery", "List the Part No of parts that sell for more than the average selling price.",
        'SELECT "Part No" FROM stock_import WHERE %s > (SELECT AVG(%s) FROM stock_import)' % (P, P))
    add("subquery", "Which makers have no parts in stock_import? Return the maker.",
        'SELECT maker FROM makers WHERE maker NOT IN (SELECT "Maker" FROM stock_import)')
    add("subquery", "Which vendors have never placed an order? Return the vendor.", "SELECT vendor FROM vendors WHERE vendor NOT IN (SELECT vendor FROM orders)")
    add("subquery", "List the Part No of parts that have never been ordered.",
        'SELECT "Part No" FROM stock_import WHERE "Part No" NOT IN (SELECT part_no FROM order_lines)')
    # --- dates
    add("dates", "List the Part No of parts last counted in March 2026.", 'SELECT "Part No" FROM stock_import WHERE "Last Counted" LIKE \'03/%/2026\'')
    add("dates", "List the order_id of orders placed in the third quarter of 2026 (July to September).",
        "SELECT order_id FROM orders WHERE ordered >= '2026-07-01' AND ordered < '2026-10-01'")
    add("dates", "List the order_id of received orders that took more than 10 days from order to receipt.",
        "SELECT order_id FROM orders WHERE received IS NOT NULL AND julianday(received) - julianday(ordered) > 10")
    add("dates", "What is the most recent order date for each vendor? Return vendor and date.", "SELECT vendor, MAX(ordered) FROM orders GROUP BY vendor")
    # --- order + limit (row order is scored)
    add("ordered", "List the Part No and selling price of the 5 most expensive parts, highest first, ties broken by Part No ascending.",
        'SELECT "Part No", %s FROM stock_import ORDER BY %s DESC, "Part No" ASC LIMIT 5' % (P, P), True)
    add("ordered", "List the order_id and ordered date of the 3 most recent orders, newest first, ties broken by order_id descending.",
        "SELECT order_id, ordered FROM orders ORDER BY ordered DESC, order_id DESC LIMIT 3", True)
    add("ordered", "List every maker with its founding year, oldest first.", "SELECT maker, founded FROM makers ORDER BY founded ASC", True)
    for i, item in enumerate(Q):
        item["id"] = "q%02d" % (i + 1)
    return Q


KIND_ORDER = ["count", "quoting", "nulls", "aggregate", "groupby", "join", "nojoin", "ties", "subquery", "dates", "ordered"]


def verify(con, qs):
    """Reject a question whose gold errors or returns no rows (as the lab does)."""
    good, bad = [], []
    for it in qs:
        rows, err = run_sql(con, it["gold"])
        if err:
            bad.append((it["id"], "SQL ERROR: " + err))
        elif not rows:
            bad.append((it["id"], "EMPTY RESULT"))
        elif all(all(v == "" for v in r) for r in rows):
            bad.append((it["id"], "ALL-NULL RESULT"))
        else:
            good.append(it)
    return good, bad


def generate(seed=SEED):
    con = build_db(seed)
    qs = questions(con)
    good, bad = verify(con, qs)
    if bad:
        raise RuntimeError("gold verification failed: %r" % bad)
    return {"seed": seed, "synthetic": True, "n_questions": len(good), "kind_order": KIND_ORDER,
            "schema": schema_ddl(con), "samples": sample_rows(con), "questions": good}


# ---------------------------------------------------------------- execution and scoring
def norm(rows, ordered):
    out = [tuple("" if v is None else (round(v, 4) if isinstance(v, float) else v) for v in r) for r in rows]
    return out if ordered else sorted(out, key=lambda r: tuple(str(x) for x in r))


def run_sql(con, sql, ordered=False):
    try:
        cur = con.execute(sql)
        return norm(cur.fetchall(), ordered), None
    except Exception as e:  # noqa: BLE001 - any SQLite complaint is the model's error class
        return None, "%s: %s" % (type(e).__name__, e)


def classify(err):
    e = (err or "").lower()
    if "no such column" in e:
        return "hallucinated column"
    if "no such table" in e:
        return "hallucinated table"
    if "syntax" in e or "incomplete input" in e or "unrecognized token" in e or "one statement" in e:
        return "syntax"
    return "other sql error"


def extract_lenient(raw):
    """Cut the query out of a reply: first fenced block if any, else from the first SQL keyword to the first
    semicolon / blank line / prose line."""
    t = (raw or "").strip()
    m = re.search(r"```[a-zA-Z]*\s*(.*?)(```|$)", t, re.S)
    if m:
        t = m.group(1).strip()
    lines, out, started = t.splitlines(), [], False
    for ln in lines:
        if not started:
            if re.match(r"\s*(SELECT|WITH)\b", ln, re.I):
                started = True
            else:
                continue
        elif not ln.strip():
            break
        out.append(ln)
        if ln.rstrip().endswith(";"):
            break
    s = "\n".join(out)
    if ";" in s:
        s = s[:s.index(";")]
    return s.strip()


def extract_strict(raw):
    """The raw reply is the query: strip whitespace and one trailing semicolon, nothing else."""
    return re.sub(r";\s*$", "", (raw or "").strip())


def format_issue(raw):
    """Why the raw reply is not a bare query: 'fence', 'prose', or '' when it is."""
    t = (raw or "").strip()
    if "```" in t:
        return "fence"
    return "" if extract_lenient(t) == extract_strict(t) else "prose"


def judge(con, item, raw, errored=False):
    """-> {'outcome': ok|wrong_rows|<error class>|empty|error, 'strict': same, 'format': ''|fence|prose}"""
    if errored:
        return {"outcome": "error", "strict": "error", "format": ""}
    if not (raw or "").strip():
        return {"outcome": "empty", "strict": "empty", "format": ""}
    gold, _ = run_sql(con, item["gold"], item["ordered"])

    def one(sql):
        if not sql:
            return "empty"
        got, err = run_sql(con, sql, item["ordered"])
        if err:
            return classify(err)
        return "ok" if got == gold else "wrong rows"
    return {"outcome": one(extract_lenient(raw)), "strict": one(extract_strict(raw)), "format": format_issue(raw)}


def summarize(items, judged):
    """items: fixture questions; judged: {id: judge dict}. -> totals, per-kind, error classes."""
    n = len(items)
    cnt = Counter(judged[i["id"]]["outcome"] for i in items)
    scnt = Counter(judged[i["id"]]["strict"] for i in items)
    by_kind = {}
    for k in KIND_ORDER:
        ks = [i for i in items if i["kind"] == k]
        if ks:
            by_kind[k] = [sum(judged[i["id"]]["outcome"] == "ok" for i in ks), len(ks)]
    fmt = Counter(judged[i["id"]]["format"] for i in items if judged[i["id"]]["outcome"] == "ok" and judged[i["id"]]["strict"] != "ok")
    return {"n": n, "lenient_ok": cnt["ok"], "strict_ok": scnt["ok"], "lenient_errors": {k: v for k, v in cnt.items() if k != "ok"},
            "strict_errors": {k: v for k, v in scnt.items() if k != "ok"},
            "format_loss": {"total": sum(fmt.values()), "fence": fmt["fence"], "prose": fmt["prose"]},
            "empty": cnt["empty"], "transport_errors": cnt["error"], "by_kind": by_kind}


# ---------------------------------------------------------------- prompt
def system_prompt():
    return ("You are a SQLite expert. Write ONE SQLite query answering the question.\nRules:\n"
            "- Use ONLY the exact table and column names given in the schema.\n"
            "- If a column name contains spaces or punctuation, wrap it in double quotes.\n"
            "- Prefer the simplest query; do not join tables you do not need.\n"
            "- Output ONLY SQL. No explanation, no markdown.\n")


def user_prompt(fx, question):
    return "SCHEMA:\n%s\n\nSAMPLE ROWS:\n%s\n%s\nQUESTION: %s\nSQL:" % (fx["schema"], fx["samples"], HINT, question)


# ---------------------------------------------------------------- selftest
def selftest():
    c = common.Checks()
    fx = generate()
    c("generation is deterministic", generate() == fx)
    if os.path.exists(FIXTURE):
        with open(FIXTURE, encoding="utf-8") as f:
            c("committed fixture equals the seed's output", json.load(f) == fx)
    qs = fx["questions"]
    con = build_db()
    c("50 questions", len(qs) == 50)
    c("question ids and texts are unique", len({q["id"] for q in qs}) == 50 and len({q["question"] for q in qs}) == 50)
    c("every kind in KIND_ORDER is used, no other", {q["kind"] for q in qs} == set(KIND_ORDER))
    g, b = verify(con, qs)
    c("every gold executes and returns rows", len(g) == 50 and not b)
    c("verify rejects an erroring gold", verify(con, [{"id": "x", "gold": "SELECT nope FROM stock_import", "ordered": False}])[1][0][1].startswith("SQL ERROR"))
    c("verify rejects an empty gold", verify(con, [{"id": "x", "gold": "SELECT 1 WHERE 0", "ordered": False}])[1][0][1] == "EMPTY RESULT")
    c("ddl has the quoted-name table", '"Unit Price / Rate"' in fx["schema"] and '"Reorder?"' in fx["schema"])
    c("the most expensive part is tied (tie question is a real tie)",
      len(run_sql(con, [q for q in qs if "most expensive part or parts" in q["question"]][0]["gold"])[0]) >= 2)
    c("the finish table omits some maker/kind pairs (the no-join trap bites)",
      con.execute("select count(*) from (select distinct maker, kind from maker_kinds except select distinct maker, kind from maker_finishes)").fetchone()[0] > 0)
    c("there are NULL costs, NULL reorder flags and open orders",
      all(con.execute(s).fetchone()[0] > 0 for s in ('select count(*) from stock_import where "Unit Cost" is null',
                                                      'select count(*) from stock_import where "Reorder?" is null',
                                                      "select count(*) from orders where received is null")))
    # no leakage of the lab's real vocabulary: the schema, questions and gold may not contain any of these words
    # (a short list of generic giveaways of the private catalog; the real names stay out of this repo entirely)
    blob = json.dumps(fx).lower()
    banned = ["q" + "bo", "cac" + "tus", "az" + "tec", "leg" + "acy", "sl" + "ab", "item" + "ized", "manufac" + "turer", "s" + "ku", "door " + "style", "catalog" + ".db"]
    c("no private-catalog vocabulary in the fixture", not [w for w in banned if w in blob])
    # extractors
    sql = 'SELECT COUNT(*) FROM stock_import'
    c("lenient: bare query", extract_lenient(sql) == sql)
    c("lenient: fenced", extract_lenient("```sql\n%s;\n```\nThis counts rows." % sql) == sql)
    c("lenient: prose after the query", extract_lenient("%s\n\nThis query counts the rows." % sql) == sql)
    c("lenient: prose before", extract_lenient("Here is the query:\n%s;" % sql) == sql)
    c("lenient: multi-line query kept", extract_lenient("SELECT a\nFROM t\nWHERE x = 1;") == "SELECT a\nFROM t\nWHERE x = 1")
    c("strict: one trailing semicolon allowed", extract_strict(sql + ";\n") == sql)
    c("format_issue: bare is clean", format_issue(sql) == "" and format_issue(sql + ";") == "")
    c("format_issue: fence and prose", format_issue("```sql\n%s\n```" % sql) == "fence" and format_issue(sql + "\n\nThat counts rows.") == "prose")
    # scoring
    item = {"gold": 'SELECT "Part No" FROM stock_import WHERE "Qty On Hand" = 0', "ordered": False}
    good = item["gold"]
    c("judge: right query", judge(con, item, good)["outcome"] == "ok")
    c("judge: right query plus prose passes lenient, fails strict",
      (lambda j: j["outcome"] == "ok" and j["strict"] != "ok" and j["format"] == "prose")(judge(con, item, good + "\n\nThis lists the empty parts.")))
    c("judge: fenced passes lenient, fails strict",
      (lambda j: j["outcome"] == "ok" and j["strict"] != "ok" and j["format"] == "fence")(judge(con, item, "```sql\n%s\n```" % good)))
    c("judge: row order does not matter when not asked", judge(con, {"gold": "SELECT maker FROM makers", "ordered": False},
                                                              "SELECT maker FROM makers ORDER BY maker DESC")["outcome"] == "ok")
    c("judge: row order matters when asked", judge(con, {"gold": "SELECT maker FROM makers ORDER BY maker", "ordered": True},
                                                  "SELECT maker FROM makers ORDER BY maker DESC")["outcome"] == "wrong rows")
    c("judge: unquoted spaced column is a syntax error", judge(con, item, "SELECT Part No FROM stock_import")["outcome"] in ("syntax", "hallucinated column"))
    c("judge: invented column", judge(con, item, "SELECT part_no FROM stock_import")["outcome"] == "hallucinated column")
    c("judge: invented table", judge(con, item, "SELECT x FROM parts")["outcome"] == "hallucinated table")
    c("judge: wrong rows", judge(con, item, 'SELECT "Part No" FROM stock_import')["outcome"] == "wrong rows")
    c("judge: empty reply and transport error are separate buckets",
      judge(con, item, "")["outcome"] == "empty" and judge(con, item, "", errored=True)["outcome"] == "error")
    c("judge: prose with no SQL at all is empty-after-extraction, not a crash", judge(con, item, "I cannot do that.")["outcome"] == "empty")
    c("classify: syntax", classify('OperationalError: near "FROM": syntax error') == "syntax")
    perfect = {q["id"]: judge(con, q, q["gold"]) for q in qs}
    s = summarize(qs, perfect)
    c("gold answers score 50/50 lenient and strict", s["lenient_ok"] == 50 and s["strict_ok"] == 50 and s["format_loss"]["total"] == 0)
    wrapped = {q["id"]: judge(con, q, q["gold"] + "\n\nExplanation follows.") for q in qs}
    s2 = summarize(qs, wrapped)
    c("gold plus trailing prose: 50 lenient, 0 strict, 50 format losses", s2["lenient_ok"] == 50 and s2["strict_ok"] == 0 and s2["format_loss"]["prose"] == 50)
    c("always-SELECT-1 scores almost nothing", summarize(qs, {q["id"]: judge(con, q, "SELECT 1") for q in qs})["lenient_ok"] <= 2)
    c("per-kind totals add up to 50", sum(v[1] for v in s["by_kind"].values()) == 50)
    return c.finish()


# ---------------------------------------------------------------- run
def main():
    ap = common.add_common_args(argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0]))
    ap.add_argument("--max-tokens", type=int, default=700, help="output cap (the lab's SQL50 used 700)")
    ap.add_argument("--limit", type=int, default=0, help="score only the first N questions")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--gen", action="store_true", help="rewrite the committed fixture from the seed")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.gen:
        os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
        with open(FIXTURE, "w", encoding="utf-8") as f:
            json.dump(generate(), f, indent=1)
            f.write("\n")
        print("wrote", FIXTURE)
        return 0
    with open(FIXTURE, encoding="utf-8") as f:
        fx = json.load(f)
    con = build_db(fx["seed"])
    items = fx["questions"][:a.limit] if a.limit else fx["questions"]
    judged, detail, wall, toks = {}, [], 0.0, 0
    for i, it in enumerate(items):
        msgs = [{"role": "system", "content": system_prompt()}, {"role": "user", "content": user_prompt(fx, it["question"])}]
        r = common.chat(a, msgs, max_tokens=a.max_tokens, temperature=0.0)
        wall += r["s"]
        toks += r.get("completion_tokens") or 0
        j = judge(con, it, r["content"], errored=bool(r.get("error")))
        if r.get("finish") == "length" and j["outcome"] != "ok" and j["outcome"] != "error":
            j["capped"] = True                       # hit the token cap: reported, still scored on what came back
        judged[it["id"]] = j
        detail.append({"id": it["id"], "kind": it["kind"], **j, "finish": r.get("finish"), "error": r.get("error"),
                       "s": round(r["s"], 1),
                       "decode_tps": (r.get("timings") or {}).get("predicted_per_second"),
                       "draft_n": (r.get("timings") or {}).get("draft_n"),
                       "draft_accepted": (r.get("timings") or {}).get("draft_n_accepted"), "reply": (r["content"] or "")[:600]})
        if (i + 1) % 10 == 0:
            common.say("  %d/%d questions, %.0fs" % (i + 1, len(items), wall))
    s = summarize(items, judged)
    capped = sum(bool(d.get("capped")) for d in detail)
    common.say("lenient %d/%d   strict %d/%d   format losses %d (fence %d, prose %d)   empty %d   capped %d   transport errors %d"
               % (s["lenient_ok"], s["n"], s["strict_ok"], s["n"], s["format_loss"]["total"], s["format_loss"]["fence"],
                  s["format_loss"]["prose"], s["empty"], capped, s["transport_errors"]))
    common.say("lenient errors: %s" % (dict(s["lenient_errors"]) or "none"))
    common.say("by kind (ok/n): " + "  ".join("%s %d/%d" % (k, v[0], v[1]) for k, v in s["by_kind"].items()))
    common.say("wall %.0fs, %d completion tokens" % (wall, toks))
    invalid = s["transport_errors"] > len(items) // 4
    if invalid:
        common.say("INVALID RUN: %d of %d questions hit a transport/HTTP error; do not publish" % (s["transport_errors"], len(items)))
    common.save_results(a, "SQL", {"fixture_seed": fx["seed"], "synthetic": True, "n_items": len(items), "summary": s,
                                   "wall_s": round(wall, 1), "completion_tokens": toks, "capped": capped,
                                   "answers": detail, "invalid": invalid})
    return 1 if invalid else 0


if __name__ == "__main__":
    sys.exit(main())
