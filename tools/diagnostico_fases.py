"""Diagnóstico gráfico por fases del pipeline de detección de costuras.

Para un PDF de isométrico genera una serie de PNGs en NEGATIVO (fondo
blanco, plano en gris claro) resaltando con colores qué detecta cada
fase del pipeline. Útil para fine-tunear el detector:

Fases:
  1. **brutos**: todos los círculos candidatos en el rango de radio.
  2. **sobre_linea**: los que están sobre tubería (dist≤tol).
  3. **dedup**: tras fusionar duplicados relleno/contorno.
  4. **filtrados**: tras descartar puntas de flecha (con triángulo cerca).
  5. **wendolets**: tras recuperar pares wendolet (resultado final).
  6. **bom**: piece marks dibujados + textos del BOM.
  7. **atlas**: las 6 fases juntas en una sola imagen (6 paneles 2x3).

Cada PNG lleva su título, leyenda con counts y delta vs fase anterior.

Uso:
    .venv/bin/python -m tools.diagnostico_fases <pdf>
    .venv/bin/python -m tools.diagnostico_fases <pdf> --dpi 220 --solo atlas
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from math import hypot
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

_INK_EXT = "/usr/share/inkscape/extensions"
_USR_EXT = str(Path.home() / ".config/inkscape/extensions")
for p in (_INK_EXT, _USR_EXT):
    if p not in sys.path:
        sys.path.insert(0, p)

import inkex  # noqa: E402

from tools.enriquecer_welding_book import (  # noqa: E402
    cargar_svg, detectar_piece_marks,
)
from tools.extraer_bom import extraer_bom  # noqa: E402
from tramear_core.seam_detector import (  # noqa: E402
    _anadir_wendolets, _buscar_circulos, _deduplicar,
    _distancia_minima_a_segmentos, _extraer_segmentos_lineales,
    _filtrar_puntas_flecha,
)


# Paleta consistente (RGBA)
COL_PLANO = (160, 160, 160)
COL_BRUTO = (180, 30, 200)         # morado
COL_SOBRE_LINEA = (30, 110, 200)   # azul
COL_DEDUP = (30, 170, 30)          # verde claro
COL_FILTRADO = (20, 140, 80)       # verde
COL_FINAL = (220, 30, 30)          # rojo intenso (resultado)
COL_WENDOLET = (255, 130, 0)       # naranja (recuperados)
COL_DESCARTE = (200, 200, 200)     # gris claro
COL_PM = (220, 200, 0)             # amarillo
COL_BOM = (130, 30, 200)           # violeta


@dataclass
class Fase:
    titulo: str
    color: tuple
    explicacion: str


# -----------------------------------------------------------------
# Datos por fase
# -----------------------------------------------------------------

def calcular_fases(svg_path: Path):
    arbol = inkex.load_svg(str(svg_path)).getroot()
    crudos = _buscar_circulos(arbol, 1.4, 2.5)
    segs = _extraer_segmentos_lineales(arbol)

    sobre_linea = []
    for c in crudos:
        d = _distancia_minima_a_segmentos(c.x, c.y, segs)
        c.distancia_linea = d
        c.sobre_linea = d <= 1.5
        if c.sobre_linea:
            sobre_linea.append(c)

    dedup = _deduplicar(sobre_linea)
    filtrados = _filtrar_puntas_flecha(dedup, segs, tolerancia=1.5)
    finales = _anadir_wendolets(filtrados, dedup)

    return {
        "brutos": crudos,
        "sobre_linea": sobre_linea,
        "dedup": dedup,
        "filtrados": filtrados,
        "finales": finales,
        "segmentos": segs,
    }


# -----------------------------------------------------------------
# Render
# -----------------------------------------------------------------

def renderizar_plano(pdf: Path, dpi: int) -> tuple[Image.Image, float]:
    """Renderiza el PDF a una imagen GRIS CLARO (no-texto blanco, texto
    apenas visible) para usar como fondo."""
    import fitz
    doc = fitz.open(pdf)
    page = doc[0]
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()

    # Pasar el plano a tonos muy claros para que los marcadores destaquen.
    arr = np.array(img).astype(np.int16)
    # Mezclar con blanco al 65% para suavizar
    arr = (arr * 0.35 + 255 * 0.65).clip(0, 255).astype(np.uint8)
    return Image.fromarray(arr), zoom


def dibujar_marcador(draw: ImageDraw.ImageDraw, x, y, zoom,
                      color, radio_px: int = 9, grosor: int = 3,
                      relleno: bool = False) -> None:
    cx = x * zoom
    cy = y * zoom
    if relleno:
        draw.ellipse((cx - radio_px, cy - radio_px,
                      cx + radio_px, cy + radio_px),
                     fill=color, outline=color, width=1)
    else:
        draw.ellipse((cx - radio_px, cy - radio_px,
                      cx + radio_px, cy + radio_px),
                     outline=color, width=grosor)


def dibujar_cruz(draw, x, y, zoom, color, s: int = 11, w: int = 3):
    cx = x * zoom
    cy = y * zoom
    draw.line((cx - s, cy - s, cx + s, cy + s), fill=color, width=w)
    draw.line((cx - s, cy + s, cx + s, cy - s), fill=color, width=w)


_OVERLAY_ACTIVO = True  # se desactiva cuando la salida es HTML


# -----------------------------------------------------------------
# Render vectorial (SVG): mantiene calidad infinita en navegador
# -----------------------------------------------------------------

def color_hex(c: tuple) -> str:
    return "#{:02x}{:02x}{:02x}".format(*c)


def _svg_marcador(x, y, color, r=4.5, grosor=1.6, relleno=False) -> str:
    fill = color_hex(color) if relleno else "none"
    return (f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r:.2f}" '
            f'fill="{fill}" stroke="{color_hex(color)}" '
            f'stroke-width="{grosor:.2f}"/>')


def _svg_cruz(x, y, color, s=6, w=2) -> str:
    c = color_hex(color)
    return (f'<g stroke="{c}" stroke-width="{w}" stroke-linecap="round">'
            f'<line x1="{x - s:.2f}" y1="{y - s:.2f}" '
            f'x2="{x + s:.2f}" y2="{y + s:.2f}"/>'
            f'<line x1="{x - s:.2f}" y1="{y + s:.2f}" '
            f'x2="{x + s:.2f}" y2="{y - s:.2f}"/></g>')


def _svg_etiqueta(x, y, texto, color) -> str:
    return (f'<text x="{x:.2f}" y="{y:.2f}" font-family="DejaVu Sans" '
            f'font-size="9" font-weight="bold" fill="{color_hex(color)}">'
            f'{texto}</text>')


def cargar_svg_base(svg_path: Path) -> tuple[str, str, float, float]:
    """Lee el SVG y devuelve (cabecera_hasta_viewbox, contenido_interior,
    width_pt, height_pt). Sirve para construir SVGs derivados eficientes."""
    contenido = svg_path.read_text(encoding="utf-8", errors="ignore")
    # Extraer ancho y alto (en pt o sin unidad)
    import re
    w_m = re.search(r'\bwidth="([0-9.]+)(\w*)"', contenido)
    h_m = re.search(r'\bheight="([0-9.]+)(\w*)"', contenido)
    vb_m = re.search(r'\bviewBox="([^"]+)"', contenido)
    if vb_m:
        _, _, vw, vh = vb_m.group(1).split()
        wpt = float(vw)
        hpt = float(vh)
    else:
        wpt = float(w_m.group(1)) if w_m else 1191.0
        hpt = float(h_m.group(1)) if h_m else 842.0
    return contenido, "", wpt, hpt


def renderizar_fase_svg(svg_base: str, w_pt: float, h_pt: float,
                        marcadores_svg: str,
                        out_path: Path) -> None:
    """Construye un SVG por fase: plano base + velo semi-transparente +
    marcadores vectoriales. Resultado autocontenido.

    Estrategia: inyecta justo antes del cierre `</svg>` un rect blanco
    semitransparente que cubre todo el viewBox y un grupo con los
    marcadores. El plano subyacente queda visible pero atenuado.
    """
    velo = (f'<rect x="0" y="0" width="{w_pt}" height="{h_pt}" '
            f'fill="white" fill-opacity="0.62"/>')
    overlay = (f'<g id="diagnostico-overlay" '
               f'style="vector-effect:non-scaling-stroke">'
               f'{velo}{marcadores_svg}</g>')
    if "</svg>" in svg_base:
        nuevo = svg_base.replace("</svg>", overlay + "</svg>", 1)
    else:
        # SVG truncado o exótico; fallback envolviendo todo.
        nuevo = svg_base + overlay
    out_path.write_text(nuevo, encoding="utf-8")


def pintar_titulo_leyenda(img: Image.Image, titulo: str,
                          leyendas: list[tuple[str, tuple]],
                          explicacion: str) -> None:
    """Overlay con título y leyenda. Cuando _OVERLAY_ACTIVO es False
    no añade nada — útil si el contenedor (HTML) ya tiene su chrome."""
    if not _OVERLAY_ACTIVO:
        return
    draw = ImageDraw.Draw(img)
    try:
        f_titulo = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
        f_normal = ImageFont.truetype("DejaVuSans.ttf", 14)
        f_pie = ImageFont.truetype("DejaVuSans.ttf", 12)
    except Exception:
        f_titulo = f_normal = f_pie = ImageFont.load_default()

    # Banda superior título
    draw.rectangle((0, 0, img.width, 36), fill=(20, 30, 60))
    draw.text((12, 6), titulo, fill=(255, 255, 255), font=f_titulo)

    # Banda inferior leyenda + explicación
    h_pie = 28 + 16 + 14 * (max(1, (len(explicacion) // 110) + 1))
    draw.rectangle((0, img.height - h_pie, img.width, img.height),
                   fill=(245, 245, 250))
    y = img.height - h_pie + 8
    x = 12
    for txt, col in leyendas:
        draw.ellipse((x, y, x + 14, y + 14), fill=col)
        draw.text((x + 20, y - 1), txt, fill=(20, 20, 20), font=f_normal)
        x += draw.textlength(txt, font=f_normal) + 50
    # explicación (envuelve)
    y2 = y + 22
    palabras = explicacion.split()
    linea = ""
    for p in palabras:
        tent = (linea + " " + p).strip()
        if draw.textlength(tent, font=f_pie) > img.width - 24:
            draw.text((12, y2), linea, fill=(60, 60, 60), font=f_pie)
            y2 += 14
            linea = p
        else:
            linea = tent
    if linea:
        draw.text((12, y2), linea, fill=(60, 60, 60), font=f_pie)


# -----------------------------------------------------------------
# Fases
# -----------------------------------------------------------------

def fase_brutos(base, datos, zoom) -> Image.Image:
    img = base.copy()
    draw = ImageDraw.Draw(img)
    for c in datos["brutos"]:
        dibujar_marcador(draw, c.x, c.y, zoom, COL_BRUTO, radio_px=7, grosor=2)
    pintar_titulo_leyenda(
        img,
        f"FASE 1 — Círculos brutos en rango (r 1.4–2.5 pt): {len(datos['brutos'])}",
        [("Círculo candidato", COL_BRUTO)],
        "Todos los elementos circulares (Circle, Ellipse, Path poligonal "
        "cuyo bbox es casi cuadrado) cuyo radio cae en el rango calibrado "
        "para costuras (1.4 a 2.5 pt). Esta fase no aplica ningún filtro "
        "de contexto — solo geometría local.",
    )
    return img


def fase_sobre_linea(base, datos, zoom) -> Image.Image:
    img = base.copy()
    draw = ImageDraw.Draw(img)
    set_sobre = {id(c) for c in datos["sobre_linea"]}
    for c in datos["brutos"]:
        if id(c) not in set_sobre:
            dibujar_marcador(draw, c.x, c.y, zoom, COL_DESCARTE,
                             radio_px=6, grosor=1)
    for c in datos["sobre_linea"]:
        dibujar_marcador(draw, c.x, c.y, zoom, COL_SOBRE_LINEA,
                         radio_px=7, grosor=2)
    delta = len(datos["brutos"]) - len(datos["sobre_linea"])
    pintar_titulo_leyenda(
        img,
        f"FASE 2 — Sobre línea de tubería: {len(datos['sobre_linea'])}  "
        f"(descartados {delta})",
        [("Sobre línea", COL_SOBRE_LINEA), ("Descartado", COL_DESCARTE)],
        "Solo los candidatos que tienen un segmento recto (tubería) a "
        f"≤1.5 pt de distancia se mantienen. Esto descarta marcadores "
        "del cajetín, BOM y leyendas que están aislados.",
    )
    return img


def fase_dedup(base, datos, zoom) -> Image.Image:
    img = base.copy()
    draw = ImageDraw.Draw(img)
    set_dedup = {(round(c.x, 1), round(c.y, 1)) for c in datos["dedup"]}
    fusionados = 0
    for c in datos["sobre_linea"]:
        if (round(c.x, 1), round(c.y, 1)) not in set_dedup:
            dibujar_marcador(draw, c.x, c.y, zoom, COL_DESCARTE,
                             radio_px=5, grosor=1)
            fusionados += 1
    for c in datos["dedup"]:
        dibujar_marcador(draw, c.x, c.y, zoom, COL_DEDUP,
                         radio_px=8, grosor=2,
                         relleno=c.relleno)
    pintar_titulo_leyenda(
        img,
        f"FASE 3 — Deduplicación: {len(datos['dedup'])}  "
        f"(fusionados {fusionados})",
        [("Único (lleno=relleno)", COL_DEDUP), ("Duplicado fusionado", COL_DESCARTE)],
        "Los PDFs dibujan cada punto negro como dos paths superpuestos "
        "(relleno + contorno). Se fusionan los que están a <1 pt entre "
        "ellos; se prefiere el path con relleno (es el visible).",
    )
    return img


def fase_filtrados(base, datos, zoom) -> Image.Image:
    img = base.copy()
    draw = ImageDraw.Draw(img)
    set_fil = {(round(c.x, 1), round(c.y, 1)) for c in datos["filtrados"]}
    descartes = 0
    for c in datos["dedup"]:
        if (round(c.x, 1), round(c.y, 1)) not in set_fil:
            dibujar_marcador(draw, c.x, c.y, zoom, COL_DESCARTE,
                             radio_px=7, grosor=2)
            dibujar_cruz(draw, c.x, c.y, zoom, COL_DESCARTE, s=6, w=2)
            descartes += 1
    for c in datos["filtrados"]:
        dibujar_marcador(draw, c.x, c.y, zoom, COL_FILTRADO,
                         radio_px=9, grosor=3, relleno=False)
    pintar_titulo_leyenda(
        img,
        f"FASE 4 — Tras filtro de puntas de flecha: "
        f"{len(datos['filtrados'])}  (descartadas {descartes})",
        [("Conservado", COL_FILTRADO), ("Descartado (triángulo cerca)", COL_DESCARTE)],
        "Si cerca del punto hay un triángulo cerrado (≤10 pt) — típica "
        "punta de flecha de cota o leyenda — se descarta. Este filtro "
        "es la causa principal de wendolets perdidos (el triángulo del "
        "injerto se confunde con flecha).",
    )
    return img


def fase_wendolets(base, datos, zoom) -> Image.Image:
    img = base.copy()
    draw = ImageDraw.Draw(img)
    set_fil = {(round(c.x, 1), round(c.y, 1)) for c in datos["filtrados"]}
    rescatadas = [c for c in datos["finales"]
                  if (round(c.x, 1), round(c.y, 1)) not in set_fil]
    for c in datos["filtrados"]:
        dibujar_marcador(draw, c.x, c.y, zoom, COL_FINAL,
                         radio_px=9, grosor=3)
    for c in rescatadas:
        dibujar_marcador(draw, c.x, c.y, zoom, COL_WENDOLET,
                         radio_px=11, grosor=3, relleno=True)
    pintar_titulo_leyenda(
        img,
        f"FASE 5 — Resultado final (wendolets recuperados): "
        f"{len(datos['finales'])}  (+{len(rescatadas)})",
        [("Costura confirmada", COL_FINAL), ("Wendolet recuperado", COL_WENDOLET)],
        "Recupera los segundos puntos de pares wendolet: rellenos, "
        "mismo radio (Δ≤0.15), distancia 6-10 pt, alineación X o Y. "
        "La alineación distingue par real de FP arbitrario.",
    )
    return img


def fase_bom(base, datos, zoom, pdf: Path) -> Image.Image:
    img = base.copy()
    draw = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("DejaVuSans-Bold.ttf", 13)
    except Exception:
        f = ImageFont.load_default()

    items = extraer_bom(pdf)
    pms = detectar_piece_marks(pdf, items)

    # Resultado final también
    for c in datos["finales"]:
        dibujar_marcador(draw, c.x, c.y, zoom, COL_FINAL,
                         radio_px=8, grosor=2)

    # Piece marks
    for pm in pms:
        dibujar_cruz(draw, pm.x, pm.y, zoom, COL_PM, s=12, w=3)
        draw.text((pm.x * zoom + 14, pm.y * zoom - 8),
                  f"PT{pm.pt_no}", fill=(180, 130, 0), font=f)

    pintar_titulo_leyenda(
        img,
        f"FASE 6 — Cruce con BOM: "
        f"{len(datos['finales'])} costuras · {len(items)} items BOM · "
        f"{len(pms)} piece marks en plano",
        [("Costura final", COL_FINAL), ("Piece mark", COL_PM)],
        "Cada piece mark (numerito en el plano) corresponde a un PT NO "
        "del BOM. Asociando costuras a sus 2 piece marks más cercanos "
        "obtenemos itemcode, descripción y diámetro de la unión soldada.",
    )
    return img


# -----------------------------------------------------------------
# Atlas (2x3)
# -----------------------------------------------------------------

def construir_atlas(fases: list[Image.Image]) -> Image.Image:
    """Pega 6 imágenes en una rejilla 2x3 reducida."""
    if not fases:
        return None
    cols, rows = 3, 2
    # Escalado para que la imagen final sea manejable.
    w0, h0 = fases[0].size
    target_w = 1200
    factor = target_w / w0
    tw = int(w0 * factor)
    th = int(h0 * factor)
    atlas = Image.new("RGB", (cols * tw + (cols + 1) * 10,
                              rows * th + (rows + 1) * 10),
                      (255, 255, 255))
    for i, im in enumerate(fases[:6]):
        r = i // cols
        c = i % cols
        small = im.resize((tw, th), Image.LANCZOS)
        atlas.paste(small, (10 + c * (tw + 10), 10 + r * (th + 10)))
    return atlas


# -----------------------------------------------------------------
# Versiones SVG de cada fase
# -----------------------------------------------------------------

def marcadores_svg_brutos(datos) -> str:
    return "".join(
        _svg_marcador(c.x, c.y, COL_BRUTO, r=3.6, grosor=1.3)
        for c in datos["brutos"]
    )


def marcadores_svg_sobre_linea(datos) -> str:
    set_sobre = {id(c) for c in datos["sobre_linea"]}
    partes = []
    for c in datos["brutos"]:
        if id(c) not in set_sobre:
            partes.append(_svg_marcador(c.x, c.y, COL_DESCARTE, r=2.8, grosor=0.8))
    for c in datos["sobre_linea"]:
        partes.append(_svg_marcador(c.x, c.y, COL_SOBRE_LINEA, r=3.6, grosor=1.3))
    return "".join(partes)


def marcadores_svg_dedup(datos) -> str:
    set_dedup = {(round(c.x, 1), round(c.y, 1)) for c in datos["dedup"]}
    partes = []
    for c in datos["sobre_linea"]:
        if (round(c.x, 1), round(c.y, 1)) not in set_dedup:
            partes.append(_svg_marcador(c.x, c.y, COL_DESCARTE, r=2.4, grosor=0.6))
    for c in datos["dedup"]:
        partes.append(_svg_marcador(c.x, c.y, COL_DEDUP, r=4.0, grosor=1.3,
                                     relleno=c.relleno))
    return "".join(partes)


def marcadores_svg_filtrados(datos) -> str:
    set_fil = {(round(c.x, 1), round(c.y, 1)) for c in datos["filtrados"]}
    partes = []
    for c in datos["dedup"]:
        if (round(c.x, 1), round(c.y, 1)) not in set_fil:
            partes.append(_svg_marcador(c.x, c.y, COL_DESCARTE, r=3.6, grosor=1.0))
            partes.append(_svg_cruz(c.x, c.y, COL_DESCARTE, s=2.4, w=0.9))
    for c in datos["filtrados"]:
        partes.append(_svg_marcador(c.x, c.y, COL_FILTRADO, r=4.5, grosor=1.6))
    return "".join(partes)


def marcadores_svg_wendolets(datos) -> str:
    set_fil = {(round(c.x, 1), round(c.y, 1)) for c in datos["filtrados"]}
    partes = []
    for c in datos["filtrados"]:
        partes.append(_svg_marcador(c.x, c.y, COL_FINAL, r=4.5, grosor=1.6))
    for c in datos["finales"]:
        if (round(c.x, 1), round(c.y, 1)) not in set_fil:
            partes.append(_svg_marcador(c.x, c.y, COL_WENDOLET,
                                         r=5.2, grosor=1.6, relleno=True))
    return "".join(partes)


def marcadores_svg_bom(datos, pdf: Path) -> str:
    from tools.extraer_bom import extraer_bom
    items = extraer_bom(pdf)
    pms = detectar_piece_marks(pdf, items)
    partes = []
    for c in datos["finales"]:
        partes.append(_svg_marcador(c.x, c.y, COL_FINAL, r=4.0, grosor=1.4))
    for pm in pms:
        partes.append(_svg_cruz(pm.x, pm.y, COL_PM, s=5.5, w=1.6))
        partes.append(_svg_etiqueta(pm.x + 6, pm.y - 3, f"PT{pm.pt_no}", COL_PM))
    return "".join(partes)


GENERADORES_SVG = {
    "brutos": marcadores_svg_brutos,
    "sobre_linea": marcadores_svg_sobre_linea,
    "dedup": marcadores_svg_dedup,
    "filtrados": marcadores_svg_filtrados,
    "wendolets": marcadores_svg_wendolets,
}


# -----------------------------------------------------------------
# Informe HTML
# -----------------------------------------------------------------

FASES_META = [
    {
        "id": "brutos",
        "n": 1,
        "titulo": "Círculos brutos en rango",
        "explicacion": (
            "Todos los elementos circulares del SVG (Circle, Ellipse, Path "
            "poligonal con bbox casi cuadrada) cuyo radio cae en el "
            "intervalo calibrado para costuras Repsol (1.4–2.5 pt). No se "
            "aplica ningún filtro de contexto: solo geometría local. "
            "Esta es la red más amplia — todo lo que se podría considerar."
        ),
        "leyenda": [("Círculo candidato", COL_BRUTO)],
        "n_callback": lambda d: len(d["brutos"]),
    },
    {
        "id": "sobre_linea",
        "n": 2,
        "titulo": "Sobre línea de tubería",
        "explicacion": (
            "Filtro de contexto: el candidato debe tener un segmento "
            "recto (tubería) a ≤1.5 pt. Esto descarta los círculos "
            "aislados del cajetín, BOM y leyendas. En estos PDFs Repsol "
            "casi todos los candidatos ya están sobre tubería, así que "
            "la fase suele descartar pocos."
        ),
        "leyenda": [("Sobre línea", COL_SOBRE_LINEA), ("Descartado", COL_DESCARTE)],
        "n_callback": lambda d: len(d["sobre_linea"]),
    },
    {
        "id": "dedup",
        "n": 3,
        "titulo": "Deduplicación de duplicados PDF",
        "explicacion": (
            "Los PDFs dibujan cada punto negro de costura como DOS paths "
            "superpuestos (relleno + contorno). Se fusionan los que están "
            "a <1 pt entre ellos. Si uno tiene relleno y otro no, se "
            "queda el relleno. El descenso de número entre fase 2 y 3 "
            "es aproximadamente x5 (de 199 a 40)."
        ),
        "leyenda": [
            ("Único (lleno=relleno)", COL_DEDUP),
            ("Duplicado fusionado", COL_DESCARTE),
        ],
        "n_callback": lambda d: len(d["dedup"]),
    },
    {
        "id": "filtrados",
        "n": 4,
        "titulo": "Filtro de puntas de flecha",
        "explicacion": (
            "Si junto al punto hay un triángulo cerrado a ≤10 pt — típica "
            "punta de flecha de cota o leyenda — se descarta el punto. "
            "Este filtro es necesario porque las flechas también terminan "
            "en pequeños círculos llenos. <strong>Es la causa principal "
            "de wendolets perdidos</strong>: el triángulo del injerto se "
            "confunde con punta de flecha."
        ),
        "leyenda": [
            ("Conservado", COL_FILTRADO),
            ("Descartado (triángulo cerca)", COL_DESCARTE),
        ],
        "n_callback": lambda d: len(d["filtrados"]),
    },
    {
        "id": "wendolets",
        "n": 5,
        "titulo": "Recuperación de wendolets",
        "explicacion": (
            "Pasada que recupera segundos puntos de pares wendolet "
            "(injertos): rellenos, mismo radio (Δ≤0.15), distancia 6–10 pt, "
            "alineación X o Y (eje del isométrico). La alineación es la "
            "clave para distinguir par real de FP arbitrario. Se exige "
            "par mutuo: A es pareja de B si y solo si B es pareja de A."
        ),
        "leyenda": [
            ("Costura confirmada", COL_FINAL),
            ("Wendolet recuperado", COL_WENDOLET),
        ],
        "n_callback": lambda d: len(d["finales"]),
    },
    {
        "id": "bom",
        "n": 6,
        "titulo": "Cruce con Bill of Material",
        "explicacion": (
            "Cada piece mark dibujado (numerito junto a la tubería) "
            "corresponde a un PT NO del BOM. Asociando cada costura a "
            "sus 2 piece marks más cercanos obtenemos: itemcode "
            "(I117921), descripción del componente, diámetro de la unión. "
            "Para piece marks de categoría soldable que NO tienen "
            "costura cerca, podemos lanzar una pasada de rescate."
        ),
        "leyenda": [("Costura final", COL_FINAL), ("Piece mark", COL_PM)],
        "n_callback": lambda d: len(d["finales"]),
    },
]


def color_a_hex(rgb: tuple) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def generar_html(out_dir: Path, pdf: Path, datos: dict,
                 imagenes_rel: dict[str, str]) -> Path:
    from datetime import datetime
    from tools.extraer_bom import extraer_bom
    from tools.extraer_cajetin import extraer_cajetin

    cajetin = extraer_cajetin(pdf)
    items_bom = extraer_bom(pdf)

    titulo_doc = cajetin.sheet or pdf.stem
    linea = cajetin.linea or "(no detectada)"
    revision = cajetin.revision or "—"

    # Construir tabla resumen de fases
    filas_fases = []
    prev_n = None
    for meta in FASES_META:
        n_actual = meta["n_callback"](datos)
        delta = ""
        if prev_n is not None:
            d = n_actual - prev_n
            if d > 0:
                delta = f"<span class='delta delta-up'>+{d}</span>"
            elif d < 0:
                delta = f"<span class='delta delta-down'>{d}</span>"
            else:
                delta = "<span class='delta delta-eq'>=</span>"
        filas_fases.append(
            f"<tr><td class='num'>{meta['n']}</td>"
            f"<td><a href='#fase-{meta['id']}'>{meta['titulo']}</a></td>"
            f"<td class='num'><strong>{n_actual}</strong></td>"
            f"<td class='num'>{delta}</td></tr>"
        )
        prev_n = n_actual

    # Secciones por fase
    secciones = []
    for meta in FASES_META:
        n = meta["n_callback"](datos)
        leyenda_html = "".join(
            f"<span class='leyenda-item'>"
            f"<span class='chip' style='background:{color_a_hex(col)}'></span>"
            f"{txt}</span>"
            for txt, col in meta["leyenda"]
        )
        img_rel = imagenes_rel.get(meta["id"], "")
        secciones.append(f"""
<section id="fase-{meta['id']}" class="fase">
  <header>
    <span class="num-badge">Fase {meta['n']}</span>
    <h2>{meta['titulo']}</h2>
    <span class="contador">{n} elementos</span>
  </header>
  <div class="leyenda">{leyenda_html}</div>
  <p class="explicacion">{meta['explicacion']}</p>
  <figure>
    <img src="{img_rel}" alt="{meta['titulo']}" loading="lazy">
  </figure>
</section>""")

    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Diagnóstico de detección — {titulo_doc}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
    color: #1c2333;
    background: #f5f7fa;
    line-height: 1.5;
  }}
  header.doc {{
    background: linear-gradient(135deg, #1f3a68 0%, #2f5496 100%);
    color: white;
    padding: 24px 32px;
    border-bottom: 4px solid #f7ca18;
  }}
  header.doc h1 {{ margin: 0 0 4px 0; font-size: 24px; }}
  header.doc .meta {{ font-size: 13px; opacity: 0.9; }}
  header.doc .meta strong {{ color: #f7ca18; }}

  .layout {{ display: grid; grid-template-columns: 240px 1fr;
            min-height: calc(100vh - 90px); }}
  nav.lateral {{
    background: white; padding: 20px 18px;
    border-right: 1px solid #dde2e8;
    position: sticky; top: 0; align-self: start;
    height: 100vh; overflow-y: auto;
  }}
  nav.lateral h3 {{ font-size: 12px; text-transform: uppercase;
                   color: #666; letter-spacing: 0.06em;
                   margin: 18px 0 8px 0; }}
  nav.lateral table {{ width: 100%; border-collapse: collapse;
                      font-size: 13px; }}
  nav.lateral td {{ padding: 4px 4px; border-bottom: 1px solid #eef1f5; }}
  nav.lateral td.num {{ text-align: right; color: #666; }}
  nav.lateral a {{ color: #2f5496; text-decoration: none; }}
  nav.lateral a:hover {{ text-decoration: underline; }}
  .delta {{ display: inline-block; padding: 1px 6px;
           border-radius: 8px; font-size: 11px; }}
  .delta-down {{ background: #fee; color: #c00; }}
  .delta-up   {{ background: #efe; color: #060; }}
  .delta-eq   {{ background: #eef1f5; color: #666; }}

  main {{ padding: 28px 36px; max-width: 1280px; }}
  section.fase {{
    background: white;
    border-radius: 8px;
    padding: 22px 26px;
    margin-bottom: 28px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  }}
  section.fase header {{ display: flex; align-items: center;
                        gap: 14px; margin-bottom: 12px; }}
  .num-badge {{
    background: #2f5496; color: white; font-weight: bold;
    padding: 4px 10px; border-radius: 6px; font-size: 13px;
  }}
  section.fase h2 {{ margin: 0; font-size: 19px; flex: 1; }}
  .contador {{ background: #f5f7fa; padding: 4px 10px;
              border-radius: 6px; color: #444; font-size: 13px; }}
  .leyenda {{ display: flex; gap: 18px; flex-wrap: wrap;
             margin-bottom: 10px; font-size: 13px; }}
  .leyenda-item {{ display: flex; align-items: center; gap: 6px; }}
  .chip {{ display: inline-block; width: 14px; height: 14px;
          border-radius: 50%; border: 1px solid rgba(0,0,0,0.1); }}
  .explicacion {{ margin: 0 0 14px 0; color: #455065;
                 font-size: 13.5px; max-width: 78ch; }}
  figure {{ margin: 0; background: #fafbfc; border-radius: 6px;
           padding: 8px; border: 1px solid #e6eaf0; }}
  figure img {{ width: 100%; height: auto; display: block;
               border-radius: 4px; }}

  /* IMPRESIÓN: una fase por página, sin nav */
  @media print {{
    body {{ background: white; font-size: 11pt; }}
    header.doc {{ background: white; color: #1c2333;
                 border-bottom: 2px solid #2f5496; padding: 12pt 0; }}
    header.doc .meta {{ opacity: 1; color: #455065; }}
    header.doc .meta strong {{ color: #1c2333; }}
    .layout {{ display: block; }}
    nav.lateral, .btn-flotante {{ display: none; }}
    main {{ padding: 0; max-width: none; }}
    section.fase {{
      box-shadow: none; border: 1px solid #dde2e8;
      padding: 18pt 22pt; margin: 12pt 0;
      page-break-before: always;
      break-before: page;
    }}
    section.fase:first-of-type {{ page-break-before: auto; }}
    figure img {{ max-height: 70vh; object-fit: contain; }}
  }}
  @page {{ size: A4 landscape; margin: 10mm; }}

  .btn-flotante {{
    position: fixed; bottom: 24px; right: 24px;
    background: #2f5496; color: white; border: none;
    padding: 12px 18px; border-radius: 26px; cursor: pointer;
    box-shadow: 0 4px 12px rgba(47,84,150,0.4);
    font-size: 14px;
  }}
  .btn-flotante:hover {{ background: #1f3a68; }}
</style>
</head>
<body>

<header class="doc">
  <h1>Diagnóstico de detección de costuras</h1>
  <div class="meta">
    Isométrico <strong>{titulo_doc}</strong>
    · Línea <strong>{linea}</strong>
    · Rev <strong>{revision}</strong>
    · BOM: <strong>{len(items_bom)}</strong> items
    · Generado: {fecha}
  </div>
</header>

<div class="layout">
  <nav class="lateral">
    <h3>Resumen de fases</h3>
    <table>
      <thead><tr><th></th><th>Fase</th><th class="num">N</th><th class="num">Δ</th></tr></thead>
      <tbody>
        {''.join(filas_fases)}
      </tbody>
    </table>
    <h3>Pipeline</h3>
    <p style="font-size:12px;color:#666;line-height:1.4;">
      Cada fase aplica un filtro de mayor especificidad. La salida final
      cruza con el BOM extraído del propio plano para enriquecer el
      welding book con itemcode, descripción y diámetro de la costura.
    </p>
  </nav>
  <main>
    {''.join(secciones)}
  </main>
</div>

<button class="btn-flotante" onclick="window.print()">🖨 Imprimir / Exportar PDF</button>

</body>
</html>
"""
    out_html = out_dir / "index.html"
    out_html.write_text(html, encoding="utf-8")
    return out_html


# -----------------------------------------------------------------
# main
# -----------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--salida-dir", default=None,
                    help="Directorio de salida; por defecto al lado del PDF.")
    ap.add_argument("--solo", choices=[
        "brutos", "sobre_linea", "dedup", "filtrados", "wendolets",
        "bom", "atlas", "todas",
    ], default="todas")
    ap.add_argument("--html", action="store_true",
                    help="Generar también index.html (sin overlay en PNGs).")
    args = ap.parse_args()

    global _OVERLAY_ACTIVO
    if args.html:
        _OVERLAY_ACTIVO = False

    pdf = Path(args.pdf)
    if not pdf.is_file():
        sys.exit(f"PDF no existe: {pdf}")

    out_dir = (Path(args.salida_dir) if args.salida_dir
               else pdf.parent / f"{pdf.stem}_diagnostico_fases")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Procesando: {pdf.name}")
    svg = cargar_svg(pdf)
    datos = calcular_fases(svg)
    base, zoom = renderizar_plano(pdf, args.dpi)
    print(f"  brutos={len(datos['brutos'])}  "
          f"linea={len(datos['sobre_linea'])}  "
          f"dedup={len(datos['dedup'])}  "
          f"fil={len(datos['filtrados'])}  "
          f"final={len(datos['finales'])}")

    generadores = {
        "brutos": lambda: fase_brutos(base, datos, zoom),
        "sobre_linea": lambda: fase_sobre_linea(base, datos, zoom),
        "dedup": lambda: fase_dedup(base, datos, zoom),
        "filtrados": lambda: fase_filtrados(base, datos, zoom),
        "wendolets": lambda: fase_wendolets(base, datos, zoom),
        "bom": lambda: fase_bom(base, datos, zoom, pdf),
    }

    orden = ["brutos", "sobre_linea", "dedup", "filtrados", "wendolets", "bom"]
    a_generar = orden if args.solo in ("todas", "atlas") else [args.solo]

    imagenes = []
    for nombre in a_generar:
        im = generadores[nombre]()
        ruta = out_dir / f"fase_{orden.index(nombre)+1}_{nombre}.png"
        im.save(ruta)
        imagenes.append(im)
        print(f"  + {ruta.name}")

    if args.solo in ("todas", "atlas") and len(imagenes) == 6:
        atlas = construir_atlas(imagenes)
        ruta_atlas = out_dir / "atlas.png"
        atlas.save(ruta_atlas)
        print(f"  + {ruta_atlas.name}")

    if args.html and args.solo == "todas":
        # Generar también SVGs vectoriales (calidad infinita en navegador)
        svg_base, _, w_pt, h_pt = cargar_svg_base(svg)
        imagenes_rel = {}
        for nombre in orden:
            if nombre == "bom":
                marcadores = marcadores_svg_bom(datos, pdf)
            else:
                marcadores = GENERADORES_SVG[nombre](datos)
            n = orden.index(nombre) + 1
            ruta_svg = out_dir / f"fase_{n}_{nombre}.svg"
            renderizar_fase_svg(svg_base, w_pt, h_pt, marcadores, ruta_svg)
            imagenes_rel[nombre] = ruta_svg.name
            print(f"  + {ruta_svg.name}")
        html_path = generar_html(out_dir, pdf, datos, imagenes_rel)
        print(f"  + {html_path.name}  (abrir en navegador, imprimir → PDF)")

    print(f"Salida: {out_dir}")


if __name__ == "__main__":
    main()
