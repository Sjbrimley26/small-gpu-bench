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
  python3 tests/vision.py --write-png outdir/        save the four images and look at them
  BASE_URL=... MODEL=... python3 tests/vision.py [--runs 1]
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


N_FIGURES = 5


def draw_poster():
    c = Canvas(640, 400, NAVY)
    c.rect(20, 20, 620, 380, NAVY)
    c.rect(20, 20, 620, 26, YELLOW)
    c.rect(20, 374, 620, 380, YELLOW)
    c.text(40, 120, "NIGHT HARBOR", 7, WHITE)
    c.text(40, 220, "A FILM BY OAK AND PINE", 4, SAND)
    c.text(40, 300, "2026", 8, YELLOW)
    return c


def draw_logo():
    c = Canvas(480, 300, WHITE)
    c.circle(240, 105, 70, RED)
    c.text(90, 215, "KESTREL", 7, BLACK)
    return c


def draw_scene():
    c = Canvas(640, 400, SKY)
    c.rect(0, 280, 640, 400, GREEN)
    c.rect(500, 30, 580, 110, YELLOW)                 # the big square "sun"
    xs = [70, 170, 280, 390, 490]
    assert len(xs) == N_FIGURES
    for x in xs:
        c.circle(x, 215, 18, BLACK)                    # head
        c.line(x, 233, x, 295, BLACK, 4)               # body
        c.line(x - 28, 258, x + 28, 258, BLACK, 4)     # arms
        c.line(x, 295, x - 22, 345, BLACK, 4)          # legs
        c.line(x, 295, x + 22, 345, BLACK, 4)
    return c


def draw_icon():
    c = Canvas(256, 256, SAND)
    c.tri_left(20, 90, 90, 60, BLUE)                   # arrowhead: tip on the LEFT
    c.rect(109, 68, 236, 112, BLUE)                    # shaft runs right
    c.text(88, 150, "7", 14, BLACK)                    # one big digit
    return c


IMAGES = {"poster": draw_poster, "logo": draw_logo, "scene": draw_scene, "icon": draw_icon}

# (id, image, weight, prompt, accept words, reject words)
QUESTIONS = [
    ("poster_title", "poster", 2, "What is the title printed on this film poster? Reply with the title only.", ["night harbor"], []),
    ("poster_year", "poster", 1, "What year is printed on this poster? Reply with the four digits only.", ["2026"], []),
    ("logo_color", "logo", 1, "What colour is the filled circle in this logo? Reply with one word.", ["red"], ["blue", "green", "yellow", "black"]),
    ("logo_word", "logo", 1, "What word is written under the circle? Reply with the word only.", ["kestrel"], []),
    ("scene_count", "scene", 2, "How many stick figures are standing on the grass? Reply with a number only.", [str(N_FIGURES), "five"],
     ["1", "2", "3", "4", "6", "7", "8", "9", "one", "two", "three", "four", "six", "seven", "eight", "nine"]),
    ("scene_sun", "scene", 1, "What colour is the large square in the sky? Reply with one word.", ["yellow"], ["red", "blue", "green", "orange", "white"]),
    ("icon_dir", "icon", 1, "Which way does the arrow point: left, right, up or down? Reply with one word.", ["left"], ["right", "up", "down"]),
    ("icon_digit", "icon", 1, "What single digit is printed in this icon? Reply with the digit only.", ["7", "seven"],
     ["0", "1", "2", "3", "4", "5", "6", "8", "9"]),
]
TOTAL = sum(q[2] for q in QUESTIONS)


def _words(reply):
    return re.findall(r"[a-z0-9]+(?:[ -][a-z]+)?", (reply or "").lower())


def has(reply, word):
    return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", (reply or "").lower()) is not None


def grade(q, reply):
    """-> True when an accepted word is present and no rejected word is."""
    _, _, _, _, accept, reject = q
    return any(has(reply, w) for w in accept) and not any(has(reply, w) for w in reject)


def score(replies):
    """replies: {question_id: text} -> {'points', 'total', 'passed': [...], 'failed': [...]}"""
    got, passed, failed = 0, [], []
    for q in QUESTIONS:
        if grade(q, replies.get(q[0], "")):
            got += q[2]
            passed.append(q[0])
        else:
            failed.append(q[0])
    return {"points": got, "total": TOTAL, "passed": passed, "failed": failed}


def selftest():
    c = common.Checks()
    imgs = {k: f() for k, f in IMAGES.items()}
    for k, cv in imgs.items():
        c("%s: dimensions %dx%d" % (k, cv.w, cv.h), len(cv.rows) == cv.h and all(len(r) == cv.w * 3 for r in cv.rows))
        c("%s: not blank" % k, len({bytes(r) for r in cv.rows}) > 1)
        png = cv.png()
        c("%s: PNG signature and IHDR" % k, png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR")
        i = png.index(b"IDAT")
        n = struct.unpack(">I", png[i - 4:i])[0]
        raw = zlib.decompress(png[i + 4:i + 4 + n])
        c("%s: PNG round-trips to the same pixels" % k, raw == b"".join(b"\x00" + bytes(r) for r in cv.rows))
    # nothing clipped (the lab's OCR page lost a digit this way): no ink within 40 px of the right edge
    # (poster: ignoring its top/bottom border bars)
    bg = {"poster": NAVY, "logo": WHITE}
    for k in ("poster", "logo"):
        cv = imgs[k]
        ys = range(30, cv.h - 30) if k == "poster" else range(cv.h)
        edge = [x for x in range(cv.w - 40, cv.w) for y in ys if tuple(cv.rows[y][x * 3:x * 3 + 3]) != bg[k]]
        c("%s: no ink within 40 px of the right edge" % k, not edge)
    c("every character used has a glyph", all(ch in FONT for t in ("NIGHT HARBOR", "A FILM BY OAK AND PINE", "2026", "KESTREL", "7") for ch in t))
    # scene: count head circles by scanning for black runs on row 215 (heads are 36 px wide, separated)
    sc = imgs["scene"]
    row = sc.rows[215]
    runs, inrun = 0, False
    for x in range(sc.w):
        black = tuple(row[x * 3:x * 3 + 3]) == BLACK
        runs += black and not inrun
        inrun = black
    c("scene has exactly %d figures (head scan)" % N_FIGURES, runs == N_FIGURES)
    # icon: the tip pixel is on the left of the arrow, the shaft extends right
    ic = imgs["icon"]
    c("icon arrow points left (tip left, shaft right)", tuple(ic.rows[90][21 * 3:21 * 3 + 3]) == BLUE and tuple(ic.rows[90][230 * 3:230 * 3 + 3]) == BLUE
      and tuple(ic.rows[68][21 * 3:21 * 3 + 3]) == SAND)
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
    ap.add_argument("--write-png", metavar="DIR", help="write the four images to DIR and exit")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    pngs = {k: f().png() for k, f in IMAGES.items()}
    if a.write_png:
        os.makedirs(a.write_png, exist_ok=True)
        for k, p in pngs.items():
            open(os.path.join(a.write_png, k + ".png"), "wb").write(p)
            print("wrote", k + ".png", len(p), "bytes")
        return 0
    runs = []
    for i in range(a.runs):
        rows, replies = [], {}
        for q in QUESTIONS:
            msgs = [{"role": "user", "content": [{"type": "text", "text": q[3]},
                                                 {"type": "image_url", "image_url": {"url": uri(pngs[q[1]])}}]}]
            r = common.chat(a, msgs, max_tokens=a.max_tokens, temperature=0.0, seed=100 + i)
            replies[q[0]] = r["content"]
            rows.append({"q": q[0], "image": q[1], "weight": q[2], "ok": grade(q, r["content"]), "reply": r["content"],
                         "s": r["s"], "finish": r.get("finish"), "error": r.get("error"),
                         "completion_tokens": r.get("completion_tokens"), "prompt_tokens": r.get("prompt_tokens"),
                         "decode_tps": (r.get("timings") or {}).get("predicted_per_second")})
        sc = score(replies)
        runs.append({"run": i, "questions": rows, **sc})
        common.say("run%d %d/%d failed=%s" % (i, sc["points"], sc["total"], sc["failed"]))
    common.save_results(a, "VISION", {"total": TOTAL, "runs": runs})
    return 0


if __name__ == "__main__":
    sys.exit(main())
