"""Visualizador: anota un PDF con los círculos detectados.

Renderiza la página a PNG y dibuja encima:
  - cada círculo detectado con un anillo de color según su radio,
  - una etiqueta con su radio en puntos.

Útil para validar la detección sin necesidad de saber aún qué círculo
es costura y cuál es decorativo: con el ojo se distingue al instante.

Uso:
    python -m tools.visualizar_circulos archivo.pdf [--salida vis.png] [--dpi 200]
    python -m tools.visualizar_circulos archivo.pdf --pagina 0 --radio-max 30
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import fitz

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from tools.pdf_extractor import extraer_pdf  # noqa: E402


# Tres rangos típicos de radio (en pt) según lo visto en el catálogo:
#   pequeño: costuras (≈ 1.4 – 2.5 pt)
#   medio:   posiblemente válvulas o símbolos especiales (2.5 – 4 pt)
#   grande:  rótulos de cajetín u otros (>4 pt)
RANGOS = (
    (0.0, 2.5, (255, 0, 0)),       # rojo:    candidato a costura
    (2.5, 4.0, (255, 165, 0)),     # naranja: símbolo medio
    (4.0, 100.0, (0, 100, 255)),   # azul:    grande / fuera de rango
)


def color_para_radio(r: float) -> tuple[int, int, int]:
    for lo, hi, color in RANGOS:
        if lo <= r < hi:
            return color
    return (128, 128, 128)


def anotar(
    pdf_path: Path,
    salida_png: Path,
    pagina: int = 0,
    dpi: int = 200,
    radio_max: float = 30.0,
) -> Path:
    extr = extraer_pdf(pdf_path, paginas=[pagina], radio_max_circulo=radio_max)
    if not extr.paginas:
        raise SystemExit(f"No se pudo extraer la página {pagina}")

    p = extr.paginas[0]

    doc = fitz.open(pdf_path)
    page = doc[pagina]
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)

    img_path = salida_png.with_suffix(".raw.png")
    pix.save(img_path)
    doc.close()

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as e:
        raise SystemExit(
            "Falta Pillow. Instala con: .venv/bin/pip install Pillow"
        ) from e

    img = Image.open(img_path).convert("RGB")
    draw = ImageDraw.Draw(img)

    try:
        fuente = ImageFont.truetype("DejaVuSans.ttf", 10)
    except Exception:
        fuente = ImageFont.load_default()

    for c in p.circulos:
        cx_px = c.cx * zoom
        cy_px = c.cy * zoom
        r_px = c.r * zoom
        anillo = max(int(r_px * 1.6), 6)
        color = color_para_radio(c.r)

        draw.ellipse(
            (cx_px - anillo, cy_px - anillo, cx_px + anillo, cy_px + anillo),
            outline=color, width=2,
        )
        etiqueta = f"r={c.r:.2f}"
        draw.text((cx_px + anillo + 3, cy_px - 6), etiqueta,
                  fill=color, font=fuente)

    # Pie con leyenda
    pie_h = 30
    leyenda = Image.new("RGB", (img.width, img.height + pie_h), "white")
    leyenda.paste(img, (0, 0))
    draw2 = ImageDraw.Draw(leyenda)
    y = img.height + 8
    x = 10
    for lo, hi, color in RANGOS:
        draw2.ellipse((x, y, x + 14, y + 14), outline=color, width=2)
        draw2.text((x + 20, y), f"r en [{lo:.1f}, {hi:.1f}) pt",
                   fill=(0, 0, 0), font=fuente)
        x += 180
    draw2.text((x, y), f"Total círculos: {len(p.circulos)}",
               fill=(0, 0, 0), font=fuente)

    salida_png.parent.mkdir(parents=True, exist_ok=True)
    leyenda.save(salida_png)
    img_path.unlink(missing_ok=True)
    return salida_png


def _main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf")
    ap.add_argument("--salida", default=None,
                    help="Ruta PNG de salida (por defecto: tools/_artifacts/<pdf>_circulos.png)")
    ap.add_argument("--pagina", type=int, default=0)
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--radio-max", type=float, default=30.0)
    args = ap.parse_args()

    pdf = Path(args.pdf)
    if not pdf.is_file():
        sys.exit(f"No existe el PDF: {pdf}")

    if args.salida:
        salida = Path(args.salida)
    else:
        salida = _HERE / "_artifacts" / f"{pdf.stem}_circulos.png"

    out = anotar(pdf, salida, pagina=args.pagina, dpi=args.dpi,
                 radio_max=args.radio_max)
    print(f"Visualización guardada en: {out}")


if __name__ == "__main__":
    _main()
