#!/usr/bin/env python3
"""Extensión Inkscape: trameado de isométricos de tubería.

Detecta automáticamente las costuras de soldadura (marcadas como
círculos pequeños sobre las uniones), las numera y exporta el welding
book.

Para que Inkscape la encuentre, este archivo y la carpeta `tramear_core`
deben copiarse a la carpeta de extensiones del usuario
(Edición → Preferencias → Sistema → Extensiones del usuario).
"""

from __future__ import annotations

import sys
from math import hypot
from pathlib import Path

# Aseguramos que `tramear_core` sea importable desde la carpeta del .py
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import inkex
from inkex import Circle, Group, Layer, PathElement, TextElement, Transform, Tspan, Use
from lxml import etree
from math import atan2, cos, degrees, sin

SVG_NS = "http://www.w3.org/2000/svg"

from tramear_core.seam_detector import (
    Costura, detectar_costuras, detectar_flechas_flujo,
    _extraer_segmentos_lineales,
)
from tramear_core.raster_detector import (
    cv2_disponible,
    detectar_costuras_raster,
)
from tramear_core.numbering import numerar_costuras, numerar_por_flujo
from tramear_core.graph_traversal import (
    numerar_por_grafo, numerar_por_proximidad,
)
from tramear_core.welding_book import exportar_csv, exportar_xlsx


class TramearIsometricoExt(inkex.EffectExtension):

    # ------------------------------------------------------------------ args

    def add_arguments(self, pars):
        pars.add_argument("--tab", default="deteccion")

        # Defaults validados: detectan correctamente las costuras del lote
        # Repsol (radio mediano 1.70 pt). El TOP-1 del optimizador subía
        # radio_min a 1.8 y descartaba las costuras reales (sesgo).
        pars.add_argument("--radio_min", type=float, default=1.4)
        pars.add_argument("--radio_max", type=float, default=2.5)
        pars.add_argument("--tolerancia_linea", type=float, default=1.5)
        pars.add_argument("--solo_sobre_linea", type=inkex.Boolean, default=True)
        pars.add_argument("--modo", default="auto",
                          choices=("auto", "vectorial", "raster"))

        pars.add_argument("--color_marca", type=inkex.Color, default=inkex.Color("#0066cc"))
        pars.add_argument("--color_tubing", type=inkex.Color, default=inkex.Color("#9b1f2a"))
        pars.add_argument("--tamano_etiqueta", type=float, default=8.0)
        pars.add_argument("--prefijo", default="W-")
        pars.add_argument("--prefijo_tubing", default="T-")
        pars.add_argument("--numero_inicial", type=int, default=1)
        pars.add_argument("--filtrar_puntas_flecha", type=inkex.Boolean, default=True)
        # Legacy: Inkscape recuerda valores de versiones anteriores del addon
        # en su cache de preferencias y los reenvía aunque el .inx actual ya
        # no exponga estos campos. Los aceptamos en silencio para no romper.
        pars.add_argument("--detectar_field_welds", type=inkex.Boolean, default=False)
        pars.add_argument("--color_field_weld", type=inkex.Color, default=inkex.Color("#003a73"))
        # Parámetros de colocación: estos sí mejoran del optimizador
        # (afectan a la calidad visual, no a cuántas costuras se detectan).
        pars.add_argument("--offset_callout", type=float, default=43.4,
                          help="Distancia inicial del callout respecto a la costura.")
        pars.add_argument("--radio_callout", type=float, default=10.4,
                          help="Radio mínimo del callout (se ajusta al ancho del texto).")
        pars.add_argument("--distancia_max_callout", type=float, default=231.4,
                          help="Distancia máxima permitida del callout a la costura.")

        pars.add_argument("--exportar_csv", type=inkex.Boolean, default=True)
        pars.add_argument("--exportar_xlsx", type=inkex.Boolean, default=True)
        pars.add_argument("--ruta_export", default="")

    # ------------------------------------------------------------------ run

    def effect(self):
        svg = self.svg
        avisos: list[str] = []

        # 1. Detección
        costuras_vec: list[Costura] = []
        costuras_ras: list[Costura] = []

        if self.options.modo in ("auto", "vectorial"):
            costuras_vec = detectar_costuras(
                svg,
                radio_min=self.options.radio_min,
                radio_max=self.options.radio_max,
                tolerancia_linea=self.options.tolerancia_linea,
                solo_sobre_linea=self.options.solo_sobre_linea,
                filtrar_puntas_flecha=self.options.filtrar_puntas_flecha,
            )

        if self.options.modo in ("auto", "raster"):
            ras, avisos_r = detectar_costuras_raster(
                svg,
                radio_min_mundo=self.options.radio_min,
                radio_max_mundo=self.options.radio_max,
                tolerancia_linea_mundo=self.options.tolerancia_linea,
                solo_sobre_linea=self.options.solo_sobre_linea,
            )
            costuras_ras = ras
            avisos.extend(avisos_r)
            if self.options.modo == "auto" and not cv2_disponible() and not costuras_vec:
                avisos.append(
                    "Modo 'auto' sin OpenCV: solo se ha analizado lo vectorial. "
                    "Si el isométrico está escaneado, instala opencv-python.")

        costuras = _fusionar(costuras_vec, costuras_ras,
                             tolerancia=max(self.options.radio_max, 1.0))

        # Descartar artifacts con coordenadas fuera del viewBox del SVG.
        # Aparecen ocasionalmente al importar PDFs con transforms anidados
        # que devuelven coordenadas absurdas (negativas, cerca del origen).
        antes = len(costuras)
        costuras = _filtrar_viewbox(svg, costuras)
        if (descartadas := antes - len(costuras)) > 0:
            avisos.append(
                f"{descartadas} candidato(s) descartado(s) por coordenadas "
                "fuera del lienzo (artifacts).")

        if not costuras:
            mensaje = ("No se han detectado costuras. Prueba a ajustar los "
                       "rangos de radio o a desactivar 'solo sobre línea'.")
            if avisos:
                mensaje += "\n\nAvisos:\n- " + "\n- ".join(avisos)
            inkex.errormsg(mensaje)
            return

        # 2. Numeración: intentar primero DFS topológico sobre el grafo
        # de segmentos (el más fiel al flujo físico de la tubería).
        # Si falla (sin segmentos, sin flechas), bajar al greedy de
        # proximidad y por último al barrido por filas.
        flechas = detectar_flechas_flujo(svg)
        segmentos_iso = _extraer_segmentos_lineales(svg)
        numeradas = []
        if segmentos_iso:
            numeradas = numerar_por_grafo(
                costuras, segmentos_iso, flechas,
                numero_inicial=self.options.numero_inicial,
            )
        if not numeradas and flechas:
            numeradas = numerar_por_proximidad(
                costuras, flechas,
                numero_inicial=self.options.numero_inicial,
            )
        if not numeradas:
            numeradas = numerar_costuras(
                costuras, numero_inicial=self.options.numero_inicial,
            )

        # 3. Marcado en el SVG
        self._dibujar_marcadores(numeradas)

        # 4. Export welding book
        rutas = self._exportar(numeradas)

        # 5. Resumen al usuario
        n_w = sum(1 for _, c in numeradas if c.tipo == "W")
        n_t = sum(1 for _, c in numeradas if c.tipo == "T")
        resumen = [
            f"Detectadas {len(numeradas)} marcas "
            f"({n_w} soldadas, {n_t} tubing).",
        ]
        if rutas:
            resumen.append("Archivos generados:")
            for r in rutas:
                resumen.append(f"  • {r}")
        if avisos:
            resumen.append("\nAvisos:")
            for a in avisos:
                resumen.append(f"  • {a}")
        inkex.errormsg("\n".join(resumen))

    # --------------------------------------------------------------- dibujo

    def _dibujar_marcadores(self, numeradas):
        capa = self._capa_costuras()
        color_w = str(self.options.color_marca)
        color_t = str(self.options.color_tubing)
        tam = float(self.options.tamano_etiqueta)
        prefijo_w = self.options.prefijo or "W-"
        prefijo_t = self.options.prefijo_tubing or "T-"
        offset = float(self.options.offset_callout)
        r_callout_min = float(self.options.radio_callout)
        dist_max = float(self.options.distancia_max_callout)

        grid_info = _construir_grid_ocupacion(self.svg, celda=6.0)
        zonas_texto = _bboxes_textos_existentes(self.svg)
        # Segmentos del isométrico: se usan para validar geométricamente
        # que el trayecto del callout a la costura no corre paralelo a
        # ninguna línea (cota o tubería). El grid de 6 pt es demasiado
        # grueso para ese check con flechas a 10°-15° y 0.5 pt de distancia.
        segmentos_iso = _extraer_segmentos_lineales(self.svg)
        # Bboxes de los callouts ya colocados: el fallback debe evitarlos
        # (el grid sí los marca como DURO, pero el fallback no usa el grid).
        callouts_puestos: list[tuple] = []

        for n, c in numeradas:
            color = color_t if c.tipo == "T" else color_w
            prefijo = prefijo_t if c.tipo == "T" else prefijo_w
            texto = f"{prefijo}{n}"
            r_callout = max(r_callout_min, _radio_para_texto(texto, tam))

            posicion = None
            if grid_info is not None:
                # Check geométrico (segmentos reales) que el trayecto
                # callout↔costura NO corra paralelo y pegado a una línea
                # del isométrico — usado como validador extra del spiral.
                def _no_paralelo(nx, ny):
                    return not _trayecto_paralelo_a_segmento(
                        nx, ny, c.x, c.y, segmentos_iso)

                # 1ª pasada: zona 100% libre + trayecto 100% libre + sin paralelo geom.
                posicion = _buscar_zona_libre(
                    grid_info, c.x, c.y, r_callout,
                    max_dist=dist_max,
                    nivel_max_aceptable=LIBRE,
                    nivel_max_trayecto=LIBRE,
                    validar_pos=_no_paralelo,
                )
                # 2ª pasada: callout no toca duro; trayecto puede cruzar
                # líneas pero sin paralelo (geom + grid≤2 consecutivos).
                if posicion is None:
                    posicion = _buscar_zona_libre(
                        grid_info, c.x, c.y, r_callout,
                        max_dist=dist_max * 1.2,
                        nivel_max_aceptable=BLANDO,
                        nivel_max_trayecto=BLANDO,
                        max_blando_paralelo=2,
                        validar_pos=_no_paralelo,
                    )
                # 3ª pasada: amplía el radio, sigue sin permitir paralelo.
                if posicion is None:
                    posicion = _buscar_zona_libre(
                        grid_info, c.x, c.y, r_callout,
                        max_dist=dist_max * 2.0,
                        nivel_max_aceptable=BLANDO,
                        nivel_max_trayecto=BLANDO,
                        max_blando_paralelo=3,
                        validar_pos=_no_paralelo,
                    )
                # 4ª pasada (último recurso): admitir paralelo —
                # mejor que el fallback ciego sin grid.
                if posicion is None:
                    posicion = _buscar_zona_libre(
                        grid_info, c.x, c.y, r_callout,
                        max_dist=dist_max * 2.0,
                        nivel_max_aceptable=BLANDO,
                        nivel_max_trayecto=BLANDO,
                        max_blando_paralelo=10**6,
                    )

            if posicion is None:
                posicion = _elegir_posicion_callout(
                    c.x, c.y, r_callout, offset,
                    zonas_evitar=zonas_texto + callouts_puestos,
                )

            cx_call, cy_call = posicion

            # Registrar el callout como zona ocupada para los siguientes:
            # en el grid (DURO) y en la lista que usa el fallback.
            callouts_puestos.append(
                _bbox_circulo(cx_call, cy_call, r_callout, margen=1.0))
            if grid_info is not None:
                _marcar_bbox_en_grid(grid_info, cx_call, cy_call, r_callout, nivel=DURO)
                # Marcar también la línea de la flecha (segmento callout↔costura)
                # como DURO, para que las flechas de costuras posteriores no la crucen.
                _marcar_segmento(grid_info, (cx_call, cy_call), (c.x, c.y), DURO)

            self._dibujar_callout(
                capa, c, n,
                color=color,
                cx_call=cx_call, cy_call=cy_call,
                r_callout=r_callout,
                tam=tam,
                texto=texto,
            )

    def _dibujar_callout(self, capa, c, n, *, color, cx_call, cy_call,
                          r_callout, tam, texto):
        """Dibuja la marca completa de UNA costura (callout + flecha apuntando)."""
        grupo = capa.add(Group())
        grupo.set("inkscape:label", f"costura-{c.tipo}-{n}")

        marker_id = self._asegurar_marker_flecha(color)

        # 1) Punto de inicio (borde del callout, lado más cercano a la
        #    costura). El final es el centro mismo de la costura para que
        #    la punta de flecha apunte exactamente al punto negro.
        ang_callout_a_costura = atan2(c.y - cy_call, c.x - cx_call)
        sx = cx_call + cos(ang_callout_a_costura) * r_callout
        sy = cy_call + sin(ang_callout_a_costura) * r_callout
        ex = c.x
        ey = c.y

        # 2) Curva ondulada (Bézier cúbica con puntos de control
        #    desplazados perpendicularmente, forma de S).
        dx = ex - sx
        dy = ey - sy
        perp_x, perp_y = -dy, dx
        plong = max((perp_x ** 2 + perp_y ** 2) ** 0.5, 0.001)
        ondulacion = max(min(plong * 0.18, 6.0), 2.5)
        perp_x = perp_x / plong * ondulacion
        perp_y = perp_y / plong * ondulacion
        cp1x = sx + dx * 0.33 + perp_x
        cp1y = sy + dy * 0.33 + perp_y
        cp2x = sx + dx * 0.66 - perp_x
        cp2y = sy + dy * 0.66 - perp_y

        # Path único: línea + punta de flecha como marker-end.
        # Al moverlo en Inkscape la punta se mueve con él.
        flecha = grupo.add(PathElement())
        flecha.path = (
            f"M {sx:.3f},{sy:.3f} "
            f"C {cp1x:.3f},{cp1y:.3f} {cp2x:.3f},{cp2y:.3f} {ex:.3f},{ey:.3f}"
        )
        flecha.style = {
            "fill": "none",
            "stroke": color,
            "stroke-width": "0.7",
            "stroke-linecap": "round",
            "marker-end": f"url(#{marker_id})",
        }
        flecha.set("inkscape:label", "guia")

        # 3) Círculo del callout (con el número dentro), fondo
        #    translúcido para no tapar texto del isométrico que pase
        #    por debajo.
        callout = grupo.add(Circle())
        callout.set("cx", f"{cx_call:.3f}")
        callout.set("cy", f"{cy_call:.3f}")
        callout.set("r", f"{r_callout:.3f}")
        callout.style = {
            "fill": "#ffffff",
            "fill-opacity": "0.85",
            "stroke": color,
            "stroke-width": "0.9",
        }

        # 4) Número dentro del callout, centrado.
        num = grupo.add(TextElement())
        num.set("x", f"{cx_call:.3f}")
        num.set("y", f"{cy_call + tam * 0.35:.3f}")
        num.set("text-anchor", "middle")
        num.style = {
            "font-size": f"{tam}px",
            "font-family": "sans-serif",
            "font-weight": "bold",
            "fill": color,
            "text-anchor": "middle",
        }
        tspan = num.add(Tspan())
        tspan.text = texto

    def _capa_costuras(self) -> Layer:
        # Búsqueda recursiva: al importar PDF, Inkscape suele envolver todo
        # en `<g transform=...>` y la capa "Costuras" creada en una ejecución
        # previa queda anidada dentro. Si solo miramos hijos directos, no la
        # encontramos y creamos una nueva en cada ejecución → callouts duplicados.
        for el in self.svg.iter():
            if not isinstance(el, Group):
                continue
            if el.get("inkscape:label") == "Costuras":
                # Vaciar contenido para que la re-ejecución reemplace los
                # callouts, no los acumule encima de los anteriores.
                for hijo in list(el):
                    el.remove(hijo)
                return el
        return self.svg.add(Layer.new("Costuras"))

    def _asegurar_marker_flecha(self, color: str) -> str:
        """Asegura que existe un <marker> con punta de flecha del color
        dado y devuelve su id. Si ya existía, lo reutiliza."""
        marker_id = "tramear-arrow-" + color.lstrip("#").lower()
        if self.svg.find(f".//{{{SVG_NS}}}marker[@id='{marker_id}']") is not None:
            return marker_id

        defs = self.svg.find(f"{{{SVG_NS}}}defs")
        if defs is None:
            defs = etree.SubElement(self.svg, f"{{{SVG_NS}}}defs")
            # Movemos defs al primer hijo
            self.svg.insert(0, defs)

        marker = etree.SubElement(defs, f"{{{SVG_NS}}}marker")
        marker.set("id", marker_id)
        marker.set("viewBox", "0 0 10 10")
        marker.set("refX", "9")
        marker.set("refY", "5")
        marker.set("markerWidth", "5")
        marker.set("markerHeight", "5")
        marker.set("orient", "auto-start-reverse")
        marker.set("markerUnits", "strokeWidth")
        punta = etree.SubElement(marker, f"{{{SVG_NS}}}path")
        punta.set("d", "M 0,0 L 10,5 L 0,10 z")
        punta.set("fill", color)
        punta.set("stroke", "none")
        return marker_id

    # --------------------------------------------------------------- export

    def _exportar(self, numeradas):
        carpeta = self._carpeta_destino()
        if carpeta is None:
            return []

        nombre_base = self._nombre_base()
        rutas = []
        if self.options.exportar_csv:
            r = exportar_csv(numeradas, carpeta / f"{nombre_base}_welding_book.csv",
                             prefijo=self.options.prefijo)
            rutas.append(r)
        if self.options.exportar_xlsx:
            r = exportar_xlsx(numeradas, carpeta / f"{nombre_base}_welding_book.xlsx",
                              prefijo=self.options.prefijo,
                              nombre_iso=nombre_base)
            if r is not None:
                rutas.append(r)
        return rutas

    def _carpeta_destino(self) -> Path | None:
        if self.options.ruta_export:
            p = Path(self.options.ruta_export).expanduser()
            if p.exists() and p.is_dir():
                return p

        doc = self.document_path()
        if doc:
            return Path(doc).resolve().parent

        return Path.home()

    def _nombre_base(self) -> str:
        doc = self.document_path()
        if doc:
            return Path(doc).stem
        return "isometrico"


# --------------------------------------------------------------------- utils

def _radio_para_texto(texto: str, font_size: float,
                       padding: float = 2.5, minimo: float = 4.5) -> float:
    """Radio del callout necesario para que `texto` quepa dentro."""
    # Ancho aproximado de un carácter sans-serif a font_size
    ancho_texto = max(1, len(texto)) * font_size * 0.55
    radio = ancho_texto / 2 + padding
    return max(minimo, radio)


DURO = 2
BLANDO = 1
LIBRE = 0


def _trayecto_paralelo_a_segmento(cx1, cy1, cx2, cy2, segmentos,
                                   *, dist_max=4.0, dif_angulo_max=20.0,
                                   solape_min=15.0, long_seg_min=15.0) -> bool:
    """¿El trayecto (cx1,cy1)->(cx2,cy2) corre paralelo y muy pegado a
    algún segmento largo del isométrico?

    El grid de ocupación (6 pt/celda) es demasiado grueso para detectar
    una flecha que corre a 0.5-1 pt de una cota o tubería con un ángulo
    de 10-15°. Este test usa geometría real:

      - Solo se consideran segmentos de longitud >= long_seg_min.
      - "Paralelo" = diferencia de ángulo (módulo 180°) < dif_angulo_max.
      - "Pegado" = distancia perpendicular de ambos endpoints al
        segmento < dist_max.
      - "Solape" = la proyección del trayecto sobre el segmento ocupa
        más de solape_min pt del propio segmento.

    Devuelve True si hay algún segmento que cumple las 3 condiciones.
    """
    from math import atan2, degrees, hypot
    tx, ty = cx2 - cx1, cy2 - cy1
    long_t = hypot(tx, ty)
    if long_t < 1e-3:
        return False
    ang_t = degrees(atan2(ty, tx)) % 180
    for s in segmentos:
        sx1, sy1, sx2, sy2 = s.x1, s.y1, s.x2, s.y2
        dx, dy = sx2 - sx1, sy2 - sy1
        long_s = hypot(dx, dy)
        if long_s < long_seg_min:
            continue
        ang_s = degrees(atan2(dy, dx)) % 180
        difa = min(abs(ang_s - ang_t), 180 - abs(ang_s - ang_t))
        if difa > dif_angulo_max:
            continue
        # distancia perpendicular de los dos endpoints del trayecto
        # al segmento (línea infinita pero proyección clamped)
        long2 = dx * dx + dy * dy
        def _dist_y_proy(px, py):
            t = ((px - sx1) * dx + (py - sy1) * dy) / long2
            t_c = max(0.0, min(1.0, t))
            qx = sx1 + t_c * dx
            qy = sy1 + t_c * dy
            return hypot(px - qx, py - qy), t
        d1, t1 = _dist_y_proy(cx1, cy1)
        d2, t2 = _dist_y_proy(cx2, cy2)
        if d1 > dist_max or d2 > dist_max:
            continue
        # solape sobre el segmento: parámetros t clamped al rango [0,1]
        a, b = sorted([t1, t2])
        a = max(0.0, a)
        b = min(1.0, b)
        if (b - a) * long_s >= solape_min:
            return True
    return False


def _construir_grid_ocupacion(svg, celda: float = 6.0):
    """Construye una matriz int de zonas ocupadas del SVG.

      DURO   = texto / símbolo relleno / callout puesto. NO cruzar.
      BLANDO = línea (tubería, cota, leader). Se puede cruzar como
               último recurso.
      LIBRE  = blanco.

    Para paths CON relleno marcamos el bbox completo (es un símbolo o
    una letra rasterizada a path). Para paths SIN relleno marcamos
    SOLO los segmentos (Bresenham); su interior queda libre, que es
    como se ve realmente en el plano.

    Excluye los descendientes de la capa "Costuras" para no contar
    callouts previos de ejecuciones anteriores.
    """
    try:
        vb = svg.get_viewbox()
    except Exception:
        vb = None
    if vb and len(vb) == 4:
        x0, y0, w, h = vb
    else:
        try:
            w = float(svg.width)
            h = float(svg.height)
        except Exception:
            return None
        x0, y0 = 0.0, 0.0

    cols = max(2, int(w / celda) + 2)
    rows = max(2, int(h / celda) + 2)
    grid = [[LIBRE] * cols for _ in range(rows)]

    info = {
        "grid": grid, "celda": celda,
        "x0": x0, "y0": y0,
        "cols": cols, "rows": rows,
    }

    def _en_capa_costuras(el):
        cur = el.getparent()
        while cur is not None:
            if cur.get("inkscape:label") == "Costuras":
                return True
            cur = cur.getparent()
        return False

    # Cache del bbox LOCAL de los elementos referenciados por <use>
    # (los glyphs de fuente se reutilizan miles de veces).
    cache_ref_bbox: dict = {}

    for el in svg.iter():
        if not isinstance(el, (TextElement, Tspan, PathElement, Circle, Use)):
            continue
        if _en_capa_costuras(el):
            continue
        if isinstance(el, Use):
            # El texto del isométrico (PDF de Poppler) son miles de
            # <use href="#glyph-…"> que referencian paths en <defs>.
            # bounding_box() sobre el <use> NO aplica los atributos x/y,
            # así que hay que componer ctm * translate(x,y) a mano.
            bb = _bbox_use_global(svg, el, cache_ref_bbox)
            if bb is not None:
                _marcar_rect(info, bb[0], bb[1], bb[2], bb[3], DURO)
            continue
        if isinstance(el, (TextElement, Tspan)):
            try:
                bb = el.bounding_box()
            except Exception:
                bb = None
            if bb is not None:
                _marcar_rect(info, bb.left, bb.top, bb.right, bb.bottom, DURO)
            continue
        if isinstance(el, Circle):
            try:
                cxv = float(el.get("cx") or 0)
                cyv = float(el.get("cy") or 0)
                rv = float(el.get("r") or 0)
            except Exception:
                continue
            _marcar_rect(info, cxv - rv, cyv - rv, cxv + rv, cyv + rv, DURO)
            continue

        # PathElement: depende de si tiene relleno y de tamaño/densidad
        try:
            bb = el.bounding_box()
        except Exception:
            bb = None
        if bb is None:
            continue
        ancho = bb.right - bb.left
        alto = bb.bottom - bb.top

        if _tiene_relleno(el):
            # Bboxes gigantescos (el cajetín entero) no deben marcar todo
            # el interior; solo el contorno.
            if ancho > 80 or alto > 80:
                _marcar_segmentos_path(info, el, BLANDO)
            else:
                _marcar_rect(info, bb.left, bb.top, bb.right, bb.bottom, DURO)
        else:
            # Sin relleno: distinguir línea fina (cota/tubería) vs
            # texto rasterizado (letras dibujadas como contornos).
            # Heurística: bbox pequeño + muchos nodos = probable texto.
            try:
                nodos = sum(1 for _ in el.path)
            except Exception:
                nodos = 0
            es_texto_raster = (
                ancho < 30 and alto < 30 and nodos >= 6
                and ancho * alto > 4  # descarta micro-paths
            )
            if es_texto_raster:
                _marcar_rect(info, bb.left, bb.top, bb.right, bb.bottom, DURO)
            else:
                _marcar_segmentos_path(info, el, BLANDO)

    return info


def _bbox_use_global(svg, use_el, cache: dict):
    """Bbox global (left, top, right, bottom) de un elemento <use>, o None.

    inkex no aplica los atributos x/y del <use> al calcular su
    bounding_box. El transform efectivo del contenido referenciado es
    `composed_transform() @ translate(x, y)`. Aplicamos eso a las 4
    esquinas del bbox LOCAL del elemento referenciado (cacheado por
    href, ya que los glyphs de fuente se repiten miles de veces).
    """
    href = (use_el.get("{http://www.w3.org/1999/xlink}href")
            or use_el.get("href") or "").lstrip("#")
    if not href:
        return None
    if href in cache:
        ref_bb = cache[href]
    else:
        ref = svg.getElementById(href)
        ref_bb = None
        if ref is not None:
            try:
                ref_bb = ref.bounding_box()
            except Exception:
                ref_bb = None
        cache[href] = ref_bb
    if ref_bb is None:
        return None
    try:
        x = float(use_el.get("x") or 0)
        y = float(use_el.get("y") or 0)
        t_eff = use_el.composed_transform() @ Transform(f"translate({x},{y})")
    except Exception:
        return None
    esquinas = [
        (ref_bb.left, ref_bb.top), (ref_bb.right, ref_bb.top),
        (ref_bb.left, ref_bb.bottom), (ref_bb.right, ref_bb.bottom),
    ]
    try:
        pts = [t_eff.apply_to_point(c) for c in esquinas]
    except Exception:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _tiene_relleno(el) -> bool:
    try:
        estilo = dict(el.style) if hasattr(el, "style") else {}
    except Exception:
        estilo = {}
    fill = (estilo.get("fill") or el.get("fill") or "").strip().lower()
    if not fill or fill == "none" or fill == "transparent":
        return False
    if fill in ("#ffffff", "#fff", "white"):
        return False
    return True


def _marcar_rect(info, left, top, right, bottom, nivel: int) -> None:
    grid = info["grid"]
    celda = info["celda"]
    x0 = info["x0"]; y0 = info["y0"]
    cols = info["cols"]; rows = info["rows"]
    ix0 = max(0, int((left - x0) / celda))
    iy0 = max(0, int((top - y0) / celda))
    ix1 = min(cols - 1, int((right - x0) / celda))
    iy1 = min(rows - 1, int((bottom - y0) / celda))
    for rr in range(iy0, iy1 + 1):
        for cc in range(ix0, ix1 + 1):
            if grid[rr][cc] < nivel:
                grid[rr][cc] = nivel


def _marcar_segmentos_path(info, el, nivel: int) -> None:
    """Recorre los segmentos rectos del path y los marca con Bresenham."""
    try:
        path = el.path.to_absolute()
    except Exception:
        return
    try:
        ctm = el.composed_transform()
    except Exception:
        ctm = None

    def transformar(p):
        if ctm is None:
            return p
        try:
            return ctm.apply_to_point(p)
        except Exception:
            return p

    prev = None
    inicio = None
    for cmd in path:
        letra = cmd.letter
        if letra == "M":
            prev = (cmd.args[0], cmd.args[1])
            inicio = prev
        elif letra in ("L", "H", "V"):
            if letra == "H":
                actual = (cmd.args[0], prev[1] if prev else 0)
            elif letra == "V":
                actual = (prev[0] if prev else 0, cmd.args[0])
            else:
                actual = (cmd.args[0], cmd.args[1])
            if prev is not None:
                _marcar_segmento(info, transformar(prev), transformar(actual), nivel)
            prev = actual
        elif letra == "Z":
            if prev is not None and inicio is not None:
                _marcar_segmento(info, transformar(prev), transformar(inicio), nivel)
            prev = inicio
        else:
            # C, Q, S, T, A — aproximar como segmento al endpoint final
            if cmd.args and len(cmd.args) >= 2:
                actual = (cmd.args[-2], cmd.args[-1])
                if prev is not None:
                    _marcar_segmento(info, transformar(prev), transformar(actual), nivel)
                prev = actual


def _marcar_segmento(info, p1, p2, nivel: int) -> None:
    """Bresenham en la rejilla: marca las celdas que la línea atraviesa."""
    grid = info["grid"]
    celda = info["celda"]
    x0 = info["x0"]; y0 = info["y0"]
    cols = info["cols"]; rows = info["rows"]
    c1 = int((p1[0] - x0) / celda)
    r1 = int((p1[1] - y0) / celda)
    c2 = int((p2[0] - x0) / celda)
    r2 = int((p2[1] - y0) / celda)
    dc = abs(c2 - c1); dr = abs(r2 - r1)
    sc = 1 if c1 < c2 else -1
    sr = 1 if r1 < r2 else -1
    err = dc - dr
    c, r = c1, r1
    max_pasos = max(cols, rows) * 2
    pasos = 0
    while pasos < max_pasos:
        if 0 <= r < rows and 0 <= c < cols:
            if grid[r][c] < nivel:
                grid[r][c] = nivel
        if c == c2 and r == r2:
            break
        e2 = 2 * err
        if e2 > -dr:
            err -= dr; c += sc
        if e2 < dc:
            err += dc; r += sr
        pasos += 1


def _buscar_zona_libre(grid_info, cx: float, cy: float,
                        r_callout: float, max_dist: float = 140.0,
                        nivel_max_aceptable: int = LIBRE,
                        nivel_max_trayecto: int | None = None,
                        max_blando_paralelo: int = 3,
                        validar_pos=None):
    """Busca en espiral creciente la celda más cercana cuya zona del
    callout no contenga ninguna celda de nivel > `nivel_max_aceptable`
    Y cuyo trayecto desde la costura no cruce celdas con nivel >
    `nivel_max_trayecto` (por defecto = nivel_max_aceptable).

      nivel_max_aceptable=LIBRE   ⇒ celda 100% en blanco (sin nada).
      nivel_max_aceptable=BLANDO  ⇒ se admite cruzar líneas pero NO texto.

    El trayecto del callout a la costura también se valida con la
    misma severidad: la flecha tampoco debe cruzar texto/cota.

    `max_blando_paralelo`: nº máximo de celdas BLANDO CONSECUTIVAS que
    el trayecto puede atravesar. Cruzar una línea perpendicular toca
    1-2 celdas; correr PARALELO y pegado a una línea toca muchas
    seguidas — eso confunde visualmente (la flecha se ve pegada a una
    cota o tubería), así que se rechaza.
    """
    if nivel_max_trayecto is None:
        nivel_max_trayecto = nivel_max_aceptable

    grid = grid_info["grid"]
    celda = grid_info["celda"]
    x0 = grid_info["x0"]
    y0 = grid_info["y0"]
    cols = grid_info["cols"]
    rows = grid_info["rows"]

    ic = int((cx - x0) / celda)
    ir = int((cy - y0) / celda)
    rc = max(1, int(r_callout / celda) + 1)
    max_radio = max(rc + 1, int(max_dist / celda))

    def acepta_callout(r, c):
        for dr in range(-rc, rc + 1):
            for dc in range(-rc, rc + 1):
                rr, cc = r + dr, c + dc
                if not (0 <= rr < rows and 0 <= cc < cols):
                    return False
                if grid[rr][cc] > nivel_max_aceptable:
                    return False
        return True

    def acepta_trayecto(c_call, r_call):
        # Bresenham desde (ic, ir) (costura) hasta (c_call, r_call) (callout).
        # Saltamos las celdas inmediatas a la costura (donde el punto
        # está dibujado) y a la del callout (donde el círculo va a estar).
        c1, r1 = ic, ir
        c2, r2 = c_call, r_call
        dc = abs(c2 - c1); dr = abs(r2 - r1)
        sc = 1 if c1 < c2 else -1
        sr = 1 if r1 < r2 else -1
        err = dc - dr
        c, r = c1, r1
        pasos = 0
        max_pasos = max(cols, rows) * 2
        blando_seguidos = 0
        while pasos < max_pasos:
            # Excluir celdas en el radio del callout y a 1 celda de la costura
            dist_callout = max(abs(c - c2), abs(r - r2))
            dist_costura = max(abs(c - c1), abs(r - r1))
            if dist_callout > rc and dist_costura > 1:
                if 0 <= r < rows and 0 <= c < cols:
                    nivel = grid[r][c]
                    if nivel > nivel_max_trayecto:
                        return False
                    if nivel >= BLANDO:
                        blando_seguidos += 1
                        if blando_seguidos > max_blando_paralelo:
                            # El trayecto corre PARALELO y pegado a una
                            # línea (cota/tubería) — visualmente confuso.
                            return False
                    else:
                        blando_seguidos = 0
            if c == c2 and r == r2:
                break
            e2 = 2 * err
            if e2 > -dr:
                err -= dr; c += sc
            if e2 < dc:
                err += dc; r += sr
            pasos += 1
        return True

    for radio in range(rc + 1, max_radio + 1):
        for dr in range(-radio, radio + 1):
            for dc in range(-radio, radio + 1):
                if max(abs(dr), abs(dc)) != radio:
                    continue
                rr, cc = ir + dr, ic + dc
                if not acepta_callout(rr, cc):
                    continue
                if not acepta_trayecto(cc, rr):
                    continue
                nx = x0 + (cc + 0.5) * celda
                ny = y0 + (rr + 0.5) * celda
                if validar_pos is not None and not validar_pos(nx, ny):
                    continue
                return (nx, ny)
    return None


def _marcar_bbox_en_grid(grid_info, cx: float, cy: float, r: float,
                          nivel: int = DURO) -> None:
    """Marca el callout recién puesto como DURO para que los próximos lo eviten."""
    _marcar_rect(grid_info,
                 cx - r - 1.0, cy - r - 1.0,
                 cx + r + 1.0, cy + r + 1.0,
                 nivel)


def _bboxes_textos_existentes(svg) -> list[tuple]:
    """Devuelve los bboxes (x1, y1, x2, y2) de todos los textos del SVG.

    Sirven como zonas a evitar al colocar callouts: nunca queremos tapar
    cotas, itemcodes, etiquetas existentes del isométrico.
    """
    out: list[tuple] = []
    for el in svg.iter():
        if not isinstance(el, (TextElement, Tspan)):
            continue
        try:
            bb = el.bounding_box()
        except Exception:
            bb = None
        if bb is None:
            continue
        margen = 1.5
        out.append((bb.left - margen, bb.top - margen,
                    bb.right + margen, bb.bottom + margen))
    return out


def _bbox_circulo(cx: float, cy: float, r: float, margen: float = 0.0) -> tuple:
    return (cx - r - margen, cy - r - margen,
            cx + r + margen, cy + r + margen)


def _solapan(a: tuple, b: tuple) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _elegir_posicion_callout(cx: float, cy: float, r_callout: float,
                              offset_inicial: float,
                              zonas_evitar: list[tuple]) -> tuple[float, float]:
    """Devuelve un (cx_call, cy_call) que no solape con ninguna zona dada.

    Prueba 8 direcciones (NE, N, NW, W, SW, S, SE, E) a varias
    distancias crecientes desde la costura. Toma la primera posición
    sin solape. Si todas solapan, devuelve la primera intentada
    (mejor algo que nada — el operario puede ajustar manualmente).
    """
    import math
    direcciones = [
        ( 1, -1),  # NE
        ( 0, -1),  # N
        (-1, -1),  # NW
        (-1,  0),  # W
        (-1,  1),  # SW
        ( 0,  1),  # S
        ( 1,  1),  # SE
        ( 1,  0),  # E
    ]
    # Normalizar diagonales para que la distancia sea consistente
    norm_direcciones = []
    for dx, dy in direcciones:
        l = math.hypot(dx, dy)
        norm_direcciones.append((dx / l, dy / l))

    # Distancias crecientes: en clusters densos los callouts cercanos
    # ya están ocupados, así que hay que poder alejarse más.
    factores = (1.0, 1.4, 1.8, 2.4, 3.2, 4.2, 5.5)
    primero = None
    for f in factores:
        d = offset_inicial * f
        for ux, uy in norm_direcciones:
            nx = cx + ux * d
            ny = cy + uy * d
            bbox = _bbox_circulo(nx, ny, r_callout, margen=1.0)
            if primero is None:
                primero = (nx, ny)
            if not any(_solapan(bbox, z) for z in zonas_evitar):
                return (nx, ny)
    return primero or (cx + offset_inicial, cy - offset_inicial)


def _filtrar_viewbox(svg, costuras):
    """Descarta costuras cuyo centro caiga fuera del viewBox del SVG."""
    try:
        vb = svg.get_viewbox()
    except Exception:
        vb = None
    if vb and len(vb) == 4:
        x0, y0, w, h = vb
    else:
        try:
            w = float(svg.width)
            h = float(svg.height)
        except Exception:
            return list(costuras)
        x0, y0 = 0.0, 0.0
    margen = 0.5
    return [
        c for c in costuras
        if (x0 - margen) <= c.x <= (x0 + w + margen)
        and (y0 - margen) <= c.y <= (y0 + h + margen)
    ]


def _fusionar(vectoriales, rasterizadas, tolerancia: float):
    """Une dos listas de costuras eliminando duplicados.

    Se prioriza la detección vectorial; una costura raster que esté a
    menos de `tolerancia` de una vectorial se descarta.
    """
    if not rasterizadas:
        return list(vectoriales)
    out = list(vectoriales)
    for c in rasterizadas:
        if any(hypot(c.x - v.x, c.y - v.y) < tolerancia for v in vectoriales):
            continue
        out.append(c)
    return out


if __name__ == "__main__":
    TramearIsometricoExt().run()
