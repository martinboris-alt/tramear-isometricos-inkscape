"""Test de humo del pipeline de extracción.

Genera un PDF sintético con una tubería en zig-zag y círculos pequeños
sobre las uniones (simulando costuras). Después corre el extractor y
verifica que se detectan los círculos en los puntos esperados.

No es un test exhaustivo; sirve para tener señal temprana de que el
pipeline funciona antes de procesar PDFs reales.
"""

from __future__ import annotations

import sys
from pathlib import Path

import fitz

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from tools.pdf_extractor import extraer_pdf  # noqa: E402


TUBERIA = [
    (60, 200),
    (200, 200),
    (260, 140),  # codo isométrico
    (400, 140),
    (460, 200),
    (560, 200),
]
RADIO_COSTURA = 3.0


def generar_pdf(ruta: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    s = page.new_shape()

    # Tubería: trazo grueso negro
    for (x1, y1), (x2, y2) in zip(TUBERIA, TUBERIA[1:]):
        s.draw_line((x1, y1), (x2, y2))
    s.finish(color=(0, 0, 0), width=1.5)

    # Costuras: círculos pequeños sobre cada vértice intermedio
    for (x, y) in TUBERIA[1:-1]:
        s.draw_circle((x, y), RADIO_COSTURA)
    s.finish(color=(1, 0, 0), width=0.4, fill=None)

    # Texto al lado (simulando rótulo)
    page.insert_text((50, 50), "ISOMETRICO DE PRUEBA", fontsize=12)
    page.insert_text((50, 350), "Línea: 4\"-PG-1234-A1A", fontsize=10)

    s.commit()
    doc.save(ruta)
    doc.close()
    return ruta


def main():
    out = _HERE / "_artifacts" / "test_sintetico.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    generar_pdf(out)
    print(f"PDF sintético: {out}")

    extr = extraer_pdf(out)
    p = extr.paginas[0]
    print(f"\nDatos extraídos página 1:")
    print(f"  Líneas detectadas:  {len(p.lineas)}")
    print(f"  Círculos detectados: {len(p.circulos)}")
    print(f"  Textos detectados:  {len(p.textos)}")
    print(f"  Imágenes (esperado 0): {len(p.imagenes)}")

    print("\nCírculos:")
    for c in p.circulos:
        print(f"  ({c.cx:.1f}, {c.cy:.1f}) r={c.r:.2f}  proc={c.procedencia}")

    print("\nTextos:")
    for t in p.textos:
        print(f"  ({t.x:.1f}, {t.y:.1f}) size={t.tamano_fuente:.1f}  «{t.texto}»")

    # Validaciones
    esperados = TUBERIA[1:-1]
    encontrados = [(c.cx, c.cy) for c in p.circulos]
    aciertos = 0
    for (ex, ey) in esperados:
        if any(abs(cx - ex) < 5 and abs(cy - ey) < 5 for (cx, cy) in encontrados):
            aciertos += 1
    print(f"\nCosturas esperadas: {len(esperados)}  encontradas: {aciertos}")
    if aciertos == len(esperados):
        print("OK — el pipeline detecta correctamente las costuras sintéticas.")
        return 0
    else:
        print("⚠ Algunas costuras no se detectaron. Revisar reconocimiento de círculos.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
