"""Detector de costuras (modo vectorial).

Recorre el árbol SVG en busca de marcadores circulares de costura
y los valida comprobando que están sobre el eje de una tubería.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import hypot, sqrt
from typing import Iterable

import inkex
from inkex import Circle, Ellipse, PathElement, Transform

from .geom import hay_triangulo_cerca


# ---------------------------------------------------------------------------
# Tipos de datos
# ---------------------------------------------------------------------------

@dataclass
class FlechaFlujo:
    """Flecha de sentido de flujo detectada sobre una tubería.

    `cx, cy` es el centroide de la flecha (la PUNTA).
    `dx, dy` es el vector unitario en la DIRECCIÓN del flujo (desde
    el segmento hacia la punta de la flecha).
    """
    cx: float
    cy: float
    dx: float
    dy: float


@dataclass
class Costura:
    x: float
    y: float
    radio: float
    fuente_id: str = ""
    sobre_linea: bool = False
    distancia_linea: float = float("inf")
    tipo: str = "W"           # "W" (welded) | "T" (tubing - press fit)
    relleno: bool = False        # True si el marcador es un círculo relleno
    forma: str = "redondo"       # "redondo" (polígono/bézier ≥8 lados) |
                                 # "angular" (≤4 lados: triángulo/cuadrado)

    def __iter__(self):
        yield self.x
        yield self.y


@dataclass
class Segmento:
    x1: float
    y1: float
    x2: float
    y2: float
    fuente_id: str = ""


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def detectar_costuras(
    svg,
    radio_min: float,
    radio_max: float,
    tolerancia_linea: float,
    solo_sobre_linea: bool = True,
    filtrar_puntas_flecha: bool = True,
    solo_rellenos: bool = True,
    detectar_field_welds: bool = False,  # legado, ignorado
) -> list[Costura]:
    """Detecta costuras vectoriales en el árbol SVG.

    `solo_rellenos`: por convención Repsol toda costura (shop, field,
    wendolet, tubing) es un símbolo NEGRO RELLENO. Activarlo elimina
    falsos positivos de paths con sólo contorno (texto rasterizado,
    bordes de cajetín, símbolos abstractos del BOM). Default True.

    `detectar_field_welds` se conserva en la firma por compatibilidad
    con código que ya lo pasa; el detector no marca FW (toda costura
    soldada se etiqueta como "W").

    Los umbrales (`radio_min/max`, `tolerancia_linea`, distancias de
    wendolet, dedup, triángulo) se reciben en PUNTOS PDF y se escalan
    internamente a las unidades de usuario del SVG. Esto hace que el
    detector funcione igual con SVG de Poppler (1 uu = 1 pt) que con
    SVG del importador interno de Inkscape (1 pt = 1.333 uu, 96 dpi).
    """
    escala = _escala_pt_a_uu(svg)
    radio_min_uu = radio_min * escala
    radio_max_uu = radio_max * escala
    tol_linea_uu = tolerancia_linea * escala

    candidatos = _buscar_circulos(svg, radio_min_uu, radio_max_uu)
    if solo_rellenos:
        candidatos = [c for c in candidatos if c.relleno]
    segmentos = _extraer_segmentos_lineales(svg)

    # Solo cogemos los círculos "sobre línea" como costuras reales. La
    # red de tubería se aproxima por los segmentos largos detectados.
    candidatos_en_linea: list[Costura] = []
    for c in candidatos:
        d_min = _distancia_minima_a_segmentos(c.x, c.y, segmentos)
        c.distancia_linea = d_min
        c.sobre_linea = d_min <= tol_linea_uu
        if solo_sobre_linea and not c.sobre_linea:
            continue
        candidatos_en_linea.append(c)

    resultado = _deduplicar(candidatos_en_linea, tolerancia=1.0 * escala)

    # Filtrar puntas de flecha de cota o soportación. Una costura real
    # tiene segmentos LARGOS pasando por encima (en mitad del segmento);
    # una punta de flecha está al EXTREMO de un segmento corto.
    if filtrar_puntas_flecha and resultado:
        resultado = _filtrar_puntas_flecha(resultado, segmentos,
                                            tolerancia=tol_linea_uu,
                                            long_max_triangulo=10.0 * escala)

    # Recuperar wendolets/picajes: el filtro anterior descarta el punto
    # inferior del par porque el triángulo del injerto cerca lo confunde
    # con punta de flecha. Lo reintroducimos buscando pares mutuos.
    resultado = _anadir_wendolets(
        resultado, _deduplicar(candidatos_en_linea, tolerancia=1.0 * escala),
        dist_min=6.0 * escala, dist_max=10.0 * escala,
        diff_radio_max=0.15 * escala, tol_alineacion=1.5 * escala,
        dist_dup=1.0 * escala,
    )

    # Detectar costuras tubing socket weld (paralelogramos negros).
    # pdftocairo dibuja el paralelogramo como un hatching denso de
    # subpaths M-L cortos solapados. Las detectamos como clusters
    # espaciales compactos sobre tubería principal.
    tubing = _detectar_tubing_socket_welds(svg, segmentos, escala)
    # Dedup contra costuras ya detectadas (la misma ubicación no debe
    # aparecer dos veces como W y T)
    for t in tubing:
        if any(hypot(t.x - r.x, t.y - r.y) <= 3.0 * escala for r in resultado):
            continue
        resultado.append(t)

    # Filtro válvulas: aplicar AL FINAL sobre lista combinada W+T para
    # que el discriminador pueda ver vecinas W cerca de una T sospechosa
    # (caso conexión bridada). Hacerlo en _detectar_tubing_socket_welds
    # solo veía las T y se perdía esta señal.
    resultado = _filtrar_valvulas(
        resultado,
        dist_min=4.0 * escala, dist_max=15.0 * escala,
        tol_centro=2.0 * escala,
        dist_aislada=50.0 * escala,
    )

    _ = detectar_field_welds  # legado
    return resultado


def _escala_pt_a_uu(svg) -> float:
    """Unidades de usuario del SVG por cada punto PDF.

    Devuelve 1.0 para SVG de Poppler (viewBox en pt) y ~1.333 para el
    importador interno de Inkscape (viewBox en px a 96 dpi). Se usa
    para escalar todos los umbrales del detector, que están en pt.
    """
    try:
        escala = float(svg.unittouu("1pt"))
        if escala > 0:
            return escala
    except Exception:
        pass
    return 1.0


def _filtrar_puntas_flecha(
    costuras: list[Costura],
    segmentos: list[Segmento],
    *,
    tolerancia: float,
    long_max_triangulo: float = 10.0,
) -> list[Costura]:
    """Descarta círculos que son la punta de una flecha de cota o
    anotación.

    Estrategia: solo descartamos si (a) la forma del candidato es
    ANGULAR (≤4 lados — un triángulo/cuadrado, como toda punta de
    flecha) Y (b) hay evidencia POSITIVA de flecha (un triángulo
    cerrado de 3 segmentos cortos cerca).

    Un candidato REDONDO (polígono ≥8 lados o bézier) nunca se descarta:
    una costura real es siempre redonda, mientras que las puntas de
    flecha son triangulares. Esto evita el falso negativo de descartar
    una costura legítima solo porque un leader de anotación termina
    apuntando a ella (caso muy común: "STEM SOUTH →", "SEE ISO →").
    """
    salida: list[Costura] = []
    segs_tup = [(s.x1, s.y1, s.x2, s.y2) for s in segmentos]
    for c in costuras:
        if c.forma == "angular" and hay_triangulo_cerca(
                c.x, c.y, segs_tup, long_max=long_max_triangulo):
            continue
        salida.append(c)
    return salida


def _anadir_wendolets(
    costuras: list[Costura],
    candidatos: list[Costura],
    *,
    dist_min: float = 6.0,
    dist_max: float = 10.0,
    diff_radio_max: float = 0.15,
    tol_alineacion: float = 1.5,
    dist_dup: float = 1.0,
) -> list[Costura]:
    """Recupera segundos puntos de wendolet/picaje.

    Para cada costura conservada, busca otro candidato bruto en el
    rango `[dist_min, dist_max]` pt con radio similar y relleno, sobre
    línea, ALINEADO VERTICAL u HORIZONTALMENTE (|Δx| < tol o |Δy| < tol)
    y que NO esté ya en `costuras`. Si lo encuentra y es a su vez el
    más cercano de la costura original (par mutuo), lo añade.

    La alineación es la clave para distinguir un par real de injerto
    (dibujado siempre sobre el eje de la tubería) de un falso positivo
    (un círculo espurio en ángulo arbitrario).
    """
    if not costuras or not candidatos:
        return costuras

    elegibles = [c for c in candidatos if c.relleno and c.sobre_linea]

    def alineado(a: Costura, b: Costura) -> bool:
        return abs(a.x - b.x) < tol_alineacion or abs(a.y - b.y) < tol_alineacion

    def mejor_par(centro: Costura, pool: list[Costura]) -> "Costura | None":
        mejor = None
        mejor_d = float("inf")
        for o in pool:
            if o is centro:
                continue
            if abs(o.radio - centro.radio) > diff_radio_max:
                continue
            if not alineado(centro, o):
                continue
            d = hypot(o.x - centro.x, o.y - centro.y)
            if dist_min <= d <= dist_max and d < mejor_d:
                mejor = o
                mejor_d = d
        return mejor

    ya = list(costuras)
    pool = ya + elegibles
    nuevas: list[Costura] = []
    for c in costuras:
        cand = mejor_par(c, elegibles)
        if cand is None:
            continue
        if any(hypot(cand.x - o.x, cand.y - o.y) < dist_dup
               for o in ya + nuevas):
            continue
        # Mutualidad: la pareja del candidato debe ser `c`.
        espejo = mejor_par(cand, pool)
        if espejo is c:
            nuevas.append(cand)

    return costuras + nuevas


def _deduplicar(costuras: list[Costura], tolerancia: float = 1.0) -> list[Costura]:
    """Fusiona costuras a misma posición (típico en PDFs con relleno + contorno
    superpuestos). Conserva la que tenga relleno, o si empatan, la primera."""
    out: list[Costura] = []
    for c in costuras:
        dup = next((o for o in out
                    if hypot(o.x - c.x, o.y - c.y) <= tolerancia), None)
        if dup is None:
            out.append(c)
        elif c.relleno and not dup.relleno:
            out[out.index(dup)] = c
    return out


# ---------------------------------------------------------------------------
# Detección de círculos candidatos
# ---------------------------------------------------------------------------

def _buscar_circulos(svg, radio_min: float, radio_max: float) -> list[Costura]:
    out: list[Costura] = []

    for el in svg.iter():
        # Saltarse contenido de <defs> (glyphs de fuentes pdftocairo, gradientes,
        # markers, etc.). En PDFs cairo los glyphs son paths pequeños con bbox
        # cuasi-cuadrada que disparaban centenares de falsos positivos al origen.
        if _en_defs(el):
            continue
        try:
            datos = _radio_centro_global(el)
        except Exception:
            continue
        if datos is None:
            continue
        radio, cx, cy, tipo, forma = datos

        if not (radio_min <= radio <= radio_max):
            continue

        out.append(Costura(
            x=cx, y=cy, radio=radio,
            fuente_id=el.get("id", "") or "",
            relleno=_es_relleno(el),
            tipo=tipo,
            forma=forma,
        ))
    return out


def _es_relleno(el) -> bool:
    """Heurística: el elemento tiene un fill no nulo distinto de blanco."""
    try:
        estilo = dict(el.style) if hasattr(el, "style") else {}
    except Exception:
        estilo = {}
    fill = (estilo.get("fill") or el.get("fill") or "").strip().lower()
    if not fill or fill == "none":
        return False
    if fill in ("#ffffff", "#fff", "white"):
        return False
    return True


def _radio_centro_global(el):
    """Devuelve (radio, cx, cy, tipo, forma) en coordenadas GLOBALES, o None.

    - tipo: "W" por defecto (costura soldada / círculo), "T" para paths
      con 4 lados rectos (tubing).
    - forma: "redondo" (Circle/Ellipse, o path con ≥8 lados / ≥6 curvas)
      | "angular" (path de ≤4 lados: triángulo o cuadrado). El detector
      usa esto para no confundir una costura redonda con una punta de
      flecha (que siempre es triangular).

    Aplica `composed_transform()` al centro y al radio para devolver
    siempre valores en el espacio del SVG raíz, sin mutar el elemento.
    """
    if isinstance(el, Circle):
        r_local = float(el.get("r") or 0)
        cx_local = float(el.get("cx") or 0)
        cy_local = float(el.get("cy") or 0)
        ctm = _ctm_seguro(el)
        cx, cy = _transformar_punto(ctm, cx_local, cy_local)
        return (r_local * _escala_uniforme(ctm), cx, cy, "W", "redondo")

    if isinstance(el, Ellipse):
        rx = float(el.get("rx") or 0)
        ry = float(el.get("ry") or 0)
        if rx <= 0 or ry <= 0:
            return None
        if min(rx, ry) / max(rx, ry) < 0.85:
            return None
        cx_local = float(el.get("cx") or 0)
        cy_local = float(el.get("cy") or 0)
        ctm = _ctm_seguro(el)
        cx, cy = _transformar_punto(ctm, cx_local, cy_local)
        return ((rx + ry) / 2 * _escala_uniforme(ctm), cx, cy, "W", "redondo")

    if isinstance(el, PathElement):
        # Pasar composed_transform() a bounding_box() devuelve el bbox
        # ya en coordenadas globales. Sin él, inkex devuelve coords
        # locales y nos arriesgamos a coords erróneas dentro de <g>
        # con transforms (típico en SVG importado de PDF).
        try:
            ctm = el.composed_transform()
        except Exception:
            ctm = None
        try:
            bbox = el.bounding_box(ctm) if ctm is not None else el.bounding_box()
        except Exception:
            return None
        if bbox is None:
            return None
        ancho, alto = bbox.width, bbox.height
        if ancho <= 0 or alto <= 0:
            return None
        if min(ancho, alto) / max(ancho, alto) < 0.85:
            return None

        tipo = "W"
        forma = "redondo"
        try:
            cmds = list(el.path)
            n_lineas = sum(1 for c in cmds if c.letter in ("L", "H", "V"))
            n_curvas = sum(1 for c in cmds
                           if c.letter in ("C", "Q", "S", "T", "A"))
            if n_lineas == 4 and n_curvas == 0:
                tipo = "T"
            # Forma: una costura real es un polígono de ≥8 lados o una
            # curva bézier (≥6 comandos C/Q). Triángulos/cuadrados de
            # ≤4 lados rectos son puntas de flecha o marcadores tubing.
            if n_lineas >= 8 or n_curvas >= 6:
                forma = "redondo"
            else:
                forma = "angular"
        except Exception:
            pass

        return ((ancho + alto) / 4, bbox.center_x, bbox.center_y, tipo, forma)

    return None


# ---------------------------------------------------------------------------
# Extracción de segmentos de tubería
# ---------------------------------------------------------------------------

def _extraer_segmentos_lineales(svg) -> list[Segmento]:
    """Saca los segmentos rectos de todos los paths del SVG.

    Las tuberías en un isométrico son trazos rectos; ignoramos las curvas.
    """
    segs: list[Segmento] = []

    for el in svg.iter():
        if not isinstance(el, PathElement):
            continue
        if _en_defs(el):
            continue

        try:
            path = el.path.to_absolute()
        except Exception:
            continue

        ctm = _ctm_seguro(el)
        prev = None
        inicio_subpath = None
        fuente_id = el.get("id", "") or ""

        for cmd in path:
            letra = cmd.letter
            if letra == "M":
                p = (cmd.args[0], cmd.args[1])
                inicio_subpath = p
                prev = p
            elif letra in ("L", "H", "V"):
                if letra == "H":
                    p = (cmd.args[0], prev[1] if prev else 0)
                elif letra == "V":
                    p = (prev[0] if prev else 0, cmd.args[0])
                else:
                    p = (cmd.args[0], cmd.args[1])
                if prev is not None:
                    segs.append(_segmento_transformado(prev, p, ctm, fuente_id))
                prev = p
            elif letra == "Z":
                if prev is not None and inicio_subpath is not None:
                    segs.append(_segmento_transformado(prev, inicio_subpath, ctm, fuente_id))
                prev = inicio_subpath
            else:
                # C, Q, A, S, T... ignoradas para tuberías rectas. Solo
                # avanzamos prev al endpoint final del comando.
                if cmd.args and len(cmd.args) >= 2:
                    prev = (cmd.args[-2], cmd.args[-1])

    return segs


def _segmento_transformado(p1, p2, ctm, fuente_id) -> Segmento:
    x1, y1 = _transformar_punto(ctm, p1[0], p1[1])
    x2, y2 = _transformar_punto(ctm, p2[0], p2[1])
    return Segmento(x1, y1, x2, y2, fuente_id)


# ---------------------------------------------------------------------------
# Geometría
# ---------------------------------------------------------------------------

def _distancia_minima_a_segmentos(px: float, py: float,
                                  segmentos: Iterable[Segmento]) -> float:
    mejor = float("inf")
    for s in segmentos:
        d = _punto_a_segmento(px, py, s.x1, s.y1, s.x2, s.y2)
        if d < mejor:
            mejor = d
            if mejor == 0:
                return 0.0
    return mejor


def _punto_a_segmento(px, py, x1, y1, x2, y2) -> float:
    dx, dy = x2 - x1, y2 - y1
    long2 = dx * dx + dy * dy
    if long2 == 0:
        return hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / long2
    t = max(0.0, min(1.0, t))
    cx = x1 + t * dx
    cy = y1 + t * dy
    return hypot(px - cx, py - cy)


# ---------------------------------------------------------------------------
# Helpers de transform
# ---------------------------------------------------------------------------

def _en_defs(el) -> bool:
    cur = getattr(el, "getparent", lambda: None)()
    while cur is not None:
        tag = getattr(cur, "tag", "")
        if isinstance(tag, str) and tag.endswith("}defs"):
            return True
        cur = cur.getparent() if hasattr(cur, "getparent") else None
    return False


def _ctm_seguro(el) -> Transform:
    try:
        return el.composed_transform()
    except Exception:
        return Transform()


def _transformar_punto(ctm: Transform, x: float, y: float) -> tuple[float, float]:
    return ctm.apply_to_point((x, y))


def _escala_uniforme(ctm: Transform) -> float:
    # Aproximamos la escala como la media geométrica de las escalas X/Y.
    m = ctm.matrix
    sx = sqrt(m[0][0] ** 2 + m[1][0] ** 2)
    sy = sqrt(m[0][1] ** 2 + m[1][1] ** 2)
    return sqrt(sx * sy) if sx * sy > 0 else 1.0


# ---------------------------------------------------------------------------
# Detector de tubing socket welds (paralelogramos negros)
# ---------------------------------------------------------------------------

def _detectar_tubing_socket_welds(svg, segmentos, escala) -> list[Costura]:
    """Detecta costuras tubing socket weld dibujadas como paralelogramos
    negros pequeños sobre la tubería.

    pdftocairo no las codifica como un único path cerrado sino como un
    hatching denso: muchos subpaths M-L cortos solapados que al
    renderizar producen visualmente un paralelogramo relleno. Las
    detectamos como clusters espaciales compactos.

    Filtros (calibrados sobre CD40F09 con 13 marcas ground truth):
      - subpath corto: longitud ≤ 6pt
      - cluster: ≥10 subpaths a distancia ≤2pt entre sí (single-link)
      - bbox del cluster ≤10pt (descarta clusters anómalos largos)
      - sobre tubería principal: ≤0.5pt a un segmento ≥20pt (descarta
        clusters cerca de texto/cotas/leaders)
    """
    cortos = []
    for x, y, L in _extraer_subpaths_ml_cortos(svg, long_max=6.0 * escala):
        cortos.append((x, y))
    if not cortos:
        return []

    clusters = _cluster_single_link(
        cortos, dist_max=2.0 * escala, min_size=10,
    )

    # Pre-filtrar a segmentos largos (tubería principal)
    segs_largos = [s for s in segmentos
                   if hypot(s.x2 - s.x1, s.y2 - s.y1) >= 20.0 * escala]
    if not segs_largos:
        return []

    def _candidato_valido(c) -> "Costura | None":
        bbox_max = max(c["w"], c["h"])
        # Filtros de tamaño: bbox demasiado grande = ruido (varios
        # paralelogramos unidos), demasiado pequeño = micro-cluster
        # espurio que pasó el re-clustering (no es un paralelogramo
        # real, son <10 puntos casi superpuestos).
        if bbox_max > 10.0 * escala:
            return None
        if bbox_max < 2.0 * escala:
            return None
        d_seg = min(
            (_punto_a_segmento(c["cx"], c["cy"], s.x1, s.y1, s.x2, s.y2)
             for s in segs_largos),
            default=float("inf"),
        )
        if d_seg > 0.5 * escala:
            return None
        return Costura(
            x=c["cx"], y=c["cy"],
            radio=max(c["w"], c["h"]) / 2,
            fuente_id="",
            sobre_linea=True,
            distancia_linea=d_seg,
            tipo="T",
            relleno=True,
            forma="angular",
        )

    salida: list[Costura] = []
    for c in clusters:
        cand = _candidato_valido(c)
        if cand is not None:
            salida.append(cand)
            continue
        # Cluster anómalo (bbox > 10pt): suele ser un paralelogramo
        # legítimo cuyo hatching toca un leader, una línea dashed o un
        # paralelogramo vecino. Re-clusterizar sus puntos con dist_max
        # más estricto suele separar el núcleo verdadero del ruido.
        sub = _cluster_single_link(c["puntos"], dist_max=1.0 * escala,
                                    min_size=6)
        for sc in sub:
            scand = _candidato_valido(sc)
            if scand is not None:
                salida.append(scand)

    # Dedup interno: clusters resultantes a <3pt entre sí (puede pasar
    # al re-clusterizar) → conservar uno solo
    dedup: list[Costura] = []
    for s in salida:
        if any(hypot(s.x - o.x, s.y - o.y) <= 3.0 * escala for o in dedup):
            continue
        dedup.append(s)

    return dedup


def detectar_flechas_flujo(svg) -> list[FlechaFlujo]:
    """Detecta flechas de sentido de flujo en el isométrico.

    Una flecha de flujo es un cluster denso de subpaths cortos (igual
    estructura que un paralelogramo tubing — hatching denso) PERO
    situado MÁS ALLÁ DEL EXTREMO de un segmento de tubería principal,
    no en mitad de un segmento.

    La dirección del flujo es el vector desde el extremo del segmento
    hacia el centroide de la flecha.
    """
    escala = _escala_pt_a_uu(svg)
    cortos = [(x, y) for x, y, L in
              _extraer_subpaths_ml_cortos(svg, long_max=6.0 * escala)]
    if not cortos:
        return []
    clusters = _cluster_single_link(cortos, dist_max=2.0 * escala, min_size=10)
    segmentos = _extraer_segmentos_lineales(svg)
    segs_largos = [s for s in segmentos
                   if hypot(s.x2 - s.x1, s.y2 - s.y1) >= 20.0 * escala]
    if not segs_largos:
        return []

    # Filtro más estricto: solo segmentos REALMENTE largos (≥30pt) que
    # son tubería principal. Leaders son segmentos cortos (≤20pt).
    segs_tuberia = [s for s in segs_largos
                    if hypot(s.x2 - s.x1, s.y2 - s.y1) >= 30.0 * escala]
    if not segs_tuberia:
        return []

    flechas: list[FlechaFlujo] = []
    for c in clusters:
        bbox_max = max(c["w"], c["h"])
        if not (2.0 * escala <= bbox_max <= 10.0 * escala):
            continue
        # Si está SOBRE línea (≤0.5pt) es tubing weld, no flecha.
        d_seg = min(
            (_punto_a_segmento(c["cx"], c["cy"], s.x1, s.y1, s.x2, s.y2)
             for s in segs_tuberia),
            default=float("inf"),
        )
        if d_seg <= 0.5 * escala:
            continue
        # Demasiado lejos de cualquier tubería = ruido (no flecha)
        if d_seg > 5.0 * escala:
            continue
        # Buscar el extremo del segmento de tubería MÁS cercano
        mejor_dx = mejor_dy = 0.0
        mejor_d_extremo = float("inf")
        for s in segs_tuberia:
            for ex, ey in ((s.x1, s.y1), (s.x2, s.y2)):
                d = hypot(c["cx"] - ex, c["cy"] - ey)
                if d < mejor_d_extremo:
                    mejor_d_extremo = d
                    mejor_dx = c["cx"] - ex
                    mejor_dy = c["cy"] - ey
        # La flecha debe estar MUY pegada al extremo (≤3pt). Más lejos
        # es un cluster aleatorio cerca de una tubería principal.
        if mejor_d_extremo > 3.0 * escala:
            continue
        norma = hypot(mejor_dx, mejor_dy)
        if norma <= 0:
            continue
        flechas.append(FlechaFlujo(
            cx=c["cx"], cy=c["cy"],
            dx=mejor_dx / norma, dy=mejor_dy / norma,
        ))
    return flechas


def _filtrar_valvulas(costuras: list[Costura], *,
                       dist_min: float, dist_max: float,
                       tol_centro: float,
                       dist_aislada: float | None = None) -> list[Costura]:
    """Descarta costuras T (tubing) que son válvulas / conexiones
    bridadas, no costuras soldadas reales.

    Caso 1: A entre dos vecinas equidistantes (válvula bridada con
      weld a cada lado). Para A: existen B, C tales que
        - d_AB, d_AC ∈ [dist_min, dist_max]
        - |d_AB - d_AC| / max < 0.2 (simétrico)
        - A a ≤ tol_centro del punto medio de B-C (alineado)

    Caso 2: A es T aislada con SOLO una vecina cercana (≤dist_max) y
      esa vecina es de tipo W (weld). Patrón típico de conexión
      bridada de válvula a una línea de tubería soldada (no a otra
      tubing weld). Una T real forma parte de una cadena de varias T's.
    """
    if len(costuras) < 2:
        return costuras
    descartar = set()
    for i, a in enumerate(costuras):
        # Caso 1: válvula entre dos welds equidistantes
        for j, b in enumerate(costuras):
            if j == i:
                continue
            d_ab = hypot(a.x - b.x, a.y - b.y)
            if not (dist_min <= d_ab <= dist_max):
                continue
            for k, c in enumerate(costuras):
                if k in (i, j):
                    continue
                d_ac = hypot(a.x - c.x, a.y - c.y)
                if not (dist_min <= d_ac <= dist_max):
                    continue
                if abs(d_ab - d_ac) / max(d_ab, d_ac) > 0.2:
                    continue
                mx = (b.x + c.x) / 2
                my = (b.y + c.y) / 2
                if hypot(a.x - mx, a.y - my) > tol_centro:
                    continue
                descartar.add(i)
                break
            if i in descartar:
                break
        if i in descartar:
            continue

        # Caso 2: T aislada con sola vecina W (conexión bridada a línea
        # soldada). Una T legítima forma cadena con otras T's vecinas.
        if a.tipo != "T":
            continue
        radio_busqueda = dist_aislada if dist_aislada is not None else dist_max
        vecinas_cerca = []  # lista de (distancia, costura)
        for j, b in enumerate(costuras):
            if j == i:
                continue
            d = hypot(a.x - b.x, a.y - b.y)
            if d <= radio_busqueda:
                vecinas_cerca.append((d, b))
        # Si solo hay 1 vecina cercana Y es W → conexión bridada
        if len(vecinas_cerca) == 1:
            _, vecina = vecinas_cerca[0]
            if vecina.tipo == "W":
                descartar.add(i)
                continue
        # Si hay MÚLTIPLES vecinas pero NINGUNA es T → tubing aislada
        # rodeada de welds → probable conexión bridada también
        if vecinas_cerca and not any(b.tipo == "T" for _, b in vecinas_cerca):
            descartar.add(i)
    return [c for idx, c in enumerate(costuras) if idx not in descartar]


def _extraer_subpaths_ml_cortos(svg, long_max: float) -> Iterable[tuple]:
    """Genera (cx, cy, longitud) de cada par M-L de cualquier path del
    SVG cuya longitud sea ≤ long_max. Aplica composed_transform para
    devolver coords globales."""
    for el in svg.iter():
        if not isinstance(el, PathElement) or _en_defs(el):
            continue
        ctm = _ctm_seguro(el)
        try:
            cmds = list(el.path.to_absolute())
        except Exception:
            continue
        prev = None
        m_pt = None
        for c in cmds:
            l = c.letter
            if l == "M":
                m_pt = (c.args[0], c.args[1])
                prev = m_pt
                continue
            elif l == "L":
                p = (c.args[0], c.args[1])
            elif l == "H":
                p = (c.args[0], prev[1] if prev else 0)
            elif l == "V":
                p = (prev[0] if prev else 0, c.args[0])
            elif l in ("Z", "z"):
                prev = m_pt
                continue
            else:
                if c.args and len(c.args) >= 2:
                    prev = (c.args[-2], c.args[-1])
                continue
            if prev is not None:
                x1, y1 = _transformar_punto(ctm, prev[0], prev[1])
                x2, y2 = _transformar_punto(ctm, p[0], p[1])
                L = hypot(x2 - x1, y2 - y1)
                if L <= long_max:
                    yield ((x1 + x2) / 2, (y1 + y2) / 2, L)
            prev = p


def _cluster_single_link(puntos, dist_max: float, min_size: int) -> list[dict]:
    """Clustering single-link sobre lista de (x, y). Devuelve clusters
    con ≥ min_size puntos. Cada cluster es {cx, cy, w, h, n, puntos}.

    `puntos` en el cluster es la lista de tuplas (x, y) miembros — útil
    para re-clusterizar un cluster anómalo con `dist_max` más estricto.
    """
    n = len(puntos)
    if n == 0:
        return []
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    # Spatial bucketing para evitar O(n²)
    cell = dist_max
    buckets: dict[tuple[int, int], list[int]] = {}
    for i, (x, y) in enumerate(puntos):
        buckets.setdefault((int(x / cell), int(y / cell)), []).append(i)

    for i, (x, y) in enumerate(puntos):
        key = (int(x / cell), int(y / cell))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in buckets.get((key[0] + dx, key[1] + dy), []):
                    if j <= i:
                        continue
                    xj, yj = puntos[j]
                    if hypot(x - xj, y - yj) <= dist_max:
                        union(i, j)

    grupos: dict[int, list[int]] = {}
    for i in range(n):
        grupos.setdefault(find(i), []).append(i)

    clusters = []
    for ids in grupos.values():
        if len(ids) < min_size:
            continue
        pts = [puntos[i] for i in ids]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        clusters.append({
            "cx": sum(xs) / len(xs),
            "cy": sum(ys) / len(ys),
            "w": max(xs) - min(xs),
            "h": max(ys) - min(ys),
            "n": len(ids),
            "puntos": pts,
        })
    return clusters
