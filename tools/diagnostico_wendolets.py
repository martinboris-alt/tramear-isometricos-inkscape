"""Diagnóstico Fase B: ¿hay un segundo punto cerca de las costuras detectadas?

Para los PDFs señalados, compara la lista de costuras detectadas con
todos los círculos pequeños del PDF crudo. Reporta candidatos pequeños
(radio 1.0-3.0 pt) que están a distancia 2-15 pt de una costura
detectada pero que NO fueron detectados como costura.

Si hay candidatos abundantes, la hipótesis "hay un segundo punto que
deduplicación o filtros descartan" queda confirmada y podemos implementar
la lógica de detección de wendolets.

Uso:
    .venv/bin/python -m tools.diagnostico_wendolets <pdf> [<pdf>...]
"""

from __future__ import annotations

import argparse
import csv
import sys
from math import hypot
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.pdf_extractor import extraer_pdf  # noqa: E402

# Importes del addon
_INK_EXT = "/usr/share/inkscape/extensions"
_USR_EXT = str(Path.home() / ".config/inkscape/extensions")
for p in (_INK_EXT, _USR_EXT):
    if p not in sys.path:
        sys.path.insert(0, p)

import inkex  # noqa: E402

from tramear_core.seam_detector import detectar_costuras  # noqa: E402


RADIO_MIN_CRUDO = 1.0     # mín de candidato bruto a considerar
RADIO_MAX_CRUDO = 3.0     # máx
DIST_MIN_VECINO = 2.0     # distancia mínima entre primer punto y vecino
DIST_MAX_VECINO = 15.0    # distancia máxima

# Defaults del addon
RADIO_MIN_ADDON = 1.4
RADIO_MAX_ADDON = 2.5
TOL_LINEA = 1.5


def cargar_costuras_addon(svg_path: Path):
    """Aplica el detector real del addon sobre un SVG."""
    svg = inkex.load_svg(str(svg_path)).getroot()
    return detectar_costuras(
        svg,
        radio_min=RADIO_MIN_ADDON,
        radio_max=RADIO_MAX_ADDON,
        tolerancia_linea=TOL_LINEA,
        solo_sobre_linea=True,
        detectar_field_welds=False,
        filtrar_puntas_flecha=True,
    )


def pdf_a_svg(pdf: Path, dst: Path) -> None:
    if dst.is_file():
        return
    import subprocess
    subprocess.run(
        ["inkscape", "--pdf-poppler", "--export-type=svg",
         f"--export-filename={dst}", str(pdf)],
        check=True, capture_output=True,
    )


def diagnostico(pdf: Path) -> dict:
    cache = ROOT / "tools" / "_artifacts" / "validacion" / "cache_svg"
    cache.mkdir(parents=True, exist_ok=True)
    svg = cache / f"{pdf.stem}.svg"
    pdf_a_svg(pdf, svg)

    costuras = cargar_costuras_addon(svg)
    extr = extraer_pdf(pdf, radio_max_circulo=5.0)
    if not extr.paginas:
        return {"pdf": pdf.name, "error": "sin páginas"}
    pag = extr.paginas[0]

    # fitz (PyMuPDF) y SVG comparten origen en top-left, así que las
    # coordenadas son directamente comparables sin invertir Y.
    crudos = []
    for c in pag.circulos:
        if RADIO_MIN_CRUDO <= c.r <= RADIO_MAX_CRUDO:
            crudos.append((c.cx, c.cy, c.r))

    # Para cada costura detectada, buscar otros círculos brutos cercanos
    hallazgos = []
    for cs in costuras:
        for (cx, cy, r) in crudos:
            d = hypot(cs.x - cx, cs.y - cy)
            if DIST_MIN_VECINO <= d <= DIST_MAX_VECINO:
                # ¿es este crudo igual a otra costura ya detectada?
                ya_detectado = any(
                    hypot(cx - o.x, cy - o.y) < 1.5 for o in costuras
                )
                if ya_detectado:
                    continue
                hallazgos.append({
                    "costura": (round(cs.x, 1), round(cs.y, 1), round(cs.radio, 2)),
                    "vecino": (round(cx, 1), round(cy, 1), round(r, 2)),
                    "dist": round(d, 1),
                })

    return {
        "pdf": pdf.name,
        "n_costuras": len(costuras),
        "n_crudos_pequenos": len(crudos),
        "n_hallazgos": len(hallazgos),
        "hallazgos": hallazgos,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("--csv", default=None)
    args = ap.parse_args()

    todos = []
    for path in args.pdfs:
        pdf = Path(path)
        if not pdf.is_file():
            print(f"  SKIP {pdf}: no existe", file=sys.stderr)
            continue
        r = diagnostico(pdf)
        todos.append(r)
        print(f"\n=== {r['pdf']} ===")
        print(f"  costuras detectadas:    {r.get('n_costuras', '-')}")
        print(f"  crudos pequeños [{RADIO_MIN_CRUDO}-{RADIO_MAX_CRUDO}] pt: {r.get('n_crudos_pequenos', '-')}")
        print(f"  vecinos NO detectados a {DIST_MIN_VECINO}-{DIST_MAX_VECINO} pt: {r.get('n_hallazgos', '-')}")
        for h in r.get("hallazgos", [])[:20]:
            print(f"    cost={h['costura']}  vecino={h['vecino']}  dist={h['dist']} pt")
        if len(r.get("hallazgos", [])) > 20:
            print(f"    … ({len(r['hallazgos']) - 20} más)")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["pdf", "costura_x", "costura_y", "vecino_x", "vecino_y",
                        "vecino_r", "dist_pt"])
            for r in todos:
                for h in r.get("hallazgos", []):
                    w.writerow([r["pdf"], *h["costura"][:2], *h["vecino"][:2],
                                h["vecino"][2], h["dist"]])
        print(f"\nCSV guardado en: {args.csv}")


if __name__ == "__main__":
    main()
