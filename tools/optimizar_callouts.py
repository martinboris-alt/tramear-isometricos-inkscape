"""Reposiciona callouts ya dibujados al punto más alejado de texto/cotas
usando la transformada de distancia euclídea (EDT) sobre el plano.

Pipeline:
  1. Cargar SVG marcado existente.
  2. Renderizar a PNG **sin la capa de Costuras** → contiene solo el
     plano original (líneas, texto, cotas).
  3. Binarizar y calcular `distance_transform_edt` → para cada píxel
     libre, distancia al píxel ocupado más cercano (en píxeles).
  4. Para cada callout de la capa Costuras:
     - Buscar en el anillo [offset_min, dist_max] el píxel con MAYOR
       distancia al texto.
     - Reposicionar callout + flecha curva apuntando a la costura.
  5. Padding adaptativo: si más del `umbral_problemas`% de callouts no
     alcanzan `padding_min`, reducir padding_min e iterar.

Uso:
    .venv/bin/python -m tools.optimizar_callouts <svg_marcado>
"""

from __future__ import annotations

import argparse
import math
import sys
import tempfile
from copy import deepcopy
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

_INK_EXT = "/usr/share/inkscape/extensions"
_USR_EXT = str(Path.home() / ".config/inkscape/extensions")
for p in (_INK_EXT, _USR_EXT):
    if p not in sys.path:
        sys.path.insert(0, p)

import inkex  # noqa: E402
from inkex import Circle, PathElement  # noqa: E402


CAPA_COSTURAS = "Costuras"
DPI_DEF = 200
OFFSET_MIN_DEF = 25.0    # pt — distancia mínima del callout a la costura
DIST_MAX_DEF = 80.0      # pt — distancia máxima (ajustado del usuario: 31-74)
PADDING_INI = 8.0        # px (≈3 pt @ 200 DPI) — separación deseable al texto
PADDING_MIN_ABS = 1.0    # px — bajo este valor nos rendimos
SEP_MIN_CALLOUTS = 25.0  # pt — separación mínima entre callouts (≈ 2× radio)


def encontrar_capa_costuras(svg) -> "inkex.Group | None":
    for el in svg.iter():
        if hasattr(el, "get") and el.get("inkscape:label") == CAPA_COSTURAS:
            return el
    return None


def renderizar_sin_costuras(svg_path: Path, dpi: int) -> tuple[np.ndarray, float]:
    """Renderiza el SVG ocultando la capa Costuras, devuelve máscara
    binaria (1 = ocupado) y factor pt→px."""
    arbol = inkex.load_svg(str(svg_path))
    raiz = arbol.getroot()
    capa = encontrar_capa_costuras(raiz)
    if capa is not None:
        # Forzar display:none sin perder estilos
        capa.set("style", "display:none")

    from lxml import etree
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp_svg:
        tmp_svg.write(etree.tostring(arbol))
        tmp_svg_path = Path(tmp_svg.name)
    png_path = tmp_svg_path.with_suffix(".png")

    import subprocess
    subprocess.run(
        ["inkscape", str(tmp_svg_path), "--export-type=png",
         f"--export-filename={png_path}", f"--export-dpi={dpi}",
         "--export-background=white"],
        check=True, capture_output=True,
    )
    img = Image.open(png_path).convert("L")
    arr = np.array(img)
    mascara = (arr < 200).astype(np.uint8)
    tmp_svg_path.unlink(missing_ok=True)
    png_path.unlink(missing_ok=True)
    return mascara, dpi / 72.0


def construir_mapa_distancias(mascara: np.ndarray) -> np.ndarray:
    """Distance transform: para cada píxel libre devuelve la distancia
    en píxeles al píxel ocupado más cercano."""
    return ndimage.distance_transform_edt(1 - mascara)


def extraer_callouts_y_costuras(capa) -> list:
    """Cada grupo de la capa Costuras tiene un Circle (callout) y un
    PathElement (flecha) cuyo extremo final es la costura. Devuelve
    lista de dicts."""
    out = []
    for grupo in capa:
        circulo = None
        flecha = None
        for el in grupo.iter() if hasattr(grupo, "iter") else [grupo]:
            if isinstance(el, Circle) and circulo is None:
                circulo = el
            elif isinstance(el, PathElement) and flecha is None:
                flecha = el
        if circulo is None or flecha is None:
            continue
        try:
            cx = float(circulo.get("cx"))
            cy = float(circulo.get("cy"))
            r = float(circulo.get("r"))
        except (TypeError, ValueError):
            continue
        # Extremo de la flecha = costura. Buscar último punto del path.
        try:
            p = flecha.path.to_absolute()
        except Exception:
            continue
        sx = sy = None
        ex = ey = None
        for cmd in p:
            if cmd.letter == "M":
                sx, sy = cmd.args[0], cmd.args[1]
            elif cmd.letter in ("L", "C"):
                if cmd.letter == "C":
                    ex, ey = cmd.args[-2], cmd.args[-1]
                else:
                    ex, ey = cmd.args[0], cmd.args[1]
        if ex is None:
            continue
        out.append({
            "grupo": grupo, "circulo": circulo, "flecha": flecha,
            "cx_call": cx, "cy_call": cy, "r": r,
            "x_cost": ex, "y_cost": ey,
            "sx": sx, "sy": sy,
        })
    return out


def _segmentos_se_cruzan(ax1, ay1, ax2, ay2,
                          bx1, by1, bx2, by2) -> bool:
    """Devuelve True si los segmentos AB y CD se cruzan (cruce propio)."""
    def ccw(x1, y1, x2, y2, x3, y3):
        return (y3 - y1) * (x2 - x1) > (y2 - y1) * (x3 - x1)
    return (ccw(ax1, ay1, bx1, by1, bx2, by2)
            != ccw(ax2, ay2, bx1, by1, bx2, by2)
            and ccw(ax1, ay1, ax2, ay2, bx1, by1)
            != ccw(ax1, ay1, ax2, ay2, bx2, by2))


def _punto_a_seg(px, py, x1, y1, x2, y2) -> float:
    dx, dy = x2 - x1, y2 - y1
    long2 = dx * dx + dy * dy
    if long2 == 0:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / long2
    t = max(0.0, min(1.0, t))
    cx = x1 + t * dx
    cy = y1 + t * dy
    return math.hypot(px - cx, py - cy)


def _distancia_min_segmentos(ax1, ay1, ax2, ay2,
                              bx1, by1, bx2, by2) -> float:
    """Distancia mínima entre dos segmentos."""
    if _segmentos_se_cruzan(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2):
        return 0.0
    return min(
        _punto_a_seg(ax1, ay1, bx1, by1, bx2, by2),
        _punto_a_seg(ax2, ay2, bx1, by1, bx2, by2),
        _punto_a_seg(bx1, by1, ax1, ay1, ax2, ay2),
        _punto_a_seg(bx2, by2, ax1, ay1, ax2, ay2),
    )


def reposicionar_callout(item, dist_map, factor_px,
                         offset_min: float, dist_max: float,
                         padding_min_px: float,
                         callouts_previos_px: list = None,
                         sep_min_px: float = 0.0,
                         flechas_previas_px: list = None,
                         ) -> tuple[float, float, float]:
    """Encuentra la mejor posición para el callout y devuelve
    (cx_nuevo, cy_nuevo, dist_lograda_px). dist_lograda_px puede ser
    menor que padding_min_px si no hay sitio.

    `callouts_previos_px`: lista de (cx, cy) en píxeles de callouts ya
    colocados. Si está dada, los candidatos a < `sep_min_px` se rechazan.
    Esto evita amontonamiento en la misma zona libre.
    """
    if callouts_previos_px is None:
        callouts_previos_px = []
    if flechas_previas_px is None:
        flechas_previas_px = []
    x_cost = item["x_cost"]
    y_cost = item["y_cost"]
    xc_cost_px = x_cost * factor_px
    yc_cost_px = y_cost * factor_px
    r_callout = item["r"]
    h, w = dist_map.shape

    # Anillo de búsqueda en pt; convertir a px
    off_px = offset_min * factor_px
    dmax_px = dist_max * factor_px
    r_call_px = r_callout * factor_px

    # Centro de la costura en píxeles
    xc_px = x_cost * factor_px
    yc_px = y_cost * factor_px

    # Probar puntos en una rejilla angular fina; el centro del callout
    # debe estar a distancia entre (off_px + r_call_px) y dmax_px.
    mejor = None
    mejor_score = -1e9
    pasos_radio = 24
    pasos_angulo = 64
    for i_r in range(pasos_radio):
        d = off_px + r_call_px + (dmax_px - off_px - r_call_px) * (i_r / max(pasos_radio - 1, 1))
        for i_a in range(pasos_angulo):
            ang = (2 * math.pi) * i_a / pasos_angulo
            x = xc_px + d * math.cos(ang)
            y = yc_px + d * math.sin(ang)
            ix, iy = int(x), int(y)
            r_int = int(math.ceil(r_call_px))
            if ix - r_int < 0 or ix + r_int >= w: continue
            if iy - r_int < 0 or iy + r_int >= h: continue
            # Rechazar si está demasiado cerca de un callout ya colocado
            if any((x - px) ** 2 + (y - py) ** 2 < sep_min_px ** 2
                   for px, py in callouts_previos_px):
                continue
            # Penalizar candidatos cuya flecha cruzaría O pasaría muy
            # cerca (≤sep_min_px) de flechas previas. La proximidad
            # captura el caso "pisando la línea" sin cruce geométrico.
            cruces = 0
            for (ax1, ay1, ax2, ay2) in flechas_previas_px:
                d_seg = _distancia_min_segmentos(
                    x, y, xc_cost_px, yc_cost_px,
                    ax1, ay1, ax2, ay2,
                )
                if d_seg < sep_min_px * 0.5:  # ≈12pt
                    cruces += 1
            sub = dist_map[iy - r_int:iy + r_int + 1,
                           ix - r_int:ix + r_int + 1]
            if sub.size == 0:
                continue
            dist_min = float(sub.min())
            # Score: priorizar (1) cumplir padding, (2) cercanía a costura,
            # (3) ligero peso a distancia al texto. El usuario coloca
            # callouts CERCA de la costura, no en el "máximo absoluto libre".
            penal_dist = d / dmax_px  # 0..1, mayor = más lejos
            penal_cruce = 2.0 * cruces  # cada cruce/proximidad penaliza fuerte
            if dist_min >= padding_min_px:
                score = (-penal_dist
                          + 0.1 * (dist_min / max(padding_min_px, 1))
                          - penal_cruce)
            else:
                score = (-1.0 + dist_min / max(padding_min_px, 1)
                          - penal_dist - penal_cruce)
            if score > mejor_score:
                mejor_score = score
                mejor = (x, y, dist_min)
    if mejor is None:
        # No hay sitio. Devolver el mejor encontrado globalmente
        # (incluyendo separación inválida).
        for i_r in range(pasos_radio):
            d = off_px + r_call_px + (dmax_px - off_px - r_call_px) * (i_r / max(pasos_radio - 1, 1))
            for i_a in range(pasos_angulo):
                ang = (2 * math.pi) * i_a / pasos_angulo
                x = xc_px + d * math.cos(ang)
                y = yc_px + d * math.sin(ang)
                ix, iy = int(x), int(y)
                r_int = int(math.ceil(r_call_px))
                if ix - r_int < 0 or ix + r_int >= w: continue
                if iy - r_int < 0 or iy + r_int >= h: continue
                sub = dist_map[iy - r_int:iy + r_int + 1,
                               ix - r_int:ix + r_int + 1]
                if sub.size == 0: continue
                dist_min = float(sub.min())
                if dist_min > mejor_score:
                    mejor_score = dist_min
                    mejor = (x, y, dist_min)
    if mejor is None:
        return (item["cx_call"], item["cy_call"], 0.0)
    return (mejor[0] / factor_px, mejor[1] / factor_px, mejor[2])


def actualizar_callout(item, cx_new: float, cy_new: float) -> None:
    """Mueve el callout (Circle, texto) y rehace la flecha apuntando
    al mismo punto de costura."""
    circulo = item["circulo"]
    flecha = item["flecha"]
    x_cost = item["x_cost"]
    y_cost = item["y_cost"]
    r = item["r"]

    circulo.set("cx", f"{cx_new}")
    circulo.set("cy", f"{cy_new}")

    # Mover también el texto del callout: hijos Text del grupo.
    from inkex import TextElement, Tspan
    for el in item["grupo"].iter():
        if isinstance(el, TextElement):
            el.set("x", f"{cx_new}")
            for ts in el.iter():
                if isinstance(ts, Tspan):
                    ts.set("x", f"{cx_new}")
            # Mantener la y centrada (aprox)
            el.set("y", f"{cy_new + r * 0.35}")
            for ts in el.iter():
                if isinstance(ts, Tspan):
                    ts.set("y", f"{cy_new + r * 0.35}")

    # Rehacer la flecha. Inicio = borde del callout más cercano a la costura.
    ang = math.atan2(y_cost - cy_new, x_cost - cx_new)
    sx = cx_new + math.cos(ang) * r
    sy = cy_new + math.sin(ang) * r
    # Curva Bézier suave hacia la costura.
    dx = x_cost - sx
    dy = y_cost - sy
    perp_x, perp_y = -dy * 0.15, dx * 0.15
    cx1 = sx + dx * 0.33 + perp_x
    cy1 = sy + dy * 0.33 + perp_y
    cx2 = sx + dx * 0.66 - perp_x
    cy2 = sy + dy * 0.66 - perp_y
    d_str = f"M {sx},{sy} C {cx1},{cy1} {cx2},{cy2} {x_cost},{y_cost}"
    flecha.set("d", d_str)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("svg")
    ap.add_argument("--dpi", type=int, default=DPI_DEF)
    ap.add_argument("--offset-min", type=float, default=OFFSET_MIN_DEF)
    ap.add_argument("--dist-max", type=float, default=DIST_MAX_DEF)
    ap.add_argument("--padding-px", type=float, default=PADDING_INI)
    ap.add_argument("--salida", default=None)
    args = ap.parse_args()

    svg_path = Path(args.svg)
    if not svg_path.is_file():
        sys.exit(f"SVG no existe: {svg_path}")

    print(f"Reposicionando callouts: {svg_path.name}")
    mascara, factor_px = renderizar_sin_costuras(svg_path, args.dpi)
    print(f"  máscara {mascara.shape}, factor px/pt={factor_px:.2f}")
    dist_map = construir_mapa_distancias(mascara)
    print(f"  distance transform OK (max={dist_map.max():.1f} px)")

    arbol = inkex.load_svg(str(svg_path))
    raiz = arbol.getroot()
    capa = encontrar_capa_costuras(raiz)
    if capa is None:
        sys.exit("No se encontró capa Costuras")
    items = extraer_callouts_y_costuras(capa)
    print(f"  callouts a reposicionar: {len(items)}")

    # Padding adaptativo: empezar con padding_ini, bajar si <70% cumplen.
    sep_px = SEP_MIN_CALLOUTS * factor_px
    padding_px = args.padding_px
    intentos = 0
    resultado = None
    while intentos < 5 and padding_px >= PADDING_MIN_ABS:
        cumple = 0
        propuesta = []
        previos_px = []     # (cx, cy) en px de callouts ya colocados
        flechas_px = []     # (x1, y1, x2, y2) en px de flechas ya trazadas
        for it in items:
            nx, ny, d = reposicionar_callout(
                it, dist_map, factor_px,
                args.offset_min, args.dist_max, padding_px,
                callouts_previos_px=previos_px,
                sep_min_px=sep_px,
                flechas_previas_px=flechas_px,
            )
            propuesta.append((it, nx, ny, d))
            previos_px.append((nx * factor_px, ny * factor_px))
            # Flecha = línea recta entre callout y costura (aproximación
            # para detección de cruces; la curva Bézier real está cerca)
            flechas_px.append((nx * factor_px, ny * factor_px,
                                it["x_cost"] * factor_px,
                                it["y_cost"] * factor_px))
            if d >= padding_px:
                cumple += 1
        ratio = cumple / max(len(items), 1)
        print(f"  padding={padding_px:.1f}px → {cumple}/{len(items)} cumplen ({ratio*100:.0f}%)")
        if ratio >= 0.70 or padding_px <= PADDING_MIN_ABS + 0.01:
            resultado = propuesta
            break
        padding_px = max(PADDING_MIN_ABS, padding_px * 0.6)
        intentos += 1
    if resultado is None:
        resultado = propuesta

    # Aplicar
    for it, nx, ny, _ in resultado:
        actualizar_callout(it, nx, ny)

    salida = (Path(args.salida) if args.salida
              else svg_path.with_name(svg_path.stem + "_optcall.svg"))
    arbol.write(str(salida))
    print(f"  salida: {salida}")


if __name__ == "__main__":
    main()
