#!/usr/bin/env python3
"""VISION: can a small multimodal model read four drawn images? 8 questions, 10 points, one denominator.

All four images are DRAWN IN CODE (rectangles, circles, a triangle and the 5x7 bitmap font from tests/ocr.py,
written as RGB PNGs with zlib; no PIL, nothing to ship, no third-party artwork). Every answer is by construction.

  poster  640x400  title "NIGHT HARBOR" (2 pts), year "2026" (1)
  logo    480x300  filled circle colour (1), wordmark "KESTREL" (1)
  scene   640x400  countable stick figures on a lawn (2), colour of the big square "sun" (1)
  icon    256x256  arrow direction (1), a single large digit (1)

Weights 2/1: two 2-point questions and six 1-point questions = 10 points. The per-question breakdown is saved so a
reader can rescore with a different weighting. One request per question, temperature 0, one image per request.

Scoring is code-side. A question has `accept` words (any, whole-word, case-insensitive) and `reject` words (a reply
that also names a wrong option fails, so "left or right" does not score). Replies are asked to be one word or number.

Needs a vision-capable model behind an OpenAI-compatible endpoint (image as a data: URI in an image_url part). A
text-only model errors or ignores the image and scores near 0: that is a result. Each record keeps wall seconds,
completion tokens and the server's own decode speed (`timings.predicted_per_second`, llama.cpp) per question, because
the projector's cost shows up in decode speed and prefill, not only in the score.

  python3 tests/vision.py --selftest
  python3 tests/vision.py --write-png outdir/ [--variants 0,1,2]   save the images and look at them
  BASE_URL=... MODEL=... python3 tests/vision.py [--runs 1] [--variants 0,1,2]

Variants: 0 is the original image set; 1 and 2 redraw the same four kinds of image with different title, year,
colours, wordmark, figure count (3 and 7), arrow direction (right, up) and digit, so a seat is read on 12 images
and 24 questions, not one picture. Each variant is scored out of its own 10 points.
"""
import argparse
import base64
import os
import re
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
from ocr import FONT  # noqa: E402  (the 5x7 bitmap font; ocr.py is part of this repo)

WHITE, BLACK = (255, 255, 255), (0, 0, 0)
RED, BLUE, GREEN, YELLOW = (200, 30, 30), (30, 70, 190), (40, 150, 60), (240, 210, 30)
SKY, NAVY, SAND, GREY = (150, 200, 240), (20, 30, 70), (225, 205, 160), (90, 90, 90)


class Canvas:
    def __init__(self, w, h, bg):
        self.w, self.h = w, h
        self.rows = [bytearray(bytes(bg) * w) for _ in range(h)]

    def px(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.rows[y][x * 3:x * 3 + 3] = bytes(c)

    def rect(self, x0, y0, x1, y1, c):
        for y in range(max(0, y0), min(self.h, y1)):
            for x in range(max(0, x0), min(self.w, x1)):
                self.rows[y][x * 3:x * 3 + 3] = bytes(c)

    def circle(self, cx, cy, r, c):
        for y in range(cy - r, cy + r + 1):
            for x in range(cx - r, cx + r + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    self.px(x, y, c)

    def line(self, x0, y0, x1, y1, c, t=3):
        n = max(abs(x1 - x0), abs(y1 - y0), 1)
        for i in range(n + 1):
            x, y = x0 + (x1 - x0) * i // n, y0 + (y1 - y0) * i // n
            self.rect(x - t // 2, y - t // 2, x - t // 2 + t, y - t // 2 + t, c)

    def tri_left(self, tip_x, cy, length, half_h, c):
        """Filled triangle whose tip is at (tip_x, cy) and whose base is `length` px to the right."""
        for x in range(tip_x, tip_x + length):
            hh = half_h * (x - tip_x) // length
            self.rect(x, cy - hh, x + 1, cy + hh + 1, c)

    def text(self, x0, y0, txt, scale, c):
        x = x0
        for ch in txt.upper():
            g = FONT[ch]
            for ry, row in enumerate(g):
                for rx, bit in enumerate(row):
                    if bit == "1":
                        self.rect(x + rx * scale, y0 + ry * scale, x + (rx + 1) * scale, y0 + (ry + 1) * scale, c)
            x += (len(g[0]) + 1) * scale

    def png(self):
        def chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        raw = b"".join(b"\x00" + bytes(r) for r in self.rows)
        ihdr = struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)          # 8-bit RGB
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


# Variant 0 is the original four images (the published n=1 rows). Variants 1 and 2 redraw the same four image
# kinds with different words, digits, colours, counts and arrow directions, so a second and third read of a seat
# cannot be a lucky guess at one picture. Every answer is still true by construction.
COLOURS = {"red": RED, "blue": BLUE, "green": GREEN, "yellow": YELLOW}
NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}
VARIANTS = [
    dict(title="NIGHT HARBOR", sub="A FILM BY OAK AND PINE", year="2026", circle="red", word="KESTREL",
         figures=5, sun="yellow", arrow="left", digit="7"),
    dict(title="IRON TIDE", sub="A FILM BY ASH AND ELM", year="1987", circle="blue", word="MARTEN",
         figures=3, sun="red", arrow="right", digit="4"),
    dict(title="SALT ROAD", sub="A FILM BY RUSH AND FERN", year="2031", circle="green", word="HERON",
         figures=7, sun="blue", arrow="up", digit="3"),
]
N_FIGURES = VARIANTS[0]["figures"]


def draw_poster(v=VARIANTS[0]):
    c = Canvas(640, 400, NAVY)
    c.rect(20, 20, 620, 380, NAVY)
    c.rect(20, 20, 620, 26, YELLOW)
    c.rect(20, 374, 620, 380, YELLOW)
    c.text(40, 120, v["title"], 7, WHITE)
    c.text(40, 220, v["sub"], 4, SAND)
    c.text(40, 300, v["year"], 8, YELLOW)
    return c


def draw_logo(v=VARIANTS[0]):
    c = Canvas(480, 300, WHITE)
    c.circle(240, 105, 70, COLOURS[v["circle"]])
    c.text(90, 215, v["word"], 7, BLACK)
    return c


def figure_xs(n):
    return [70, 170, 280, 390, 490] if n == 5 else [60 + i * (500 // max(n - 1, 1)) for i in range(n)]


def draw_scene(v=VARIANTS[0]):
    c = Canvas(640, 400, SKY)
    c.rect(0, 280, 640, 400, GREEN)
    c.rect(500, 30, 580, 110, COLOURS[v["sun"]])      # the big square "sun"
    xs = figure_xs(v["figures"])
    assert len(xs) == v["figures"]
    for x in xs:
        c.circle(x, 215, 18, BLACK)                    # head
        c.line(x, 233, x, 295, BLACK, 4)               # body
        c.line(x - 28, 258, x + 28, 258, BLACK, 4)     # arms
        c.line(x, 295, x - 22, 345, BLACK, 4)          # legs
        c.line(x, 295, x + 22, 345, BLACK, 4)
    return c


def draw_icon(v=VARIANTS[0]):
    c = Canvas(256, 256, SAND)
    d = v["arrow"]
    if d == "left":
        c.tri_left(20, 90, 90, 60, BLUE)               # arrowhead: tip on the LEFT
        c.rect(109, 68, 236, 112, BLUE)                # shaft runs right
    elif d == "right":
        for x in range(166, 236):                      # arrowhead: tip on the RIGHT
            hh = 60 * (235 - x) // 70
            c.rect(x, 90 - hh, x + 1, 90 + hh + 1, BLUE)
        c.rect(20, 68, 167, 112, BLUE)
    elif d == "up":                                    # tip at the top, shaft runs down
        for y in range(10, 70):
            hh = 45 * (y - 10) // 60
            c.rect(128 - hh, y, 128 + hh + 1, y + 1, BLUE)
        c.rect(106, 68, 151, 138, BLUE)
    c.text(88, 150, v["digit"], 14, BLACK)             # one big digit
    return c


def images(v):
    return {"poster": draw_poster(v), "logo": draw_logo(v), "scene": draw_scene(v), "icon": draw_icon(v)}


IMAGES = {"poster": draw_poster, "logo": draw_logo, "scene": draw_scene, "icon": draw_icon}


def _other(word, pool):
    return [w for w in pool if w != word]


def questions(v):
    """(id, image, weight, prompt, accept words, reject words) for one variant, 8 questions, 10 points."""
    n, digit = v["figures"], v["digit"]
    nums = [str(i) for i in range(0, 15) if i != n] + [w for i, w in NUMBER_WORDS.items() if i != n]
    digits = [str(i) for i in range(10) if str(i) != digit] + [w for i, w in NUMBER_WORDS.items() if str(i) != digit]
    cols = list(COLOURS)
    return [
        ("poster_title", "poster", 2, "What is the title printed on this film poster? Reply with the title only.", [v["title"].lower()], []),
        ("poster_year", "poster", 1, "What year is printed on this poster? Reply with the four digits only.", [v["year"]], []),
        ("logo_color", "logo", 1, "What colour is the filled circle in this logo? Reply with one word.", [v["circle"]],
         _other(v["circle"], cols + ["black"])),
        ("logo_word", "logo", 1, "What word is written under the circle? Reply with the word only.", [v["word"].lower()], []),
        ("scene_count", "scene", 2, "How many stick figures are standing on the grass? Reply with a number only.",
         [str(n), NUMBER_WORDS[n]], nums),
        ("scene_sun", "scene", 1, "What colour is the large square in the sky? Reply with one word.", [v["sun"]],
         _other(v["sun"], cols + ["orange", "white"])),
        ("icon_dir", "icon", 1, "Which way does the arrow point: left, right, up or down? Reply with one word.", [v["arrow"]],
         _other(v["arrow"], ["left", "right", "up", "down"])),
        ("icon_digit", "icon", 1, "What single digit is printed in this icon? Reply with the digit only.",
         [digit, NUMBER_WORDS[int(digit)]], digits),
    ]


QUESTIONS = questions(VARIANTS[0])
TOTAL = sum(q[2] for q in QUESTIONS)


def _words(reply):
    return re.findall(r"[a-z0-9]+(?:[ -][a-z]+)?", (reply or "").lower())


def has(reply, word):
    return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", (reply or "").lower()) is not None


def grade(q, reply):
    """-> True when an accepted word is present and no rejected word is."""
    _, _, _, _, accept, reject = q
    return any(has(reply, w) for w in accept) and not any(has(reply, w) for w in reject)


def score(replies, qs=None):
    """replies: {question_id: text} -> {'points', 'total', 'passed': [...], 'failed': [...]}"""
    got, passed, failed = 0, [], []
    for q in (qs or QUESTIONS):
        if grade(q, replies.get(q[0], "")):
            got += q[2]
            passed.append(q[0])
        else:
            failed.append(q[0])
    return {"points": got, "total": sum(q[2] for q in (qs or QUESTIONS)), "passed": passed, "failed": failed}


def selftest_variant(c, vi, v):
    imgs = images(v)
    tag = "v%d " % vi
    for k, cv in imgs.items():
        c(tag + "%s: dimensions %dx%d" % (k, cv.w, cv.h), len(cv.rows) == cv.h and all(len(r) == cv.w * 3 for r in cv.rows))
        c(tag + "%s: not blank" % k, len({bytes(r) for r in cv.rows}) > 1)
        png = cv.png()
        c(tag + "%s: PNG signature and IHDR" % k, png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR")
        i = png.index(b"IDAT")
        n = struct.unpack(">I", png[i - 4:i])[0]
        raw = zlib.decompress(png[i + 4:i + 4 + n])
        c(tag + "%s: PNG round-trips to the same pixels" % k, raw == b"".join(b"\x00" + bytes(r) for r in cv.rows))
    # nothing clipped (the lab's OCR page lost a digit this way): no ink within 40 px of the right edge
    # (poster: ignoring its top/bottom border bars)
    bg = {"poster": NAVY, "logo": WHITE}
    for k in ("poster", "logo"):
        cv = imgs[k]
        ys = range(30, cv.h - 30) if k == "poster" else range(cv.h)
        edge = [x for x in range(cv.w - 40, cv.w) for y in ys if tuple(cv.rows[y][x * 3:x * 3 + 3]) != bg[k]]
        c(tag + "%s: no ink within 40 px of the right edge" % k, not edge)
    c(tag + "every character used has a glyph", all(ch in FONT for t in (v["title"], v["sub"], v["year"], v["word"], v["digit"]) for ch in t))
    # scene: count head circles by scanning for black runs on row 215 (heads are 36 px wide, separated)
    sc = imgs["scene"]
    row = sc.rows[215]
    runs, inrun = 0, False
    for x in range(sc.w):
        black = tuple(row[x * 3:x * 3 + 3]) == BLACK
        runs += black and not inrun
        inrun = black
    c(tag + "scene has exactly %d figures (head scan)" % v["figures"], runs == v["figures"])
    c(tag + "sun is the stated colour", tuple(sc.rows[70][540 * 3:540 * 3 + 3]) == COLOURS[v["sun"]])
    c(tag + "logo circle is the stated colour", tuple(imgs["logo"].rows[105][240 * 3:240 * 3 + 3]) == COLOURS[v["circle"]])
    # icon: probe the tip end and the shaft end of the arrow
    ic = imgs["icon"]
    px = lambda x, y: tuple(ic.rows[y][x * 3:x * 3 + 3])
    probes = {"left": ((21, 90), (230, 90), (21, 68)), "right": ((234, 90), (25, 90), (234, 68)),
              "up": ((128, 12), (128, 135), (108, 12))}[v["arrow"]]
    c(tag + "icon arrow points %s (tip end inked, far shaft end inked, tip corner empty)" % v["arrow"],
      px(*probes[0]) == BLUE and px(*probes[1]) == BLUE and px(*probes[2]) == SAND)
    c(tag + "icon digit area is inked", any(px(x, y) == BLACK for x in range(88, 186) for y in range(150, 248)))
    qs = questions(v)
    ids = [q[0] for q in qs]
    c(tag + "8 questions, unique ids, 10 points, weights 2/1", len(qs) == 8 and len(set(ids)) == 8 and sum(q[2] for q in qs) == 10 and {q[2] for q in qs} == {1, 2})
    c(tag + "every wrong-option word is also rejected for its own question", all(not any(w in q[5] for w in q[4]) for q in qs))
    gold = {"poster_title": v["title"].title(), "poster_year": v["year"], "logo_color": v["circle"].title() + ".", "logo_word": v["word"],
            "scene_count": str(v["figures"]), "scene_sun": v["sun"], "icon_dir": v["arrow"].title(), "icon_digit": v["digit"]}
    c(tag + "a perfect reply scores 10/10", score(gold, qs)["points"] == 10)
    c(tag + "an empty reply scores 0/10", score({}, qs)["points"] == 0)
    c(tag + "a weighted miss costs 2", score({**gold, "poster_title": "Night"}, qs)["points"] == 8)
    c(tag + "the other variants' answers do not score on this one", all(
        score({"poster_title": o["title"], "poster_year": o["year"], "logo_color": o["circle"], "logo_word": o["word"],
               "scene_count": str(o["figures"]), "scene_sun": o["sun"], "icon_dir": o["arrow"], "icon_digit": o["digit"]}, qs)["points"] < 10
        for o in VARIANTS if o is not v))


def selftest():
    c = common.Checks()
    for vi, v in enumerate(VARIANTS):
        selftest_variant(c, vi, v)
    # questions
    ids = [q[0] for q in QUESTIONS]
    c("8 questions, unique ids", len(QUESTIONS) == 8 and len(set(ids)) == 8)
    c("10 points, weights 2/1", TOTAL == 10 and {q[2] for q in QUESTIONS} == {1, 2})
    c("every question's image exists", all(q[1] in IMAGES for q in QUESTIONS))
    gold = {"poster_title": "Night Harbor", "poster_year": "2026", "logo_color": "Red.", "logo_word": "KESTREL",
            "scene_count": "5", "scene_sun": "yellow", "icon_dir": "Left", "icon_digit": "7"}
    c("a perfect reply scores 10/10", score(gold)["points"] == 10)
    c("an empty reply scores 0/10", score({})["points"] == 0)
    c("'left or right' does not score", not grade(QUESTIONS[6], "left or right"))
    c("'There are 15 figures' does not score as 5", not grade(QUESTIONS[4], "There are 15 figures"))
    c("'five' scores as 5", grade(QUESTIONS[4], "Five."))
    c("'The year 2026' scores", grade(QUESTIONS[1], "The year is 2026"))
    c("a wrong colour fails", not grade(QUESTIONS[2], "blue"))
    c("a weighted miss costs 2", score({**gold, "poster_title": "Night"})["points"] == 8)
    c("every wrong-option word is also rejected for its own question", all(not any(w in q[5] for w in q[4]) for q in QUESTIONS))
    return c.finish()


def uri(png):
    return "data:image/png;base64," + base64.b64encode(png).decode()


def main():
    ap = common.add_common_args(argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0]))
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--max-tokens", type=int, default=200)
    ap.add_argument("--only", default="", help="comma list of question ids to ask, each optionally prefixed v<N>: (e.g. v2:logo_word,icon_dir); scoring is over the questions actually asked")
    ap.add_argument("--variants", default="0", help="comma list of image sets to run: 0 = the original four images, 1 and 2 = redrawn with other answers (default 0)")
    ap.add_argument("--write-png", metavar="DIR", help="write the four images of each chosen variant to DIR and exit")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    vs = [int(x) for x in a.variants.split(",")]
    if a.write_png:
        os.makedirs(a.write_png, exist_ok=True)
        for vi in vs:
            for k, cv in images(VARIANTS[vi]).items():
                name = k + ("" if vi == 0 else "-v%d" % vi) + ".png"
                p = cv.png()
                open(os.path.join(a.write_png, name), "wb").write(p)
                print("wrote", name, len(p), "bytes")
        return 0
    runs = []
    only = [x.strip() for x in a.only.split(",") if x.strip()]
    for vi in vs:
        v = VARIANTS[vi]
        qs = questions(v)
        if only:
            qs = [q for q in qs if q[0] in only or ("v%d:%s" % (vi, q[0])) in only]
            if not qs:
                continue
        pngs = {k: cv.png() for k, cv in images(v).items()}
        for i in range(a.runs):
            rows, replies = [], {}
            for q in qs:
                msgs = [{"role": "user", "content": [{"type": "text", "text": q[3]},
                                                     {"type": "image_url", "image_url": {"url": uri(pngs[q[1]])}}]}]
                r = common.chat(a, msgs, max_tokens=a.max_tokens, temperature=0.0, seed=100 + i)
                replies[q[0]] = r["content"]
                rows.append({"q": q[0], "image": q[1], "weight": q[2], "ok": grade(q, r["content"]), "reply": r["content"],
                             "s": r["s"], "finish": r.get("finish"), "error": r.get("error"),
                             "completion_tokens": r.get("completion_tokens"), "prompt_tokens": r.get("prompt_tokens"),
                             "decode_tps": (r.get("timings") or {}).get("predicted_per_second")})
            sc = score(replies, qs)
            runs.append({"run": i, "variant": vi, "questions": rows, "asked": [q[0] for q in qs], **sc})
            common.say("variant%d run%d %d/%d failed=%s" % (vi, i, sc["points"], sc["total"], sc["failed"]))
    common.save_results(a, "VISION", {"total": TOTAL, "runs": runs})
    return 0


if __name__ == "__main__":
    sys.exit(main())
