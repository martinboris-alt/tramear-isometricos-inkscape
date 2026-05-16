"""Recorrido topológico del grafo de tuberías para numerar costuras.

Construye el grafo a partir de los segmentos largos del SVG (la red de
tubería principal), asigna cada costura a su edge, y recorre el grafo
en DFS desde el nodo de entrada del flujo. En cada tee (grado ≥3)
prioriza la rama más corta para terminarla antes de continuar.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from math import hypot
from typing import Sequence

from .seam_detector import Costura, FlechaFlujo, Segmento


# ---------------------------------------------------------------------------
# Construcción del grafo
# ---------------------------------------------------------------------------

@dataclass
class Edge:
    n1: int
    n2: int
    longitud: float
    seg: Segmento
    costuras: list[tuple[float, Costura]] = field(default_factory=list)
    # cada elemento: (t∈[0,1] a lo largo del edge desde n1, costura)


def construir_grafo(segmentos: Sequence[Segmento],
                     long_min: float = 10.0,
                     tol_nodo: float = 2.5):
    """Devuelve (nodos, edges, adj):
      nodos: list[(x, y)] indexable por nodo_id
      edges: list[Edge]
      adj: dict nodo_id -> list of (otro_nodo_id, edge_idx)
    """
    largos = [s for s in segmentos
              if hypot(s.x2 - s.x1, s.y2 - s.y1) >= long_min]

    nodos: list[tuple[float, float]] = []

    def get_or_add(p):
        for i, q in enumerate(nodos):
            if hypot(p[0] - q[0], p[1] - q[1]) <= tol_nodo:
                return i
        nodos.append(p)
        return len(nodos) - 1

    edges: list[Edge] = []
    adj: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for s in largos:
        n1 = get_or_add((s.x1, s.y1))
        n2 = get_or_add((s.x2, s.y2))
        if n1 == n2:
            continue
        L = hypot(s.x2 - s.x1, s.y2 - s.y1)
        ei = len(edges)
        edges.append(Edge(n1, n2, L, s))
        adj[n1].append((n2, ei))
        adj[n2].append((n1, ei))
    return nodos, edges, adj


def componente_conexa(nodo_inicial: int, adj) -> set[int]:
    """BFS para obtener la componente conexa que contiene nodo_inicial."""
    visitados = set()
    cola = [nodo_inicial]
    while cola:
        n = cola.pop()
        if n in visitados:
            continue
        visitados.add(n)
        for nbr, _ in adj[n]:
            if nbr not in visitados:
                cola.append(nbr)
    return visitados


def asignar_costuras_a_edges(costuras: Sequence[Costura],
                              edges: list[Edge],
                              nodos: list[tuple[float, float]],
                              componente: set[int] | None = None) -> None:
    """Asigna cada costura al edge más cercano (de la componente, si dada).

    Modifica `edges` in-place añadiendo a `Edge.costuras` la lista de
    (t, costura) donde t∈[0,1] es la posición a lo largo del edge desde n1.
    """
    for c in costuras:
        mejor_ei = None
        mejor_d = float("inf")
        mejor_t = 0.0
        for ei, e in enumerate(edges):
            if componente is not None and (
                    e.n1 not in componente and e.n2 not in componente):
                continue
            dx = e.seg.x2 - e.seg.x1
            dy = e.seg.y2 - e.seg.y1
            L2 = dx * dx + dy * dy
            if L2 == 0:
                continue
            t = ((c.x - e.seg.x1) * dx + (c.y - e.seg.y1) * dy) / L2
            t_clamped = max(0.0, min(1.0, t))
            cx = e.seg.x1 + t_clamped * dx
            cy = e.seg.y1 + t_clamped * dy
            d = hypot(c.x - cx, c.y - cy)
            if d < mejor_d:
                mejor_d = d
                mejor_ei = ei
                mejor_t = t_clamped
        if mejor_ei is not None:
            edges[mejor_ei].costuras.append((mejor_t, c))


# ---------------------------------------------------------------------------
# DFS topológico
# ---------------------------------------------------------------------------

def nodo_entrada(flechas: Sequence[FlechaFlujo],
                  nodos: list[tuple[float, float]],
                  componente: set[int] | None = None) -> int | None:
    """Devuelve el nodo de la componente más cercano a una flecha de flujo.

    Si la flecha apunta DESDE un endpoint del segmento (es decir, la flecha
    está más allá del extremo en la dirección del flujo), el nodo de entrada
    es el OPUESTO al endpoint de la flecha (es el extremo más "aguas arriba"
    del segmento que contiene la flecha).
    """
    if not flechas or not nodos:
        return None
    # Por simplicidad: para cada flecha, encontrar el endpoint más cercano
    # (es el extremo del segmento adyacente a la flecha = SALIDA del flujo).
    # El nodo de ENTRADA es el OPUESTO de ese endpoint en el mismo segmento.
    # Aquí simplificamos: tomamos como entrada el endpoint MÁS LEJANO de la
    # flecha entre TODOS los nodos de la componente, en la dirección opuesta
    # al flujo. El que MAXIMIZA proyección sobre (-dx, -dy) es la entrada.
    f = flechas[0]
    pool = ([n for n in range(len(nodos)) if n in componente]
            if componente else list(range(len(nodos))))
    if not pool:
        return None
    # Proyección sobre vector OPUESTO al flujo (la entrada está aguas arriba)
    def proy(n):
        x, y = nodos[n]
        return x * (-f.dx) + y * (-f.dy)
    return max(pool, key=proy)


def dfs_topologico(nodo_inicial: int, adj, edges: list[Edge]
                    ) -> list[Costura]:
    """DFS desde `nodo_inicial`. En cada nodo, prioriza el edge cuya
    rama termine antes (más corta hasta llegar a un extremo grado-1).

    Devuelve costuras en orden de visita.
    """
    visitados_edges: set[int] = set()
    orden: list[Costura] = []

    def longitud_rama_desde(edge_idx: int, desde_nodo: int,
                             ya_visit: set[int]) -> float:
        """Estima la longitud total de la rama que arranca por `edge_idx`
        desde `desde_nodo`, sin reentrar a `ya_visit`.

        Útil para decidir en una tee cuál rama es más corta. Cuenta la
        longitud acumulada del DFS sobre esa subrama.
        """
        local_visit = set(ya_visit)
        local_visit.add(edge_idx)
        e = edges[edge_idx]
        siguiente = e.n2 if e.n1 == desde_nodo else e.n1
        total = e.longitud
        # Sumar longitudes de los siguientes edges, recursivamente, ELIGIENDO
        # SIEMPRE la subrama más corta primero (aproximación)
        siguientes = [ei for nbr, ei in adj[siguiente]
                       if ei not in local_visit]
        for ei in siguientes:
            total += longitud_rama_desde(ei, siguiente, local_visit)
        return total

    def visitar_edge(edge_idx: int, desde_nodo: int):
        if edge_idx in visitados_edges:
            return
        visitados_edges.add(edge_idx)
        e = edges[edge_idx]
        # Ordenar costuras del edge según dirección de recorrido (desde
        # desde_nodo). Si entramos por n1, t va creciendo; si por n2,
        # decreciendo.
        if desde_nodo == e.n1:
            costuras_orden = sorted(e.costuras, key=lambda tc: tc[0])
        else:
            costuras_orden = sorted(e.costuras, key=lambda tc: -tc[0])
        for _, c in costuras_orden:
            orden.append(c)
        siguiente = e.n2 if e.n1 == desde_nodo else e.n1
        # Edges salientes del siguiente nodo
        salientes = [ei for nbr, ei in adj[siguiente]
                      if ei not in visitados_edges]
        # Ordenar por longitud de rama ASCENDENTE (rama más corta primero)
        salientes.sort(key=lambda ei: longitud_rama_desde(
            ei, siguiente, visitados_edges))
        for ei in salientes:
            visitar_edge(ei, siguiente)

    # Empezar por todos los edges adyacentes al nodo inicial, en orden
    # de rama más corta primero
    salientes_iniciales = [ei for _, ei in adj[nodo_inicial]]
    salientes_iniciales.sort(key=lambda ei: longitud_rama_desde(
        ei, nodo_inicial, set()))
    for ei in salientes_iniciales:
        visitar_edge(ei, nodo_inicial)

    return orden


def numerar_por_proximidad(costuras: Sequence[Costura],
                            flechas: Sequence[FlechaFlujo],
                            numero_inicial: int = 1,
                            ) -> list[tuple[int, Costura]]:
    """Numera costuras con DFS sobre el grafo de proximidad entre costuras.

    Más robusto que el grafo topológico cuando los codos curvos
    fragmentan la red de segmentos rectos. Funciona así:

    1. Empieza por la costura más "aguas arriba" según la flecha (la
       que MINIMIZA la proyección sobre el vector del flujo).
    2. DFS: para el nodo actual, ordenar costuras NO VISITADAS por
       distancia ASCENDENTE. Elegir la primera Y completar TODA su
       subrama antes de pasar a la siguiente.

    Es greedy pero respetando ramificaciones: si una costura no
    visitada está MUY cerca de la actual (a < cluster_max), se considera
    "misma rama"; si está más lejos, se considera "siguiente rama" y
    se difiere hasta agotar la rama actual.

    Funciona bien cuando las costuras de una misma rama están
    encadenadas por proximidad (caso típico).
    """
    if not costuras or not flechas:
        return []
    f = flechas[0]
    # Costura más aguas arriba: minimiza proyección sobre (dx, dy)
    inicio = min(range(len(costuras)),
                  key=lambda i: costuras[i].x * f.dx + costuras[i].y * f.dy)

    # 1. Greedy puro tipo TSP: empezar en `inicio`, siempre ir a la
    #    costura no visitada más cercana.
    visitados: set[int] = {inicio}
    orden: list[int] = [inicio]
    actual = inicio
    while len(visitados) < len(costuras):
        candidatos = [(hypot(costuras[actual].x - costuras[j].x,
                              costuras[actual].y - costuras[j].y), j)
                       for j in range(len(costuras)) if j not in visitados]
        d_min, siguiente = min(candidatos)
        orden.append(siguiente)
        visitados.add(siguiente)
        actual = siguiente

    # 2. Detectar saltos anómalos: la transición orden[i-1]→orden[i] cuya
    #    longitud es MUCHO mayor que la mediana de saltos del recorrido.
    #    Las costuras al INICIO de un salto anómalo son "huérfanas" —
    #    el greedy las visitó al final porque están en una rama lateral.
    saltos = []
    for i in range(1, len(orden)):
        a = costuras[orden[i - 1]]
        b = costuras[orden[i]]
        saltos.append(hypot(a.x - b.x, a.y - b.y))
    if not saltos:
        return [(numero_inicial + i, costuras[k])
                for i, k in enumerate(orden)]
    saltos_ord = sorted(saltos)
    mediana_salto = saltos_ord[len(saltos_ord) // 2]
    # Salto anómalo = > 2.5× mediana Y > 80pt absoluto (evita falsas
    # alertas en pequeñas variaciones).
    umbral_anomalo = max(80.0, 2.5 * mediana_salto)

    huerfanos_idx: list[int] = []  # índices DENTRO de `orden`
    for i in range(1, len(orden)):
        if saltos[i - 1] > umbral_anomalo:
            huerfanos_idx.append(i)

    if not huerfanos_idx:
        return [(numero_inicial + i, costuras[k])
                for i, k in enumerate(orden)]

    # 3. Re-insertar cada huérfana en su MEJOR posición: la que minimiza
    #    la distancia a su nuevo vecino del orden principal. La idea es
    #    que la huérfana representa una rama lateral; debe colocarse
    #    JUSTO DESPUÉS de la costura del tramo principal más cercana.
    huerfanos = [orden[i] for i in huerfanos_idx]
    orden_filtrado = [k for i, k in enumerate(orden) if i not in huerfanos_idx]

    # Distancia mediana entre costuras vecinas (para distinguir
    # "cluster-jump" de "rama lateral aislada")
    dmin_por_costura = []
    for i, a in enumerate(costuras):
        d_min = min((hypot(a.x - b.x, a.y - b.y)
                     for j, b in enumerate(costuras) if i != j),
                    default=0)
        dmin_por_costura.append(d_min)
    dmin_por_costura.sort()
    dist_local = (dmin_por_costura[len(dmin_por_costura) // 2]
                   if dmin_por_costura else 1.0)

    def _coste_insertar(h_idx: int, pos: int, lst: list[int]) -> float:
        """Costo de insertar `h_idx` en posición `pos` del recorrido `lst`:
        suma de tramos nuevos menos el tramo que se rompe."""
        ch = costuras[h_idx]
        if pos == 0:
            # Al principio: solo añade dist(h, lst[0])
            return hypot(ch.x - costuras[lst[0]].x,
                          ch.y - costuras[lst[0]].y)
        if pos >= len(lst):
            # Al final: solo añade dist(lst[-1], h)
            return hypot(costuras[lst[-1]].x - ch.x,
                          costuras[lst[-1]].y - ch.y)
        # En el medio: rompe el tramo lst[pos-1]→lst[pos]
        a = costuras[lst[pos - 1]]
        b = costuras[lst[pos]]
        d_ah = hypot(a.x - ch.x, a.y - ch.y)
        d_hb = hypot(ch.x - b.x, ch.y - b.y)
        d_ab = hypot(a.x - b.x, a.y - b.y)
        return d_ah + d_hb - d_ab

    for h in huerfanos:
        ch = costuras[h]
        # ¿Es rama lateral aislada? Su vecino más cercano del orden
        # principal está >>5× la distancia mediana entre vecinas.
        d_vecino_min = min(
            (hypot(ch.x - costuras[orden_filtrado[i]].x,
                    ch.y - costuras[orden_filtrado[i]].y)
             for i in range(len(orden_filtrado))),
            default=0,
        )
        es_rama_lateral = d_vecino_min > 5.0 * dist_local
        if es_rama_lateral:
            # Insertar TRAS el vecino más cercano (convención del usuario:
            # "tras la tee, completar la rama lateral")
            mejor_i = min(range(len(orden_filtrado)),
                           key=lambda i: hypot(
                               ch.x - costuras[orden_filtrado[i]].x,
                               ch.y - costuras[orden_filtrado[i]].y))
            orden_filtrado.insert(mejor_i + 1, h)
        else:
            # Cluster-jump: usar inserción óptima por mínimo costo
            mejor_pos = min(range(len(orden_filtrado) + 1),
                             key=lambda p: _coste_insertar(
                                 h, p, orden_filtrado))
            orden_filtrado.insert(mejor_pos, h)

    return [(numero_inicial + i, costuras[k])
            for i, k in enumerate(orden_filtrado)]


def numerar_por_grafo(costuras: Sequence[Costura],
                       segmentos: Sequence[Segmento],
                       flechas: Sequence[FlechaFlujo],
                       numero_inicial: int = 1,
                       long_min_segmento: float = 10.0,
                       tol_nodo: float = 2.5
                       ) -> list[tuple[int, Costura]]:
    """Numera costuras siguiendo el grafo topológico de la tubería.

    1. Construye grafo con segmentos largos.
    2. Identifica componente conexa con la flecha de flujo.
    3. Asigna costuras a edges.
    4. DFS desde nodo de entrada, priorizando ramas más cortas en tees.

    Si falla (sin flechas, sin componente, etc.), devuelve [].
    """
    if not costuras or not flechas:
        return []
    nodos, edges, adj = construir_grafo(
        segmentos, long_min=long_min_segmento, tol_nodo=tol_nodo)
    if not edges:
        return []
    # Nodo más cercano a la flecha (es la SALIDA del flujo en ese segmento)
    f = flechas[0]
    nodo_cerca_flecha = min(range(len(nodos)),
                             key=lambda n: hypot(nodos[n][0] - f.cx,
                                                  nodos[n][1] - f.cy))
    componente = componente_conexa(nodo_cerca_flecha, adj)
    if not componente:
        return []
    asignar_costuras_a_edges(costuras, edges, nodos, componente)
    entrada = nodo_entrada(flechas, nodos, componente)
    if entrada is None:
        return []
    orden = dfs_topologico(entrada, adj, edges)
    # Añadir costuras no asignadas al final (no deberían existir pero por
    # seguridad)
    pendientes = [c for c in costuras if c not in orden]
    orden.extend(pendientes)
    return [(numero_inicial + i, c) for i, c in enumerate(orden)]
