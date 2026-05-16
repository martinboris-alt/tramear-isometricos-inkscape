"""Numeración de costuras detectadas.

Si se proporciona la flecha de sentido de flujo del isométrico,
numeramos siguiendo el flujo (T-1 = primera costura en el sentido del
flujo). Si no, fallback a barrido de plano por filas.
"""

from __future__ import annotations

from typing import Sequence

from .seam_detector import Costura, FlechaFlujo


def numerar_por_flujo(
    costuras: Sequence[Costura],
    flechas: Sequence[FlechaFlujo],
    numero_inicial: int = 1,
) -> list[tuple[int, Costura]]:
    """Numera costuras siguiendo el sentido del flujo.

    Estrategia simple: usar la flecha promedio (o única) como vector
    de dirección, proyectar cada costura sobre el vector OPUESTO al
    flujo (porque T-1 = "más arriba del flujo" = la primera a ser
    alcanzada por el fluido viniendo de aguas arriba). Ordenar por
    proyección ascendente.

    Si no hay flechas, devuelve lista vacía — el caller debe usar el
    fallback (`numerar_costuras` por barrido).
    """
    if not costuras or not flechas:
        return []
    # Vector promedio del flujo (por si hay varias flechas)
    dx = sum(f.dx for f in flechas) / len(flechas)
    dy = sum(f.dy for f in flechas) / len(flechas)
    norma = (dx * dx + dy * dy) ** 0.5
    if norma <= 0:
        return []
    dx /= norma
    dy /= norma
    # T-1 = aguas arriba (el fluido la encuentra primero) = la costura
    # MÁS LEJOS en la dirección OPUESTA al flujo = la de MENOR
    # proyección sobre (dx, dy).
    # Ejemplo: flujo (dx=0, dy=-1, hacia arriba en pantalla) →
    # ordenar por c.y · (-1) ascendente → mayor c.y (abajo) primero.
    ordenadas = sorted(costuras,
                       key=lambda c: c.x * dx + c.y * dy)
    return [(numero_inicial + i, c) for i, c in enumerate(ordenadas)]


def numerar_costuras(
    costuras: Sequence[Costura],
    numero_inicial: int = 1,
    tolerancia_fila: float | None = None,
) -> list[tuple[int, Costura]]:
    """Asigna un número a cada costura ordenando por filas.

    `tolerancia_fila` define qué tan cercanas en Y deben estar dos
    costuras para considerarse en la misma fila. Si es None, se calcula
    como 2 × el radio medio.
    """
    if not costuras:
        return []

    if tolerancia_fila is None:
        radios = [c.radio for c in costuras if c.radio > 0]
        tolerancia_fila = 2.0 * (sum(radios) / len(radios)) if radios else 1.0

    por_y = sorted(costuras, key=lambda c: c.y)

    # Comparar con la MEDIA Y de la fila acumulada, no con el último
    # elemento: si los costuras de la fila divergen lentamente en Y,
    # comparar con el último introduce drift acumulativo y agrupa mal
    # en planos densos.
    filas: list[list[Costura]] = [[por_y[0]]]
    medias_y: list[float] = [por_y[0].y]
    for c in por_y[1:]:
        if abs(c.y - medias_y[-1]) <= tolerancia_fila:
            filas[-1].append(c)
            medias_y[-1] = sum(x.y for x in filas[-1]) / len(filas[-1])
        else:
            filas.append([c])
            medias_y.append(c.y)

    ordenadas: list[Costura] = []
    for fila in filas:
        fila.sort(key=lambda c: c.x)
        ordenadas.extend(fila)

    return [(numero_inicial + i, c) for i, c in enumerate(ordenadas)]


def numerar_por_recorrido(costuras, segmentos):
    """Stub: numeración siguiendo conectividad de la red de tuberías.

    Pendiente de implementar en v2. Por ahora, delega en
    `numerar_costuras` para no romper la API.
    """
    return numerar_costuras(costuras)
