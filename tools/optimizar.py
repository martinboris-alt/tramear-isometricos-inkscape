"""Loop de optimización automática de parámetros del addon.

Cada iteración:
  1. Elige parámetros aleatorios del addon (radio_min/max, tolerancia,
     radio_callout, offset_callout, dist_max_callout).
  2. Aplica el addon sobre N SVG pre-convertidos.
  3. Mide métricas automáticas en cada SVG marcado:
       - callouts solapando zona dura del grid (texto/cota)
       - flechas cuyo trayecto cruza zona dura
       - pares de flechas que se intersectan geométricamente
       - distancia media callout↔costura (penaliza callouts lejos)
  4. Combina las métricas en un score (menor = mejor).
  5. Guarda en historial JSON.

Tras N iteraciones, genera un HTML con los TOP resultados y los SVG
marcados de cada uno, listos para inspección visual.

Uso:
    .venv/bin/python -m tools.optimizar \
        --carpeta revision_10pdfs --pdfs 3 --iters 100
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from math import hypot
from pathlib import Path

# Asegurar imports de inkex y del addon
_INK_EXT = "/usr/share/inkscape/extensions"
_USR_EXT = str(Path.home() / ".config/inkscape/extensions")
for p in (_INK_EXT, _USR_EXT):
    if p and p not in sys.path:
        sys.path.insert(0, p)

import inkex  # noqa: E402
from inkex import Circle, PathElement  # noqa: E402

try:
    import numpy as np
    from PIL import Image
except ImportError as e:
    raise SystemExit(
        "Faltan dependencias. Instala con .venv: pip install numpy Pillow"
    ) from e


# ---------------------------------------------------------------------------
# Espacio de búsqueda
# ---------------------------------------------------------------------------

@dataclass
class Params:
    # Detección (FIJOS): valores validados con el lote Repsol.
    # Subir radio_min descarta costuras reales (sesgo del optimizador).
    radio_min: float = 1.4
    radio_max: float = 2.5
    tolerancia_linea: float = 1.5
    # Colocación (VARIABLES): el optimizador solo toca estos.
    radio_callout: float = 11.4
    offset_callout: float = 15.3
    dist_max_callout: float = 183.7

    @classmethod
    def aleatorio(cls, rng: random.Random) -> "Params":
        # Solo variar parámetros de colocación. Los de detección
        # se mantienen fijos para no perder costuras reales.
        return cls(
            radio_callout=round(rng.uniform(7.5, 14.0), 1),
            offset_callout=round(rng.uniform(10.0, 45.0), 1),
            dist_max_callout=round(rng.uniform(100.0, 250.0), 1),
        )


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------

@dataclass
class Metricas:
    n_callouts: int = 0
    callouts_sobre_duro: int = 0
    flechas_cruzan_duro: int = 0
    cruces_entre_flechas: int = 0
    dist_media: float = 0.0
    n_callouts_esperado: int = 0   # baseline para penalizar baja detección

    def score(self) -> float:
        """Más bajo es mejor. Fallos en valor absoluto (no por callout,
        porque eso degeneraba en "detectar menos para tener pocos
        fallos"). Penalización fuerte si se detectan menos costuras
        que el baseline."""
        if self.n_callouts == 0:
            return 1e9
        deficit = max(0, self.n_callouts_esperado - self.n_callouts)
        return (
            self.callouts_sobre_duro * 10.0 +
            self.flechas_cruzan_duro * 6.0 +
            self.cruces_entre_flechas * 4.0 +
            self.dist_media * 0.05 +
            deficit * 15.0  # cada costura perdida pesa más que un fallo de colocación
        )


def cargar_mascara(svg_path: Path, dpi: int = 150) -> tuple:
    """Renderiza el SVG a PNG y devuelve (máscara_binaria, dpi).

    La máscara tiene 1 donde hay contenido oscuro (texto/línea/etc.)
    y 0 donde es blanco. Es lo que el usuario realmente ve cuando dice
    "el callout pisa texto".
    """
    png = svg_path.with_suffix(".raster.png")
    if not png.is_file():
        subprocess.run(
            ["inkscape", str(svg_path), "--export-type=png",
             f"--export-filename={png}", f"--export-dpi={dpi}",
             "--export-background=white"],
            capture_output=True, check=True,
        )
    img = Image.open(png).convert("L")
    arr = np.array(img)
    mascara = (arr < 200).astype(np.uint8)
    return mascara, dpi


def _zona_callout_pisa_contenido(mascara, dpi: int,
                                  cx: float, cy: float, r: float,
                                  umbral: float = 0.05) -> bool:
    """¿La zona del callout en el PNG tiene contenido oscuro?

    `umbral` es la fracción mínima de píxeles oscuros para considerar
    que "pisa". 0.05 = más de un 5% del área del callout sobre texto.
    """
    factor = dpi / 72.0
    cx_px = cx * factor; cy_px = cy * factor; r_px = r * factor
    h, w = mascara.shape
    x1 = max(0, int(cx_px - r_px)); y1 = max(0, int(cy_px - r_px))
    x2 = min(w, int(cx_px + r_px) + 1); y2 = min(h, int(cy_px + r_px) + 1)
    if x2 <= x1 or y2 <= y1:
        return False
    zona = mascara[y1:y2, x1:x2]
    if zona.size == 0:
        return False
    return float(zona.mean()) > umbral


def _bresenham_pisa_contenido(mascara, dpi: int,
                               sx: float, sy: float,
                               ex: float, ey: float,
                               margen_pt: float = 3.0,
                               umbral_oscuros: int = 3) -> bool:
    """¿El trayecto recto (sx,sy)→(ex,ey) cruza píxeles oscuros del PNG?

    Excluye los extremos `margen_pt` pt para no contar el propio callout
    ni el punto de la costura. `umbral_oscuros` es el nº mínimo de
    píxeles oscuros para considerar que cruza.
    """
    factor = dpi / 72.0
    h, w = mascara.shape
    sx_px = sx * factor; sy_px = sy * factor
    ex_px = ex * factor; ey_px = ey * factor
    # Acortar el segmento para excluir extremos
    dx = ex_px - sx_px; dy = ey_px - sy_px
    long_px = (dx * dx + dy * dy) ** 0.5
    if long_px < 1:
        return False
    m_px = margen_pt * factor
    if long_px <= 2 * m_px:
        return False
    ux = dx / long_px; uy = dy / long_px
    sx_px2 = sx_px + ux * m_px; sy_px2 = sy_px + uy * m_px
    ex_px2 = ex_px - ux * m_px; ey_px2 = ey_px - uy * m_px

    n = max(int(long_px - 2 * m_px), 1)
    oscuros = 0
    for i in range(n + 1):
        t = i / n
        x = int(sx_px2 + (ex_px2 - sx_px2) * t)
        y = int(sy_px2 + (ey_px2 - sy_px2) * t)
        if 0 <= y < h and 0 <= x < w and mascara[y, x]:
            oscuros += 1
            if oscuros >= umbral_oscuros:
                return True
    return False


def _ccw(A, B, C) -> bool:
    return (C[1] - A[1]) * (B[0] - A[0]) > (B[1] - A[1]) * (C[0] - A[0])


def _segmentos_se_cruzan(p1, p2, p3, p4) -> bool:
    return (_ccw(p1, p3, p4) != _ccw(p2, p3, p4)
            and _ccw(p1, p2, p3) != _ccw(p1, p2, p4))


def evaluar_svg_marcado(svg_marcado_path: Path, mascara, dpi: int) -> Metricas:
    """Lee el SVG marcado y extrae callouts + flechas; mide las métricas
    contra la máscara raster del SVG original (ground truth)."""
    svg = inkex.load_svg(str(svg_marcado_path)).getroot()
    capa = None
    for el in svg.iter():
        if hasattr(el, "get") and el.get("inkscape:label") == "Costuras":
            capa = el
            break
    if capa is None:
        return Metricas()

    callouts = []        # (cx, cy, r)
    flechas = []         # ((sx,sy), (ex,ey))
    costuras_xy = []     # (x, y) — el extremo final de cada flecha

    for grupo in capa:
        cx_call = cy_call = r_call = None
        flecha_start = flecha_end = None
        for el in grupo.iter() if hasattr(grupo, "iter") else [grupo]:
            if isinstance(el, Circle):
                try:
                    cx_call = float(el.get("cx") or 0)
                    cy_call = float(el.get("cy") or 0)
                    r_call = float(el.get("r") or 0)
                except Exception:
                    pass
            elif isinstance(el, PathElement):
                try:
                    p = el.path.to_absolute()
                    for cmd in p:
                        if cmd.letter == "M":
                            flecha_start = (cmd.args[0], cmd.args[1])
                        elif cmd.letter in ("L", "C"):
                            if cmd.letter == "C":
                                flecha_end = (cmd.args[-2], cmd.args[-1])
                            else:
                                flecha_end = (cmd.args[0], cmd.args[1])
                except Exception:
                    pass

        if cx_call is not None and r_call:
            callouts.append((cx_call, cy_call, r_call))
        if flecha_start and flecha_end:
            flechas.append((flecha_start, flecha_end))
            costuras_xy.append(flecha_end)

    n_callouts = len(callouts)
    callouts_dur = sum(1 for cx, cy, r in callouts
                       if _zona_callout_pisa_contenido(mascara, dpi, cx, cy, r))
    flechas_dur = sum(1 for s, e in flechas
                      if _bresenham_pisa_contenido(mascara, dpi, s[0], s[1], e[0], e[1]))
    cruces = 0
    for i in range(len(flechas)):
        for j in range(i + 1, len(flechas)):
            if _segmentos_se_cruzan(flechas[i][0], flechas[i][1],
                                     flechas[j][0], flechas[j][1]):
                cruces += 1

    # Distancia media callout↔costura
    if callouts and costuras_xy and len(callouts) == len(costuras_xy):
        dists = [hypot(c[0] - q[0], c[1] - q[1])
                 for (c, q) in zip([(cx, cy) for cx, cy, _ in callouts], costuras_xy)]
        dist_media = sum(dists) / len(dists) if dists else 0.0
    else:
        dist_media = 0.0

    return Metricas(
        n_callouts=n_callouts,
        callouts_sobre_duro=callouts_dur,
        flechas_cruzan_duro=flechas_dur,
        cruces_entre_flechas=cruces,
        dist_media=dist_media,
    )


# ---------------------------------------------------------------------------
# Ejecución del addon
# ---------------------------------------------------------------------------

def convertir_pdf_a_svg(pdf: Path, dst: Path) -> None:
    if dst.is_file():
        return
    subprocess.run(
        ["inkscape", "--pdf-poppler", "--export-type=svg",
         f"--export-filename={dst}", str(pdf)],
        check=True, capture_output=True,
    )


def aplicar_addon(svg_in: Path, svg_out: Path, params: Params) -> bool:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{_INK_EXT}:{_USR_EXT}"
    cmd = [
        "python3", str(Path(_USR_EXT) / "tramear_isometrico.py"),
        f"--radio_min={params.radio_min}",
        f"--radio_max={params.radio_max}",
        f"--tolerancia_linea={params.tolerancia_linea}",
        "--solo_sobre_linea=true",
        "--detectar_field_welds=false",
        "--filtrar_puntas_flecha=true",
        "--modo=vectorial",
        "--color_marca=#0066cc",
        "--color_field_weld=#003a73",
        "--color_tubing=#9b1f2a",
        "--tamano_etiqueta=8.0",
        f"--radio_callout={params.radio_callout}",
        f"--offset_callout={params.offset_callout}",
        f"--distancia_max_callout={params.dist_max_callout}",
        "--prefijo=W-",
        "--prefijo_tubing=T-",
        "--numero_inicial=1",
        "--exportar_csv=false",
        "--exportar_xlsx=false",
        "--ruta_export=",
        str(svg_in),
    ]
    with svg_out.open("w") as f:
        r = subprocess.run(cmd, stdout=f, stderr=subprocess.DEVNULL, env=env)
    return r.returncode == 0 and svg_out.stat().st_size > 1000


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--carpeta", default="revision_10pdfs")
    ap.add_argument("--pdfs", type=int, default=3,
                    help="Cuántos PDFs usar como muestra")
    ap.add_argument("--iters", type=int, default=100)
    ap.add_argument("--top", type=int, default=5,
                    help="Cuántos mejores resultados conservar")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--salida", default="tools/_artifacts/optimizacion")
    args = ap.parse_args()

    carpeta = Path(args.carpeta).resolve()
    salida = Path(args.salida).resolve()
    salida.mkdir(parents=True, exist_ok=True)
    cache = salida / "cache_svg"
    cache.mkdir(exist_ok=True)
    runs = salida / "runs"
    runs.mkdir(exist_ok=True)

    rng = random.Random(args.seed)

    # Selección de PDFs: preferir los que YA tienen welding_book con
    # muchas costuras (más útil para optimizar). Los que no tienen
    # costuras no aportan señal.
    todos = sorted(carpeta.glob("*.pdf"))
    if not todos:
        sys.exit(f"No hay PDFs en {carpeta}")
    pares = []
    for pdf in todos:
        csv_path = carpeta / f"{pdf.stem}_welding_book.csv"
        n = 0
        if csv_path.is_file():
            try:
                with csv_path.open() as fh:
                    n = sum(1 for _ in fh) - 1
            except Exception:
                n = 0
        pares.append((pdf, n))
    pares.sort(key=lambda x: x[1], reverse=True)
    pdfs = [p for p, n in pares if n > 0][: args.pdfs]
    if not pdfs:
        pdfs = [p for p, _ in pares][: args.pdfs]

    print(f"Pre-cargando {len(pdfs)} PDFs…")
    casos = []  # (svg_orig_path, mascara, dpi, nombre, n_esperado)
    for pdf in pdfs:
        nombre = pdf.stem
        svg_orig = cache / f"{nombre}.svg"
        convertir_pdf_a_svg(pdf, svg_orig)
        t0 = time.time()
        mascara, dpi = cargar_mascara(svg_orig, dpi=140)
        t = time.time() - t0
        oscuros = float(mascara.mean()) * 100
        # Baseline de costuras esperadas: las del welding_book actual
        csv_baseline = carpeta / f"{nombre}_welding_book.csv"
        n_esperado = 0
        if csv_baseline.is_file():
            with csv_baseline.open() as fh:
                n_esperado = max(0, sum(1 for _ in fh) - 1)
        print(f"  {nombre[:60]} máscara {t:.1f}s, oscuros={oscuros:.1f}%, baseline={n_esperado}")
        casos.append({"svg": svg_orig, "mascara": mascara,
                       "dpi": dpi, "nombre": nombre,
                       "n_esperado": n_esperado})

    print(f"\nIniciando {args.iters} iteraciones sobre {len(casos)} PDFs…")
    historial = []
    mejor_score = float("inf")
    t_inicio = time.time()
    json_path = salida / "historial.json"

    for i in range(args.iters):
        params = Params.aleatorio(rng)
        agregadas = Metricas()
        n_ok = 0
        rutas_iter = []
        for caso in casos:
            svg_out = runs / f"iter{i:04d}__{caso['nombre']}.svg"
            ok = aplicar_addon(caso["svg"], svg_out, params)
            if not ok:
                continue
            m = evaluar_svg_marcado(svg_out, caso["mascara"], caso["dpi"])
            agregadas.n_callouts += m.n_callouts
            agregadas.callouts_sobre_duro += m.callouts_sobre_duro
            agregadas.flechas_cruzan_duro += m.flechas_cruzan_duro
            agregadas.cruces_entre_flechas += m.cruces_entre_flechas
            agregadas.dist_media += m.dist_media * m.n_callouts
            agregadas.n_callouts_esperado += caso["n_esperado"]
            n_ok += 1
            rutas_iter.append(str(svg_out))
        if agregadas.n_callouts > 0:
            agregadas.dist_media /= agregadas.n_callouts
        score = agregadas.score()

        marca = ""
        if score < mejor_score:
            mejor_score = score
            marca = " ★ nuevo mejor"

        if (i + 1) % 5 == 0 or marca:
            elapsed = time.time() - t_inicio
            por_iter = elapsed / (i + 1)
            queda = por_iter * (args.iters - i - 1)
            print(f"  iter {i+1:>3}/{args.iters}  score={score:7.3f}  "
                  f"callouts={agregadas.n_callouts:>3}  "
                  f"sobre_duro={agregadas.callouts_sobre_duro:>2}  "
                  f"flechas_duro={agregadas.flechas_cruzan_duro:>2}  "
                  f"cruces={agregadas.cruces_entre_flechas:>2}  "
                  f"(~{queda:.0f}s restantes){marca}")

        historial.append({
            "iter": i,
            "params": asdict(params),
            "metricas": asdict(agregadas),
            "score": score,
            "svgs": rutas_iter,
        })

        # Guardado incremental por si interrumpimos
        if (i + 1) % 5 == 0 or i + 1 == args.iters:
            with json_path.open("w") as f:
                json.dump(historial, f, indent=2)

    print(f"\nHistorial: {json_path}")

    # Generar HTML con TOP
    historial.sort(key=lambda x: x["score"])
    top = historial[: args.top]
    html_path = salida / "top.html"
    _generar_html_top(top, html_path)
    print(f"Reporte TOP-{args.top}: {html_path}")

    print("\n=== TOP 5 ===")
    for k, h in enumerate(top[:5], 1):
        p = h["params"]; m = h["metricas"]
        print(f"#{k}  score={h['score']:.3f}  "
              f"rmin={p['radio_min']}/rmax={p['radio_max']}  "
              f"tol={p['tolerancia_linea']}  rcall={p['radio_callout']}  "
              f"off={p['offset_callout']}  dmax={p['dist_max_callout']}  "
              f"→ duro={m['callouts_sobre_duro']} flecha_duro={m['flechas_cruzan_duro']} "
              f"cruces={m['cruces_entre_flechas']} dist={m['dist_media']:.1f}")


def _generar_html_top(top, salida: Path):
    rows = []
    for k, h in enumerate(top, 1):
        p = h["params"]; m = h["metricas"]
        svgs_html = "".join(
            f'<li><a href="file://{s}" target="_blank"><code>{Path(s).name}</code></a></li>'
            for s in h["svgs"]
        )
        rows.append(f"""
<details {"open" if k <= 3 else ""}>
  <summary><strong>#{k}</strong> · score = {h['score']:.3f}
    · sobre_duro={m['callouts_sobre_duro']}
    · flecha_duro={m['flechas_cruzan_duro']}
    · cruces={m['cruces_entre_flechas']}
    · dist_media={m['dist_media']:.1f}
  </summary>
  <p><strong>Parámetros:</strong></p>
  <pre>{json.dumps(p, indent=2)}</pre>
  <p><strong>SVGs marcados:</strong></p>
  <ul>{svgs_html}</ul>
</details>
""")
    body = "\n".join(rows)
    html = f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>TOP optimización</title>
<style>
  body {{ font-family: system-ui; max-width: 900px; margin: 20px auto; padding: 0 20px; }}
  details {{ background: #f8f9fa; padding: 12px; border-radius: 6px; margin: 10px 0; }}
  summary {{ cursor: pointer; font-size: 1.05em; }}
  pre {{ background: white; padding: 8px; border-radius: 4px; }}
  code {{ font-family: ui-monospace, monospace; }}
</style></head><body>
<h1>TOP resultados de la optimización</h1>
<p>Ordenados por <em>score</em> (menor = mejor). Score combina:
callouts sobre zona dura · flechas cruzando zona dura ·
pares de flechas que se cruzan entre sí · distancia media.</p>
{body}
</body></html>"""
    salida.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
