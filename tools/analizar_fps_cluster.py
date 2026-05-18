"""Analiza qué distingue los clusters acierto vs FP para refinar el
discriminador. Mide para cada cluster:
  - tamaño bbox
  - distancia a segmento de tubería LARGO (>20pt) — costura debe estar
    SOBRE tubería, no aislada
  - densidad de hatching: subpaths / área
  - solapamiento con otros clusters (clusters duplicados que apuntan al
    mismo paralelogramo)
"""

from __future__ import annotations

import sys
from math import hypot
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))
for p in ("/usr/share/inkscape/extensions",
          str(Path.home() / ".config/inkscape/extensions")):
    if p not in sys.path:
        sys.path.insert(0, p)

import inkex  # noqa: E402

from tramear_core.seam_detector import (  # noqa: E402
    _extraer_segmentos_lineales, _punto_a_segmento,
)
from tools.prototipo_tubing import (  # noqa: E402
    _extraer_subpaths_ml, _clusterizar,
)
from tools.leer_marcas_usuario import extraer_marcas  # noqa: E402


def main():
    svg_orig = Path("tools/_artifacts/validacion/cache_svg/"
                    "19-000-2-02-00001 sheet 2121CD40F09-2_IS01.svg")
    svg_user = Path("revision_10pdfs/fase_c_CD40F09_candidatos.svg")

    arbol = inkex.load_svg(str(svg_orig)).getroot()
    subs = _extraer_subpaths_ml(arbol)
    cortos = [(x, y, L, pid) for x, y, L, pid in subs if L <= 6.0]
    clusters = _clusterizar(cortos, dist_max=2.0, min_size=10)
    marcas = [m for m in extraer_marcas(svg_user) if m[2] < 15]
    segs = _extraer_segmentos_lineales(arbol)
    # Solo segmentos LARGOS = tubería principal o lineas-eje
    segs_largos = [s for s in segs
                   if hypot(s.x2 - s.x1, s.y2 - s.y1) >= 20]
    print(f"Segmentos largos (≥20pt): {len(segs_largos)}\n")

    # Anotar cada cluster con métricas
    info = []
    for c in clusters:
        d_marca = float("inf")
        mejor_m = None
        for mi, (mx, my, *_) in enumerate(marcas, 1):
            d = hypot(c["cx"] - mx, c["cy"] - my)
            if d < d_marca:
                d_marca = d
                mejor_m = mi
        # Distancia a segmento LARGO
        d_seg_largo = float("inf")
        for s in segs_largos:
            d = _punto_a_segmento(c["cx"], c["cy"], s.x1, s.y1, s.x2, s.y2)
            if d < d_seg_largo:
                d_seg_largo = d
        # Distancia a OTRO cluster (mejor candidato a duplicado)
        d_otro = float("inf")
        for c2 in clusters:
            if c2 is c:
                continue
            d = hypot(c["cx"] - c2["cx"], c["cy"] - c2["cy"])
            if d < d_otro:
                d_otro = d
        area = max(c["w"] * c["h"], 0.01)
        densidad = c["n"] / area
        info.append({
            **c,
            "d_marca": d_marca,
            "marca_id": mejor_m,
            "d_seg_largo": d_seg_largo,
            "d_otro": d_otro,
            "densidad": densidad,
            "es_acierto": d_marca <= 5.0,
        })

    print(f"{'#':>3} {'cx':>7} {'cy':>7} {'w':>5} {'h':>5} {'n':>4} "
          f"{'dens':>5} {'d_seg':>5} {'d_otro':>6} {'d_U':>5} {'tipo'}")
    for i, c in enumerate(sorted(info, key=lambda x: -x["es_acierto"]), 1):
        tipo = "✓ ACIERTO" if c["es_acierto"] else "✗ FP"
        print(f"{i:>3} {c['cx']:>7.1f} {c['cy']:>7.1f} {c['w']:>5.1f} "
              f"{c['h']:>5.1f} {c['n']:>4d} {c['densidad']:>5.1f} "
              f"{c['d_seg_largo']:>5.1f} {c['d_otro']:>6.1f} "
              f"{c['d_marca']:>5.1f}  {tipo} (U{c['marca_id']})")

    # Estadísticas comparativas
    aciertos = [c for c in info if c["es_acierto"]]
    fps = [c for c in info if not c["es_acierto"]]
    print(f"\n=== Estadísticas ===")
    print(f"Aciertos: {len(aciertos)},  FPs: {len(fps)}")
    for metrica in ["w", "h", "n", "densidad", "d_seg_largo", "d_otro"]:
        a = [c[metrica] for c in aciertos]
        f = [c[metrica] for c in fps]
        a.sort(); f.sort()
        def med(xs): return xs[len(xs)//2] if xs else 0
        print(f"  {metrica:>12}: acierto min/med/max = "
              f"{min(a):.1f}/{med(a):.1f}/{max(a):.1f}   "
              f"FP min/med/max = "
              f"{min(f):.1f}/{med(f):.1f}/{max(f):.1f}")


if __name__ == "__main__":
    main()
