#!/usr/bin/env python3
"""RED: redaction recall on synthetic business documents.

12 documents (email, invoice, bank statement, phone note; 3 of each) are generated in code from a seed, with
78 planted identifiers (names, emails, phones in 4 formats, street addresses, account/routing numbers incl.
one split in groups of digits, card last-4, tax IDs, an obfuscated "x at y dot com" email) and 72 keep-items
(company names, SKUs, amounts, dates) that a redactor must leave alone.

Arms: `regex` (no model), `model` (the model alone), `hybrid` (regex first, then the model on what is left).
A digit identifier counts as leaked if its digits survive in any punctuation. Lower leaked is better; kept
should stay at 72/72.

Everything here is invented: the names, companies, SKUs, addresses and numbers are random or fictional.
The fixtures differ from the ones behind the published numbers only in vocabulary (company, SKU and city
names were replaced; the random draw structure and the grader are unchanged), so counts of planted items
match (78 / 72) but a model's score is not guaranteed identical.

  python3 tests/red.py --selftest                       graders vs oracle / no-op / over-redactor, no model
  BASE_URL=http://localhost:8080 MODEL=my-model python3 tests/red.py [--arms model,hybrid] [--docs 12]
"""
import argparse
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

FIRST = ["Rosa", "Miguel", "Dana", "Trevor", "Lucia", "Hector", "Brianna", "Wendell", "Ines", "Garrett", "Marisol", "Dwayne"]
LAST = ["Quintero", "Halvorsen", "Abernathy", "Okafor", "Villalobos", "Pritchett", "Szabo", "Delacroix", "Murtagh", "Ybarra"]
STREETS = ["Cedar Ridge Rd", "W Larkspur Ave", "N 43rd Pl", "Hollow Oak Dr", "E Juniper Blvd", "Copperleaf Ln"]
CITIES = ["Riverton, ZZ 10001", "Lakeview, ZZ 10002", "Fairmont, ZZ 10003", "Eastport, ZZ 10004"]
COMPANIES = ["Alder Building Products", "Birch Doors", "Cedar Millwork", "Dover Trim & Door", "Elm Distribution"]
SKUS = ["K4410-A", "TR-205-16", "PN-882-PR", "QX-30417", "LM-1180-12", "SH-1x4-FJ"]
RETAILER = "HARDWARE MART"


def _phone(r, style):
    a, b, c = "%03d" % r.choice([212, 415, 312, 617]), "555", "%04d" % r.randint(100, 9999)
    return {0: "(%s) %s-%s" % (a, b, c), 1: "%s.%s.%s" % (a, b, c), 2: "%s %s %s" % (a, b, c),
            3: "%s-%s-%s" % (a, b, c)}[style], a + b + c


def make_red_docs(n_per=3, seed=20261107):
    """Return docs: {id, fmt, text, secrets:[{kind, value, check}], keeps:[str]}. check = name|digits|digits4|text."""
    r = random.Random(seed)
    docs = []
    for k in range(n_per):
        for fmt in ("email", "invoice", "bank", "phone_note"):
            fn, ln = r.choice(FIRST), r.choice(LAST)
            fn2, ln2 = r.choice([x for x in FIRST if x != fn]), r.choice([x for x in LAST if x != ln])
            co, co2 = r.sample(COMPANIES, 2)
            sku, sku2 = r.sample(SKUS, 2)
            qty, price = r.randint(8, 400), r.randint(40, 4000) + r.choice([0.0, 0.25, 0.5, 0.92])
            amt = "$%s" % format(round(qty * price / 10, 2), ",.2f")
            date = "%d/%d/2026" % (r.randint(1, 10), r.randint(1, 28))
            ph, phd = _phone(r, r.randint(0, 3))
            ph2, ph2d = _phone(r, (k + 1) % 4)
            em = "%s.%s@%s.example" % (fn[0].lower(), ln.lower(), co.split()[0].lower())
            addr = "%d %s" % (r.randint(100, 9899), r.choice(STREETS))
            city = r.choice(CITIES)
            acct = "".join(str(r.randint(0, 9)) for _ in range(10))
            rout = "1220%05d" % r.randint(0, 99999)
            ein = "%02d-%07d" % (r.randint(10, 99), r.randint(0, 9999999))
            last4 = "%04d" % r.randint(1000, 9999)
            S = lambda kind, value, check: {"kind": kind, "value": value, "check": check}  # noqa: E731
            if fmt == "email":
                text = (f"From: {fn} {ln} <{em}>\nTo: purchasing@example.test\nDate: {date}\n"
                        f"Subject: PO for {sku}\n\nHi {fn2},\n\nPlease confirm {qty} LF of {sku} at the quoted price. "
                        f"Ship to {addr}, {city}. If anything changes call me on {ph} or my cell {ph2}.\n"
                        f"Total should come to {amt}. {ln2} in receiving will sign for it.\n\nThanks,\n-- {fn.lower()} {ln.lower()}\n{co}")
                sec = [S("name", f"{fn} {ln}", "name"), S("email", em, "text"), S("name", fn2, "name"),
                       S("address", addr, "text"), S("phone", phd, "digits"), S("phone", ph2d, "digits"),
                       S("name", ln2, "name")]
                keeps = [sku, str(qty) + " LF", amt, co, date]
            elif fmt == "invoice":
                text = (f"{co2}\nINVOICE  {date}\nSold to: {co}\nAttn: {fn} {ln}\n{addr}\n{city}\n"
                        f"Phone {ph}   Fed Tax ID {ein}\n\nLINE  ITEM         QTY    UNIT    EXT\n"
                        f"1     {sku}    {qty} LF  {price:.2f}  {amt}\n2     {sku2}     12 EA  18.40   $220.80\n\n"
                        f"Remit by ACH: routing {rout}, account {acct}. Questions: {em}")
                sec = [S("name", f"{fn} {ln}", "name"), S("address", addr, "text"), S("phone", phd, "digits"),
                       S("taxid", ein, "digits"), S("routing", rout, "digits"), S("account", acct, "digits"),
                       S("email", em, "text")]
                keeps = [co2, co, sku, sku2, amt, "$220.80", date]
            elif fmt == "bank":
                sp = " ".join(acct[i:i + 4] for i in range(0, 10, 4))   # irregular: account split in groups
                text = (f"Statement period {date}\nAccount holder: {fn.upper()} {ln.upper()}\n{addr}, {city}\n"
                        f"Account no. {sp}\n\nDATE    DESCRIPTION                                  AMOUNT\n"
                        f"{date}  ACH DEBIT {co.upper()} INV {sku}                 -{amt}\n"
                        f"{date}  TRANSFER TO {fn2} {ln2} memo trim deposit         -$450.00\n"
                        f"{date}  CARD ENDING {last4} {RETAILER}                      -$86.12\n"
                        f"{date}  DEPOSIT REF {rout}                                +$2,400.00")
                sec = [S("name", f"{fn} {ln}", "name"), S("address", addr, "text"), S("account", acct, "digits"),
                       S("name", f"{fn2} {ln2}", "name"), S("card", last4, "digits4"), S("routing", rout, "digits")]
                keeps = [co.upper(), sku, "-$450.00", "-$86.12", "+$2,400.00", RETAILER]
            else:  # phone_note: free text, obfuscated email, possessive, name only once
                obf = "%s.%s at %s dot com" % (fn[0].lower(), ln.lower(), co.split()[0].lower())
                text = (f"Phone order taken {date}. Caller was {fn} from {co} (last name {ln}). "
                        f"{fn} wants {qty} pieces of {sku} and two boxes of {sku2}, delivered to {addr} "
                        f"by Friday. {ln}'s callback number is {ph}; email is {obf}. "
                        f"Quoted {amt}. Told her {fn2} {ln2} would follow up with the {co2} lead time.")
                sec = [S("name", fn, "name"), S("name", ln, "name"), S("address", addr, "text"),
                       S("phone", phd, "digits"), S("email", obf.split(" at ")[0], "text"),
                       S("name", f"{fn2} {ln2}", "name")]
                keeps = [co, sku, sku2, amt, co2, str(qty) + " pieces"]
            docs.append({"id": "%s-%d" % (fmt, k), "fmt": fmt, "text": text, "secrets": sec, "keeps": keeps})
    return docs


def _digits(s):
    return re.sub(r"\D", "", s)


def leaked(secret, out):
    v, c = secret["value"], secret["check"]
    if c == "name":
        return any(re.search(r"\b%s\b" % re.escape(tok), out, re.I) for tok in v.split() if len(tok) >= 3)
    if c == "text":
        return re.sub(r"\s+", " ", v).lower() in re.sub(r"\s+", " ", out).lower()
    if c == "digits4":   # card last four: as a standalone 4-digit token
        return re.search(r"(?<!\d)%s(?!\d)" % v, out) is not None
    return _digits(v) in _digits(out)   # digits survive even if re-spaced or re-punctuated


def kept(item, out):
    return re.sub(r"\s+", " ", item) in re.sub(r"\s+", " ", out)


def grade_red(doc, out):
    lk = [s for s in doc["secrets"] if leaked(s, out)]
    kp = [k for k in doc["keeps"] if kept(k, out)]
    return {"secrets": len(doc["secrets"]), "leaked": len(lk), "leaked_kinds": [s["kind"] for s in lk],
            "keeps": len(doc["keeps"]), "kept": len(kp), "lost": [k for k in doc["keeps"] if k not in kp]}


PH = r"(?:\(\d{3}\)\s?|\b\d{3}[\s.-])\d{3}[\s.-]\d{4}\b"


def regex_redact(t):
    t = re.sub(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b", "[EMAIL]", t)
    t = re.sub(PH, "[PHONE]", t)
    t = re.sub(r"\b\d{2}-\d{7}\b", "[TAXID]", t)
    t = re.sub(r"\b\d{4}(?: \d{4}){1,3}(?: \d{1,4})?\b|\b\d{9,17}\b", "[ACCOUNT]", t)
    t = re.sub(r"\b\d{3,5} (?:[NSEW] )?[A-Z0-9][\w ]{1,30}? (?:Rd|Ave|Pl|Dr|Blvd|Ln|St|Way|Ct)\b", "[ADDRESS]", t)
    t = re.sub(r"(?i)\b(card ending) \d{4}\b", r"\1 [CARD]", t)
    return t


RED_SYS = "You are an AI assistant that sanitizes business documents by removing personal information."
RED_USER = ("Replace every person name (first or last), email address, phone number, street address, bank account "
            "or routing number, card number and tax ID with a tag: [NAME], [EMAIL], [PHONE], [ADDRESS], [ACCOUNT], "
            "[CARD], [TAXID]. Keep everything else exactly as written: company names, SKUs and item codes, "
            "quantities, prices, dates, line layout. Existing tags like [PHONE] stay as they are. "
            "Output only the redacted document, nothing else.\n\nDOCUMENT:\n{d}")


def strip_fence(t):
    return re.sub(r"^```\w*\n?|\n?```$", "", (t or "").strip()).strip()


def selftest():
    c = common.Checks()
    docs = make_red_docs()
    nsec, nkeep = sum(len(d["secrets"]) for d in docs), sum(len(d["keeps"]) for d in docs)
    c("12 docs", len(docs) == 12)
    c("78 planted identifiers and 72 keep-items", (nsec, nkeep) == (78, 72))
    c("every planted secret is present in its own doc (no-op leaks all)",
      all(grade_red(d, d["text"])["leaked"] == len(d["secrets"]) for d in docs))
    c("no-op keeps everything", all(grade_red(d, d["text"])["kept"] == len(d["keeps"]) for d in docs))

    def oracle(d):
        t = d["text"]
        for s in sorted(d["secrets"], key=lambda s: -len(s["value"])):
            if s["check"] == "name":
                for tok in s["value"].split():
                    t = re.sub(r"\b%s\b" % re.escape(tok), "[NAME]", t, flags=re.I)
            elif s["kind"] == "account":
                t = t.replace(s["value"], "[ACCOUNT]")
                t = t.replace(" ".join(s["value"][i:i + 4] for i in range(0, 10, 4)), "[ACCOUNT]")
            elif s["kind"] == "phone":
                t = re.sub(PH, "[PHONE]", t)
            else:
                t = re.sub(re.escape(s["value"]), "[X]", t, flags=re.I)
        return t
    orc = [grade_red(d, oracle(d)) for d in docs]
    c("oracle leaks 0", sum(g["leaked"] for g in orc) == 0)
    c("oracle keeps all", all(g["kept"] == g["keeps"] for g in orc))
    over = [grade_red(d, "[REDACTED]") for d in docs]
    c("over-redactor leaks 0 but keeps 0", sum(g["leaked"] for g in over) == 0 and sum(g["kept"] for g in over) == 0)
    rg = [grade_red(d, regex_redact(d["text"])) for d in docs]
    c("regex arm cannot see names", any("name" in g["leaked_kinds"] for g in rg))
    c("regex arm keeps all keep-items", all(g["kept"] == g["keeps"] for g in rg))
    print("regex arm: leaked %d/%d, kept %d/%d" % (sum(g["leaked"] for g in rg), nsec,
                                                   sum(g["kept"] for g in rg), nkeep))
    return c.finish()


def main():
    ap = common.add_common_args(argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0]))
    ap.add_argument("--arms", default="model,hybrid", help="comma list of model,hybrid (regex always reported)")
    ap.add_argument("--docs", type=int, default=12, help="use only the first N documents (smoke runs)")
    ap.add_argument("--max-tokens", type=int, default=1500)
    ap.add_argument("--selftest", action="store_true", help="grader checks, no model")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    docs = make_red_docs()[:a.docs]
    arms = [x for x in a.arms.split(",") if x]
    regex_rows = [grade_red(d, regex_redact(d["text"])) for d in docs]
    rows = []
    for d in docs:
        for arm in arms:
            src = d["text"] if arm == "model" else regex_redact(d["text"])
            r = common.chat(a, [{"role": "system", "content": RED_SYS},
                                {"role": "user", "content": RED_USER.replace("{d}", src)}], max_tokens=a.max_tokens)
            out = strip_fence(r["content"])
            g = grade_red(d, out) if not r.get("error") else {"error": True}
            rows.append({"doc": d["id"], "arm": arm, "s": r["s"], "error": r.get("error"), "finish": r.get("finish"),
                         "reply": out, **g})
            common.say("red %-12s %-6s leaked=%s/%s kept=%s/%s %.0fs" % (
                d["id"], arm, g.get("leaked"), g.get("secrets"), g.get("kept"), g.get("keeps"), r["s"]))
    summary = {"regex": {"leaked": sum(g["leaked"] for g in regex_rows), "secrets": sum(g["secrets"] for g in regex_rows),
                         "kept": sum(g["kept"] for g in regex_rows), "keeps": sum(g["keeps"] for g in regex_rows)}}
    for arm in arms:
        ok = [x for x in rows if x["arm"] == arm and not x.get("error")]
        summary[arm] = {"docs": len(ok), "errors": len([x for x in rows if x["arm"] == arm and x.get("error")]),
                        "leaked": sum(x["leaked"] for x in ok), "secrets": sum(x["secrets"] for x in ok),
                        "kept": sum(x["kept"] for x in ok), "keeps": sum(x["keeps"] for x in ok)}
    for k, v in summary.items():
        common.say("RED %-7s leaked %d/%d  kept %d/%d" % (k, v["leaked"], v["secrets"], v["kept"], v["keeps"]))
    common.save_results(a, "RED", {"summary": summary, "rows": rows})
    return 0


if __name__ == "__main__":
    sys.exit(main())
