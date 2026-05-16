#!/usr/bin/env bash
# Procesa un PDF: lo convierte a SVG con Inkscape y aplica la extensión
# Tramear Isométrico. Genera SVG marcado y welding book en la misma
# carpeta donde está el PDF.
#
# Uso: tools/tramear_pdf.sh <pdf>

set -euo pipefail

PDF="$1"
if [[ ! -f "$PDF" ]]; then
    echo "PDF no encontrado: $PDF" >&2
    exit 1
fi

BASE="$(dirname "$PDF")"
NAME="$(basename "${PDF%.*}")"
# Usar el mismo NAME para el SVG temporal — así el welding_book hereda
# el nombre correcto.
TMP_DIR="$(mktemp -d)"
SVG_ORIG="$TMP_DIR/${NAME}.svg"
SVG_MARC="$BASE/${NAME}_marcado.svg"
EXPORT_DIR="$BASE"

echo "→ Convirtiendo PDF a SVG: $NAME"
inkscape --pdf-poppler --export-type=svg \
         --export-filename="$SVG_ORIG" "$PDF" 2>/dev/null

INK_EXT=/usr/share/inkscape/extensions
USR_EXT="$HOME/.config/inkscape/extensions"

echo "→ Detectando costuras…"
LOG="$TMP_DIR/extension.log"
# IMPORTANTE: redirigir SOLO stdout al SVG. stderr a un log aparte —
# si los mezcláramos, los mensajes del addon contaminarían el SVG.
PYTHONPATH="$INK_EXT:$USR_EXT" python3 \
  "$USR_EXT/tramear_isometrico.py" \
  --radio_min=1.4 --radio_max=2.5 --tolerancia_linea=1.5 \
  --solo_sobre_linea=true --filtrar_puntas_flecha=true --modo=vectorial \
  --color_marca='#0066cc' --color_tubing='#9b1f2a' \
  --tamano_etiqueta=8.0 --radio_callout=10.4 --offset_callout=43.4 \
  --distancia_max_callout=231.4 \
  --prefijo='W-' --prefijo_tubing='T-' --numero_inicial=1 \
  --exportar_csv=true --exportar_xlsx=false \
  --ruta_export="$EXPORT_DIR" \
  "$SVG_ORIG" > "$SVG_MARC" 2>"$LOG"
head -5 "$LOG"

rm -rf "$TMP_DIR"

# Optimizar posición de callouts (post-procesado): mueve los círculos
# numerados a zonas libres de texto/cotas y evita cruces entre flechas.
# Requiere scipy + Pillow del .venv del proyecto — saltar si no están.
PROY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PY="$PROY_DIR/.venv/bin/python"
if [[ -x "$VENV_PY" ]] && "$VENV_PY" -c "import scipy, PIL" 2>/dev/null; then
    echo "→ Optimizando posición de callouts…"
    cd "$PROY_DIR"
    if "$VENV_PY" -m tools.optimizar_callouts "$SVG_MARC" 2>&1 \
            | tail -3 | sed 's/^/  /'; then
        OPTCALL="${SVG_MARC%.svg}_optcall.svg"
        if [[ -f "$OPTCALL" ]]; then
            mv "$OPTCALL" "$SVG_MARC"
        fi
    fi
fi

echo "✓ Marcado:   $SVG_MARC"
ls "$EXPORT_DIR/${NAME}"*welding_book.csv 2>/dev/null | head -1 \
    | xargs -r -I{} echo "✓ Welding:   {}"
