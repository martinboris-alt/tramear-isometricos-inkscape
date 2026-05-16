# Tramear Isométricos — Addon para Inkscape

Extensión de Inkscape para **trameado automático de isométricos de tubería**.
Detecta costuras de soldadura, las numera siguiendo el sentido de flujo y genera el welding book en CSV.

## Características

- Detección automática de costuras soldadas (W) y tubing socket weld (T)
- Numeración siguiendo el sentido de flujo (flecha de flujo del isométrico)
- Filtrado de falsos positivos: puntas de flecha, cotas, válvulas bridadas
- Callouts numerados con optimización de posición (evita texto y cruces)
- Exporta **welding book** en CSV (y XLSX si openpyxl está instalado)

---

## Instalación en Windows

### Opción A — Script automático (recomendado)

1. Descarga o clona este repositorio
2. Haz doble clic en **`install_windows.bat`**
3. Reinicia Inkscape

La extensión aparecerá en **Extensiones → Tramear isométrico → Tramear isométrico**.

### Opción B — Manual

1. Descarga el ZIP desde `dist/tramear_isometrico.zip`
2. Extrae el contenido directamente en la carpeta de extensiones de Inkscape:

   ```
   C:\Users\<TU_USUARIO>\AppData\Roaming\inkscape\extensions\
   ```

   > Para abrir esa carpeta rápido: pulsa `Win+R`, escribe `%APPDATA%\inkscape\extensions` y pulsa Enter.

3. Asegúrate de que queda esta estructura:
   ```
   extensions\
     tramear_isometrico.inx
     tramear_isometrico.py
     tramear_core\
       __init__.py
       geom.py
       graph_traversal.py
       numbering.py
       raster_detector.py
       seam_detector.py
       welding_book.py
   ```

4. Reinicia Inkscape.

---

## Instalación en Linux / macOS

```bash
bash make_zip.sh          # regenera el ZIP (opcional)
```

O copia los archivos directamente:

```bash
EXT_DIR="$HOME/.config/inkscape/extensions"
cp inkscape_addon/tramear_isometrico.{inx,py} "$EXT_DIR/"
cp -r inkscape_addon/tramear_core "$EXT_DIR/"
```

Reinicia Inkscape.

---

## Uso rápido (línea de comandos — Linux/macOS)

```bash
bash tools/tramear_pdf.sh ruta/al/isometrico.pdf
```

Genera `*_marcado.svg` y `*_welding_book.csv` en la misma carpeta que el PDF.

---

## Requisitos

| Requisito | Versión | Notas |
|-----------|---------|-------|
| Inkscape | 1.0 o superior | Incluye Python e inkex |
| Python | 3.8+ | Bundled con Inkscape en Windows |
| lxml | cualquiera | Bundled con Inkscape |
| scipy + Pillow | opcional | Solo para optimización de callouts (herramienta CLI) |
| openpyxl | opcional | Solo para exportar XLSX |

---

## Estructura del proyecto

```
inkscape_addon/          Código fuente de la extensión
  tramear_isometrico.inx   Definición de la extensión (parámetros UI)
  tramear_isometrico.py    Punto de entrada
  tramear_core/            Módulos de detección y numeración
dist/
  tramear_isometrico.zip   ZIP con los archivos del addon
tools/
  tramear_pdf.sh           Procesado por lotes desde terminal
  optimizar_callouts.py    Post-proceso de posición de callouts
install_windows.bat        Instalador automático para Windows
make_zip.sh               Regenera el ZIP de distribución
revision_10pdfs/           PDFs de ejemplo para validación
```
