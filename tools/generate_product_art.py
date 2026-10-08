"""Generate flat vector product art for Nibbles.

The catalogue has 124 SKUs, but they reduce to 23 category and flavour pairs
in three pack forms. This script draws one SVG per pair and form, so every SKU
gets art that matches its nut, flavour and pack size, with no photography.

Output: apps/nibbles/static/products/<category>--<flavour>--<form>.svg
Usage:  python tools/generate_product_art.py [--out DIR] [--sheet FILE]
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import re
from html import escape
from pathlib import Path
from typing import Callable, Dict, List, Tuple

W, H = 480, 360
INK = "#2A1D14"

# --------------------------------------------------------------------------
# palette
# --------------------------------------------------------------------------

# Pack colour per category: [body, darker shade, label accent]
PACK = {
    "Peanuts":    ("#C2562A", "#9A4120", "#F6C453"),
    "Cashews":    ("#E3A43A", "#B9822A", "#2A1D14"),
    "Almonds":    ("#8C5A3C", "#6E452D", "#F6C453"),
    "Macadamia":  ("#2F6B5A", "#235244", "#F6C453"),
    "Pistachios": ("#6E8B3D", "#55702C", "#F6E7C1"),
    "Walnuts":    ("#5B3A2E", "#452B22", "#E9C88E"),
    "Mixed Nuts": ("#2A1D14", "#160F0A", "#F6C453"),
    "Snacks":     ("#2C6E74", "#215558", "#F6C453"),
}

# Background tint per flavour.
TINT = {
    "Honey": "#F8E2A8", "Roasted": "#F3D3B5", "Natural Baked": "#ECE4CC",
    "Smoked": "#E4D8CC", "Baked": "#ECE4CC", "Salted": "#E3E9E5",
    "Cracker": "#F6D9B8", "Ikan Bilis": "#DCE6EA", "Sugar": "#F7E3E8",
    "Cocktail Mix": "#F1DDC4", "Fancy Mix": "#EFE0C2",
    "Fruits & Nutty": "#F2DAD3", "Ruby Mix": "#F3D3D3",
    "Coated Green Peas": "#DDEBD2", "Mixed Snacks": "#F2E2C2",
    "Satay Broad Beans": "#F3D0B8",
}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def form_for(grams: int) -> str:
    if grams <= 80:
        return "snack"
    if grams <= 150:
        return "pouch"
    return "jar"


def filename(category: str, flavour: str, grams: int) -> str:
    return f"{slug(category)}--{slug(flavour)}--{form_for(grams)}.svg"


# --------------------------------------------------------------------------
# kernels: each draws one item centred on 0,0, roughly 40 units tall
# --------------------------------------------------------------------------

def stroke(w: float = 2.2) -> str:
    return f'stroke="{INK}" stroke-width="{w}" stroke-linejoin="round" stroke-linecap="round"'


def peanut_shell(fill: str = "#E4C48F") -> str:
    return (
        f'<path d="M0,-26 C13,-26 15,-13 10,-4 C7,2 15,8 13,17 C11,27 -11,27 -13,17 '
        f'C-15,8 -7,2 -10,-4 C-15,-13 -13,-26 0,-26Z" fill="{fill}" {stroke()}/>'
        f'<g fill="none" stroke="{INK}" stroke-opacity=".35" stroke-width="1.4" stroke-linecap="round">'
        f'<path d="M-6,-18 L-3,-15 M4,-19 L6,-15 M-5,-9 L-2,-11 M5,-8 L2,-11 '
        f'M-7,11 L-4,14 M5,10 L8,13 M-3,20 L0,18 M6,19 L4,22"/></g>'
    )


def peanut_kernel(fill: str = "#E9B97A") -> str:
    return (
        f'<ellipse rx="9" ry="13" fill="{fill}" {stroke(2)}/>'
        f'<path d="M0,-12 L0,12" stroke="{INK}" stroke-opacity=".45" stroke-width="1.4"/>'
        f'<ellipse cx="-3" cy="-5" rx="2.2" ry="4" fill="#fff" fill-opacity=".45"/>'
    )


def coated_ball(fill: str = "#D9893A") -> str:
    return (
        f'<circle r="12" fill="{fill}" {stroke(2)}/>'
        f'<g fill="{INK}" fill-opacity=".25"><circle cx="4" cy="3" r="1.4"/>'
        f'<circle cx="-4" cy="5" r="1.1"/><circle cx="2" cy="-5" r="1.2"/>'
        f'<circle cx="6" cy="-2" r="1"/></g>'
        f'<ellipse cx="-4" cy="-4" rx="3" ry="2.2" fill="#fff" fill-opacity=".45"/>'
    )


def cashew(fill: str = "#F1D39D") -> str:
    return (
        f'<path d="M-6,-20 C-22,-14 -22,10 -6,18 C6,24 20,18 20,8 C20,2 14,0 9,4 '
        f'C3,8 -4,4 -5,-4 C-6,-10 -1,-14 -2,-18 C-3,-21 -4,-21 -6,-20Z" fill="{fill}" {stroke()}/>'
        f'<path d="M-12,-6 C-14,4 -8,12 0,14" fill="none" stroke="#fff" stroke-opacity=".5" '
        f'stroke-width="2.4" stroke-linecap="round"/>'
    )


def almond(fill: str = "#B9784A") -> str:
    return (
        f'<path d="M0,-22 C13,-12 15,8 0,21 C-15,8 -13,-12 0,-22Z" fill="{fill}" {stroke()}/>'
        f'<g fill="none" stroke="{INK}" stroke-opacity=".3" stroke-width="1.3" stroke-linecap="round">'
        f'<path d="M-4,-12 C-6,-2 -6,8 -3,15 M3,-14 C5,-4 6,6 4,14 M0,-18 L0,18"/></g>'
    )


def macadamia(fill: str = "#F3E3BC") -> str:
    return (
        f'<circle r="16" fill="{fill}" {stroke()}/>'
        f'<path d="M-9,4 C-6,10 2,12 8,8" fill="none" stroke="{INK}" stroke-opacity=".25" '
        f'stroke-width="1.5" stroke-linecap="round"/>'
        f'<circle cx="2" cy="-2" r="2" fill="{INK}" fill-opacity=".18"/>'
        f'<ellipse cx="-6" cy="-7" rx="4.5" ry="3" fill="#fff" fill-opacity=".6"/>'
    )


def pistachio(shell: str = "#E7D3A6") -> str:
    return (
        f'<ellipse rx="14" ry="19" fill="{shell}" {stroke()}/>'
        f'<path d="M-3,-16 C-8,-6 -8,8 -2,17 C5,10 6,-6 -3,-16Z" fill="#8DB04A" {stroke(1.8)}/>'
        f'<path d="M-5,-10 C-6,-2 -5,6 -3,11" fill="none" stroke="#C9DE8C" stroke-width="2" '
        f'stroke-linecap="round"/>'
    )


def walnut(fill: str = "#B9844F") -> str:
    pts = []
    for i in range(73):
        t = i / 72 * 2 * math.pi
        r = 17 + 1.6 * math.sin(7 * t)
        pts.append(f"{r * math.cos(t) * 1.05:.1f},{r * math.sin(t) * 0.92:.1f}")
    outline = "M" + " L".join(pts) + "Z"
    return (
        f'<path d="{outline}" fill="{fill}" {stroke(2)}/>'
        f'<g fill="none" stroke="{INK}" stroke-opacity=".35" stroke-width="1.5" stroke-linecap="round">'
        f'<path d="M0,-15 C-3,-5 3,5 0,15"/>'
        f'<path d="M-6,-9 C-11,-6 -11,0 -7,2 C-12,4 -11,10 -6,11"/>'
        f'<path d="M6,-9 C11,-6 11,0 7,2 C12,4 11,10 6,11"/></g>'
    )


def green_pea(fill: str = "#86B556") -> str:
    return (
        f'<circle r="10" fill="{fill}" {stroke(2)}/>'
        f'<ellipse cx="-3.5" cy="-3.5" rx="3" ry="2" fill="#fff" fill-opacity=".5"/>'
    )


def broad_bean(fill: str = "#C17A40") -> str:
    return (
        f'<path d="M-15,-2 C-15,-12 -2,-13 6,-11 C15,-8 17,4 10,10 C2,15 -15,12 -15,-2Z" '
        f'fill="{fill}" {stroke(2)}/>'
        f'<path d="M-9,-1 C-3,-5 4,-5 9,0" fill="none" stroke="{INK}" stroke-opacity=".5" '
        f'stroke-width="1.6" stroke-linecap="round"/>'
        f'<circle cx="2" cy="4" r="1.2" fill="#D23B1E"/><circle cx="-5" cy="5" r="1" fill="#D23B1E"/>'
    )


def cracker_bit(fill: str = "#E8A94B") -> str:
    return (
        f'<rect x="-11" y="-8" width="22" height="16" rx="5" fill="{fill}" {stroke(2)}/>'
        f'<g fill="{INK}" fill-opacity=".3"><circle cx="-5" r="1.3"/><circle cx="0" r="1.3"/>'
        f'<circle cx="5" r="1.3"/></g>'
    )


def raisin(fill: str = "#5A2E3E") -> str:
    return (
        f'<path d="M-8,-6 C-4,-11 6,-10 9,-4 C12,3 6,10 -1,9 C-8,8 -12,0 -8,-6Z" '
        f'fill="{fill}" {stroke(1.8)}/>'
        f'<path d="M-4,-3 C-1,0 2,1 5,0" fill="none" stroke="#fff" stroke-opacity=".3" stroke-width="1.4"/>'
    )


def cranberry(fill: str = "#C8323F") -> str:
    return (
        f'<path d="M-9,-3 C-8,-10 4,-11 8,-5 C12,1 7,9 0,9 C-7,9 -10,3 -9,-3Z" fill="{fill}" {stroke(1.8)}/>'
        f'<ellipse cx="-3" cy="-4" rx="2.5" ry="1.6" fill="#fff" fill-opacity=".5"/>'
    )


def anchovy(fill: str = "#B7C3C9") -> str:
    return (
        f'<path d="M-16,0 C-10,-6 6,-6 12,0 C6,6 -10,6 -16,0Z" fill="{fill}" {stroke(1.8)}/>'
        f'<path d="M12,0 L19,-5 L18,0 L19,5Z" fill="{fill}" {stroke(1.6)}/>'
        f'<circle cx="-11" cy="-1" r="1.4" fill="{INK}"/>'
    )


# What scatters around the pack, and the hero on the label, per pair.
Kernel = Callable[[], str]


def kernels(category: str, flavour: str) -> Tuple[Kernel, List[Kernel]]:
    if category == "Peanuts":
        if flavour == "Cracker":
            return coated_ball, [coated_ball, coated_ball, peanut_kernel]
        if flavour == "Ikan Bilis":
            return peanut_kernel, [peanut_kernel, peanut_kernel, anchovy]
        if flavour == "Sugar":
            return (lambda: coated_ball("#F2E2C4")), [lambda: coated_ball("#F2E2C4"), peanut_kernel]
        return peanut_shell, [peanut_kernel, peanut_kernel, peanut_shell]
    if category == "Cashews":
        honey = flavour == "Honey"
        c = (lambda: cashew("#EDC07A")) if honey else (lambda: cashew("#F1D39D")) \
            if flavour == "Natural Baked" else (lambda: cashew("#E9BE80"))
        return c, [c]
    if category == "Almonds":
        shade = {"Honey": "#C98A4E", "Smoked": "#8E5A38", "Roasted": "#A86A3E"}.get(flavour, "#B9784A")
        a = lambda: almond(shade)
        return a, [a]
    if category == "Macadamia":
        m = (lambda: macadamia("#F0D79C")) if flavour == "Honey" else macadamia
        return m, [m]
    if category == "Pistachios":
        p = (lambda: pistachio("#EFE3C8")) if flavour == "Salted" else pistachio
        return p, [p]
    if category == "Walnuts":
        return walnut, [walnut]
    if category == "Mixed Nuts":
        base = [cashew, almond, macadamia, peanut_kernel]
        if flavour == "Fruits & Nutty":
            return cashew, base + [raisin, raisin]
        if flavour == "Ruby Mix":
            return cranberry, base + [cranberry, cranberry]
        if flavour == "Fancy Mix":
            return walnut, [cashew, almond, macadamia, walnut, pistachio]
        return almond, base + [coated_ball]
    if category == "Snacks":
        if flavour == "Coated Green Peas":
            return green_pea, [green_pea]
        if flavour == "Satay Broad Beans":
            return broad_bean, [broad_bean]
        return cracker_bit, [green_pea, broad_bean, cracker_bit, peanut_kernel]
    return peanut_kernel, [peanut_kernel]


# --------------------------------------------------------------------------
# flavour badges, drawn in a 24x24 box centred on 0,0
# --------------------------------------------------------------------------

def badge_icon(flavour: str) -> str:
    s = f'fill="none" stroke="{INK}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
    icons = {
        "Honey": f'<path d="M0,-9 C4,-3 7,1 7,4 A7,7 0 0 1 -7,4 C-7,1 -4,-3 0,-9Z" fill="#F2B533" '
                 f'stroke="{INK}" stroke-width="2" stroke-linejoin="round"/>',
        "Roasted": f'<path d="M0,-10 C5,-4 8,0 7,5 A7,7 0 0 1 -7,5 C-8,1 -5,-2 -3,-5 C-2,-1 0,0 1,-2 '
                   f'C2,-5 1,-8 0,-10Z" fill="#E8692C" stroke="{INK}" stroke-width="2" stroke-linejoin="round"/>',
        "Smoked": f'<path d="M-4,9 C-8,4 0,1 -4,-3 C-7,-6 -2,-9 -2,-9 M4,9 C0,4 8,1 4,-3 C1,-6 6,-9 6,-9" {s}/>',
        "Natural Baked": f'<path d="M-7,7 C-8,-3 -1,-9 8,-8 C9,1 3,8 -7,7Z" fill="#8DB04A" '
                         f'stroke="{INK}" stroke-width="2" stroke-linejoin="round"/>'
                         f'<path d="M-7,7 L3,-3" {s}/>',
        "Salted": f'<g fill="#fff" stroke="{INK}" stroke-width="1.8" stroke-linejoin="round">'
                  f'<rect x="-8" y="-1" width="7" height="7" rx="1"/><rect x="1" y="-7" width="7" height="7" rx="1"/>'
                  f'<rect x="2" y="2" width="5" height="5" rx="1"/></g>',
        "Sugar": f'<path d="M0,-9 L2,-2 L9,0 L2,2 L0,9 L-2,2 L-9,0 L-2,-2Z" fill="#fff" '
                 f'stroke="{INK}" stroke-width="1.8" stroke-linejoin="round"/>',
        "Cracker": f'<rect x="-8" y="-6" width="16" height="12" rx="3" fill="#E8A94B" '
                   f'stroke="{INK}" stroke-width="2"/><g fill="{INK}"><circle cx="-3" r="1.2"/>'
                   f'<circle cx="3" r="1.2"/></g>',
        "Ikan Bilis": f'<g transform="scale(.55)">{anchovy()}</g>',
        "Satay Broad Beans": f'<path d="M-3,9 C-6,2 -3,-5 4,-8 C7,-9 9,-7 7,-5 C2,-2 1,3 2,8 '
                             f'C2,10 -2,11 -3,9Z" fill="#D23B1E" stroke="{INK}" stroke-width="2" '
                             f'stroke-linejoin="round"/><path d="M5,-8 L7,-11" {s}/>',
        "Coated Green Peas": f'<g transform="scale(.7)">{green_pea()}</g>',
        "Fruits & Nutty": f'<g transform="scale(.8)">{raisin()}</g>',
        "Ruby Mix": f'<g transform="scale(.8)">{cranberry()}</g>',
    }
    if flavour in ("Baked",):
        return icons["Natural Baked"]
    if flavour in icons:
        return icons[flavour]
    # Cocktail, Fancy and Mixed Snacks: a little cluster of three dots.
    return (f'<g stroke="{INK}" stroke-width="1.8"><circle cx="-4" cy="3" r="4" fill="#E9B97A"/>'
            f'<circle cx="4" cy="3" r="4" fill="#B9784A"/><circle cy="-4" r="4" fill="#F3E3BC"/></g>')


# --------------------------------------------------------------------------
# packs
# --------------------------------------------------------------------------

def place(fn: Kernel, x: float, y: float, rot: float, scale: float) -> str:
    return f'<g transform="translate({x:.1f},{y:.1f}) rotate({rot:.0f}) scale({scale:.2f})">{fn()}</g>'


def pouch(category: str, flavour: str, hero: Kernel, scale: float) -> str:
    body, shade, accent = PACK[category]
    # Stand-up pouch, 200 wide by 240 tall before scaling, origin at bottom centre.
    zig = " ".join(f"L{-92 + i * 9.2:.1f},{-236 + (4 if i % 2 else 0)}" for i in range(21))
    return f'''
<g transform="translate(240,322) scale({scale})">
  <ellipse cx="0" cy="2" rx="104" ry="10" fill="{INK}" fill-opacity=".12"/>
  <path d="M-92,-236 {zig} L92,-236 L92,-222 C98,-160 100,-60 96,-12 C95,-4 88,0 80,0
           L-80,0 C-88,0 -95,-4 -96,-12 C-100,-60 -98,-160 -92,-222Z"
        fill="{body}" {stroke(3)}/>
  <path d="M-92,-222 L92,-222" stroke="{INK}" stroke-width="2" stroke-opacity=".6"/>
  <path d="M-96,-30 C-40,-18 40,-18 96,-30 L96,-12 C95,-4 88,0 80,0 L-80,0 C-88,0 -95,-4 -96,-12Z"
        fill="{shade}"/>
  <path d="M-92,-222 C-98,-160 -100,-60 -96,-12 C-95,-4 -88,0 -80,0 L80,0 C88,0 95,-4 96,-12
           C100,-60 98,-160 92,-222Z" fill="none" {stroke(3)}/>
  <path d="M-74,-200 C-80,-150 -80,-90 -76,-50" fill="none" stroke="#fff" stroke-opacity=".22"
        stroke-width="8" stroke-linecap="round"/>
  <rect x="-62" y="-196" width="124" height="22" rx="11" fill="{accent}" {stroke(2)}/>
  <text x="0" y="-180" text-anchor="middle" font-family="'Bricolage Grotesque','DM Sans',sans-serif"
        font-weight="700" font-size="14" letter-spacing="1" fill="{body if accent != INK else '#F6C453'}">C4N</text>
  <circle cx="0" cy="-104" r="56" fill="#FBF5EC" {stroke(3)}/>
  <g transform="translate(0,-104) scale(1.75)">{hero()}</g>
  <g transform="translate(58,-150)">
    <circle r="20" fill="#FFFFFF" {stroke(2.4)}/>
    {badge_icon(flavour)}
  </g>
</g>'''


def jar(category: str, flavour: str, hero: Kernel, fill: List[Kernel], rng: random.Random) -> str:
    body, shade, accent = PACK[category]
    inside = []
    for row in range(7):
        for col in range(6):
            x = -78 + col * 31 + (15 if row % 2 else 0) + rng.uniform(-4, 4)
            y = -24 - row * 27 + rng.uniform(-3, 3)
            inside.append(place(rng.choice(fill), x, y, rng.uniform(0, 360), 0.95))
    return f'''
<g transform="translate(240,326)">
  <ellipse cx="0" cy="2" rx="112" ry="10" fill="{INK}" fill-opacity=".12"/>
  <clipPath id="jarclip"><rect x="-96" y="-206" width="192" height="206" rx="26"/></clipPath>
  <rect x="-96" y="-206" width="192" height="206" rx="26" fill="#FFFDF9"/>
  <g clip-path="url(#jarclip)">{''.join(inside)}</g>
  <rect x="-96" y="-206" width="192" height="206" rx="26" fill="#FFFFFF" fill-opacity=".18" {stroke(3)}/>
  <path d="M-78,-186 L-78,-40" stroke="#fff" stroke-opacity=".7" stroke-width="7" stroke-linecap="round"/>
  <rect x="-84" y="-246" width="168" height="44" rx="12" fill="{body}" {stroke(3)}/>
  <path d="M-84,-224 L84,-224" stroke="{shade}" stroke-width="10"/>
  <rect x="-84" y="-246" width="168" height="44" rx="12" fill="none" {stroke(3)}/>
  <rect x="-70" y="-150" width="140" height="78" rx="14" fill="#FBF5EC" {stroke(2.6)}/>
  <rect x="-70" y="-150" width="140" height="20" rx="10" fill="{body}"/>
  <rect x="-70" y="-140" width="140" height="10" fill="{body}"/>
  <rect x="-70" y="-150" width="140" height="78" rx="14" fill="none" {stroke(2.6)}/>
  <text x="0" y="-135" text-anchor="middle" font-family="'Bricolage Grotesque','DM Sans',sans-serif"
        font-weight="700" font-size="12" letter-spacing="1" fill="{accent if accent != INK else '#FBF5EC'}">C4N</text>
  <g transform="translate(-24,-100) scale(1.05)">{hero()}</g>
  <g transform="translate(26,-100)">
    <circle r="17" fill="#FFFFFF" {stroke(2.2)}/>
    <g transform="scale(.85)">{badge_icon(flavour)}</g>
  </g>
</g>'''


def artwork(category: str, flavour: str, form: str) -> str:
    rng = random.Random(f"{category}|{flavour}|{form}")
    hero, scatter = kernels(category, flavour)
    tint = TINT.get(flavour, "#F3ECE1")

    # Loose kernels in front of the pack, kept clear of the label.
    loose = []
    spots = [(-150, 300), (-118, 330), (-176, 336), (150, 302), (120, 334), (178, 330),
             (-200, 300), (204, 306)]
    count = 6 if form != "snack" else 8
    for x, y in spots[:count]:
        loose.append(place(rng.choice(scatter), 240 + x + rng.uniform(-6, 6), y + rng.uniform(-4, 4),
                           rng.uniform(-40, 40), rng.uniform(0.9, 1.15)))

    if form == "jar":
        pack = jar(category, flavour, hero, scatter, rng)
    else:
        pack = pouch(category, flavour, hero, 0.98 if form == "pouch" else 0.78)

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="{escape(category)}, {escape(flavour)}">
<rect width="{W}" height="{H}" fill="{tint}"/>
<circle cx="78" cy="70" r="120" fill="#FFFFFF" fill-opacity=".28"/>
<circle cx="430" cy="300" r="90" fill="#FFFFFF" fill-opacity=".18"/>
{pack}
{''.join(loose)}
</svg>
'''


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", default=str(root / "data/Team_Cashew_Synthetic_Data/sku_master.csv"))
    ap.add_argument("--out", default=str(root / "apps/nibbles/static/products"))
    ap.add_argument("--sheet", default="")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(open(args.master, encoding="utf-8")))
    made: Dict[str, Tuple[str, str, str]] = {}
    for row in rows:
        name = filename(row["category"], row["flavour"], int(float(row["pack_size_g"])))
        if name not in made:
            form = form_for(int(float(row["pack_size_g"])))
            (out / name).write_text(artwork(row["category"], row["flavour"], form), encoding="utf-8")
            made[name] = (row["category"], row["flavour"], form)
    print(f"{len(made)} images for {len(rows)} SKUs in {out}")

    if args.sheet:
        cells = "".join(
            f'<figure><img src="{out.as_posix()}/{n}"><figcaption>{c} · {f} · {fm}</figcaption></figure>'
            for n, (c, f, fm) in sorted(made.items()))
        Path(args.sheet).write_text(
            "<!doctype html><meta charset=utf-8><style>body{margin:0;padding:16px;background:#F3ECE1;"
            "font:12px system-ui;display:grid;grid-template-columns:repeat(6,1fr);gap:12px}"
            "figure{margin:0}img{width:100%;border-radius:12px;display:block}"
            "figcaption{padding:4px 2px;color:#2A1D14}</style>" + cells, encoding="utf-8")


if __name__ == "__main__":
    main()
