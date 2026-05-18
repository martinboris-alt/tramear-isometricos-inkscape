#!/usr/bin/env bash
# Instalador del addon Tramear Isométrico para Inkscape (Linux / macOS).
#
# Copia los archivos del addon a la carpeta de extensiones del usuario
# y, opcionalmente, instala las dependencias Python necesarias para
# el modo raster y la exportación a XLSX.

set -euo pipefail

AQUI="$(cd "$(dirname "$0")" && pwd)"
ORIGEN="$AQUI/inkscape_addon"

if [[ "$OSTYPE" == "darwin"* ]]; then
    DESTINO="$HOME/Library/Application Support/org.inkscape.Inkscape/config/inkscape/extensions"
else
    DESTINO="$HOME/.config/inkscape/extensions"
fi

if [[ ! -d "$ORIGEN" ]]; then
    echo "No se encuentra la carpeta inkscape_addon junto a este script." >&2
    exit 1
fi

mkdir -p "$DESTINO"
echo "Copiando addon a: $DESTINO"
cp -r "$ORIGEN/." "$DESTINO/"

echo
echo "Instalación de archivos completada."
echo
echo "Dependencias Python opcionales (XLSX y detección raster):"
echo "  - openpyxl  -> welding book en .xlsx"
echo "  - opencv-python, Pillow, numpy -> detección raster"
echo
read -r -p "¿Instalar dependencias con pip ahora? [y/N] " resp
if [[ "$resp" =~ ^[yYsS]$ ]]; then
    PYBIN="$(command -v python3 || true)"
    if [[ -z "$PYBIN" ]]; then
        echo "No se encuentra python3 en el PATH; instálalas manualmente con pip." >&2
        exit 0
    fi
    "$PYBIN" -m pip install --user -r "$AQUI/requirements.txt"
fi

echo
echo "Listo. Abre Inkscape y busca: Extensiones → Tramear isométrico."
