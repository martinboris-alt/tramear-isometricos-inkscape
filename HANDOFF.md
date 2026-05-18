# HANDOFF — Addon Inkscape para trameado de isométricos

**Última actualización:** 2026-05-13
**Estado:** funcional con deuda técnica conocida; pipeline detección → BOM → welding book operativo; diagnóstico HTML/SVG generable; fase D pendiente.

> Este documento está pensado para que cualquier AI (o persona) pueda **continuar el desarrollo desde cero** sin sesión previa. Léelo entero antes de tocar código. Las rutas son absolutas.

---

## 1. Qué es y para qué

El usuario es **profesional de obra de tubería industrial** (oil & gas / petroquímica). Trabaja con **isométricos PDF** generados desde SP3D + SmartPlant Isometrics. Hoy traza manualmente las costuras (welds) sobre los planos para preparar el welding book.

El proyecto es un **addon de Inkscape** (extensión Python/`inkex`) que:

1. **Detecta automáticamente costuras** en isométricos vectoriales (importados a Inkscape).
2. **Las numera y dibuja un callout** (círculo + flecha al punto) para cada una.
3. **Cruza con el BOM del propio plano** para enriquecer cada costura con itemcode, descripción y diámetro.
4. **Exporta welding book** consolidado (CSV / XLSX) con metadatos del cajetín.

**MVP** (memoria `project_mvp_v1.md`): detección automática de costuras desde PDF importado a Inkscape, marcadas + numeradas + welding book exportado. **MVP cumplido.**

---

## 2. Convenciones del proyecto Repsol/Sines (Alba Project)

Calibrado contra **1824 PDFs reales** de la planta LLDPE/PP de Sines. Ver `memory/project_convenciones_repsol.md`.

| Aspecto | Valor |
|---|---|
| Generador | Amyuni PDF Converter (1792/1824) — backend SP3D + SmartPlant Isometrics |
| Idioma | Inglés |
| Página típica | A3 apaisado, 1191 × 842 pt (1 pt ≈ 0.353 mm) |
| Mediana círculos/PDF | 14 |
| Radio costura mediana | **1.70 pt** (rango p25-p75: 1.70-2.21) |
| Forma de los círculos | **Polígonos de 12-16 lados**, NO Bézier — el detector debe reconocer ambos |
| Shop weld | **punto negro relleno** sobre la línea (radio ~1.7 pt) |
| Field weld | **punto negro CRUZADO por una X / aspa** (no usado en código actual) |
| Wendolet/picaje | **par de puntos llenos a ~7.7-7.9 pt entre sí, alineados X o Y** sobre el eje de la tubería + triángulo del injerto cerca |
| Numeración costuras | **NO está en el plano** — la genera nuestro software |
| Cuadritos con número | **Piece marks del BOM** — referencian items de la tabla |

**Falsos positivos típicos** (detección de círculos):
- Cajetín (esquina inf. derecha, radios ~5 pt)
- BOM/leyenda (esquina sup. derecha)
- Símbolos de soportes (zona inf. izquierda)
- Punto decimal en cotas grandes (radio <1 pt)

**Identificadores documentales típicos:**
- Línea: `2"-BU-10C13-1E21` (patrón `<diámetro>"-<spec>-<resto>`)
- Documento: `4274-XH-DL-<sheet>`
- Sheet en cabecera: `19-000-2-02-00001 sheet 2121BU10C13-1`

---

## 3. Arquitectura del repositorio

```
programa para tramear isométricos/
├── 0-ISOS 2121/                       # 1824 PDFs originales del proyecto
├── revision_10pdfs/                   # subset de validación (10 PDFs)
│   ├── *.pdf
│   ├── *_marcado.svg                  # output del addon
│   ├── *_welding_book.csv             # output por PDF
│   ├── *_welding_book_enriquecido.xlsx (E.5 prototipo)
│   ├── cajetines.csv                  # metadatos extraídos
│   ├── boms.csv                       # 108 items BOM consolidados
│   └── welding_book_consolidado.xlsx  # E.1 (10 PDFs unidos)
│
├── inkscape_addon/
│   ├── tramear_isometrico.inx         # UI metadata Inkscape  ⚠ desactualizado
│   ├── tramear_isometrico.py          # entrypoint addon (830 LOC)
│   └── tramear_core/
│       ├── seam_detector.py           # detector vectorial + Fase B (477 LOC)
│       ├── geom.py                    # utilidades (hay_triangulo_cerca, etc.)
│       ├── numbering.py               # ⚠ numerar_por_recorrido sigue siendo stub
│       ├── raster_detector.py         # detector raster (no validado E2E)
│       └── welding_book.py            # serializador CSV/XLSX
│
├── tools/                             # scripts CLI auxiliares
│   ├── pdf_extractor.py               # extracción base con PyMuPDF (fitz)
│   ├── extraer_bom.py                 # E.2 parser tabla BOM
│   ├── extraer_cajetin.py             # extractor cajetín
│   ├── enriquecer_welding_book.py     # E.3+E.4+E.5 + rescate por BOM
│   ├── consolidar_welding_book.py     # E.1 consolidación 10 PDFs
│   ├── diagnostico_fases.py           # generación HTML+SVG por fases ✨
│   ├── diagnostico_wendolets.py       # diagnóstico ad-hoc Fase B
│   ├── visualizar_circulos.py         # visualizador rápido de círculos PDF
│   ├── visualizar_diagnostico.py      # diagnóstico aceptados/rechazados
│   ├── optimizar.py                   # optimizador random-search parámetros
│   ├── optimizar_callouts.py          # ⚠ Fase D — NO PROBADO aún
│   ├── validar_top1.py                # validación TOP-1 vs baseline
│   ├── validar_detector.py            # tests legacy
│   ├── catalogar_samples.py           # catalogador inicial de PDFs
│   └── _artifacts/
│       ├── PLAN_DESATENDIDO.md        # log de avance vivo
│       ├── optimizacion/              # historial.json, top.html, runs/
│       └── validacion/cache_svg/      # SVGs cache PDF→SVG
│
├── tramear_pdf.sh → tools/tramear_pdf.sh   # one-shot CLI
├── install.sh / install.bat
├── requirements.txt
├── samples_inventory.csv              # inventario inicial 1824 PDFs
└── HANDOFF.md                         # este documento
```

---

## 4. Pipeline completo

```
PDF ──┐
      ├─► tramear_pdf.sh
      │     ├─ Inkscape: PDF → SVG (intermedio)
      │     ├─ tramear_isometrico.py (addon)
      │     │     ├─ seam_detector.detectar_costuras
      │     │     │     ├─ _buscar_circulos (geom + radio)
      │     │     │     ├─ filtro sobre_linea
      │     │     │     ├─ _deduplicar (relleno+contorno)
      │     │     │     ├─ _filtrar_puntas_flecha (descarta triángulos cerca)
      │     │     │     └─ _anadir_wendolets ★ Fase B (recupera pares mutuos)
      │     │     ├─ numbering.numerar_por_filas (barrido — stub para flujo)
      │     │     ├─ _construir_grid_ocupacion (espacio disponible)
      │     │     ├─ colocar callouts (espiral creciente, 3 pasadas)
      │     │     └─ welding_book.exportar_csv / xlsx
      │     └─ outputs: *_marcado.svg + *_welding_book.csv
      │
      ├─► extraer_bom.py            → 108 items × 7 campos
      ├─► extraer_cajetin.py        → cajetines.csv
      ├─► enriquecer_welding_book.py
      │     ├─ detectar_piece_marks (textos numéricos cerca de tubería)
      │     ├─ rescatar_por_bom ★ guiado por BOM
      │     ├─ asociar (2 PMs más cercanos por costura)
      │     └─ welding_book_enriquecido.xlsx
      │
      ├─► consolidar_welding_book.py → 10 PDFs en uno solo
      └─► diagnostico_fases.py --html → index.html + SVG vectorial × 6 fases
```

---

## 5. Estado de las fases (plan vs realidad)

| Fase | Descripción | Estado | Archivo principal |
|---|---|---|---|
| **A** | Optimización random-search | ✅ Hecho. TOP-1 aplicado: rcall=10.4, off=43.4, dmax=231.4 | `tools/optimizar.py` |
| **B** | Wendolets/picajes | ✅ Hecho. +4 en ET40F04-42, 0 FPs | `seam_detector.py::_anadir_wendolets` |
| **C** | Tubing CD40F09/F10 (0 detecciones) | ❌ Pendiente. Causa probable: cuadrados como Bézier, no `n_lineas==4` | `seam_detector.py::_radio_centro_tipo` |
| **D** | Callouts inteligentes (espacios en blanco) | ⚠ **Diseñado pero NO probado**. Distance transform listo en `optimizar_callouts.py`, integración con addon pendiente | `tools/optimizar_callouts.py` |
| **E.1** | Welding book consolidado | ✅ Hecho. 69 costuras × 21 cols | `tools/consolidar_welding_book.py` |
| **E.2** | Parser BOM | ✅ Hecho. 108 items, 100% campos | `tools/extraer_bom.py` |
| **E.3+4+5** | Piece marks + asociación + enriquecimiento | ✅ Hecho prototipo en ET40F04-42. Cobertura 100% ≥1 PM, ~88% ≥2 | `tools/enriquecer_welding_book.py` |
| **F** | Numeración por flujo de tubería | ❌ Pendiente. Stub en `numbering.py` | `inkscape_addon/tramear_core/numbering.py` |

---

## 6. Lo que viene: Fase D detallada (espacios en blanco + colocación de callouts)

**Problema reportado por el usuario (recurrente):** los callouts (círculos numerados) caen frecuentemente sobre texto, cotas o líneas del plano, haciéndolos ilegibles. El optimizador (Fase A) solo lo redujo de 21 a 19 callouts conflictivos sobre 65 (~30% sigue mal).

**Diseño propuesto** (idea original del usuario): **mapa de espacio negativo** + **padding adaptativo**.

### Algoritmo

1. **Renderizar el plano sin la capa Costuras** a PNG (200 DPI).
2. **Binarizar** (oscuro = ocupado).
3. **Distance transform** (`scipy.ndimage.distance_transform_edt`) → para cada píxel libre, distancia al ocupado más cercano.
4. **Para cada costura**, buscar en el anillo `[offset_min, dist_max]` (típico 25-230 pt) el píxel que MAXIMIZA la distancia al ocupado, usando 24 radios × 64 ángulos.
5. **Padding adaptativo**: empezar con `padding_min = 8 px`. Si <70% de callouts cumplen, reducir 0.6× e iterar (mín 1 px).
6. **Reposicionar** callout + flecha curva.

### Estado del código

- ✅ `tools/optimizar_callouts.py` (326 LOC) — implementación standalone como **post-procesado** del SVG marcado.
- ⚠ NO ha sido probado todavía. El usuario lo pausó pidiendo primero diagnóstico visual.
- ❌ NO integrado al addon — corre como CLI sobre SVG ya marcado.

### Cómo retomar la Fase D

```bash
cd "/home/mart-n-boris/Escritorio/programa para tramear isométricos"
.venv/bin/python -m tools.optimizar_callouts \
    "revision_10pdfs/19-000-2-02-00001 sheet 2121ET40F04-42_IS00_marcado.svg"
# Genera: ..._marcado_optcall.svg
```

Para **integrar al addon** (mejor que post-procesado): exponer la lógica de distance transform como módulo `tramear_core/placement_dt.py`, llamar desde `tramear_isometrico.py::_dibujar_marcadores` antes de pintar callouts. Pero **scipy NO está disponible en el Python embebido de Inkscape** — verificar y, si no, implementar EDT manual con numpy puro (Felzenszwalb-Huttenlocher 2D, ~50 LOC) o fallback al algoritmo actual de espiral.

---

## 7. Diagnóstico gráfico HTML por fases ✨

**Herramienta clave para fine-tunear el detector.** Genera un informe HTML con SVG vectorial de las 6 fases del pipeline.

```bash
.venv/bin/python -m tools.diagnostico_fases <pdf> --html
```

Genera carpeta `<pdf>_diagnostico_fases/` con:
- `index.html` — informe interactivo, navegación lateral, botón "Imprimir/Exportar PDF"
- `fase_N_*.svg` × 6 — SVG vectoriales (calidad infinita)
- `fase_N_*.png` × 6 — fallback raster con overlay título/leyenda
- `atlas.png` — vista combinada 2×3

**Las 6 fases:**
1. **Brutos** — círculos candidatos en rango (199 en ET40F04-42)
2. **Sobre línea** — descartar aislados (199)
3. **Dedup** — fusionar duplicados PDF (40)
4. **Filtrados** — descartar puntas de flecha (12)
5. **Wendolets** — recuperar pares mutuos (16)
6. **BOM** — cruce con piece marks

CSS de impresión: cada fase salto de página, sin nav. `@page A4 landscape margin: 10mm`.

---

## 8. Cómo iterar (workflow recomendado)

```bash
# 1. Setup (solo primera vez)
cd "/home/mart-n-boris/Escritorio/programa para tramear isométricos"
.venv/bin/pip install -r requirements.txt
bash install.sh                       # copia addon a ~/.config/inkscape/extensions/

# 2. Procesar 1 PDF
bash tools/tramear_pdf.sh "revision_10pdfs/<algun>.pdf"

# 3. Ver diagnóstico fase por fase
.venv/bin/python -m tools.diagnostico_fases "<pdf>" --html
xdg-open "<pdf>_diagnostico_fases/index.html"

# 4. Si tocas el detector, reinstalar SIEMPRE:
cp -r inkscape_addon/. ~/.config/inkscape/extensions/
find ~/.config/inkscape/extensions -name __pycache__ -exec rm -rf {} +

# 5. Validar contra baseline (10 PDFs)
for pdf in revision_10pdfs/*.pdf; do
  bash tools/tramear_pdf.sh "$pdf"
done
# Comparar contra los 65 W documentados en el plan.

# 6. Re-optimizar parámetros (~30 min)
.venv/bin/python -m tools.optimizar --pdfs 3 --iters 100 --top 5

# 7. Pipeline completo welding book enriquecido
.venv/bin/python -m tools.extraer_bom --carpeta revision_10pdfs --csv revision_10pdfs/boms.csv
.venv/bin/python -m tools.extraer_cajetin --carpeta revision_10pdfs --salida revision_10pdfs/cajetines.csv
.venv/bin/python -m tools.enriquecer_welding_book "<pdf>"
.venv/bin/python -m tools.consolidar_welding_book --carpeta revision_10pdfs --salida revision_10pdfs/welding_book_consolidado.xlsx
```

**Defaults validados (NO cambiar sin re-validar):**
- `radio_min = 1.4`, `radio_max = 2.5`, `tolerancia_linea = 1.5`
- `radio_callout = 10.4`, `offset_callout = 43.4`, `distancia_max_callout = 231.4`

**Baseline costuras 10 PDFs (suma):** 65 W antes de Fase B → 69 W tras Fase B (+4 wendolets en ET40F04-42).

---

## 9. Auditoría — fallos y deuda técnica conocidos

> Auditoría realizada 2026-05-13 por agente independiente. Citar cualquier punto al iterar.

### Fallos / bugs reales

1. **`tramear_isometrico.inx` desactualizado vs `.py`**: faltan params `color_tubing`, `prefijo_tubing`, `distancia_max_callout`. Por GUI el usuario no puede editar esos valores. Aún expone `detectar_field_welds` que el código ignora (`seam_detector.py:99`). **Prioridad máxima — bloquea uso por GUI.**
2. **`raster_detector.py:124`** construye `Costura` sin `tipo` ni `relleno`; al fusionar con vectoriales un raster nunca etiquetará "T".
3. **`numbering.py:37`** compara `c.y` con `filas[-1][-1].y` (último elemento) en vez de la media → drift acumulativo agrupa mal en planos densos.
4. **`seam_detector.py::_radio_centro_tipo`** marca `el._tramear_skip_ctm = True` siempre. Si el mismo `el` se reutiliza en varias pasadas (pasa: `_buscar_circulos` se llama desde `enriquecer_welding_book.py` y `diagnostico_fases.py`), la CTM no se aplica más. **Estado mutable global sobre nodos lxml.**
5. **`tramear_isometrico.py:316`** busca capa Costuras solo en hijos directos. Si el SVG envuelve todo en `<g transform=...>` (típico al importar PDF), no la encuentra y crea una nueva en cada ejecución → **callouts duplicados al re-ejecutar.**
6. **`seam_detector.py::_anadir_wendolets`** acepta params `segmentos` y `long_max_triangulo` pero NO los usa (líneas 197-198). La firma miente.

### Riesgos / deuda técnica

- **CERO tests automatizados.** Regresión solo se detecta abriendo PDFs a mano.
- **Hardcodes "Repsol"**: columnas X del BOM (`extraer_bom.py:36-42`), regex cajetín (`extraer_cajetin.py:42-66`), zona piece marks (`enriquecer_welding_book.py:51-53`). Cualquier PDF de otra empresa romperá silenciosamente.
- **`/usr/share/inkscape/extensions` empujado en `sys.path`** desde múltiples archivos. Frágil entre versiones de Inkscape; en Windows la ruta no existe.
- **`enriquecer_welding_book.py:114-130`** importa funciones privadas (`_buscar_circulos`, etc.) del addon → acoplamiento fuerte.
- **`tramear_isometrico.py::_construir_grid_ocupacion`** llama `bounding_box()` por cada elemento del SVG: O(n) costoso. En planos grandes (10k elementos) puede ser problema.
- **Cache `cargar_svg` en `tools/_artifacts/validacion/cache_svg/`** sin invalidar por mtime — si el PDF cambia, el cache se queda viejo.

### Redundancia / duplicación

- `_radio_y_centro` (`seam_detector.py:326`) **muerto** — sustituido por `_radio_centro_tipo`. Borrar.
- `_marcar_field_welds` (`seam_detector.py:216`) **muerto** — el comentario en :96-99 dice que ya no se usan. Borrar + imports `hay_x_cerca`/`dist_y_t`.
- **Distancia punto-segmento implementada 3 veces** (seam_detector, geom, raster_detector).
- **Bresenham implementado 2 veces** (tramear_isometrico vs optimizar.py).
- **Heurística `_es_relleno` / `_tiene_relleno` duplicada** en seam_detector.py:257 y tramear_isometrico.py:513.
- **Render PNG vía subprocess inkscape** repetido en 3 archivos sin abstraer.

### Inconsistencias plan ↔ código

- **`project_convenciones_repsol.md`** habla de field welds; el código los desactivó.
- **`project_mvp_v1.md`** dice "numeración debe seguir flujo lógico" — sigue siendo barrido por filas (Fase F sin tocar).
- **PLAN Fase C** (tubing CD40F09/F10): no implementada; el código sigue con `n_lineas == 4`.
- **PLAN Fase D**: existe `optimizar_callouts.py` pero NO PROBADO.

---

## 10. Top 5 mejoras prioritarias (recomendación auditor)

1. **Sincronizar `.inx` con `.py`** — bloquea uso del addon por GUI.
2. **Tests de regresión sobre `revision_10pdfs/`** — snapshot de count costuras por PDF (W=69 tras Fase B). Ejecutar antes de cualquier commit en `seam_detector.py`.
3. **Borrar código muerto** (`_radio_y_centro`, `_marcar_field_welds`, args ignorados, imports sin uso) — reduce 80+ líneas y desambigua qué está vivo.
4. **Detector de cuadrados (tubing) robusto** — aceptar también `n_lineas + n_curvas == 4` con bbox cuadrada (resuelve Fase C).
5. **Cache de `bounding_box()` en grid** — memoizar por `id(el)`; el grid se recorre dos veces.

---

## 11. Memoria estructurada (memory/)

Sistema de memoria persistente del agente en `/home/mart-n-boris/.claude/projects/-home-mart-n-boris-Escritorio-programa-para-tramear-isom-tricos/memory/`:

- `MEMORY.md` — índice
- `project_addon_isometricos.md` — visión general
- `project_mvp_v1.md` — MVP definido
- `project_convenciones_repsol.md` — convenciones de los 1824 PDFs
- `project_wendolets_convencion.md` — patrón par a 7.8 pt alineado
- `user_profile.md` — perfil del usuario (operario, no experto en informática)

Una IA sucesora **debe leer todos** estos archivos antes de iterar.

---

## 12. Cómo continuar — orden sugerido

1. **Leer este HANDOFF + memoria.** Sin contexto, no toques nada.
2. **Verificar entorno**: `bash install.sh` instala addon. `.venv/bin/python -c "import scipy, openpyxl, fitz, inkex"` debe pasar sin errores.
3. **Smoke test**: `.venv/bin/python -m tools.diagnostico_fases "revision_10pdfs/19-000-2-02-00001 sheet 2121ET40F04-42_IS00.pdf" --html` debe generar el informe sin error.
4. **Atender deuda técnica trivial primero** (auditoría sección 9: borrar muerto, sync .inx). Acción de bajo riesgo, contexto mínimo.
5. **Decidir siguiente fase** con el usuario: D (callouts) > C (tubing) > F (numeración por flujo).
6. **Para cualquier cambio en el detector**: validar contra los 10 PDFs de `revision_10pdfs/` y comparar con baseline (W=69, suma).

---

## 13. Comandos útiles de referencia rápida

```bash
# Procesar 1 PDF (genera SVG marcado + welding_book.csv)
bash tools/tramear_pdf.sh "<pdf>"

# Diagnóstico HTML completo
.venv/bin/python -m tools.diagnostico_fases "<pdf>" --html

# Diagnóstico Fase B (par wendolets perdidos)
.venv/bin/python -m tools.diagnostico_wendolets "<pdf>"

# BOM solo
.venv/bin/python -m tools.extraer_bom "<pdf>"

# Welding book enriquecido (E.5)
.venv/bin/python -m tools.enriquecer_welding_book "<pdf>"

# Optimizar parámetros (~30 min, NO con Inkscape abierto)
.venv/bin/python -m tools.optimizar --pdfs 3 --iters 100 --top 5

# Validar TOP-1 vs baseline
.venv/bin/python -m tools.validar_top1

# Reposicionar callouts (Fase D, NO PROBADO)
.venv/bin/python -m tools.optimizar_callouts "<svg_marcado>"

# Reinstalar addon tras tocar código del addon
cp -r inkscape_addon/. ~/.config/inkscape/extensions/
find ~/.config/inkscape/extensions -name __pycache__ -exec rm -rf {} +
```

---

## 14. Contacto / autoridad de cambios

El usuario es el único stakeholder. **No tomar decisiones autónomas sobre:**
- Cambiar defaults validados sin re-validar contra los 10 PDFs.
- Borrar archivos en `revision_10pdfs/` o `0-ISOS 2121/`.
- Push a remoto (no hay; el proyecto es local).
- Instalar dependencias nuevas sin avisar (Python embebido de Inkscape no las verá).

**Sí tomar decisiones autónomas sobre:**
- Refactor interno que no cambia comportamiento observable.
- Tests automatizados.
- Docstrings y limpieza de código muerto.
- Diagnósticos y herramientas auxiliares en `tools/`.

---

*Fin del documento. Si tras leerlo aún tienes dudas sobre cómo continuar, prefiere preguntar al usuario antes de tocar el detector.*
