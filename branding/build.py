"""Every file in this folder, drawn from three things: the wheel, the name and the colours below.

The wheel is the site's (`web/src/wheel.tsx`, `Mark`), and the name is set the way the site's header
sets it: Sora SemiBold, tracked -0.02em, turned into outlines so no file needs the font installed.
Change a colour or a size here and run it again from the repository root:

    .venv/bin/pip install brotli uharfbuzz      # once; fontTools, Pillow and Playwright are already there
    .venv/bin/python branding/build.py
"""

import base64
import io
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from PIL import Image
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
FONTS = HERE.parent / "web/node_modules/@fontsource-variable"
SORA = FONTS / "sora/files/sora-latin-wght-normal.woff2"
INTER = FONTS / "inter/files/inter-latin-wght-normal.woff2"

PURPLE = "#8b3dff"  # the brand colour: the wheel, buttons, anything that says "Mahoraga"
LILAC = "#ba8cff"  # the accent: the purple as words or highlights on graphite
GRAPHITE = "#0f1013"  # the background, and black wherever black is needed
PAPER = "#f5f6f8"  # the white: words on graphite, light backgrounds
WHITE = "#ffffff"
COLOURS = [
    ("Purple", PURPLE, "Brand colour", "The wheel, buttons, links. White text on it reads at 5:1."),
    ("Lilac", LILAC, "Accent", "Purple words and highlights on graphite, where purple is too dark (7.5:1)."),
    ("Graphite", GRAPHITE, "Background", "Dark backgrounds, and text on light ones."),
    ("Paper", PAPER, "Light", "Text on graphite, and light backgrounds."),
]
TAGLINE = "The job search that", "runs itself."

SPOKES = (
    "M12 2.4v6.9M12 14.7v6.9M2.4 12h6.9M14.7 12h6.9"
    "M5.2 5.2l4.9 4.9M13.9 13.9l4.9 4.9M18.8 5.2l-4.9 4.9M10.1 13.9l-4.9 4.9"
)


def n(v: float, places: int = 2) -> str:
    s = f"{v:.{places}f}".rstrip("0").rstrip(".")
    return "0" if s in {"-0", ""} else s


def rgb(hex_colour: str) -> str:
    return ", ".join(str(int(hex_colour[i : i + 2], 16)) for i in (1, 3, 5))


def wheel(cx: float, cy: float, d: float, colour: str, stroke: float = 1.8) -> str:
    """The wheel, `d` across at its outer edge, centred on (cx, cy). `stroke` is on the site's 24 grid."""
    s = d / (19.2 + stroke)
    return (
        f'<g transform="translate({n(cx - 12 * s)} {n(cy - 12 * s)}) scale({n(s, 4)})" fill="none" '
        f'stroke="{colour}" stroke-width="{stroke}" stroke-linecap="round">'
        f'<circle cx="12" cy="12" r="9.6"/><circle cx="12" cy="12" r="2.7"/><path d="{SPOKES}"/></g>'
    )


def _sora() -> tuple[TTFont, hb.Font]:
    font = instantiateVariableFont(TTFont(SORA), {"wght": 600})
    font.flavor = None
    data = io.BytesIO()
    font.save(data)
    return font, hb.Font(hb.Face(data.getvalue()))


FONT, HB = _sora()
UPEM = FONT["head"].unitsPerEm
CAP = FONT["OS/2"].sCapHeight / UPEM


def word(text: str, size: float, colour: str, x: float, baseline: float) -> tuple[str, float, float, float]:
    """`text` as outlines with its ink starting at x. Returns the path, its width, and its ink top and
    bottom relative to the baseline."""
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(HB, buf)
    glyphs, order = FONT.getGlyphSet(), FONT.getGlyphOrder()
    pen, box = SVGPathPen(glyphs, ntos=n), BoundsPen(glyphs)
    k, at = size / UPEM, 0.0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions, strict=True):
        t = (k, 0, 0, -k, (at + pos.x_offset) * k, -pos.y_offset * k)
        glyphs[order[info.codepoint]].draw(TransformPen(pen, t))
        glyphs[order[info.codepoint]].draw(TransformPen(box, t))
        at += pos.x_advance - 0.02 * UPEM
    x0, top, x1, bottom = box.bounds
    path = f'<path transform="translate({n(x - x0)} {n(baseline)})" fill="{colour}" d="{pen.getCommands()}"/>'
    return path, x1 - x0, top, bottom


def doc(body: str, x: float, y: float, w: float, h: float, *, pad: float = 0.01) -> tuple[str, float, float]:
    """An SVG file around `body`, its box (x, y, w, h) with a hair of room so no edge is clipped."""
    p = max(w, h) * pad
    vw, vh = w + 2 * p, h + 2 * p
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{n(x - p)} {n(y - p)} {n(vw)} {n(vh)}" '
        f'width="{n(vw)}" height="{n(vh)}">{body}</svg>'
    )
    return svg, vw, vh


def horizontal(mark: str, ink: str) -> tuple[str, float, float]:
    """The wheel, then the name, the wheel centred on the capitals."""
    f, d, gap = 100, 126, 40
    name, w, top, bottom = word("Mahoraga", f, ink, d + gap, 0)
    cy = -CAP * f / 2
    y0, y1 = min(top, cy - d / 2), max(bottom, cy + d / 2)
    return doc(wheel(d / 2, cy, d, mark) + name, 0, y0, d + gap + w, y1 - y0)


def stacked(mark: str, ink: str) -> tuple[str, float, float]:
    """The wheel above the name, centred: for square places."""
    f, d, gap = 100, 250, 56
    _, w, top, bottom = word("Mahoraga", f, ink, 0, 0)
    width = max(w, d)
    baseline = d + gap - top
    name, *_ = word("Mahoraga", f, ink, (width - w) / 2, baseline)
    # A lighter line than the default: at this size the default outweighs the letters beneath it.
    return doc(wheel(width / 2, d / 2, d, mark, 1.3) + name, 0, 0, width, baseline + bottom)


def tile(bg: str, fg: str, *, d: float = 0.56, radius: float = 0, stroke: float = 1.6) -> str:
    """A square of `bg` with the wheel in `fg`, `d` of its width across. 100 units a side."""
    r = f' rx="{n(100 * radius)}"' if radius else ""
    rect = f'<rect width="100" height="100"{r} fill="{bg}"/>'
    return doc(rect + wheel(50, 50, 100 * d, fg, stroke), 0, 0, 100, 100, pad=0)[0]


def pill(bg: str, fg: str, line: str = "") -> tuple[str, float, float]:
    """The member badge: the wheel and "Member" in a pill, filled with `bg` or drawn in `line`."""
    f, h, d, left, gap, right = 46, 100, 54, 24, 14, 34
    _, w, *_ = word("Member", f, fg, 0, 0)
    width = left + d + gap + w + right
    name, *_ = word("Member", f, fg, left + d + gap, h / 2 + CAP * f / 2)
    i = 2.5 if line else 0  # half the outline, so it is drawn inside the box rather than clipped by it
    edge = f' stroke="{line}" stroke-width="5"' if line else ""
    rect = (
        f'<rect x="{i}" y="{i}" width="{n(width - 2 * i)}" height="{h - 2 * i}" rx="{h / 2 - i}" '
        f'fill="{bg}"{edge}/>'
    )
    return doc(rect + wheel(left + d / 2, h / 2, d, fg, 2.0) + name, 0, 0, width, h, pad=0)


def seal(ring: str) -> str:
    """The member seal for small round places (beside a name, on a picture): the wheel in a ringed disc."""
    body = (
        f'<circle cx="50" cy="50" r="48" fill="none" stroke="{ring}" stroke-width="4"/>'
        f'<circle cx="50" cy="50" r="41" fill="{PURPLE}"/>{wheel(50, 50, 58, WHITE, 2.0)}'
    )
    return doc(body, 0, 0, 100, 100, pad=0)[0]


def fit(svg: str, style: str = "width:100%;height:auto") -> str:
    return svg.replace("<svg ", f'<svg style="{style}" ', 1)


def font_face(family: str, path: Path) -> str:
    data = base64.b64encode(path.read_bytes()).decode()
    return f"@font-face{{font-family:{family};font-weight:100 900;src:url(data:font/woff2;base64,{data})}}"


FONT_CSS = font_face("Sora", SORA) + font_face("Inter", INTER) + "html,body{margin:0;background:transparent}"
FILL = "svg{display:block;width:100vw;height:100vh}"  # one SVG, drawn across the whole viewport


class Out:
    """Writes one design in every format it is wanted in, rendering through Chromium."""

    def __init__(self, page) -> None:
        self.page = page

    def png(self, markup: str, w: int, h: int, path: Path, *, html: bool = False) -> Image.Image:
        """An SVG drawn at w × h, or (`html`) a page w wide and as tall as it needs, with h a minimum."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.set_viewport_size({"width": w, "height": h})
        self.page.set_content(f"<style>{FONT_CSS}{'' if html else FILL}</style>{markup}")
        self.page.evaluate("document.fonts.ready")
        self.page.screenshot(path=path, omit_background=True, full_page=html)
        return Image.open(path).convert("RGBA")

    def all(self, stem: Path, svg: str, vw: float, vh: float, width: int, *, jpg: str = "",
            pad: float = 0, pdf: bool = False) -> None:
        """stem.svg, .png and .webp at `width` px; a .jpg on `jpg` with `pad` of the height around it;
        a .pdf, vector, for print."""
        stem.parent.mkdir(parents=True, exist_ok=True)
        stem.with_suffix(".svg").write_text(svg + "\n")
        img = self.png(svg, width, round(width * vh / vw), stem.with_suffix(".png"))
        img.save(stem.with_suffix(".webp"), lossless=True)
        if jpg:
            p = round(img.height * pad)
            flat = Image.new("RGB", (img.width + 2 * p, img.height + 2 * p), jpg)
            flat.paste(img, (p, p), img)
            flat.save(stem.with_suffix(".jpg"), quality=92)
        if pdf:
            box = f"width:{n(vw)}px;height:{n(vh)}px"
            page = f"@page{{size:{n(vw)}px {n(vh)}px;margin:0}}"
            self.page.set_content(f"<style>{page}{FONT_CSS}</style>{fit(svg, box)}")
            self.page.pdf(path=stem.with_suffix(".pdf"), width=f"{n(vw)}px", height=f"{n(vh)}px")


LOGOS = {  # name: the wheel, the words, the JPEG's background
    "on-dark": (PURPLE, PAPER, GRAPHITE),
    "on-light": (PURPLE, GRAPHITE, WHITE),
    "white": (WHITE, WHITE, PURPLE),
    "black": (GRAPHITE, GRAPHITE, WHITE),
}


def logos(out: Out) -> None:
    for name, (mark, ink, bg) in LOGOS.items():
        out.all(HERE / f"logo/horizontal/mahoraga-logo-{name}", *horizontal(mark, ink), 2400,
                jpg=bg, pad=0.6, pdf=True)
        out.all(HERE / f"logo/stacked/mahoraga-logo-stacked-{name}", *stacked(mark, ink), 1600,
                jpg=bg, pad=0.2, pdf=True)
    for name, colour in {"purple": PURPLE, "white": WHITE, "black": GRAPHITE}.items():
        out.all(HERE / f"mark/mahoraga-wheel-{name}", *doc(wheel(50, 50, 100, colour), 0, 0, 100, 100), 1024)


def icons(out: Out) -> None:
    out.all(HERE / "app-icon/app-icon", tile(PURPLE, WHITE), 100, 100, 1024, jpg=PURPLE)
    out.all(HERE / "app-icon/app-icon-dark", tile(GRAPHITE, PURPLE), 100, 100, 1024, jpg=GRAPHITE)
    out.all(HERE / "app-icon/app-icon-rounded", tile(PURPLE, WHITE, radius=0.22), 100, 100, 1024)
    fav = HERE / "favicon"
    # Small sizes get the wheel larger and its line heavier: at 16 px the app icon's proportions blur.
    small = tile(PURPLE, WHITE, d=0.86, radius=0.22, stroke=2.0)
    fav.mkdir(parents=True, exist_ok=True)
    (fav / "favicon.svg").write_text(small + "\n")
    sizes = [out.png(small, s, s, fav / f"favicon-{s}.png") for s in (16, 32, 48)]
    sizes[-1].save(fav / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)], append_images=sizes[:-1])
    full = tile(PURPLE, WHITE)
    for s, name in ((180, "apple-touch-icon"), (192, "icon-192"), (512, "icon-512")):
        out.png(full, s, s, fav / f"{name}.png")
    # Android crops this one to any shape inside the middle 80%; the wheel keeps well inside it.
    out.png(tile(PURPLE, WHITE, d=0.5), 512, 512, fav / "icon-maskable-512.png")
    out.all(HERE / "social/avatar", full, 100, 100, 800, jpg=PURPLE)


def member(out: Out) -> None:
    badges = {"member-badge": (PURPLE, WHITE, ""), "member-badge-outline-on-dark": ("none", LILAC, LILAC),
              "member-badge-outline-on-light": ("none", PURPLE, PURPLE)}
    for name, args in badges.items():
        out.all(HERE / f"member/{name}", *pill(*args), 600)
    out.all(HERE / "member/member-seal-on-dark", seal(LILAC), 100, 100, 512)
    out.all(HERE / "member/member-seal-on-light", seal(PURPLE), 100, 100, 512)


def banner(w: int, h: int, logo_w: int, tag_px: int) -> str:
    """Graphite, the name and the tagline, and a large faint wheel off the right edge."""
    big = h * 1.5
    ghost = doc(wheel(50, 50, 100, PURPLE, 1.2), 0, 0, 100, 100)[0]
    return (
        f'<div style="position:relative;width:{w}px;height:{h}px;overflow:hidden;background:{GRAPHITE};'
        f'display:flex;flex-direction:column;justify-content:center;padding-left:{round(w * 0.08)}px;'
        f'box-sizing:border-box">'
        f'<div style="position:absolute;width:{big}px;right:{-big * 0.3}px;top:{(h - big) / 2}px;'
        f'opacity:0.16">{fit(ghost)}</div>'
        f'<div style="width:{logo_w}px;position:relative">{fit(horizontal(PURPLE, PAPER)[0])}</div>'
        f'<p style="position:relative;margin:{round(tag_px * 0.9)}px 0 0;font:600 {tag_px}px/1.15 Sora;'
        f'letter-spacing:-0.02em;color:{PAPER}">{TAGLINE[0]} '
        f'<span style="color:{LILAC}">{TAGLINE[1]}</span></p>'
        f"</div>"
    )


def social(out: Out) -> None:
    shapes = {"og-image": (1200, 630, 560, 52), "banner": (1500, 500, 520, 46)}  # w, h, logo, words
    for name, (w, h, logo_w, tag) in shapes.items():
        img = out.png(banner(w, h, logo_w, tag), w, h, HERE / f"social/{name}.png", html=True)
        img.convert("RGB").save(HERE / f"social/{name}.jpg", quality=92)


SHEET_CSS = f"""
body{{background:{GRAPHITE};color:{PAPER};font:400 18px/1.5 Inter}}
.page{{padding:72px;display:grid;gap:56px}}
h1{{font:600 15px/1 Inter;letter-spacing:.12em;text-transform:uppercase;color:{LILAC};margin:0 0 20px}}
.row{{display:flex;gap:24px;align-items:stretch}}
.panel{{flex:1;border-radius:22px;padding:56px;display:flex;align-items:center;justify-content:center;
  border:1px solid rgba(245,246,248,.1)}}
.light{{background:{WHITE}}} .purple{{background:{PURPLE};border:0}}
.cap{{font-size:14px;color:rgba(245,246,248,.55);margin-top:12px}}
.sw{{flex:1}} .chip{{height:150px;border-radius:18px;border:1px solid rgba(245,246,248,.14)}}
.sw b{{display:block;font:600 24px/1.2 Sora;margin-top:16px}}
.sw i{{font-style:normal;color:{LILAC};font-size:15px}}
.sw code{{display:block;font:500 17px/1.6 Inter;margin-top:8px}}
.sw p{{margin:6px 0 0;font-size:15px;color:rgba(245,246,248,.66)}}
"""


def swatches() -> str:
    return '<div class="row">' + "".join(
        f'<div class="sw"><div class="chip" style="background:{hx}"></div><b>{name}</b><i>{role}</i>'
        f"<code>{hx.upper()}<br>RGB {rgb(hx)}</code><p>{use}</p></div>"
        for name, hx, role, use in COLOURS
    ) + "</div>"


def sheet(out: Out) -> None:
    def panel(svg: str, width: int, cls: str = "") -> str:
        return f'<div class="panel {cls}"><div style="width:{width}px">{fit(svg)}</div></div>'

    def icon(svg: str, size: int, label: str) -> str:
        return f'<div><div style="width:{size}px">{fit(svg)}</div><div class="cap">{label}</div></div>'

    fav = tile(PURPLE, WHITE, d=0.86, radius=0.22, stroke=2.0)
    dark = tile(GRAPHITE, PURPLE, radius=0.22).replace("<rect", '<rect stroke="#2a2b31" stroke-width="1"')
    page = f"""<style>{SHEET_CSS}</style><div class="page">
      <div style="width:420px">{fit(horizontal(PURPLE, PAPER)[0])}</div>
      <section><h1>Logo</h1><div class="row">{panel(horizontal(PURPLE, PAPER)[0], 520)}
        {panel(horizontal(PURPLE, GRAPHITE)[0], 520, "light")}</div>
        <div class="row" style="margin-top:24px">{panel(stacked(PURPLE, PAPER)[0], 260)}
        {panel(stacked(PURPLE, GRAPHITE)[0], 260, "light")}
        {panel(stacked(WHITE, WHITE)[0], 260, "purple")}</div></section>
      <section><h1>Icons</h1><div class="row" style="align-items:flex-end;gap:40px">
        {icon(tile(PURPLE, WHITE, radius=0.22), 200, "App icon")}
        {icon(dark, 200, "App icon, dark")}
        {icon(fav, 48, "48")}{icon(fav, 32, "32")}{icon(fav, 16, "16")}
        <div class="cap" style="flex:1;align-self:center">Favicons are drawn bolder: at 16 px the app
          icon's proportions blur into a dot.</div></div></section>
      <section><h1>Member</h1><div class="row" style="align-items:center;gap:40px">
        {icon(pill(PURPLE, WHITE)[0], 260, "Badge")}
        {icon(pill("none", LILAC, LILAC)[0], 260, "Badge, outline")}
        {icon(pill(PURPLE, WHITE)[0], 100, "At 24 px tall")}{icon(seal(LILAC), 140, "Seal")}
        {icon(seal(LILAC), 28, "Seal, small")}</div></section>
      <section><h1>Colour</h1>{swatches()}</section>
      <section><h1>Type</h1><div class="row" style="gap:80px">
        <div><div style="font:600 64px/1 Sora;letter-spacing:-.02em">Mahoraga</div>
          <div class="cap">Sora SemiBold: the name and headings</div></div>
        <div><div style="font:400 26px/1.4 Inter;max-width:640px">Every job that fits you, in one list.
          A CV for each in one tap.</div><div class="cap">Inter: everything else</div></div></div></section>
    </div>"""
    out.png(page, 1600, 400, HERE / "brand-sheet.png", html=True)
    colours = f'<style>{SHEET_CSS}</style><div class="page">{swatches()}</div>'
    out.png(colours, 1600, 300, HERE / "colours.png", html=True)


if __name__ == "__main__":
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        out = Out(browser.new_page())
        for step in (logos, icons, member, social, sheet):
            step(out)
        browser.close()
