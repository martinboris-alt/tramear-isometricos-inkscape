"""Validación del detector de costuras sobre PDFs reales, sin Inkscape.

Usa el extractor PDF + la geometría compartida con el addon para
detectar shop welds y field welds. Genera:

  - una visualización PNG con SW en rojo y FW en azul
  - un welding book CSV preliminar

NO sustituye al addon de Inkscape (que es el producto final). Se usa
para calibrar parámetros y validar que la lógica acierta antes de pasar
por Inkscape.

Uso:
    python -m tools.validar_detector <pdf>
    python -m tools.validar_detector <pdf> --radio-min 1.4 --radio-max 2.5
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import fitz

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
for p in (ROOT, ROOT / "inkscape_addon"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from tools.pdf_extractor import extraer_pdf  # noqa: E402
from tramear_core.geom import hay_x_cerca, punto_a_segmento  # noqa: E402


def detectar(pdf: Path, *, radio_min: float, radio_max: float,
             tolerancia_linea: float, pagina: int = 0,
             tolerancia_dedup: float = 1.0):
    from math import hypot

    extr = extraer_pdf(pdf, paginas=[pagina], radio_max_circulo=radio_max + 1)
    p = extr.paginas[0]

    segs_tubo = [(l.x1, l.y1, l.x2, l.y2) for l in p.lineas]

    candidatas = []
    for c in p.circulos:
        if not (radio_min <= c.r <= radio_max):
            continue
        d = min(
            (punto_a_segmento(c.cx, c.cy, *s) for s in segs_tubo),
            default=float("inf"),
        )
        if d > tolerancia_linea:
            continue
        candidatas.append({
            "x": c.cx, "y": c.cy, "r": c.r,
            "dist_linea": d, "relleno": c.relleno,
        })

    # Deduplicación: misma posición ⇒ una sola costura (preferir rellena)
    dedup: list[dict] = []
    for cand in candidatas:
        dup = next((o for o in dedup
                    if hypot(o["x"] - cand["x"], o["y"] - cand["y"]) <= tolerancia_dedup),
                   None)
        if dup is None:
            dedup.append(cand)
        elif cand["relleno"] and not dup["relleno"]:
            dedup[dedup.index(dup)] = cand

    for cand in dedup:
        cand["tipo"] = "FW" if hay_x_cerca(cand["x"], cand["y"], cand["r"], segs_tubo) else "SW"

    return p, dedup


def dibujar(pdf: Path, pagina: int, p, costuras, salida: Path, dpi: int = 200):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as e:
        raise SystemExit("Instala Pillow: .venv/bin/pip install Pillow") from e

    salida.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf)
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = doc[pagina].get_pixmap(matrix=mat, alpha=False)
    raw = salida.with_suffix(".raw.png")
    pix.save(raw)
    doc.close()

    img = Image.open(raw).convert("RGB")
    draw = ImageDraw.Draw(img)
    try:
        fuente = ImageFont.truetype("DejaVuSans.ttf", 11)
    except Exception:
        fuente = ImageFont.load_default()

    n_sw = sum(1 for c in costuras if c["tipo"] == "SW")
    n_fw = sum(1 for c in costuras if c["tipo"] == "FW")

    for i, c in enumerate(costuras, start=1):
        cx, cy, r = c["x"] * zoom, c["y"] * zoom, c["r"] * zoom
        anillo = max(int(r * 2.0), 8)
        color = (0, 100, 255) if c["tipo"] == "FW" else (220, 20, 20)
        draw.ellipse(
            (cx - anillo, cy - anillo, cx + anillo, cy + anillo),
            outline=color, width=2,
        )
        etiqueta = f"{i}{'(FW)' if c['tipo'] == 'FW' else ''}"
        draw.text((cx + anillo + 2, cy - 7), etiqueta, fill=color, font=fuente)

    pie_h = 30
    leyenda = Image.new("RGB", (img.width, img.height + pie_h), "white")
    leyenda.paste(img, (0, 0))
    draw2 = ImageDraw.Draw(leyenda)
    y = img.height + 8
    draw2.ellipse((10, y, 24, y + 14), outline=(220, 20, 20), width=2)
    draw2.text((30, y), f"Shop Weld (SW): {n_sw}", fill=(0, 0, 0), font=fuente)
    draw2.ellipse((180, y, 194, y + 14), outline=(0, 100, 255), width=2)
    draw2.text((200, y), f"Field Weld (FW): {n_fw}", fill=(0, 0, 0), font=fuente)
    draw2.text((340, y), f"TOTAL: {len(costuras)}", fill=(0, 0, 0), font=fuente)

    salida.parent.mkdir(parents=True, exist_ok=True)
    leyenda.save(salida)
    raw.unlink(missing_ok=True)


def exportar_csv(pdf: Path, costuras, salida: Path, prefijo: str = "W-"):
    salida.parent.mkdir(parents=True, exist_ok=True)
    with salida.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(("Nº", "Etiqueta", "Tipo", "X_pt", "Y_pt",
                    "Radio_pt", "Dist_línea_pt", "Relleno", "Archivo"))
        for i, c in enumerate(costuras, start=1):
            w.writerow([
                i, f"{prefijo}{i:03d}", c["tipo"],
                f"{c['x']:.2f}", f"{c['y']:.2f}",
                f"{c['r']:.2f}", f"{c['dist_linea']:.2f}",
                "sí" if c["relleno"] else "no",
                pdf.name,
            ])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf")
    ap.add_argument("--pagina", type=int, default=0)
    ap.add_argument("--radio-min", type=float, default=1.4)
    ap.add_argument("--radio-max", type=float, default=2.5)
    ap.add_argument("--tolerancia-linea", type=float, default=1.5)
    ap.add_argument("--dpi", type=int, default=200)
    args = ap.parse_args()

    pdf = Path(args.pdf)
    if not pdf.is_file():
        sys.exit(f"No existe el PDF: {pdf}")

    p, costuras = detectar(
        pdf,
        radio_min=args.radio_min,
        radio_max=args.radio_max,
        tolerancia_linea=args.tolerancia_linea,
        pagina=args.pagina,
    )

    n_sw = sum(1 for c in costuras if c["tipo"] == "SW")
    n_fw = sum(1 for c in costuras if c["tipo"] == "FW")
    print(f"Detectadas {len(costuras)} costuras  →  {n_sw} SW + {n_fw} FW")

    out_dir = _HERE / "_artifacts" / "validacion"
    img_path = out_dir / f"{pdf.stem}_validado.png"
    csv_path = out_dir / f"{pdf.stem}_welding_preliminar.csv"

    dibujar(pdf, args.pagina, p, costuras, img_path, dpi=args.dpi)
    exportar_csv(pdf, costuras, csv_path)

    print(f"\nVisualización: {img_path}")
    print(f"CSV prelim.:   {csv_path}")


if __name__ == "__main__":
    main()
