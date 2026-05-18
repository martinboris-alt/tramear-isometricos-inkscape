"""Construye el grafo de tubería de CD40F09 para entender su topología.

- Toma todos los segmentos LARGOS (≥10pt) del SVG.
- Fusiona endpoints cercanos (≤1pt) en nodos del grafo.
- Lista nodos por grado: extremos (grado 1), continuaciones (grado 2),
  tees/intersecciones (grado ≥3).
- Asigna cada costura tubing al edge más cercano.

Objetivo: ver cuántas ramificaciones (Ts) tiene CD40F09 y entender
qué heurística de "ir por la rama primero" debemos aplicar.
"""

from __future__ import annotations

import sys
from collections import defaultdict
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
    detectar_costuras, detectar_flechas_flujo,
)


def construir_grafo(segs, escala=1.0, long_min=4.0, tol_nodo=2.5):
    """Devuelve (nodos, edges):
      nodos: dict id_nodo -> (x, y)
      edges: list de (id_n1, id_n2, longitud, segmento_original)
    """
    largos = [s for s in segs
              if hypot(s.x2 - s.x1, s.y2 - s.y1) >= long_min * escala]

    # Unificar puntos: recolectar todos los endpoints, fusionar
    puntos: list[tuple[float, float]] = []
    asignacion = {}  # idx en endpoints → idx en nodos
    for i, s in enumerate(largos):
        for p in [(s.x1, s.y1), (s.x2, s.y2)]:
            asignado = None
            for j, q in enumerate(puntos):
                if hypot(p[0] - q[0], p[1] - q[1]) <= tol_nodo * escala:
                    asignado = j
                    break
            if asignado is None:
                asignado = len(puntos)
                puntos.append(p)

    # Indexar nodos
    nodos = {j: p for j, p in enumerate(puntos)}

    # Construir edges
    edges = []
    adj = defaultdict(list)  # nodo_id → list de (otro_nodo_id, edge_idx)
    for s in largos:
        n1 = next(j for j, q in enumerate(puntos)
                  if hypot(s.x1 - q[0], s.y1 - q[1]) <= tol_nodo * escala)
        n2 = next(j for j, q in enumerate(puntos)
                  if hypot(s.x2 - q[0], s.y2 - q[1]) <= tol_nodo * escala)
        if n1 == n2:
            continue
        long_seg = hypot(s.x2 - s.x1, s.y2 - s.y1)
        edge_idx = len(edges)
        edges.append((n1, n2, long_seg, s))
        adj[n1].append((n2, edge_idx))
        adj[n2].append((n1, edge_idx))
    return nodos, edges, adj


def main():
    svg = Path("tools/_artifacts/validacion/cache_svg/"
               "19-000-2-02-00001 sheet 2121CD40F09-2_IS01.svg")
    arbol = inkex.load_svg(str(svg)).getroot()
    segs = _extraer_segmentos_lineales(arbol)
    print(f"Segmentos totales: {len(segs)}")

    # Probar varios umbrales
    for long_min, tol in [(4, 2.5), (4, 1.5), (8, 2.5)]:
        nodos, edges, adj = construir_grafo(segs, long_min=long_min, tol_nodo=tol)
        grados = defaultdict(int)
        for n in nodos:
            grados[len(adj[n])] += 1
        print(f"\nlong_min={long_min}pt tol={tol}: {len(nodos)} nodos, {len(edges)} edges")
        for grado in sorted(grados):
            print(f"  grado {grado}: {grados[grado]} nodos")
        # Tees (grado ≥3) — coordenadas
        tees = [(n, nodos[n]) for n in nodos if len(adj[n]) >= 3]
        if tees:
            print(f"  Tees (grado≥3):")
            for n, (x, y) in tees[:10]:
                print(f"    nodo {n}: ({x:6.1f},{y:6.1f}) grado={len(adj[n])}")

    # Análisis con long_min=4, tol=2.5: identificar componentes conexas
    nodos, edges, adj = construir_grafo(segs, long_min=4, tol_nodo=2.5)
    # BFS para componentes
    visitados = set()
    comps = []
    for n in nodos:
        if n in visitados:
            continue
        comp = set()
        stack = [n]
        while stack:
            x = stack.pop()
            if x in visitados:
                continue
            visitados.add(x)
            comp.add(x)
            for nbr, _ in adj[x]:
                if nbr not in visitados:
                    stack.append(nbr)
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    print(f"\nComponentes conexas: {len(comps)}")
    for i, c in enumerate(comps[:5]):
        n_grado3 = sum(1 for n in c if len(adj[n]) >= 3)
        n_max = max(c, key=lambda n: nodos[n][1])
        print(f"  comp #{i}: {len(c)} nodos, {n_grado3} tees, "
              f"bbox Y máx en nodo {n_max} ({nodos[n_max][0]:.1f},{nodos[n_max][1]:.1f})")
    costuras = detectar_costuras(arbol, 1.4, 2.5, 1.5)
    print(f"\n{len(costuras)} costuras detectadas. Asignando a edges:")
    for c in costuras:
        mejor_edge = None
        mejor_d = float("inf")
        for ei, (n1, n2, L, s) in enumerate(edges):
            d = _punto_a_segmento(c.x, c.y, s.x1, s.y1, s.x2, s.y2)
            if d < mejor_d:
                mejor_d = d
                mejor_edge = ei
        if mejor_edge is None:
            continue
        n1, n2, L, s = edges[mejor_edge]
        # Posición t∈[0,1] en el edge
        dx, dy = s.x2 - s.x1, s.y2 - s.y1
        t = ((c.x - s.x1) * dx + (c.y - s.y1) * dy) / (L * L) if L else 0
        print(f"  ({c.x:6.1f},{c.y:6.1f}) → edge#{mejor_edge} nodos({n1},{n2}) "
              f"L={L:.1f} t={t:.2f} d={mejor_d:.2f}")

    # Flecha
    flechas = detectar_flechas_flujo(arbol)
    print(f"\nFlechas de flujo: {len(flechas)}")
    for f in flechas:
        # Endpoint más cercano
        mejor = None
        mejor_d = float("inf")
        for nid, (x, y) in nodos.items():
            d = hypot(f.cx - x, f.cy - y)
            if d < mejor_d:
                mejor_d = d
                mejor = nid
        print(f"  ({f.cx:6.1f},{f.cy:6.1f}) dir=({f.dx:+.2f},{f.dy:+.2f}) "
              f"→ endpoint más cercano: nodo {mejor} a {mejor_d:.2f}pt")


if __name__ == "__main__":
    main()
