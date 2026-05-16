#!/usr/bin/env bash
# Genera dist/tramear_isometrico.zip listo para instalar en Inkscape.
# Uso: bash make_zip.sh
# Instalación: Inkscape → Extensiones → Administrar extensiones → Instalar desde archivo

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
ADDON="$ROOT/inkscape_addon"
OUT="$ROOT/dist/tramear_isometrico.zip"

mkdir -p "$ROOT/dist"
rm -f "$OUT"

cd "$ADDON"
zip -r "$OUT" \
    tramear_isometrico.inx \
    tramear_isometrico.py \
    tramear_core/__init__.py \
    tramear_core/geom.py \
    tramear_core/graph_traversal.py \
    tramear_core/numbering.py \
    tramear_core/raster_detector.py \
    tramear_core/seam_detector.py \
    tramear_core/welding_book.py \
    -x "*/__pycache__/*" "*.pyc"

echo "✓ Generado: $OUT"
echo ""
echo "Para instalar:"
echo "  Inkscape → Extensiones → Administrar extensiones → Instalar desde archivo"
echo "  Selecciona: $OUT"
echo "  Reinicia Inkscape."
