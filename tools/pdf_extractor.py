"""Extractor de objetos vectoriales de PDFs de ingeniería.

Lee un PDF, identifica si es vectorial o escaneado, y para cada página
devuelve los círculos, líneas, textos e imágenes con sus coordenadas en
unidades de PDF (puntos = 1/72 pulgada).

Estos datos son la base sobre la que después se aplican las
convenciones específicas del usuario (qué círculo es costura, qué texto
es itemcode, etc.).

Uso CLI:
    python -m tools.pdf_extractor <archivo.pdf> [--json salida.json]
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from math import hypot
from pathlib import Path
from typing import Iterable

try:
    import fitz  # PyMuPDF
except ImportError as e:
    raise SystemExit(
        "Falta PyMuPDF. Instala con: pip install pymupdf"
    ) from e


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------

@dataclass
class Circulo:
    cx: float
    cy: float
    r: float
    stroke: str | None = None
    fill: str | None = None
    relleno: bool = False
    procedencia: str = ""  # "bezier" | "rect_cuadrado" | "poligonal"


@dataclass
class Linea:
    x1: float
    y1: float
    x2: float
    y2: float
    grosor: float = 0.0


@dataclass
class Texto:
    texto: str
    x: float            # esquina inf-izda en coords PDF (y crece hacia arriba en fitz)
    y: float
    ancho: float
    alto: float
    tamano_fuente: float = 0.0
    fuente: str = ""
    rotacion: float = 0.0


@dataclass
class ImagenInfo:
    bbox: tuple[float, float, float, float]
    ancho_px: int
    alto_px: int


@dataclass
class PaginaExtraida:
    numero: int
    ancho: float
    alto: float
    es_vectorial: bool
    circulos: list[Circulo] = field(default_factory=list)
    lineas: list[Linea] = field(default_factory=list)
    textos: list[Texto] = field(default_factory=list)
    imagenes: list[ImagenInfo] = field(default_factory=list)


@dataclass
class ExtraccionPDF:
    archivo: str
    paginas: list[PaginaExtraida] = field(default_factory=list)
    es_vectorial: bool = True
    productor: str = ""
    creador: str = ""

    def resumen(self) -> dict:
        return {
            "archivo": self.archivo,
            "paginas": len(self.paginas),
            "es_vectorial": self.es_vectorial,
            "productor": self.productor,
            "creador": self.creador,
            "total_circulos": sum(len(p.circulos) for p in self.paginas),
            "total_lineas": sum(len(p.lineas) for p in self.paginas),
            "total_textos": sum(len(p.textos) for p in self.paginas),
            "total_imagenes": sum(len(p.imagenes) for p in self.paginas),
        }


# ---------------------------------------------------------------------------
# API principal
# ---------------------------------------------------------------------------

def extraer_pdf(
    ruta: str | Path,
    radio_max_circulo: float = 20.0,
    paginas: Iterable[int] | None = None,
) -> ExtraccionPDF:
    """Lee un PDF y devuelve sus objetos vectoriales.

    `radio_max_circulo` filtra "círculos" muy grandes (que casi nunca
    son marcadores de costura). Se da en puntos PDF.
    """
    ruta = Path(ruta)
    doc = fitz.open(ruta)
    meta = doc.metadata or {}

    extraccion = ExtraccionPDF(
        archivo=str(ruta),
        productor=meta.get("producer", "") or "",
        creador=meta.get("creator", "") or "",
    )

    indices = list(paginas) if paginas is not None else range(doc.page_count)
    for i in indices:
        if not (0 <= i < doc.page_count):
            continue
        page = doc[i]
        extraccion.paginas.append(_extraer_pagina(page, i, radio_max_circulo))

    extraccion.es_vectorial = any(p.es_vectorial for p in extraccion.paginas)
    doc.close()
    return extraccion


def _extraer_pagina(page, numero: int, radio_max_circulo: float) -> PaginaExtraida:
    pag = PaginaExtraida(
        numero=numero,
        ancho=page.rect.width,
        alto=page.rect.height,
        es_vectorial=False,
    )

    # ---- Drawings (paths vectoriales) ----
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []

    for d in drawings:
        items = d.get("items", [])
        stroke = _color_a_hex(d.get("color"))
        fill = _color_a_hex(d.get("fill"))
        grosor = float(d.get("width", 0) or 0)
        relleno = bool(d.get("fill"))

        # Identificar círculos en este drawing
        circulos_d = _extraer_circulos_de_items(items, radio_max_circulo)
        for c in circulos_d:
            c.stroke = stroke
            c.fill = fill
            c.relleno = relleno
            pag.circulos.append(c)

        # Líneas rectas
        for it in items:
            if it[0] == "l":
                _, p1, p2 = it
                pag.lineas.append(Linea(p1.x, p1.y, p2.x, p2.y, grosor))
            elif it[0] == "re":
                # Rectángulo → 4 segmentos
                r = it[1]
                pag.lineas.extend([
                    Linea(r.x0, r.y0, r.x1, r.y0, grosor),
                    Linea(r.x1, r.y0, r.x1, r.y1, grosor),
                    Linea(r.x1, r.y1, r.x0, r.y1, grosor),
                    Linea(r.x0, r.y1, r.x0, r.y0, grosor),
                ])

    # ---- Texto ----
    try:
        td = page.get_text("dict")
    except Exception:
        td = {"blocks": []}

    for bloque in td.get("blocks", []):
        if bloque.get("type", 0) != 0:  # solo bloques de texto (no imagen)
            continue
        for linea in bloque.get("lines", []):
            origen = linea.get("dir", (1.0, 0.0))
            rot = _rotacion_desde_dir(origen)
            for span in linea.get("spans", []):
                texto = span.get("text", "").strip()
                if not texto:
                    continue
                bbox = span.get("bbox", (0, 0, 0, 0))
                pag.textos.append(Texto(
                    texto=texto,
                    x=bbox[0],
                    y=bbox[1],
                    ancho=bbox[2] - bbox[0],
                    alto=bbox[3] - bbox[1],
                    tamano_fuente=float(span.get("size", 0) or 0),
                    fuente=span.get("font", "") or "",
                    rotacion=rot,
                ))

    # ---- Imágenes embebidas (indicador de PDF escaneado total/parcial) ----
    for img in page.get_images(full=True):
        try:
            xref = img[0]
            bbox = page.get_image_bbox(img)
            pix_info = page.parent.extract_image(xref)
            pag.imagenes.append(ImagenInfo(
                bbox=(bbox.x0, bbox.y0, bbox.x1, bbox.y1),
                ancho_px=int(pix_info.get("width", 0) or 0),
                alto_px=int(pix_info.get("height", 0) or 0),
            ))
        except Exception:
            continue

    # Heurística "es vectorial":
    # - tiene objetos vectoriales suficientes
    # - el área ocupada por imágenes no domina la página
    area_pagina = pag.ancho * pag.alto
    area_imagenes = sum(
        max(0, (i.bbox[2] - i.bbox[0])) * max(0, (i.bbox[3] - i.bbox[1]))
        for i in pag.imagenes
    )
    cobertura_img = area_imagenes / area_pagina if area_pagina else 0
    objetos_vect = len(pag.lineas) + len(pag.circulos)
    pag.es_vectorial = objetos_vect > 50 and cobertura_img < 0.7

    return pag


# ---------------------------------------------------------------------------
# Reconocimiento de círculos en items PDF
# ---------------------------------------------------------------------------

def _extraer_circulos_de_items(items, radio_max_circulo: float) -> list[Circulo]:
    """Identifica subpaths que son círculos.

    Estrategias:
      1. Subpath compuesto de curvas Bézier ("c") cuya bbox es cuadrada
         y razonablemente pequeña → círculo aproximado a Bézier (lo más
         común en PDFs de ingeniería).
      2. Subpath cerrado con muchos segmentos cortos y bbox cuadrado
         → círculo poligonal.
    """
    circulos: list[Circulo] = []
    if not items:
        return circulos

    # Particionar items en subpaths (un subpath termina al volver al inicio
    # o al alcanzar otro M-equivalente). En PyMuPDF los items no llevan
    # "moveto" explícito; un nuevo subpath empieza cuando el origen del
    # siguiente item no coincide con el destino del anterior.
    subpaths = _trocear_subpaths(items)

    for sp in subpaths:
        bbox = _bbox_de_items(sp)
        if bbox is None:
            continue
        x0, y0, x1, y1 = bbox
        ancho, alto = x1 - x0, y1 - y0
        if ancho <= 0 or alto <= 0:
            continue
        if max(ancho, alto) > 2 * radio_max_circulo:
            continue
        ratio = min(ancho, alto) / max(ancho, alto)
        if ratio < 0.85:
            continue

        tipos = {it[0] for it in sp}
        proc = None
        if tipos == {"c"} or tipos == {"c", "l"} and sum(1 for it in sp if it[0] == "c") >= 3:
            proc = "bezier"
        elif tipos == {"l"} and len(sp) >= 8:
            proc = "poligonal"

        if proc is None:
            continue

        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        r = (ancho + alto) / 4
        circulos.append(Circulo(cx=cx, cy=cy, r=r, procedencia=proc))

    return circulos


def _trocear_subpaths(items):
    """Agrupa los items consecutivos por subpath.

    Heurística: un nuevo subpath empieza cuando el primer punto del item
    no es continuación del último punto del item anterior.
    """
    subs: list[list] = []
    actual: list = []
    ultimo_punto = None

    for it in items:
        primer, ultimo = _puntos_inicio_fin(it)
        if primer is None or ultimo is None:
            continue
        if ultimo_punto is not None and _dist(primer, ultimo_punto) > 0.05:
            if actual:
                subs.append(actual)
            actual = []
        actual.append(it)
        ultimo_punto = ultimo

    if actual:
        subs.append(actual)
    return subs


def _puntos_inicio_fin(it):
    """Devuelve (punto_inicio, punto_fin) en coordenadas (x, y)."""
    if it[0] == "l":
        _, p1, p2 = it
        return ((p1.x, p1.y), (p2.x, p2.y))
    if it[0] == "c":
        _, p1, _p2, _p3, p4 = it
        return ((p1.x, p1.y), (p4.x, p4.y))
    if it[0] == "re":
        r = it[1]
        return ((r.x0, r.y0), (r.x0, r.y0))
    if it[0] == "qu":
        q = it[1]
        return ((q.ul.x, q.ul.y), (q.ul.x, q.ul.y))
    return (None, None)


def _bbox_de_items(items):
    xs: list[float] = []
    ys: list[float] = []
    for it in items:
        if it[0] == "l":
            _, p1, p2 = it
            xs.extend([p1.x, p2.x]); ys.extend([p1.y, p2.y])
        elif it[0] == "c":
            for p in it[1:]:
                xs.append(p.x); ys.append(p.y)
        elif it[0] == "re":
            r = it[1]
            xs.extend([r.x0, r.x1]); ys.extend([r.y0, r.y1])
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _dist(p1, p2):
    return hypot(p1[0] - p2[0], p1[1] - p2[1])


def _color_a_hex(color) -> str | None:
    if not color:
        return None
    try:
        r, g, b = (int(round(c * 255)) for c in color[:3])
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return None


def _rotacion_desde_dir(direccion) -> float:
    dx, dy = direccion
    from math import atan2, degrees
    return degrees(atan2(dy, dx))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf", help="Ruta al PDF a analizar")
    ap.add_argument("--json", default=None, help="Si se da, vuelca todo a JSON")
    ap.add_argument("--paginas", default=None,
                    help="Lista de páginas 0-indexadas separadas por coma")
    ap.add_argument("--radio-max", type=float, default=20.0)
    args = ap.parse_args()

    pags = None
    if args.paginas:
        pags = [int(x) for x in args.paginas.split(",") if x.strip()]

    extr = extraer_pdf(args.pdf, radio_max_circulo=args.radio_max, paginas=pags)
    resumen = extr.resumen()
    print(json.dumps(resumen, indent=2, ensure_ascii=False))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(_extr_a_dict(extr), f, ensure_ascii=False, indent=2)
        print(f"\nVolcado completo: {args.json}")


def _extr_a_dict(extr: ExtraccionPDF) -> dict:
    return {
        "archivo": extr.archivo,
        "es_vectorial": extr.es_vectorial,
        "productor": extr.productor,
        "creador": extr.creador,
        "paginas": [
            {
                "numero": p.numero,
                "ancho": p.ancho,
                "alto": p.alto,
                "es_vectorial": p.es_vectorial,
                "circulos": [asdict(c) for c in p.circulos],
                "lineas": [asdict(l) for l in p.lineas],
                "textos": [asdict(t) for t in p.textos],
                "imagenes": [asdict(i) for i in p.imagenes],
            }
            for p in extr.paginas
        ],
    }


if __name__ == "__main__":
    _main()
