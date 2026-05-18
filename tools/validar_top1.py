"""Compara métricas (callouts, sobre_duro, flechas_duro, cruces, dist_media)
entre los SVG marcados con defaults antiguos (`*_marcado_old.svg`) y los
nuevos defaults TOP-1 (`*_marcado.svg`) sobre la carpeta revision_10pdfs.

Uso:
    .venv/bin/python -m tools.validar_top1
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.optimizar import (  # noqa: E402
    cargar_mascara,
    convertir_pdf_a_svg,
    evaluar_svg_marcado,
)

CARPETA = ROOT / "revision_10pdfs"
CACHE = ROOT / "tools" / "_artifacts" / "validacion" / "cache_svg"
CACHE.mkdir(parents=True, exist_ok=True)


def main() -> None:
    pdfs = sorted(CARPETA.glob("*.pdf"))
    if not pdfs:
        print(f"No hay PDFs en {CARPETA}", file=sys.stderr)
        sys.exit(1)

    filas = []
    tot_old = [0, 0, 0, 0, 0.0]
    tot_new = [0, 0, 0, 0, 0.0]

    for pdf in pdfs:
        nombre = pdf.stem
        svg_old = CARPETA / f"{nombre}_marcado_old.svg"
        svg_new = CARPETA / f"{nombre}_marcado.svg"
        svg_orig = CACHE / f"{nombre}.svg"

        if not svg_old.is_file() or not svg_new.is_file():
            print(f"  SKIP {nombre}: falta old o nuevo")
            continue

        convertir_pdf_a_svg(pdf, svg_orig)
        mascara, dpi = cargar_mascara(svg_orig, dpi=140)

        m_old = evaluar_svg_marcado(svg_old, mascara, dpi)
        m_new = evaluar_svg_marcado(svg_new, mascara, dpi)

        filas.append((nombre, m_old, m_new))
        for i, k in enumerate(("n_callouts", "callouts_sobre_duro",
                                "flechas_cruzan_duro", "cruces_entre_flechas")):
            tot_old[i] += getattr(m_old, k)
            tot_new[i] += getattr(m_new, k)
        tot_old[4] += m_old.dist_media
        tot_new[4] += m_new.dist_media

    print()
    print(f"{'PDF':50s}  {'callouts':>15s}  {'duro':>9s}  {'flechaD':>9s}  {'cruces':>7s}  {'dist':>11s}")
    print("-" * 120)
    for nombre, mo, mn in filas:
        nombre_corto = nombre[:50]
        print(f"{nombre_corto:50s}  "
              f"{mo.n_callouts:3d}→{mn.n_callouts:3d}  "
              f"{mo.callouts_sobre_duro:4d}→{mn.callouts_sobre_duro:3d}  "
              f"{mo.flechas_cruzan_duro:4d}→{mn.flechas_cruzan_duro:3d}  "
              f"{mo.cruces_entre_flechas:3d}→{mn.cruces_entre_flechas:2d}  "
              f"{mo.dist_media:5.1f}→{mn.dist_media:5.1f}")
    print("-" * 120)
    n = len(filas) or 1
    print(f"{'TOTAL':50s}  "
          f"{tot_old[0]:3d}→{tot_new[0]:3d}  "
          f"{tot_old[1]:4d}→{tot_new[1]:3d}  "
          f"{tot_old[2]:4d}→{tot_new[2]:3d}  "
          f"{tot_old[3]:3d}→{tot_new[3]:2d}  "
          f"{tot_old[4]/n:5.1f}→{tot_new[4]/n:5.1f}")
    print()

    delta = tot_new[1] + tot_new[2] - (tot_old[1] + tot_old[2])
    if delta < 0:
        print(f"OK: nuevos defaults reducen fallos duros en {-delta} (antes={tot_old[1]+tot_old[2]}, ahora={tot_new[1]+tot_new[2]}).")
    elif delta == 0:
        print(f"= : mismo número de fallos duros ({tot_new[1]+tot_new[2]}). Mirar dist_media para juzgar.")
    else:
        print(f"REGRESIÓN: nuevos defaults añaden {delta} fallos duros respecto a la baseline.")


if __name__ == "__main__":
    main()
