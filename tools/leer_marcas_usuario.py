"""Extrae las marcas azules que el usuario añadió al SVG de candidatos
para indicar dónde están las costuras tubing reales (ground truth)."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))
for p in ("/usr/share/inkscape/extensions",
          str(Path.home() / ".config/inkscape/extensions")):
    if p not in sys.path:
        sys.path.insert(0, p)

import inkex  # noqa: E402
from inkex import Circle, Ellipse, PathElement  # noqa: E402

from tramear_core.seam_detector import _ctm_seguro, _transformar_punto, _es_relleno  # noqa: E402


def _color_es_azul(c: str) -> bool:
    c = c.lower().strip()
    if not c or c == "none":
        return False
    # Hex
    if c.startswith("#"):
        h = c.lstrip("#")
        if len(h) == 3:
            r, g, b = (int(h[i] * 2, 16) for i in range(3))
        elif len(h) == 6:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        else:
            return False
        return b > 150 and b > r + 40 and b > g + 40
    if c.startswith("rgb"):
        nums = [int(n) for n in c.replace("rgb(", "").replace(")", "")
                .replace("%", "").split(",")]
        if "%" in c:
            nums = [int(n * 2.55) for n in nums]
        if len(nums) >= 3:
            r, g, b = nums[0], nums[1], nums[2]
            return b > 150 and b > r + 40 and b > g + 40
    return c in ("blue", "cornflowerblue", "dodgerblue", "royalblue", "navy")


def _es_azul(el) -> bool:
    try:
        st = dict(el.style) if hasattr(el, "style") else {}
    except Exception:
        st = {}
    return (_color_es_azul(st.get("stroke") or el.get("stroke") or "")
            or _color_es_azul(st.get("fill") or el.get("fill") or ""))


def extraer_marcas(svg_path: Path):
    arbol = inkex.load_svg(str(svg_path)).getroot()
    marcas = []
    for el in arbol.iter():
        if not _es_azul(el):
            continue
        # Excluir capas/grupos azules accidentales
        if isinstance(el, Circle):
            ctm = _ctm_seguro(el)
            cx, cy = _transformar_punto(ctm,
                                         float(el.get("cx") or 0),
                                         float(el.get("cy") or 0))
            r = float(el.get("r") or 0)
            marcas.append((cx, cy, r, "circle", el.get("id") or ""))
        elif isinstance(el, Ellipse):
            ctm = _ctm_seguro(el)
            cx, cy = _transformar_punto(ctm,
                                         float(el.get("cx") or 0),
                                         float(el.get("cy") or 0))
            r = (float(el.get("rx") or 0) + float(el.get("ry") or 0)) / 2
            marcas.append((cx, cy, r, "ellipse", el.get("id") or ""))
        elif isinstance(el, PathElement):
            try:
                ctm = el.composed_transform()
                bbox = el.bounding_box(ctm) if ctm else el.bounding_box()
            except Exception:
                continue
            if bbox is None or bbox.width <= 0 or bbox.height <= 0:
                continue
            r = max(bbox.width, bbox.height) / 2
            marcas.append((bbox.center_x, bbox.center_y, r,
                           "path", el.get("id") or ""))
    return marcas


if __name__ == "__main__":
    svg = Path("revision_10pdfs/fase_c_CD40F09_candidatos.svg")
    marcas = extraer_marcas(svg)
    print(f"{len(marcas)} marcas azules encontradas en {svg.name}:")
    for cx, cy, r, t, mid in marcas:
        print(f"  ({cx:7.2f}, {cy:7.2f})  r={r:.2f}  tipo={t}  id={mid}")
