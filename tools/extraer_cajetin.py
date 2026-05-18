"""Extractor de datos del cajetín / título de cada isométrico.

Para cada PDF (vectorial), extrae los textos con sus posiciones y
aplica patrones regex sobre la zona del cajetín (esquinas) para
identificar:
  - Número de línea (ej. 2"-BU-10C13-1E21)
  - Código de documento (ej. 4274-XH-DL-2121BU10C13-1)
  - Número de hoja / sheet
  - Revisión (ej. 01)
  - Estado (APPROVED FOR CONSTRUCTION / ISSUED FOR REFERENCE / HOLD)
  - Cliente / proyecto (si aparece)

Uso CLI:
    .venv/bin/python -m tools.extraer_cajetin <pdf>
    .venv/bin/python -m tools.extraer_cajetin --carpeta 0-ISOS\\ 2121 --salida cajetines.csv

El módulo también expone `extraer_cajetin(pdf_path) -> dict` para
usarse como librería desde el welding book consolidado.
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


# ---------------------------------------------------------------------------
# Patrones de los planos Repsol/Alba Project. Para otras empresas habrá
# que ajustar — los regex están sueltos para tolerar variaciones.
# ---------------------------------------------------------------------------

PATRONES = {
    # Línea: <diámetro>"-<spec>-<serial>
    # Ej.: 2"-BU-10C13-1E21,  10"-CO-40F01-1A2A
    "linea": re.compile(
        r'\b\d+(?:\.\d+)?"\s*-\s*[A-Z]{1,4}\s*-\s*\w{3,}\s*-\s*\w{3,}\b'
    ),
    # Documento: típicamente 4274-XX-XX-<hoja>
    "doc": re.compile(r'\b\d{4}-[A-Z]{2}-[A-Z]{2}-\w{8,}\b'),
    # Sheet code: ej. 2121BU10C13-1, 2121HPN40A07-3, 2121IA91FAJ-1
    "sheet": re.compile(r'\b21\d{2}[A-Z0-9]{6,15}-\d+\b'),
    "sheet_filename": re.compile(r'sheet\s+(21\d{2}[A-Z0-9]+-\d+)', re.IGNORECASE),
    # Revisión: número de 1-3 dígitos seguido de letra opcional
    "rev": re.compile(r'\bREV\.?\s*[A-Z]*\s*:?\s*(\d{1,3}[A-Z]?)\b', re.IGNORECASE),
    # Revisión en el nombre del archivo: _IS01, _IS00A, etc.
    "rev_filename": re.compile(r'_IS(\d{1,3}[A-Z]?)(?:\.pdf)?$', re.IGNORECASE),
    # Estado del documento
    "estado_approved": re.compile(
        r'\bAPPROVED\s+FOR\s+CONSTRUCTION\b', re.IGNORECASE),
    "estado_issued": re.compile(
        r'\bISSUED\s+FOR\s+(?:REFERENCE|REVIEW|CONSTRUCTION|APPROVAL)\b',
        re.IGNORECASE),
    "estado_hold": re.compile(r'\bHOLD\b'),
    # NPS / diámetro nominal independiente
    "diametro_nps": re.compile(r'\b(\d+(?:\.\d+)?)"\s*N(?:PS|PD)\b', re.IGNORECASE),
}


@dataclass
class DatosCajetin:
    archivo: str = ""
    linea: str = ""
    documento: str = ""
    sheet: str = ""
    revision: str = ""
    estado: str = ""
    diametro: str = ""
    cliente: str = ""
    proyecto: str = ""
    notas: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def extraer_cajetin(pdf_path: str | Path) -> DatosCajetin:
    pdf_path = Path(pdf_path)
    extr = extraer_pdf(pdf_path, paginas=[0])
    if not extr.paginas:
        return DatosCajetin(archivo=str(pdf_path))
    p = extr.paginas[0]

    # Construir texto concatenado (todos los spans), preservando duplicados
    # para que regex pille incluso cosas separadas en celdas distintas.
    textos = [t.texto for t in p.textos]
    todo = " ".join(textos)

    d = DatosCajetin(archivo=pdf_path.name)

    if (m := PATRONES["linea"].search(todo)):
        d.linea = m.group(0).strip()
    if (m := PATRONES["doc"].search(todo)):
        d.documento = m.group(0).strip()
    if (m := PATRONES["sheet"].search(todo)):
        d.sheet = m.group(0).strip()
    elif (m := PATRONES["sheet_filename"].search(pdf_path.name)):
        d.sheet = m.group(1).strip()
    if (m := PATRONES["rev"].search(todo)):
        d.revision = m.group(1).strip()
    elif (m := PATRONES["rev_filename"].search(pdf_path.name)):
        # Fallback: revisión del sufijo _ISNN del nombre del PDF.
        d.revision = m.group(1).strip()
    if (m := PATRONES["diametro_nps"].search(todo)):
        d.diametro = m.group(1).strip() + "\""

    # Estado: prioridad APPROVED > ISSUED > HOLD si coinciden varios
    if PATRONES["estado_approved"].search(todo):
        d.estado = "APPROVED FOR CONSTRUCTION"
    elif PATRONES["estado_issued"].search(todo):
        m = PATRONES["estado_issued"].search(todo)
        d.estado = m.group(0).upper()
    elif PATRONES["estado_hold"].search(todo):
        d.estado = "HOLD"

    # Cliente y proyecto: patrones específicos Repsol/Alba.
    # Si encontramos "REPSOL" en algún texto, usar.
    for t in textos:
        u = t.upper()
        if "REPSOL" in u and not d.cliente:
            d.cliente = "REPSOL"
        if ("ALBA PROJECT" in u or "ALBA" == u) and not d.proyecto:
            d.proyecto = "ALBA PROJECT"
        if "POLYETHILENE" in u or "POLYPROPYLENE" in u or "POLIPROPILENO" in u:
            if not d.proyecto:
                d.proyecto = t.strip()

    # Notas: buscar líneas que empiecen por "NOTES:" o que contengan
    # palabras clave de obra. Mantenemos solo si son cortas.
    for t in textos:
        if len(t) > 200:
            continue
        u = t.upper()
        if u.startswith("NOTES:") or u.startswith("REMARKS"):
            d.notas.append(t.strip())

    return d


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _filas_csv(datos: list[DatosCajetin]) -> list[dict]:
    out = []
    for d in datos:
        f = asdict(d)
        f["notas"] = " | ".join(d.notas) if d.notas else ""
        out.append(f)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf", nargs="?", help="Un solo PDF a analizar")
    ap.add_argument("--carpeta", help="Procesar todos los PDFs de una carpeta")
    ap.add_argument("--salida", default="cajetines.csv",
                    help="CSV de salida (default: cajetines.csv)")
    args = ap.parse_args()

    pdfs: list[Path] = []
    if args.pdf:
        pdfs = [Path(args.pdf)]
    elif args.carpeta:
        pdfs = sorted(Path(args.carpeta).glob("*.pdf"))
    else:
        ap.error("Debes dar un PDF o --carpeta")

    datos = []
    for i, pdf in enumerate(pdfs, 1):
        if i % 25 == 0 or i == len(pdfs):
            print(f"  [{i}/{len(pdfs)}] {pdf.name[:60]}")
        try:
            d = extraer_cajetin(pdf)
        except Exception as e:
            print(f"    ! error: {e}")
            d = DatosCajetin(archivo=pdf.name, notas=[f"ERROR: {e}"])
        datos.append(d)

    if len(pdfs) == 1:
        # Output legible
        d = datos[0]
        print()
        print(f"Archivo:    {d.archivo}")
        print(f"Línea:      {d.linea or '—'}")
        print(f"Documento:  {d.documento or '—'}")
        print(f"Sheet:      {d.sheet or '—'}")
        print(f"Revisión:   {d.revision or '—'}")
        print(f"Estado:     {d.estado or '—'}")
        print(f"Diámetro:   {d.diametro or '—'}")
        print(f"Cliente:    {d.cliente or '—'}")
        print(f"Proyecto:   {d.proyecto or '—'}")
        if d.notas:
            print("Notas:")
            for n in d.notas:
                print(f"  - {n[:120]}")
    else:
        # Volcar CSV
        salida = Path(args.salida).resolve()
        filas = _filas_csv(datos)
        cols = ["archivo", "linea", "documento", "sheet", "revision",
                "estado", "diametro", "cliente", "proyecto", "notas"]
        with salida.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols, delimiter=";")
            w.writeheader()
            for fila in filas:
                w.writerow({k: fila.get(k, "") for k in cols})
        print(f"\nCSV escrito: {salida}")
        n_con_linea = sum(1 for d in datos if d.linea)
        n_con_doc = sum(1 for d in datos if d.documento)
        n_con_estado = sum(1 for d in datos if d.estado)
        print(f"  con línea:    {n_con_linea}/{len(datos)}")
        print(f"  con doc:      {n_con_doc}/{len(datos)}")
        print(f"  con estado:   {n_con_estado}/{len(datos)}")


if __name__ == "__main__":
    main()
