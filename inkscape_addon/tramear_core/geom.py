"""Geometría pura, reusable fuera del entorno Inkscape.

Estas funciones operan sobre tipos genéricos (tuplas, listas) y NO
dependen de inkex. Las usa tanto el addon (`seam_detector.py`) como las
herramientas CLI de validación (`tools/`).
"""

from __future__ import annotations

from math import atan2, degrees, hypot


# --- Distancia punto-segmento ---------------------------------------------

def punto_a_segmento(px: float, py: float,
                     x1: float, y1: float,
                     x2: float, y2: float) -> float:
    dx, dy = x2 - x1, y2 - y1
    long2 = dx * dx + dy * dy
    if long2 == 0:
        return hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / long2
    t = max(0.0, min(1.0, t))
    qx = x1 + t * dx
    qy = y1 + t * dy
    return hypot(px - qx, py - qy)


def dist_y_t(px: float, py: float,
             x1: float, y1: float,
             x2: float, y2: float) -> tuple[float, float]:
    """Devuelve (distancia_al_segmento, parámetro_t).

    El parámetro `t` (sin clamping a [0,1]) indica dónde cae la
    proyección del punto sobre la recta del segmento: 0 = inicio,
    1 = final, intermedio = interior, fuera de [0,1] = más allá de
    alguno de los extremos.
    """
    dx, dy = x2 - x1, y2 - y1
    long2 = dx * dx + dy * dy
    if long2 == 0:
        return hypot(px - x1, py - y1), 0.0
    t_raw = ((px - x1) * dx + (py - y1) * dy) / long2
    t_clamp = max(0.0, min(1.0, t_raw))
    qx = x1 + t_clamp * dx
    qy = y1 + t_clamp * dy
    return hypot(px - qx, py - qy), t_raw


# --- Detección de patrón X ------------------------------------------------

def hay_x_cerca(cx: float, cy: float, radio_circulo: float,
                segmentos, *,
                long_min: float = 1.5, long_max: float = 7.0,
                ang_min: float = 70.0, ang_max: float = 110.0,
                ratio_long_min: float = 0.6,
                tol_cruce: float | None = None) -> bool:
    """Hay dos segmentos CORTOS Y SIMÉTRICOS que se cruzan ~90° cerca del centro.

    Aspa real de field weld: dos rayas cortas de longitud similar que se
    cruzan formando un ángulo cercano a 90°, con la intersección sobre
    o muy cerca del punto de soldadura.

    Para reducir falsos positivos por codos isométricos (donde dos
    tuberías largas se cruzan en el punto) exigimos:
      - longitudes pequeñas (long_min..long_max, por defecto 1.5–7 pt)
      - longitudes parecidas (ratio min/max >= ratio_long_min)
      - ángulo casi 90° (70–110°)
      - intersección dentro de `tol_cruce` del centro del círculo
    """
    if tol_cruce is None:
        tol_cruce = max(radio_circulo * 1.5, 2.5)

    radio_busqueda = max(radio_circulo * 4.0, 5.0)
    cercanos: list[tuple] = []
    longitudes: list[float] = []
    for x1, y1, x2, y2 in segmentos:
        L = hypot(x2 - x1, y2 - y1)
        if not (long_min <= L <= long_max):
            continue
        mx = (x1 + x2) / 2
        my = (y1 + y2) / 2
        if hypot(mx - cx, my - cy) <= radio_busqueda:
            cercanos.append((x1, y1, x2, y2))
            longitudes.append(L)

    if len(cercanos) < 2:
        return False

    angulos = [_angulo(s) for s in cercanos]
    for i, a1 in enumerate(angulos):
        for j in range(i + 1, len(angulos)):
            delta = abs(a1 - angulos[j])
            if not (ang_min < delta < ang_max):
                continue
            l1, l2 = longitudes[i], longitudes[j]
            if min(l1, l2) / max(l1, l2) < ratio_long_min:
                continue
            inter = _interseccion_rectas(cercanos[i], cercanos[j])
            if inter is None:
                continue
            ix, iy = inter
            if hypot(ix - cx, iy - cy) <= tol_cruce:
                return True
    return False


def hay_triangulo_cerca(cx: float, cy: float, segmentos,
                         *, long_max: float = 10.0,
                         tol_vertice: float = 0.8,
                         radio_busqueda: float | None = None) -> bool:
    """¿Hay 3 segmentos cortos cuyos extremos forman un triángulo cerrado,
    con el punto (cx, cy) dentro o muy cerca de ese triángulo?

    Una punta de flecha real está dibujada como un triángulo CERRADO
    (M-L-L-Z). Lo distingue de una X / aspa de soldadura, que son 2
    segmentos cruzados sin extremos compartidos.

    `tol_vertice` es la distancia máxima a la que dos extremos se
    consideran "el mismo vértice" del triángulo.
    """
    if radio_busqueda is None:
        radio_busqueda = long_max * 2

    cortos: list[tuple] = []
    for s in segmentos:
        x1, y1, x2, y2 = s[0], s[1], s[2], s[3]
        L = hypot(x2 - x1, y2 - y1)
        if not (0.5 <= L <= long_max):
            continue
        mx = (x1 + x2) / 2
        my = (y1 + y2) / 2
        if hypot(mx - cx, my - cy) > radio_busqueda:
            continue
        cortos.append((x1, y1, x2, y2))

    if len(cortos) < 3:
        return False

    n = len(cortos)
    for i in range(n):
        for j in range(i + 1, n):
            v_ij = _vertice_compartido(cortos[i], cortos[j], tol_vertice)
            if v_ij is None:
                continue
            for k in range(j + 1, n):
                v_jk = _vertice_compartido(cortos[j], cortos[k], tol_vertice)
                if v_jk is None or _muy_cerca(v_jk, v_ij, tol_vertice):
                    continue
                v_ik = _vertice_compartido(cortos[i], cortos[k], tol_vertice)
                if v_ik is None or _muy_cerca(v_ik, v_ij, tol_vertice) \
                        or _muy_cerca(v_ik, v_jk, tol_vertice):
                    continue
                # Hay triángulo cerrado entre los 3 segmentos.
                # Verificar que el círculo cae dentro o muy cerca.
                if _punto_en_o_cerca_triangulo(cx, cy, v_ij, v_jk, v_ik,
                                                tol=tol_vertice * 2):
                    return True
    return False


def _vertice_compartido(s1, s2, tol):
    """Devuelve el (x, y) común a dos segmentos, o None."""
    extremos_1 = ((s1[0], s1[1]), (s1[2], s1[3]))
    extremos_2 = ((s2[0], s2[1]), (s2[2], s2[3]))
    for p in extremos_1:
        for q in extremos_2:
            if hypot(p[0] - q[0], p[1] - q[1]) <= tol:
                return ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
    return None


def _muy_cerca(p, q, tol) -> bool:
    return hypot(p[0] - q[0], p[1] - q[1]) < tol


def _punto_en_o_cerca_triangulo(px, py, a, b, c, *, tol: float = 1.0) -> bool:
    """Test de punto dentro de triángulo + tolerancia exterior."""
    # Coordenadas baricéntricas
    denom = ((b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1]))
    if denom == 0:
        return False
    u = ((b[1] - c[1]) * (px - c[0]) + (c[0] - b[0]) * (py - c[1])) / denom
    v = ((c[1] - a[1]) * (px - c[0]) + (a[0] - c[0]) * (py - c[1])) / denom
    w = 1 - u - v
    # Si u,v,w ∈ [0,1] → dentro. Pequeña tolerancia para borde.
    margen = tol / 10
    return -margen <= u <= 1 + margen and -margen <= v <= 1 + margen and -margen <= w <= 1 + margen


def _angulo(s) -> float:
    return degrees(atan2(s[3] - s[1], s[2] - s[0])) % 180


def _interseccion_rectas(a, b):
    x1, y1, x2, y2 = a
    x3, y3, x4, y4 = b
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if denom == 0:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
