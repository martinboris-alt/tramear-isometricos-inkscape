#!/usr/bin/env bash
# Prepara el entorno para los scripts CLI de tools/ (catalogador,
# extractor de PDFs, etc.). NO afecta a la extensión de Inkscape
# (esa se instala con install.sh y usa el Python interno de Inkscape).

set -euo pipefail

AQUI="$(cd "$(dirname "$0")" && pwd)"
VENV="$AQUI/.venv"

if [[ ! -d "$VENV" ]]; then
    echo "Creando entorno virtual en $VENV …"
    python3 -m venv "$VENV"
fi

echo "Actualizando pip e instalando dependencias…"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r "$AQUI/requirements.txt"

echo
echo "Listo. Ejemplos de uso:"
echo
echo "  Catálogo de todos los PDFs en samples/:"
echo "    .venv/bin/python -m tools.catalogar_samples"
echo
echo "  Extraer objetos de un PDF concreto a JSON:"
echo "    .venv/bin/python -m tools.pdf_extractor samples/mi_iso.pdf --json mi_iso.json"
echo
echo "  Test de humo (genera un PDF sintético y lo procesa):"
echo "    .venv/bin/python -m tools._smoke_test"
