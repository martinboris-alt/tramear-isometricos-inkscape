"""Detector de costuras (modo raster, OpenCV).

Para PDFs escaneados que llegan a Inkscape como una imagen embebida.
Se extrae la imagen, se detectan los círculos con HoughCircles y, si
está disponible, se valida proximidad a líneas con HoughLinesP.
"""

from __future__ import annotations

import base64
import io
from typing import TYPE_CHECKING

from inkex import Image as InkImage, Transform

from .seam_detector import Costura

if TYPE_CHECKING:
    import numpy as np  # noqa: F401


CV2_NO_DISPONIBLE = (
    "OpenCV (cv2) no está instalado en el Python de Inkscape. "
    "El detector raster no se ejecutará. Instala opencv-python con pip "
    "para habilitarlo."
)


def cv2_disponible() -> bool:
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        return False


def detectar_costuras_raster(
    svg,
    radio_min_mundo: float,
    radio_max_mundo: float,
    tolerancia_linea_mundo: float,
    solo_sobre_linea: bool = True,
) -> tuple[list[Costura], list[str]]:
    """Recorre imágenes embebidas y aplica detección de círculos.

    Devuelve (costuras, avisos). Los rangos de radio/tolerancia se dan
    en unidades del SVG; se convierten a píxeles dentro de cada imagen
    según su tamaño y CTM.
    """
    avisos: list[str] = []
    if not cv2_disponible():
        return ([], [CV2_NO_DISPONIBLE])

    import cv2
    import numpy as np
    from PIL import Image as PILImage

    todas: list[Costura] = []

    for el in svg.iter():
        if not isinstance(el, InkImage):
            continue

        href = el.get("{http://www.w3.org/1999/xlink}href") or el.get("href") or ""
        if not href.startswith("data:image"):
            avisos.append(f"Imagen externa (href={href[:50]}…) — saltada.")
            continue

        try:
            cabecera, b64 = href.split(",", 1)
            bin_img = base64.b64decode(b64)
            pil = PILImage.open(io.BytesIO(bin_img)).convert("RGB")
            arr = np.array(pil)
        except Exception as e:
            avisos.append(f"No se pudo decodificar imagen {el.get('id')}: {e}")
            continue

        # Tamaño del <image> en unidades SVG y CTM al usuario.
        try:
            w_svg = float(el.get("width") or arr.shape[1])
            h_svg = float(el.get("height") or arr.shape[0])
            x_svg = float(el.get("x") or 0)
            y_svg = float(el.get("y") or 0)
        except ValueError:
            avisos.append(f"Imagen {el.get('id')} con dimensiones inválidas.")
            continue

        px_por_unidad_x = arr.shape[1] / w_svg if w_svg else 1.0
        px_por_unidad_y = arr.shape[0] / h_svg if h_svg else 1.0
        px_por_unidad = (px_por_unidad_x + px_por_unidad_y) / 2

        r_min_px = max(1, int(radio_min_mundo * px_por_unidad))
        r_max_px = max(r_min_px + 1, int(radio_max_mundo * px_por_unidad))

        gris = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        gris = cv2.medianBlur(gris, 3)

        circulos = cv2.HoughCircles(
            gris,
            cv2.HOUGH_GRADIENT,
            dp=1.0,
            minDist=r_min_px * 2,
            param1=120,
            param2=18,
            minRadius=r_min_px,
            maxRadius=r_max_px,
        )

        if circulos is None:
            continue

        # Líneas (para validar proximidad)
        lineas = None
        if solo_sobre_linea:
            bordes = cv2.Canny(gris, 60, 180, apertureSize=3)
            lineas = cv2.HoughLinesP(
                bordes, 1, 3.14159 / 180,
                threshold=80,
                minLineLength=max(20, r_min_px * 8),
                maxLineGap=5,
            )

        tol_px = tolerancia_linea_mundo * px_por_unidad
        ctm = _ctm_seguro(el)

        for (cx_px, cy_px, r_px) in circulos[0]:
            if solo_sobre_linea and not _cerca_de_alguna_linea(cx_px, cy_px, lineas, tol_px):
                continue

            # Píxel → unidades del <image> → unidades SVG mundo
            x_local = x_svg + cx_px / px_por_unidad_x
            y_local = y_svg + cy_px / px_por_unidad_y
            xw, yw = ctm.apply_to_point((x_local, y_local))

            radio_mundo = r_px / px_por_unidad
            todas.append(Costura(
                x=xw, y=yw, radio=radio_mundo,
                fuente_id=el.get("id", "") or "",
                sobre_linea=True,
                distancia_linea=0.0,
                tipo="W",
                relleno=True,
            ))

    return (todas, avisos)


def _cerca_de_alguna_linea(cx, cy, lineas, tol_px) -> bool:
    if lineas is None:
        return False
    for ln in lineas[:, 0, :]:
        x1, y1, x2, y2 = ln
        if _dist_punto_segmento(cx, cy, x1, y1, x2, y2) <= tol_px:
            return True
    return False


def _dist_punto_segmento(px, py, x1, y1, x2, y2) -> float:
    dx, dy = x2 - x1, y2 - y1
    long2 = dx * dx + dy * dy
    if long2 == 0:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    t = ((px - x1) * dx + (py - y1) * dy) / long2
    t = max(0.0, min(1.0, t))
    qx, qy = x1 + t * dx, y1 + t * dy
    return ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5


def _ctm_seguro(el) -> Transform:
    try:
        return el.composed_transform()
    except Exception:
        return Transform()
