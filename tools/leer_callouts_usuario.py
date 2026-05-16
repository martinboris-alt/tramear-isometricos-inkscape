"""Lee las posiciones manuales de los callouts que el usuario movió en el
SVG marcado, las cruza con la posición original de cada costura, y
extrae el vector delta (callout - costura) para aprender la heurística
de placement que el usuario aplica.

Salida:
  - Tabla con (costura X,Y) → (callout X,Y) → Δx, Δy, distancia
  - Estadísticas: distancia mediana costura-callout, dirección preferida
  - Identificación de patrón: ¿siempre hacia un lado? ¿cerca o lejos?
"""

from __future__ import annotations

import csv
import sys
from math import atan2, degrees, hypot
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))
for p in ("/usr/share/inkscape/extensions",
          str(Path.home() / ".config/inkscape/extensions")):
    if p not in sys.path:
        sys.path.insert(0, p)

import inkex  # noqa: E402
from inkex import Circle, PathElement, TextElement  # noqa: E402

from tramear_core.seam_detector import _ctm_seguro, _transformar_punto  # noqa: E402


def encontrar_capa_costuras(raiz):
    """Busca la capa 'Costuras' anidada (puede estar dentro de <g>)."""
    INK_LABEL = "{http://www.inkscape.org/namespaces/inkscape}label"
    for el in raiz.iter():
        if el.get(INK_LABEL) == "Costuras" or el.get("id") == "Costuras":
            return el
    return None


def leer_callouts(svg_path: Path):
    """Para cada Circle de la capa Costuras, devuelve (id, cx, cy, num).

    El número se identifica buscando el TextElement asociado más cercano
    al círculo (mismo grupo o hermano cercano).
    """
    arbol = inkex.load_svg(str(svg_path)).getroot()
    capa = encontrar_capa_costuras(arbol)
    if capa is None:
        print("No se encontró capa 'Costuras'")
        return []

    callouts = []
    for el in capa.iter():
        if isinstance(el, Circle):
            ctm = _ctm_seguro(el)
            cx, cy = _transformar_punto(ctm,
                                         float(el.get("cx") or 0),
                                         float(el.get("cy") or 0))
            r = float(el.get("r") or 0)
            callouts.append({
                "id": el.get("id") or "",
                "cx": cx, "cy": cy, "r": r,
                "num": None,
            })

    # Asociar número (Text) a cada callout: el text más cercano
    textos = []
    for el in capa.iter():
        if isinstance(el, TextElement):
            try:
                ctm = _ctm_seguro(el)
                tx, ty = _transformar_punto(ctm,
                                             float(el.get("x") or 0),
                                             float(el.get("y") or 0))
                contenido = (el.text or "").strip() or "".join(
                    t.text or "" for t in el.iter() if t.text)
                contenido = contenido.strip()
                if contenido:
                    textos.append((tx, ty, contenido))
            except Exception:
                pass

    for c in callouts:
        if not textos:
            break
        mejor = min(textos, key=lambda t: hypot(t[0] - c["cx"], t[1] - c["cy"]))
        d = hypot(mejor[0] - c["cx"], mejor[1] - c["cy"])
        if d < 20:
            c["num"] = mejor[2]

    return callouts


def leer_welding_book(csv_path: Path):
    """Devuelve lista de (numero, x, y) del CSV."""
    out = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            try:
                out.append((int(row["Nº costura"]),
                            float(row["X"]),
                            float(row["Y"])))
            except (ValueError, KeyError):
                continue
    return out


def main():
    svg = Path("revision_10pdfs/19-000-2-02-00001 sheet 2121CD40F09-2_IS01_marcado.svg")
    csv_path = svg.with_name(svg.stem.replace("_marcado", "_welding_book") + ".csv")

    callouts = leer_callouts(svg)
    welding = leer_welding_book(csv_path)
    print(f"Callouts leídos: {len(callouts)}")
    print(f"Costuras en CSV: {len(welding)}\n")

    # Asociar callout → costura más cercana del welding book por número (si
    # el callout tiene número) o por proximidad
    if not callouts or not welding:
        return

    print(f"{'#':>3} {'costura X,Y':>20} {'callout X,Y':>20} "
          f"{'Δx':>7} {'Δy':>7} {'dist':>6} {'ang':>6}")
    deltas = []
    for num, sx, sy in welding:
        # Buscar callout por número o por proximidad
        cand = None
        if callouts:
            # Por proximidad: callout más cercano a la costura
            cand = min(callouts, key=lambda c: hypot(c["cx"] - sx, c["cy"] - sy))
        if cand is None:
            continue
        dx = cand["cx"] - sx
        dy = cand["cy"] - sy
        d = hypot(dx, dy)
        ang = degrees(atan2(dy, dx))
        deltas.append((dx, dy, d, ang, num))
        print(f"{num:>3} ({sx:7.1f},{sy:7.1f})  ({cand['cx']:7.1f},{cand['cy']:7.1f})"
              f"  {dx:+7.1f} {dy:+7.1f} {d:6.1f} {ang:+6.1f}°")

    # Estadísticas
    if deltas:
        print(f"\nEstadísticas:")
        ds = sorted(d for *_, d, _, _ in [(d,) for d in deltas])
        ds = sorted(d for _, _, d, _, _ in deltas)
        print(f"  Distancia callout-costura: "
              f"min={min(ds):.1f} med={ds[len(ds)//2]:.1f} max={max(ds):.1f}")
        angs = [a for _, _, _, a, _ in deltas]
        # Distribución por cuadrantes
        cuad = {"NE": 0, "NW": 0, "SE": 0, "SW": 0}
        for a in angs:
            if -90 <= a <= 0:
                cuad["NE"] += 1
            elif 0 <= a <= 90:
                cuad["SE"] += 1
            elif a >= 90:
                cuad["SW"] += 1
            else:
                cuad["NW"] += 1
        print(f"  Direcciones: {cuad}")


if __name__ == "__main__":
    main()
