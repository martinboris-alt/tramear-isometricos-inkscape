"""Catalogador de los PDFs de samples/.

Recorre todos los .pdf bajo samples/ (recursivo), los analiza con
pdf_extractor sobre la primera página, y genera:

  - samples_inventory.csv  → una fila por PDF con métricas resumen
  - samples_summary.txt    → resumen estadístico legible

Pensado para correr antes de calibrar el detector: te dice cuáles son
vectoriales puros, cuáles llevan imágenes escaneadas, qué programa los
generó, y qué rangos de radios de círculo aparecen.

Uso:
    python -m tools.catalogar_samples
    python -m tools.catalogar_samples --raiz otra/carpeta --salida cat.csv
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

# Permitir ejecutar tanto con `python -m tools.catalogar_samples` como
# con `python tools/catalogar_samples.py`.
_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from tools.pdf_extractor import extraer_pdf  # noqa: E402


COLUMNAS = (
    "archivo",
    "subcarpeta",
    "tamano_mb",
    "paginas",
    "productor",
    "creador",
    "p1_es_vectorial",
    "p1_ancho_pt",
    "p1_alto_pt",
    "p1_n_circulos",
    "p1_n_lineas",
    "p1_n_textos",
    "p1_n_imagenes",
    "p1_radio_min",
    "p1_radio_p25",
    "p1_radio_mediana",
    "p1_radio_p75",
    "p1_radio_max",
    "p1_fuente_min",
    "p1_fuente_mediana",
    "p1_fuente_max",
)


def catalogar(raiz: Path, salida_csv: Path, salida_resumen: Path) -> int:
    pdfs = sorted(raiz.rglob("*.pdf"))
    if not pdfs:
        print(f"No se encontró ningún PDF bajo {raiz}")
        return 0

    print(f"Encontrados {len(pdfs)} PDFs. Analizando…")
    filas: list[dict] = []

    for i, pdf in enumerate(pdfs, 1):
        rel = pdf.relative_to(raiz)
        print(f"  [{i}/{len(pdfs)}] {rel}")
        try:
            extr = extraer_pdf(pdf, paginas=[0])
        except Exception as e:
            print(f"      ! error: {e}")
            filas.append(_fila_error(pdf, raiz, str(e)))
            continue
        filas.append(_fila_ok(pdf, raiz, extr))

    _escribir_csv(salida_csv, filas)
    _escribir_resumen(salida_resumen, filas)

    print(f"\nCSV:      {salida_csv}")
    print(f"Resumen:  {salida_resumen}")
    return len(filas)


def _fila_error(pdf: Path, raiz: Path, msg: str) -> dict:
    f = {col: "" for col in COLUMNAS}
    f["archivo"] = pdf.name
    f["subcarpeta"] = str(pdf.parent.relative_to(raiz))
    f["tamano_mb"] = f"{pdf.stat().st_size / 1e6:.2f}"
    f["productor"] = f"ERROR: {msg}"
    return f


def _fila_ok(pdf: Path, raiz: Path, extr) -> dict:
    f = {col: "" for col in COLUMNAS}
    f["archivo"] = pdf.name
    f["subcarpeta"] = str(pdf.parent.relative_to(raiz))
    f["tamano_mb"] = f"{pdf.stat().st_size / 1e6:.2f}"
    f["paginas"] = len(extr.paginas)  # solo p0 cargada, no doc.page_count
    f["productor"] = extr.productor
    f["creador"] = extr.creador

    if extr.paginas:
        p = extr.paginas[0]
        f["p1_es_vectorial"] = "sí" if p.es_vectorial else "no"
        f["p1_ancho_pt"] = f"{p.ancho:.1f}"
        f["p1_alto_pt"] = f"{p.alto:.1f}"
        f["p1_n_circulos"] = len(p.circulos)
        f["p1_n_lineas"] = len(p.lineas)
        f["p1_n_textos"] = len(p.textos)
        f["p1_n_imagenes"] = len(p.imagenes)

        radios = sorted(c.r for c in p.circulos)
        if radios:
            f["p1_radio_min"] = f"{radios[0]:.2f}"
            f["p1_radio_p25"] = f"{_percentil(radios, 25):.2f}"
            f["p1_radio_mediana"] = f"{statistics.median(radios):.2f}"
            f["p1_radio_p75"] = f"{_percentil(radios, 75):.2f}"
            f["p1_radio_max"] = f"{radios[-1]:.2f}"

        fuentes = sorted(t.tamano_fuente for t in p.textos if t.tamano_fuente > 0)
        if fuentes:
            f["p1_fuente_min"] = f"{fuentes[0]:.1f}"
            f["p1_fuente_mediana"] = f"{statistics.median(fuentes):.1f}"
            f["p1_fuente_max"] = f"{fuentes[-1]:.1f}"

    return f


def _percentil(valores, p):
    if not valores:
        return 0.0
    k = (len(valores) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(valores) - 1)
    if lo == hi:
        return valores[lo]
    return valores[lo] + (valores[hi] - valores[lo]) * (k - lo)


def _escribir_csv(salida: Path, filas):
    salida.parent.mkdir(parents=True, exist_ok=True)
    with salida.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS, delimiter=";")
        w.writeheader()
        w.writerows(filas)


def _escribir_resumen(salida: Path, filas):
    n = len(filas)
    vect = sum(1 for f in filas if f.get("p1_es_vectorial") == "sí")
    con_img = sum(1 for f in filas if str(f.get("p1_n_imagenes") or "0") not in ("", "0"))
    con_err = sum(1 for f in filas if str(f.get("productor", "")).startswith("ERROR"))

    productores = {}
    for f in filas:
        p = f.get("productor") or "(sin productor)"
        if p.startswith("ERROR"):
            continue
        productores[p] = productores.get(p, 0) + 1

    radios_mediana = [
        float(f["p1_radio_mediana"]) for f in filas
        if f.get("p1_radio_mediana") not in ("", None)
    ]

    lineas: list[str] = []
    lineas.append("=" * 60)
    lineas.append(f"Catálogo de muestras: {n} PDFs analizados")
    lineas.append("=" * 60)
    lineas.append(f"Vectoriales (página 1):  {vect} / {n}")
    lineas.append(f"Con imágenes embebidas:  {con_img} / {n}")
    lineas.append(f"Errores de lectura:      {con_err} / {n}")
    lineas.append("")
    lineas.append("Programas de origen (campo 'productor'):")
    for p, c in sorted(productores.items(), key=lambda x: -x[1]):
        lineas.append(f"  - {c:>4d}× {p}")
    lineas.append("")
    if radios_mediana:
        radios_mediana.sort()
        lineas.append("Distribución de radios MEDIANOS de círculos por PDF (en puntos PDF):")
        lineas.append(f"  min      : {radios_mediana[0]:.2f}")
        lineas.append(f"  p25      : {_percentil(radios_mediana, 25):.2f}")
        lineas.append(f"  mediana  : {statistics.median(radios_mediana):.2f}")
        lineas.append(f"  p75      : {_percentil(radios_mediana, 75):.2f}")
        lineas.append(f"  max      : {radios_mediana[-1]:.2f}")
        lineas.append("")
        lineas.append("(Usa estos valores como pista para los rangos de radio del addon.")
        lineas.append(" 1 punto PDF ≈ 0.353 mm)")
    lineas.append("")

    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text("\n".join(lineas), encoding="utf-8")


def _main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raiz", default="samples", help="Carpeta con los PDFs")
    ap.add_argument("--salida", default="samples_inventory.csv")
    ap.add_argument("--resumen", default="samples_summary.txt")
    args = ap.parse_args()

    raiz = Path(args.raiz).resolve()
    if not raiz.is_dir():
        print(f"No existe la carpeta: {raiz}")
        sys.exit(1)

    catalogar(raiz, Path(args.salida).resolve(), Path(args.resumen).resolve())


if __name__ == "__main__":
    _main()
