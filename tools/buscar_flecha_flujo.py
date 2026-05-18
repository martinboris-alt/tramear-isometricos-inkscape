"""Para la marca azul del usuario en (540.53, 468.82) — ubicación de la
flecha de sentido de flujo en CD40F09 — lista todos los elementos
cercanos para caracterizar el símbolo.

Hipótesis: la flecha de flujo es un triángulo relleno alargado SOBRE
la tubería principal (segmento largo) sin pareja con otra flecha cerca.
Diferenciador clave vs puntas de cota: las flechas de cota van en
pares en los extremos de una línea corta; la flecha de flujo está
sola sobre una línea larga.
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
from inkex import PathElement  # noqa: E402

from tramear_core.seam_detector import (  # noqa: E402
    _en_defs, _es_relleno, _ctm_seguro, _transformar_punto,
    _extraer_segmentos_lineales, _punto_a_segmento,
)


def main():
    svg = Path("tools/_artifacts/validacion/cache_svg/"
               "19-000-2-02-00001 sheet 2121CD40F09-2_IS01.svg")
    arbol = inkex.load_svg(str(svg)).getroot()

    target_x, target_y = 540.53, 468.82
    print(f"Buscando elementos cerca de la flecha flujo en ({target_x}, {target_y})\n")

    # 1. Todos los PathElements rellenos cercanos
    elementos = []
    for el in arbol.iter():
        if not isinstance(el, PathElement) or _en_defs(el):
            continue
        # Buscar TODO: relleno y contorno
        try:
            ctm = el.composed_transform()
            bbox = el.bounding_box(ctm) if ctm else el.bounding_box()
            cmds = list(el.path)
        except Exception:
            continue
        if bbox is None or bbox.width <= 0 or bbox.height <= 0:
            continue
        d = hypot(bbox.center_x - target_x, bbox.center_y - target_y)
        if d > 15:
            continue
        # Excluir paths gigantes que cubren todo el dibujo
        if bbox.width > 50 or bbox.height > 50:
            continue
        n_l = sum(1 for c in cmds if c.letter in ("L", "H", "V"))
        n_c = sum(1 for c in cmds if c.letter in ("C", "Q", "S", "T", "A"))
        elementos.append({
            "el": el, "cx": bbox.center_x, "cy": bbox.center_y,
            "w": bbox.width, "h": bbox.height, "rell": _es_relleno(el),
            "n_l": n_l, "n_c": n_c, "n_cmds": len(cmds), "d": d,
        })

    elementos.sort(key=lambda e: e["d"])
    print(f"Paths rellenos a <20pt de la marca: {len(elementos)}")
    for e in elementos[:10]:
        print(f"  d={e['d']:5.2f} ({e['cx']:6.1f},{e['cy']:6.1f}) "
              f"{e['w']:.2f}x{e['h']:.2f} L={e['n_l']} C={e['n_c']} "
              f"cmds={e['n_cmds']}  id={e['el'].get('id') or ''}")

    # 2. Segmentos largos cerca (tubería principal)
    segs = _extraer_segmentos_lineales(arbol)
    segs_cerca = []
    for s in segs:
        L = hypot(s.x2 - s.x1, s.y2 - s.y1)
        if L < 20:
            continue
        d = _punto_a_segmento(target_x, target_y, s.x1, s.y1, s.x2, s.y2)
        if d > 5:
            continue
        from math import atan2, degrees
        ang = degrees(atan2(s.y2 - s.y1, s.x2 - s.x1))
        segs_cerca.append((d, L, ang, s))
    segs_cerca.sort()
    print(f"\nSegmentos largos a <5pt de la marca: {len(segs_cerca)}")
    for d, L, ang, s in segs_cerca[:5]:
        print(f"  d={d:5.2f} L={L:6.2f} ang={ang:+6.1f}°  "
              f"({s.x1:6.1f},{s.y1:6.1f})→({s.x2:6.1f},{s.y2:6.1f})")


if __name__ == "__main__":
    main()
