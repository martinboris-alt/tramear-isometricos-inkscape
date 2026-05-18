"""Parser de la tabla Bill of Material (BOM) de un isométrico Repsol.

La tabla está siempre en la esquina superior derecha y tiene columnas:
  PT NO  |  COMPONENT DESCRIPTION  |  N.S. (IN)  |  ITEM CODE  |  QTY  |  STATUS

Las filas pueden ser:
  - Categoría (PIPE, FITTINGS, FLANGES, BOLTS & GASKETS, PIPE SUPPORTS):
    única columna con texto, sirve para agrupar.
  - Item: PT NO entero, descripción, diámetro, itemcode, qty (status opcional).
  - Continuación: descripción adicional sin PT NO (sangría) que pertenece
    al item anterior.

Uso CLI:
    .venv/bin/python -m tools.extraer_bom <pdf> [--csv salida.csv]
    .venv/bin/python -m tools.extraer_bom --carpeta revision_10pdfs --csv boms.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from tools.pdf_extractor import extraer_pdf  # noqa: E402


# Columnas X aproximadas (validadas sobre los PDFs Repsol/Sines).
# Tolerancia ±5 pt absorbe las variaciones entre revisiones.
COL_PT = 784
COL_DESC = 793
COL_NS = 1041
COL_CODE = 1071
COL_QTY = 1119
COL_STATUS = 1149
TOL_COL = 8

# Categorías conocidas (orden = el del cajetín).
CATEGORIAS = {"PIPE", "FITTINGS", "FLANGES", "BOLTS & GASKETS",
              "BOLTS", "GASKETS", "PIPE SUPPORTS", "VALVES",
              "INSTRUMENTS", "SPECIALS"}

TOL_FILA = 2.5  # diferencia máx en Y para considerar mismo "renglón"


@dataclass
class ItemBOM:
    pt_no: str = ""
    categoria: str = ""
    descripcion: str = ""
    diametro: str = ""          # nominal en pulgadas, tal cual aparece
    itemcode: str = ""          # I117921, 701, etc.
    cantidad: str = ""          # "20.6 M", "5", etc.
    status: str = ""
    archivo: str = ""           # opcional, para salida CSV


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def extraer_bom(pdf_path: str | Path) -> list[ItemBOM]:
    pdf_path = Path(pdf_path)
    extr = extraer_pdf(pdf_path, paginas=[0])
    if not extr.paginas:
        return []
    pag = extr.paginas[0]

    # Localizar la fila de la cabecera (la línea con "PT"+"N.S."+"HOLD")
    pt = next((t for t in pag.textos if t.texto.strip() == "PT" and t.x > 700), None)
    if pt is None:
        return []
    y_cabecera1 = pt.y
    # La fila "NO COMPONENT DESCRIPTION (IN) ITEM CODE QTY STATUS" está
    # justo debajo de "PT … N.S. … HOLD". Asumimos 5-12 pt más abajo.
    y_cabecera2 = y_cabecera1 + 7.6
    y_inicio = y_cabecera2 + 5.0  # primeras filas de datos

    # Textos por debajo de la cabecera, en la zona x > 770.
    textos_bom = [t for t in pag.textos
                  if t.y > y_inicio and t.x > 770 and t.texto.strip()]
    textos_bom.sort(key=lambda t: (t.y, t.x))

    # Agrupar por renglón (mismo Y ± TOL_FILA)
    renglones: list[list] = []
    actual: list = []
    y_actual = -999.0
    for t in textos_bom:
        if abs(t.y - y_actual) < TOL_FILA:
            actual.append(t)
        else:
            if actual:
                renglones.append(actual)
            actual = [t]
            y_actual = t.y
    if actual:
        renglones.append(actual)

    items: list[ItemBOM] = []
    categoria_actual = ""

    for ren in renglones:
        ren.sort(key=lambda t: t.x)
        # ¿Es una categoría? único texto, en x ≈ COL_PT, sin números.
        if len(ren) == 1:
            txt = ren[0].texto.strip().upper()
            if txt in CATEGORIAS:
                categoria_actual = txt
                continue
            # texto único pero no categoría: puede ser continuación de
            # descripción → adjuntar al último item.
            if items and ren[0].x > COL_PT + TOL_COL:
                items[-1].descripcion = (items[-1].descripcion + " "
                                          + ren[0].texto.strip()).strip()
            continue

        # Localizar campos por columna.
        pt_no = _campo(ren, COL_PT, TOL_COL)
        desc = _concatenar(ren, COL_DESC, COL_NS - TOL_COL)
        ns = _campo(ren, COL_NS, TOL_COL)
        code = _campo(ren, COL_CODE, TOL_COL)
        qty = _campo(ren, COL_QTY, TOL_COL)
        status = _campo(ren, COL_STATUS, TOL_COL)

        if not pt_no.isdigit():
            # Continuación: descripción que se desbordó del item anterior.
            if items and desc:
                items[-1].descripcion = (items[-1].descripcion + " "
                                          + desc).strip()
            continue

        items.append(ItemBOM(
            pt_no=pt_no,
            categoria=categoria_actual,
            descripcion=desc.strip(),
            diametro=ns.strip(),
            itemcode=code.strip(),
            cantidad=qty.strip(),
            status=status.strip(),
            archivo=pdf_path.name,
        ))

    return items


def _campo(ren: list, x_obj: float, tol: float) -> str:
    """Devuelve el texto en la columna cuya X está en ±tol de x_obj."""
    for t in ren:
        if abs(t.x - x_obj) <= tol:
            return t.texto.strip()
    return ""


def _concatenar(ren: list, x_min: float, x_max: float) -> str:
    """Concatena (en orden de X) todos los textos cuyo X cae en
    [x_min, x_max]. Útil para descripciones que el extractor separa en
    spans distintos."""
    partes = [t.texto for t in ren if x_min - 2 <= t.x <= x_max]
    return " ".join(p.strip() for p in partes if p.strip())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf", nargs="?")
    ap.add_argument("--carpeta")
    ap.add_argument("--csv", default=None)
    args = ap.parse_args()

    pdfs: list[Path] = []
    if args.pdf:
        pdfs.append(Path(args.pdf))
    elif args.carpeta:
        pdfs.extend(sorted(Path(args.carpeta).glob("*.pdf")))
    else:
        ap.error("Hay que pasar un PDF o --carpeta")

    todos: list[ItemBOM] = []
    for pdf in pdfs:
        items = extraer_bom(pdf)
        todos.extend(items)
        print(f"{pdf.name}: {len(items)} items")
        if not args.csv:
            for i in items:
                print(f"  [{i.categoria:14s}] PT={i.pt_no:>3s}  "
                      f"NS={i.diametro:>8s}  CODE={i.itemcode:>10s}  "
                      f"QTY={i.cantidad:>8s}  | {i.descripcion[:70]}")

    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
            campos = ["archivo", "pt_no", "categoria", "descripcion",
                      "diametro", "itemcode", "cantidad", "status"]
            w = csv.DictWriter(f, fieldnames=campos, delimiter=";")
            w.writeheader()
            for i in todos:
                w.writerow({c: getattr(i, c) for c in campos})
        print(f"\nCSV: {args.csv}  ({len(todos)} filas)")


if __name__ == "__main__":
    main()
