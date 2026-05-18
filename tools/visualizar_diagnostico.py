"""Visualizador diagnóstico: superpone TODOS los círculos candidatos al
plano original, distinguiendo:

  - VERDE (relleno): aceptados como costura por el detector final.
  - ROJO (anillo): rechazados (descartados por el filtro de triángulo,
    deduplicación, fuera de rango, etc.).
  - AZUL (anillo grueso): círculos NO sobre línea (no candidatos).
  - AMARILLO (cruz): piece marks del BOM detectados sobre el plano.

Útil para identificar visualmente qué costuras se están perdiendo y por qué.

Uso:
    .venv/bin/python -m tools.visualizar_diagnostico <pdf>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

# Inkex / addon
_INK_EXT = "/usr/share/inkscape/extensions"
_USR_EXT = str(Path.home() / ".config/inkscape/extensions")
for p in (_INK_EXT, _USR_EXT):
    if p not in sys.path:
        sys.path.insert(0, p)

import inkex  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from tools.enriquecer_welding_book import (  # noqa: E402
    candidatos_brutos, cargar_svg, costuras_de, detectar_piece_marks,
)
from tools.extraer_bom import extraer_bom  # noqa: E402
from tramear_core.seam_detector import (  # noqa: E402
    _buscar_circulos, _distancia_minima_a_segmentos, _extraer_segmentos_lineales,
)


def renderizar_pdf(pdf: Path, dpi: int = 200) -> tuple[Image.Image, float]:
    import fitz
    doc = fitz.open(pdf)
    page = doc[0]
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    return img, zoom


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--salida", default=None)
    args = ap.parse_args()

    pdf = Path(args.pdf)
    if not pdf.is_file():
        sys.exit(f"PDF no existe: {pdf}")

    print(f"Procesando: {pdf.name}")
    img, zoom = renderizar_pdf(pdf, args.dpi)
    draw = ImageDraw.Draw(img)
    try:
        fuente = ImageFont.truetype("DejaVuSans.ttf", 11)
        fuente_pm = ImageFont.truetype("DejaVuSans-Bold.ttf", 13)
    except Exception:
        fuente = fuente_pm = ImageFont.load_default()

    svg = cargar_svg(pdf)
    aceptadas = costuras_de(svg)
    set_aceptadas = {(round(c.x, 1), round(c.y, 1)) for c in aceptadas}

    # Todos los crudos en el rango (sobre línea o no, rellenos o no)
    arbol = inkex.load_svg(str(svg)).getroot()
    crudos = _buscar_circulos(arbol, 1.4, 2.5)
    segs = _extraer_segmentos_lineales(arbol)
    for c in crudos:
        c.distancia_linea = _distancia_minima_a_segmentos(c.x, c.y, segs)
        c.sobre_linea = c.distancia_linea <= 1.5

    items = extraer_bom(pdf)
    pms = detectar_piece_marks(pdf, items)

    # Pintar
    R = 9     # radio de anillo en píxeles base
    n_acep = n_rech = n_off = 0
    for c in crudos:
        x = c.x * zoom
        y = c.y * zoom
        key = (round(c.x, 1), round(c.y, 1))
        if not c.sobre_linea:
            draw.ellipse((x - R, y - R, x + R, y + R), outline=(40, 80, 220), width=1)
            n_off += 1
        elif key in set_aceptadas:
            draw.ellipse((x - R, y - R, x + R, y + R), outline=(0, 160, 0), width=3)
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(0, 160, 0))
            n_acep += 1
        else:
            draw.ellipse((x - R, y - R, x + R, y + R), outline=(220, 30, 30), width=2)
            draw.line((x - 5, y, x + 5, y), fill=(220, 30, 30), width=2)
            n_rech += 1

    for pm in pms:
        x = pm.x * zoom
        y = pm.y * zoom
        s = 12
        draw.line((x - s, y - s, x + s, y + s), fill=(220, 200, 0), width=3)
        draw.line((x - s, y + s, x + s, y - s), fill=(220, 200, 0), width=3)
        draw.text((x + s + 2, y - 8), f"PT{pm.pt_no}",
                  fill=(160, 130, 0), font=fuente_pm)

    # Leyenda
    leyenda = [
        (f"{n_acep} aceptadas (verde)", (0, 160, 0)),
        (f"{n_rech} rechazadas (rojo)", (220, 30, 30)),
        (f"{n_off} fuera de línea (azul)", (40, 80, 220)),
        (f"{len(pms)} piece marks (amarillo)", (220, 200, 0)),
    ]
    y0 = img.height - 90
    for i, (txt, col) in enumerate(leyenda):
        y = y0 + i * 20
        draw.rectangle((10, y, 28, y + 14), outline=col, width=2)
        draw.text((35, y), txt, fill=(0, 0, 0), font=fuente)

    salida = (Path(args.salida) if args.salida
              else pdf.parent / f"{pdf.stem}_diagnostico.png")
    img.save(salida)
    print(f"  aceptadas={n_acep}  rechazadas={n_rech}  off={n_off}  PM={len(pms)}")
    print(f"  salida: {salida}")


if __name__ == "__main__":
    main()
