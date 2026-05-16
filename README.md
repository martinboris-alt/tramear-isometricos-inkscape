# Tramear Isométricos — Addon para Inkscape

Extensión de Inkscape para **trameado automático de isométricos de tubería**.
Detecta costuras de soldadura, las numera siguiendo el sentido de flujo y genera el welding book en CSV.

## Características

- Detección automática de costuras soldadas (W) y tubing socket weld (T)
- Numeración siguiendo el sentido de flujo (flecha de flujo del isométrico)
- Filtrado de falsos positivos: puntas de flecha, cotas, válvulas bridadas
- Callouts numerados con optimización de posición (evita texto y cruces)
- Exporta **welding book** en CSV (y XLSX si openpyxl está instalado)

## Instalación

1. Descarga `dist/tramear_isometrico.zip`
2. Abre Inkscape
3. Ve a **Extensiones → Administrar extensiones → Instalar desde archivo**
4. Selecciona el ZIP descargado
5. Reinicia Inkscape

La extensión aparece en **Extensiones → Tramear isométrico → Tramear isométrico**.

## Uso rápido (línea de comandos)

```bash
bash tools/tramear_pdf.sh ruta/al/isometrico.pdf
```

Genera `*_marcado.svg` y `*_welding_book.csv` en la misma carpeta que el PDF.

## Requisitos

- Inkscape 1.0 o superior
- Python 3.8+
- `inkex` (incluido con Inkscape)
- Opcional: `scipy`, `Pillow` para optimización de callouts

## Estructura del proyecto

```
inkscape_addon/          Código fuente de la extensión
  tramear_isometrico.inx   Definición de la extensión (parámetros UI)
  tramear_isometrico.py    Punto de entrada
  tramear_core/            Módulos de detección y numeración
dist/
  tramear_isometrico.zip   ZIP listo para instalar en Inkscape
tools/
  tramear_pdf.sh           Procesado por lotes desde terminal
  optimizar_callouts.py    Post-proceso de posición de callouts
revision_10pdfs/           PDFs de ejemplo para validación
```
