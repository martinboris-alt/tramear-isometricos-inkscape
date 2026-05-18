"""Cruza las marcas azules del usuario (ground truth de costuras tubing
en CD40F09) con los triángulos rellenos del SVG, para entender qué
patrón geométrico tienen las costuras tubing REALES y derivar un
discriminador robusto contra los falsos positivos (cotas)."""

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
from inkex import PathElement  # noqa: E402

from tramear_core.seam_detector import (  # noqa: E402
    _en_defs, _es_relleno,
    _extraer_segmentos_lineales, _distancia_minima_a_segmentos,
    _punto_a_segmento,
)

from tools.leer_marcas_usuario import extraer_marcas  # noqa: E402


def _todos_los_triangulos(svg_path: Path):
    arbol = inkex.load_svg(str(svg_path)).getroot()
    tris = []
    for el in arbol.iter():
        if not isinstance(el, PathElement) or _en_defs(el):
            continue
        try:
            ctm = el.composed_transform()
            bbox = el.bounding_box(ctm) if ctm else el.bounding_box()
        except Exception:
            continue
        if bbox is None or bbox.width <= 0 or bbox.height <= 0:
            continue
        if min(bbox.width, bbox.height) / max(bbox.width, bbox.height) < 0.5:
            continue
        if not (1.0 <= max(bbox.width, bbox.height) <= 6.0):
            continue
        try:
            cmds = list(el.path)
        except Exception:
            continue
        n_l = sum(1 for c in cmds if c.letter in ("L", "H", "V"))
        n_c = sum(1 for c in cmds if c.letter in ("C", "Q", "S", "T", "A"))
        if n_l > 4 or n_c > 0:
            continue
        if not _es_relleno(el):
            continue
        tris.append({"cx": bbox.center_x, "cy": bbox.center_y,
                     "w": bbox.width, "h": bbox.height,
                     "id": el.get("id") or ""})
    return tris, arbol


def main():
    svg_orig = Path("tools/_artifacts/validacion/cache_svg/"
                    "19-000-2-02-00001 sheet 2121CD40F09-2_IS01.svg")
    svg_marcado = Path("revision_10pdfs/fase_c_CD40F09_candidatos.svg")

    # 1. Ground truth del usuario
    marcas = extraer_marcas(svg_marcado)
    # Excluir marcas demasiado grandes (cajetín, ruido)
    marcas = [(x, y, r, t, i) for x, y, r, t, i in marcas if r < 15]
    print(f"Ground truth: {len(marcas)} costuras tubing marcadas por el usuario\n")

    # 2. Triángulos en el SVG original
    tris, arbol = _todos_los_triangulos(svg_orig)
    print(f"Triángulos rellenos pequeños en el plano: {len(tris)}\n")

    # 3. Segmentos para análisis
    segs = _extraer_segmentos_lineales(arbol)

    # 4. Para cada marca azul, encontrar triángulos dentro del radio
    print("Para cada costura tubing real, los triángulos asociados:")
    print("=" * 80)
    asignados = set()
    pares_reales = []
    for mi, (mx, my, mr, _, _) in enumerate(marcas, 1):
        cercanos = []
        for ti, t in enumerate(tris):
            d = hypot(t["cx"] - mx, t["cy"] - my)
            if d <= mr:
                cercanos.append((d, ti, t))
        cercanos.sort()
        print(f"\nMarca #{mi} ({mx:6.1f},{my:6.1f}) r={mr:.1f} → "
              f"{len(cercanos)} triángulos dentro:")
        for d, ti, t in cercanos:
            asignados.add(ti)
            print(f"   d={d:4.1f}  ({t['cx']:6.1f},{t['cy']:6.1f}) "
                  f"w={t['w']:.2f} h={t['h']:.2f}  {t['id']}")
        if len(cercanos) == 2:
            a, b = cercanos[0][2], cercanos[1][2]
            dpar = hypot(a["cx"] - b["cx"], a["cy"] - b["cy"])
            pares_reales.append((a, b, dpar))
            print(f"   → par bowtie: dist={dpar:.2f}, "
                  f"size={max(a['w'], a['h'], b['w'], b['h']):.2f}, "
                  f"ratio={dpar/max(a['w'], a['h'], b['w'], b['h']):.2f}")

    print(f"\n=== Resumen ===")
    print(f"Triángulos asignados a una marca tubing: {len(asignados)} / {len(tris)}")
    print(f"Triángulos huérfanos (no son tubing): {len(tris) - len(asignados)}")

    # 5. Pares reales: estadísticas
    if pares_reales:
        print(f"\nPares bowtie REALES encontrados: {len(pares_reales)}")
        ds = [p[2] for p in pares_reales]
        sizes = [max(p[0]["w"], p[0]["h"], p[1]["w"], p[1]["h"]) for p in pares_reales]
        ratios = [d/s for d, s in zip(ds, sizes)]
        print(f"  Distancia entre triángulos del par: "
              f"min={min(ds):.2f} med={sorted(ds)[len(ds)//2]:.2f} max={max(ds):.2f}")
        print(f"  Ratio dist/size: "
              f"min={min(ratios):.2f} med={sorted(ratios)[len(ratios)//2]:.2f} max={max(ratios):.2f}")

    # 6. Triángulos huérfanos (no asignados a tubing) — son las cotas
    huerfanos = [tris[i] for i in range(len(tris)) if i not in asignados]
    print(f"\nTriángulos huérfanos (cotas): {len(huerfanos)}")

    # 7. Comparar: para cada triángulo asignado a tubing vs huérfano, qué hay
    # cerca (texto numérico, otro triángulo, etc.)
    # → analizamos longitud del segmento "principal" bajo cada uno
    print("\n=== Característica candidata: longitud del segmento ÚNICO largo cerca ===")
    print("(excluye lados del propio triángulo: descarta segs <7pt)")
    print()

    def linea_principal(cx, cy, max_dist=1.5):
        """Devuelve (longitud_max, num_segs_largos_cerca) cerca de (cx,cy)."""
        candidatos = []
        for s in segs:
            long_seg = hypot(s.x2 - s.x1, s.y2 - s.y1)
            if long_seg < 7.0:
                continue
            d = _punto_a_segmento(cx, cy, s.x1, s.y1, s.x2, s.y2)
            if d <= max_dist:
                candidatos.append(long_seg)
        if not candidatos:
            return (0.0, 0)
        return (max(candidatos), len(candidatos))

    print("TUBING (ground truth):")
    for a, b, dpar in pares_reales:
        cx = (a["cx"] + b["cx"]) / 2
        cy = (a["cy"] + b["cy"]) / 2
        L, n = linea_principal(cx, cy)
        print(f"  ({cx:6.1f},{cy:6.1f}) dpar={dpar:.2f} → "
              f"L_max={L:.1f}pt n_segs_cerca={n}")

    print("\nHUÉRFANOS (probables flechas de cota):")
    for h in huerfanos[:20]:
        L, n = linea_principal(h["cx"], h["cy"])
        print(f"  ({h['cx']:6.1f},{h['cy']:6.1f}) → "
              f"L_max={L:.1f}pt n_segs_cerca={n}  {h['id']}")


if __name__ == "__main__":
    main()
