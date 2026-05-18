"""Reporte de revisión: HTML interactivo con servidor local.

Recorre todos los CSV welding_book de una carpeta y crea un formulario
con checkboxes por costura (con los fallos más típicos ya predefinidos)
+ campos de texto libre. Al pulsar "Generar reporte", se imprime el
reporte completo en un textarea para copiarlo y pegárselo a Claude.

Cada plano tiene además un botón "🖼️ Abrir en Inkscape" que lanza
Inkscape sobre el SVG marcado. Para ello se levanta un pequeño
servidor local (sin dependencias) con un endpoint /abrir.

Uso:
    .venv/bin/python -m tools.generar_reporte_revision --carpeta revision_10pdfs

Se sirve en http://localhost:8765 y se abre el navegador. Ctrl+C para
parar.
"""

from __future__ import annotations

import argparse
import csv
import html
import http.server
import os
import socketserver
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from string import Template
from urllib.parse import parse_qs, unquote, urlparse


# Fallos típicos que ya hemos identificado en iteraciones previas.
# Cada uno: (id, etiqueta_corta, descripción_completa para el reporte).
FALLOS_POR_COSTURA = [
    ("fp_flecha",      "Falso positivo: flecha de cota / soportación"),
    ("fp_otro",        "Falso positivo (otra razón)"),
    ("tipo_mal_t",     "Debería ser T (tubing) — no W"),
    ("tipo_mal_w",     "Debería ser W (soldada) — no T"),
    ("sobre_texto",    "Callout pisa texto del isométrico"),
    ("sobre_cota",     "Callout pisa número/texto de cota"),
    ("flecha_pisa",    "La flecha pasa por encima de texto/cota"),
    ("cruza_callout",  "Su flecha cruza la de otro callout"),
    ("callout_lejos",  "Callout demasiado lejos de la costura"),
    ("orden_mal",      "Numeración fuera de orden (no sigue flujo)"),
]


def carga_csvs(carpeta: Path) -> list[dict]:
    """Devuelve una lista de planos con sus costuras detectadas."""
    out = []
    for csv_path in sorted(carpeta.glob("*welding_book.csv")):
        nombre = csv_path.stem.replace("_welding_book", "")
        nombre_corto = nombre.split("sheet 2121")[-1] if "sheet 2121" in nombre else nombre
        with csv_path.open(encoding="utf-8-sig") as f:
            filas = list(csv.DictReader(f, delimiter=";"))
        svg_path = csv_path.parent / f"{nombre}_marcado.svg"
        pdf_path = csv_path.parent / f"{nombre}.pdf"
        out.append({
            "nombre_completo": nombre,
            "nombre_corto": nombre_corto,
            "filas": filas,
            "svg": str(svg_path) if svg_path.exists() else "",
            "pdf": str(pdf_path) if pdf_path.exists() else "",
        })
    return out


HTML_TPL = Template(r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Reporte de revisión — Tramear Isométrico</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 1100px; margin: 0 auto;
         padding: 20px; background: #f6f7f9; color: #222; }
  h1 { color: #0a3d7e; }
  .plano { background: white; border: 1px solid #d0d7de; border-radius: 8px;
           margin: 16px 0; padding: 12px 18px; }
  .plano summary { font-weight: 600; cursor: pointer; padding: 4px 0;
                   font-size: 1.05em; color: #0a3d7e; }
  .conteo { font-weight: 400; color: #555; margin-left: 8px; }
  table { width: 100%; border-collapse: collapse; margin-top: 8px;
          font-size: 0.92em; }
  th { text-align: left; background: #f0f3f6; padding: 6px 8px;
       border-bottom: 1px solid #d0d7de; position: sticky; top: 0; }
  td { padding: 4px 8px; vertical-align: top; border-bottom: 1px solid #eee; }
  td.num { font-weight: 600; color: #0066cc; width: 70px; }
  td.tipo { width: 40px; color: #666; }
  td.coords { width: 130px; color: #888; font-family: monospace; font-size: 0.85em; }
  td.checks label { display: inline-block; margin-right: 14px;
                    padding: 1px 6px; border-radius: 3px;
                    font-size: 0.86em; }
  td.checks label:hover { background: #e6f0fa; }
  td.checks input { margin-right: 3px; }
  td.notes input { width: 95%; padding: 3px 6px;
                   border: 1px solid #ccc; border-radius: 3px;
                   font-size: 0.88em; }
  .acciones { margin: 6px 0 10px 0; display: flex; gap: 8px; align-items: center; }
  .acciones .btn-abrir { padding: 5px 12px; font-size: 0.85em;
                          background: #0a3d7e; color: white; border: none;
                          border-radius: 4px; cursor: pointer; }
  .acciones .btn-abrir:hover { background: #0c4d9e; }
  .acciones-estado { color: #555; font-size: 0.85em; }
  .acciones-estado.ok { color: #2a8b2a; font-weight: 600; }
  .acciones-estado.err { color: #b32b2b; font-weight: 600; }
  textarea.libre { width: 100%; min-height: 70px; padding: 6px;
                   border: 1px solid #ccc; border-radius: 4px;
                   font-family: system-ui, sans-serif; font-size: 0.92em;
                   box-sizing: border-box; margin-top: 6px; }
  .seccion-libre { margin-top: 12px; }
  .seccion-libre label { display: block; font-weight: 600;
                         margin-bottom: 4px; color: #555; }
  #barra { position: sticky; bottom: 0; background: #0a3d7e; color: white;
           padding: 10px 18px; border-radius: 8px; margin: 24px 0;
           display: flex; gap: 10px; align-items: center;
           box-shadow: 0 4px 12px rgba(0,0,0,.15); }
  #barra button { padding: 8px 18px; font-size: 1em; cursor: pointer;
                  border: none; border-radius: 4px; background: white;
                  color: #0a3d7e; font-weight: 600; }
  #barra button:hover { background: #e6f0fa; }
  #reporte_salida { width: 100%; min-height: 240px; margin-top: 16px;
                    font-family: ui-monospace, "Courier New", monospace;
                    font-size: 0.86em; padding: 10px;
                    border: 2px solid #0a3d7e; border-radius: 6px;
                    box-sizing: border-box; }
  .copiar-ok { color: #2a8b2a; font-weight: 600; margin-left: 12px; }
  details[open] summary { border-bottom: 1px solid #eef1f4;
                          margin-bottom: 8px; padding-bottom: 6px; }
</style>
</head>
<body>

<h1>Reporte de revisión — Tramear Isométrico</h1>
<p>Para cada costura detectada marca los fallos que veas. Al final
pulsa <strong>Generar reporte</strong> y copia el texto producido al
chat. Si un fallo no encaja en la lista, escríbelo en
"Otros fallos / notas" del plano correspondiente.</p>

$cuerpo_planos

<div id="barra">
  <button onclick="generarReporte()">📝 Generar reporte</button>
  <button onclick="copiarReporte()">📋 Copiar al portapapeles</button>
  <span id="copiar_estado"></span>
</div>

<textarea id="reporte_salida" placeholder="El reporte aparecerá aquí…"
          readonly></textarea>

<script>
const FALLOS = $fallos_js;

function recolectarPlano(plano) {
    const id = plano.dataset.planoId;
    const nombre = plano.dataset.planoNombre;
    const items = [];
    plano.querySelectorAll("tr.costura").forEach(tr => {
        const num = tr.dataset.num;
        const tipo = tr.dataset.tipo;
        const checks = [];
        tr.querySelectorAll("input[type=checkbox]:checked").forEach(cb => {
            checks.push(FALLOS[cb.dataset.idx]);
        });
        const notaInput = tr.querySelector("input.nota-costura");
        const nota = notaInput ? notaInput.value.trim() : "";
        if (checks.length === 0 && !nota) return;
        items.push({ num, tipo, checks, nota });
    });
    const faltantes = plano.querySelector("textarea.faltantes").value.trim();
    const notas = plano.querySelector("textarea.notas").value.trim();
    return { nombre, items, faltantes, notas };
}

function generarReporte() {
    const planos = document.querySelectorAll("details.plano");
    const lineas = ["## Reporte de revisión\n"];
    let huboAlgo = false;
    planos.forEach(p => {
        const dat = recolectarPlano(p);
        if (dat.items.length === 0 && !dat.faltantes && !dat.notas) return;
        huboAlgo = true;
        lineas.push("### " + dat.nombre);
        if (dat.items.length > 0) {
            lineas.push("Fallos por costura:");
            dat.items.forEach(it => {
                let etiqueta = it.tipo + "-" + it.num;
                let texto = "  - " + etiqueta + ": ";
                const partes = [];
                if (it.checks.length > 0) partes.push(it.checks.join("; "));
                if (it.nota) partes.push(it.nota);
                texto += partes.join(" — ");
                lineas.push(texto);
            });
        }
        if (dat.faltantes) {
            lineas.push("Costuras NO detectadas:");
            dat.faltantes.split(/\n+/).forEach(l => {
                l = l.trim();
                if (l) lineas.push("  - " + l);
            });
        }
        if (dat.notas) {
            lineas.push("Otros fallos / notas:");
            dat.notas.split(/\n+/).forEach(l => {
                l = l.trim();
                if (l) lineas.push("  - " + l);
            });
        }
        lineas.push("");
    });
    if (!huboAlgo) {
        lineas.push("_(Sin observaciones — el detector está bien en todos los planos revisados.)_");
    }
    document.getElementById("reporte_salida").value = lineas.join("\n");
}

function copiarReporte() {
    const ta = document.getElementById("reporte_salida");
    if (!ta.value) generarReporte();
    ta.select();
    navigator.clipboard.writeText(ta.value).then(() => {
        const e = document.getElementById("copiar_estado");
        e.textContent = "✓ Copiado al portapapeles";
        e.className = "copiar-ok";
        setTimeout(() => { e.textContent = ""; }, 3500);
    }).catch(err => {
        document.execCommand("copy");
    });
}

function abrirEnInkscape(btn, tipo) {
    const plano = btn.closest("details.plano");
    const ruta = plano.dataset[tipo];
    const estado = plano.querySelector(".acciones-estado");
    if (!ruta) {
        estado.textContent = "✗ Sin archivo " + tipo;
        estado.className = "acciones-estado err";
        return;
    }
    estado.textContent = "abriendo…";
    estado.className = "acciones-estado";
    fetch("/abrir?f=" + encodeURIComponent(ruta))
        .then(r => {
            if (r.ok) {
                estado.textContent = "✓ Inkscape lanzado";
                estado.className = "acciones-estado ok";
            } else {
                return r.text().then(t => {
                    estado.textContent = "✗ " + t;
                    estado.className = "acciones-estado err";
                });
            }
        })
        .catch(err => {
            estado.textContent = "✗ " + err.message;
            estado.className = "acciones-estado err";
        });
    setTimeout(() => { estado.textContent = ""; }, 5000);
}
</script>
</body>
</html>
""")


def construir_html(carpeta: Path, salida: Path) -> Path:
    planos = carga_csvs(carpeta)
    fallos_js = "[" + ", ".join(f'"{html.escape(d)}"' for _, _, *rest in []
                                  for d in []) + "]"
    # ↑ no usado; lo construimos abajo con bucle normal:
    fallos_js_items = [f'"{html.escape(desc)}"' for _, desc in FALLOS_POR_COSTURA]
    fallos_js = "[" + ", ".join(fallos_js_items) + "]"

    secciones = []
    for plano in planos:
        secciones.append(_render_plano(plano))
    cuerpo = "\n".join(secciones)
    if not secciones:
        cuerpo = (
            "<p><em>No se han encontrado CSVs welding_book en la carpeta. "
            "Procesa antes los PDFs con <code>tools/tramear_pdf.sh</code>.</em></p>"
        )

    html_doc = HTML_TPL.substitute(cuerpo_planos=cuerpo, fallos_js=fallos_js)
    salida.write_text(html_doc, encoding="utf-8")
    return salida


def _render_plano(plano: dict) -> str:
    nombre = html.escape(plano["nombre_completo"])
    corto = html.escape(plano["nombre_corto"])
    filas = plano["filas"]
    n = len(filas)
    n_w = sum(1 for f in filas if f.get("Tipo") == "W")
    n_t = sum(1 for f in filas if f.get("Tipo") == "T")
    cabecera = f"{corto} <span class='conteo'>· {n} marcas ({n_w} W, {n_t} T)</span>"

    if not filas:
        cuerpo_tabla = "<p><em>Sin costuras detectadas</em></p>"
    else:
        rows = []
        for fila in filas:
            num = html.escape(str(fila.get("Nº costura", "?")))
            tipo = html.escape(str(fila.get("Tipo", "W")))
            x = fila.get("X", "")
            y = fila.get("Y", "")
            checks = []
            for idx, (cid, etiqueta) in enumerate(FALLOS_POR_COSTURA):
                eid = f"c{plano['nombre_corto']}_{num}_{cid}"
                checks.append(
                    f'<label><input type="checkbox" data-idx="{idx}" '
                    f'id="{html.escape(eid)}"> {html.escape(etiqueta)}</label>'
                )
            checks_html = "\n".join(checks)
            rows.append(f"""
<tr class="costura" data-num="{num}" data-tipo="{tipo}">
  <td class="num">{tipo}-{num}</td>
  <td class="tipo">{tipo}</td>
  <td class="coords">({x}, {y})</td>
  <td class="checks">{checks_html}</td>
  <td class="notes"><input type="text" class="nota-costura" placeholder="otra nota corta"></td>
</tr>""")
        cuerpo_tabla = (
            "<table><thead><tr>"
            "<th>Marca</th><th>Tipo</th><th>Coord</th>"
            "<th>Fallos típicos (marcar los que apliquen)</th>"
            "<th>Nota</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )

    svg_attr = html.escape(plano.get("svg", "") or "")
    pdf_attr = html.escape(plano.get("pdf", "") or "")
    botones = []
    if svg_attr:
        botones.append(
            f'<button class="btn-abrir" onclick="abrirEnInkscape(this, \'svg\')">'
            f'🖼️ Abrir SVG marcado en Inkscape</button>'
        )
    if pdf_attr:
        botones.append(
            f'<button class="btn-abrir" onclick="abrirEnInkscape(this, \'pdf\')">'
            f'📄 Abrir PDF original</button>'
        )
    botones_html = " ".join(botones) if botones else ""

    return f"""
<details class="plano" open data-plano-id="{html.escape(corto)}"
         data-plano-nombre="{nombre}"
         data-svg="{svg_attr}" data-pdf="{pdf_attr}">
  <summary>{cabecera}</summary>
  <div class="acciones">{botones_html}<span class="acciones-estado"></span></div>
  {cuerpo_tabla}
  <div class="seccion-libre">
    <label>Costuras NO detectadas en este plano (una por línea):</label>
    <textarea class="libre faltantes" placeholder="Ej.: 'falta la costura del wendolet sobre W-3', 'reducción al inicio con 2 costuras sin marcar'…"></textarea>
  </div>
  <div class="seccion-libre">
    <label>Otros fallos / notas sobre este plano (una por línea):</label>
    <textarea class="libre notas" placeholder="Cualquier cosa que no encaje en los checkboxes."></textarea>
  </div>
</details>
"""


def _construir_handler(html_bytes: bytes, raiz_permitida: Path):
    class _Handler(http.server.BaseHTTPRequestHandler):

        def log_message(self, fmt, *args):
            # Silenciar el log estándar de http.server (es ruidoso)
            pass

        def _send(self, status, body, ctype="text/plain; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            p = urlparse(self.path)
            if p.path in ("/", "/index.html"):
                self._send(200, html_bytes, "text/html; charset=utf-8")
                return
            if p.path == "/abrir":
                qs = parse_qs(p.query)
                ruta = unquote(qs.get("f", [""])[0])
                if not ruta:
                    self._send(400, b"Falta parametro f")
                    return
                ruta_abs = Path(ruta).resolve()
                # Solo permitir abrir archivos dentro del proyecto
                try:
                    ruta_abs.relative_to(raiz_permitida)
                except ValueError:
                    self._send(403, b"Ruta fuera del proyecto")
                    return
                if not ruta_abs.is_file():
                    self._send(404, b"Archivo no encontrado")
                    return
                try:
                    subprocess.Popen(
                        ["inkscape", str(ruta_abs)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                except FileNotFoundError:
                    self._send(500, b"Inkscape no esta en PATH")
                    return
                self._send(200, b"OK")
                return
            self._send(404, b"Not Found")
    return _Handler


def servir(carpeta: Path, puerto: int, raiz: Path) -> None:
    planos = carga_csvs(carpeta)
    fallos_js_items = [f'"{html.escape(desc)}"' for _, desc in FALLOS_POR_COSTURA]
    fallos_js = "[" + ", ".join(fallos_js_items) + "]"
    secciones = [_render_plano(p) for p in planos]
    cuerpo = "\n".join(secciones) if secciones else (
        "<p><em>No se han encontrado CSVs welding_book en la carpeta.</em></p>"
    )
    html_str = HTML_TPL.substitute(cuerpo_planos=cuerpo, fallos_js=fallos_js)
    html_bytes = html_str.encode("utf-8")

    Handler = _construir_handler(html_bytes, raiz)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", puerto), Handler) as httpd:
        url = f"http://localhost:{puerto}/"
        print(f"Servidor escuchando en {url}")
        print(f"Carpeta de planos: {carpeta}")
        print("Pulsa Ctrl+C para parar.")
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServidor detenido.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--carpeta", default="revision_10pdfs",
                    help="Carpeta con CSVs welding_book (default: revision_10pdfs)")
    ap.add_argument("--puerto", type=int, default=8765)
    ap.add_argument("--salida-html",
                    help="Solo generar el HTML estático en esta ruta y salir.")
    args = ap.parse_args()

    carpeta = Path(args.carpeta).resolve()
    if not carpeta.is_dir():
        raise SystemExit(f"Carpeta no encontrada: {carpeta}")

    if args.salida_html:
        salida = Path(args.salida_html).resolve()
        construir_html(carpeta, salida)
        print(f"HTML estático generado: {salida}")
        print("Aviso: en modo estático el botón Abrir-en-Inkscape no funciona.")
        return

    raiz = Path.cwd().resolve()
    servir(carpeta, args.puerto, raiz)


if __name__ == "__main__":
    main()
