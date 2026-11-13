#!/usr/bin/env python3
"""OCR: can a vision model read a synthetic invoice page? (invoice read only)

Fixture note: the page differs from the lab's original in one way. That page was 1240 px wide and the long
lines ran off the right edge, so the amount column and the last digit of the planted total were clipped
(found by looking at a render while porting). This version is wide enough; the lab's published OCR scores
(results.csv, OCR rows) appear to come from the clipped page and carry a caveat in the exported CSV.

The page is DRAWN IN CODE (a 5x7 bitmap font scaled up, black on white, written as a PNG with zlib; no PIL,
no files to ship). It carries five planted values: invoice number, PO number, date, total due, vendor name.
The model is asked for them in a fixed line format and the reply is scored by substring: fields right out of 5.

Needs a vision-capable model behind an OpenAI-compatible endpoint (image sent as a data: URI in an
image_url content part). Text-only models will error or ignore the image and score 0/5 - that is a result.
The lab's separate pointing/coordinates test (vision-extras) is not ported.

  python3 tests/ocr.py --selftest
  python3 tests/ocr.py --write-png page.png          save the page and look at it
  BASE_URL=... MODEL=... python3 tests/ocr.py [--runs 1]
"""
import argparse
import base64
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

W, H = 1400, 1100   # the lab's original page was 1240 wide and clipped the right-hand amount column
                    # (the last digit of the planted total was off the page); widened here so every planted value is whole

FONT = {
    'A': ['01110', '10001', '10001', '11111', '10001', '10001', '10001'],
    'B': ['11110', '10001', '11110', '10001', '10001', '10001', '11110'],
    'C': ['01111', '10000', '10000', '10000', '10000', '10000', '01111'],
    'D': ['11110', '10001', '10001', '10001', '10001', '10001', '11110'],
    'E': ['11111', '10000', '11110', '10000', '10000', '10000', '11111'],
    'F': ['11111', '10000', '11110', '10000', '10000', '10000', '10000'],
    'G': ['01111', '10000', '10000', '10111', '10001', '10001', '01111'],
    'H': ['10001', '10001', '11111', '10001', '10001', '10001', '10001'],
    'I': ['11111', '00100', '00100', '00100', '00100', '00100', '11111'],
    'J': ['00111', '00010', '00010', '00010', '00010', '10010', '01100'],
    'K': ['10001', '10010', '10100', '11000', '10100', '10010', '10001'],
    'L': ['10000', '10000', '10000', '10000', '10000', '10000', '11111'],
    'M': ['10001', '11011', '10101', '10001', '10001', '10001', '10001'],
    'N': ['10001', '11001', '10101', '10011', '10001', '10001', '10001'],
    'O': ['01110', '10001', '10001', '10001', '10001', '10001', '01110'],
    'P': ['11110', '10001', '10001', '11110', '10000', '10000', '10000'],
    'Q': ['01110', '10001', '10001', '10001', '10101', '10010', '01101'],
    'R': ['11110', '10001', '10001', '11110', '10100', '10010', '10001'],
    'S': ['01111', '10000', '10000', '01110', '00001', '00001', '11110'],
    'T': ['11111', '00100', '00100', '00100', '00100', '00100', '00100'],
    'U': ['10001', '10001', '10001', '10001', '10001', '10001', '01110'],
    'V': ['10001', '10001', '10001', '10001', '10001', '01010', '00100'],
    'W': ['10001', '10001', '10001', '10101', '10101', '11011', '10001'],
    'X': ['10001', '10001', '01010', '00100', '01010', '10001', '10001'],
    'Y': ['10001', '10001', '01010', '00100', '00100', '00100', '00100'],
    'Z': ['11111', '00001', '00010', '00100', '01000', '10000', '11111'],
    '0': ['01110', '10001', '10011', '10101', '11001', '10001', '01110'],
    '1': ['00100', '01100', '00100', '00100', '00100', '00100', '01110'],
    '2': ['01110', '10001', '00001', '00110', '01000', '10000', '11111'],
    '3': ['11110', '00001', '00001', '01110', '00001', '00001', '11110'],
    '4': ['00010', '00110', '01010', '10010', '11111', '00010', '00010'],
    '5': ['11111', '10000', '11110', '00001', '00001', '10001', '01110'],
    '6': ['00110', '01000', '10000', '11110', '10001', '10001', '01110'],
    '7': ['11111', '00001', '00010', '00100', '01000', '01000', '01000'],
    '8': ['01110', '10001', '10001', '01110', '10001', '10001', '01110'],
    '9': ['01110', '10001', '10001', '01111', '00001', '00010', '01100'],
    '$': ['00100', '01111', '10100', '01110', '00101', '11110', '00100'],
    '-': ['00000', '00000', '00000', '11111', '00000', '00000', '00000'],
    '.': ['00000', '00000', '00000', '00000', '00000', '01100', '01100'],
    ' ': ['00000', '00000', '00000', '00000', '00000', '00000', '00000'],
}

LINES = [  # (x, y, text, scale)
    (60, 80, "INVOICE 88213", 8),
    (60, 220, "ACME DOOR AND TRIM SUPPLY", 6),
    (60, 300, "PO NUMBER 4471", 6),
    (60, 380, "INVOICE DATE 2026-09-14", 6),
    (60, 480, "DESCRIPTION          QTY   UNIT     AMOUNT", 5),
    (60, 560, "DOOR SLAB OAK        12    46.75    561.00", 5),
    (60, 630, "TRIM BOARD 8FT       40    12.40    496.00", 5),
    (60, 700, "HINGE SET BRASS      24     8.25    198.00", 5),
    (60, 800, "SUBTOTAL                          1255.00", 5),
    (60, 870, "SALES TAX 7.25 PERCENT              90.99", 5),
    (60, 940, "TOTAL DUE                         1345.99", 5),
]
WANT = {"88213": "invoice number", "4471": "po", "2026-09-14": "date", "1345.99": "total", "ACME": "vendor"}
DOC_Q = ("Read this document page. Reply in exactly these lines:\n"
         "TITLE: <the invoice number as printed>\nPO: <the PO number>\nDATE: <the date>\n"
         "TOTAL: <the total due>\nVENDOR: <the vendor name>\n"
         "If a value is unreadable write 'unclear'.")


def render():
    """-> grey-scale rows as bytes (0 = black, 255 = white)."""
    canvas = [bytearray(b"\xff" * W) for _ in range(H)]
    for x0, y0, text, scale in LINES:
        x = x0
        for ch in text.upper():
            glyph = FONT.get(ch, FONT[" "])
            for ry, row in enumerate(glyph):
                for rx, bit in enumerate(row):
                    if bit == "1":
                        for dy in range(scale):
                            py = y0 + ry * scale + dy
                            if 0 <= py < H:
                                px0 = x + rx * scale
                                for dx in range(scale):
                                    if 0 <= px0 + dx < W:
                                        canvas[py][px0 + dx] = 0
            x += (len(glyph[0]) + 1) * scale
    return canvas


def png_bytes(rows):
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    ihdr = struct.pack(">IIBBBBB", W, H, 8, 0, 0, 0, 0)          # 8-bit grey
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def score(content):
    hits = [k for k in WANT if k in (content or "")]
    return {"hits": hits, "score": "%d/%d" % (len(hits), len(WANT)), "n_hit": len(hits),
            "missed": [v for k, v in WANT.items() if k not in hits]}


def selftest():
    c = common.Checks()
    rows = render()
    png = png_bytes(rows)
    c("page is %dx%d" % (W, H), len(rows) == H and len(rows[0]) == W)
    c("page has ink (not blank)", any(0 in r for r in rows))
    ink_cols = [x for x in range(W) if any(r[x] == 0 for r in rows)]
    c("no ink within 20 px of the right edge (nothing clipped; rightmost ink at x=%d)" % max(ink_cols), max(ink_cols) < W - 20)
    c("valid PNG signature and IHDR", png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR")
    raw = zlib.decompress(b"".join(png[i + 8:i + 8 + n] for i, n in [(png.index(b"IDAT") - 4, struct.unpack(">I", png[png.index(b"IDAT") - 4:png.index(b"IDAT")])[0])]))
    c("PNG decodes to the same pixels", raw == b"".join(b"\x00" + bytes(r) for r in rows))
    c("every character used has a glyph", all(ch in FONT for _, _, t, _ in LINES for ch in t.upper()))
    c("planted values are on the page text", all(any(k in t for _, _, t, _ in LINES) for k in WANT))
    c("full reply scores 5/5", score("TITLE: 88213\nPO: 4471\nDATE: 2026-09-14\nTOTAL: 1345.99\nVENDOR: ACME DOOR")["n_hit"] == 5)
    c("garbled reply scores lower", score("TITLE: 88218\nPO: 4471")["n_hit"] == 1)
    return c.finish()


def main():
    ap = common.add_common_args(argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0]))
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--max-tokens", type=int, default=400)
    ap.add_argument("--write-png", metavar="FILE", help="write the page to FILE and exit")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    png = png_bytes(render())
    if a.write_png:
        open(a.write_png, "wb").write(png)
        print("wrote", a.write_png, len(png), "bytes")
        return 0
    uri = "data:image/png;base64," + base64.b64encode(png).decode()
    msgs = [{"role": "user", "content": [{"type": "text", "text": DOC_Q},
                                         {"type": "image_url", "image_url": {"url": uri}}]}]
    rows = []
    for i in range(a.runs):
        r = common.chat(a, msgs, max_tokens=a.max_tokens, temperature=0.0, seed=100 + i)
        g = score(r["content"])
        rows.append({"run": i, "s": r["s"], "error": r.get("error"), "reply": r["content"], **g})
        common.say("run%d %s missed=%s %.1fs %s" % (i, g["score"], g["missed"], r["s"], r.get("error") or ""))
    common.save_results(a, "OCR", {"rows": rows})
    return 0


if __name__ == "__main__":
    sys.exit(main())
