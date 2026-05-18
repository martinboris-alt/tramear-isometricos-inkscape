# Convenciones de tus isométricos

Rellena lo que sepas; lo que dejes en blanco lo deduciré de los PDFs y
te lo preguntaré después. Cuanto más concreto, mejor calibración.
Si tienes una foto/recorte de cada símbolo, también vale (pega la ruta).

---

## 1. Programa de ingeniería de origen

- Programa que genera los isométricos (SP3D / SmartPlant Isometrics / SPI,
  Isogen, CADWorx, AutoPLANT, PDMS, otro): ___
- Versión/proyecto (si lo sabes): ___
- ¿Usáis una plantilla/standard propia de empresa por encima del programa?: ___

---

## 2. Costuras de soldadura (welds)

Para cada tipo que aparezca, marca cómo se dibuja y cómo se anota.

### 2.1 Costuras de taller (Shop Weld, SW)
- Símbolo gráfico: ☐ círculo relleno ☐ círculo vacío ☐ punto ☐ otro: ___
- Tamaño aproximado del símbolo respecto al grosor de la tubería: ___
- Color/línea distintivos (si hay): ___
- ¿Lleva número o etiqueta al lado?: ___ (formato: ej. "1", "W-001", "SW-12"…)
- Posición de la etiqueta respecto al símbolo: ___ (arriba/derecha/encima/línea guía)

### 2.2 Costuras de campo / obra (Field Weld, FW)
- Símbolo gráfico: ___ (a menudo es una "bandera" o círculo con relleno distinto)
- Diferencias respecto a shop weld: ___
- Formato de etiqueta: ___ (ej. "FW-001")

### 2.3 ¿Otros tipos de unión soldada? (socket weld, butt weld, etc.)
- Tipo: ___ → cómo lo distingues gráficamente: ___
- Tipo: ___ → cómo lo distingues gráficamente: ___

---

## 3. Uniones roscadas (threaded)

- Símbolo gráfico: ☐ pequeño rectángulo ☐ triángulos enfrentados ☐ "T" o "TH"
  ☐ otro: ___
- ¿Aparece etiqueta de texto?: ___ (ej. "TH", "THD", "ROSC")
- ¿Cómo se distingue de una costura soldada para alguien que mira el plano?: ___

---

## 4. Uniones bridadas (flanges) y otros accesorios

(No es prioridad MVP, pero útil para no confundirlos con costuras)

- Brida: símbolo y etiqueta: ___
- Válvula: símbolo y etiqueta: ___
- Reducción: ___
- Codo: ___
- Tee: ___

---

## 5. Nomenclaturas / textos en el plano

### 5.1 Numeración de costuras
- Formato real (ej. `W-001`, `1`, `SW1-001`, `001`): ___
- ¿Reinicia por isométrico o es global del proyecto?: ___
- ¿Orden de numeración esperado? (recorrido de tubería / izquierda-derecha / siguiendo
  flujo / criterio propio): ___

### 5.2 Itemcode / código de material
- Formato típico (ej. `PE-1234`, `MT-AA-001`, alfanumérico de N caracteres): ___
- ¿Dónde aparece en el plano? (tabla lateral, línea junto al tramo,
  flecha desde el elemento, otro): ___
- ¿Hay tabla de materiales (BOM/MTO) embebida en cada PDF?: ___

### 5.3 Datos asociados que conviene capturar al welding book
Marca los que sí necesitas que aparezcan:
- ☐ Diámetro nominal (ej. 4", DN100)
- ☐ Schedule / espesor
- ☐ Material (A106-B, A312-TP316, …)
- ☐ Especificación de línea / "spec"
- ☐ Pintura / aislamiento
- ☐ Test pressure / servicio
- ☐ Otro: ___

### 5.4 Línea/spec de la tubería
- ¿Cómo se identifica cada línea?: ___ (ej. `4"-PG-1234-A1A`)
- ¿Una línea por isométrico, o varias?: ___

---

## 6. Cuadros y zonas del plano

- ¿Dónde está el **rótulo / cajetín** habitualmente? (esquina inferior derecha…): ___
- ¿Hay **tabla de costuras** ya impresa en el plano que queráis cotejar?: ___
- ¿Hay un **norte / orientación isométrica** que use el programa siempre igual?: ___

---

## 7. Casos raros / excepciones que recuerdes

(Cosas que confunden, cambios de convención entre proyectos, símbolos
ambiguos, situaciones donde a mano dudas):
- ___
- ___
- ___

---

## 8. Salida esperada (welding book)

- Formato preferido: ☐ Excel ☐ CSV ☐ los dos
- Plantilla concreta a respetar (si tenéis una): pega ruta o pégala como
  tabla aquí debajo.
- Campos obligatorios que TIENE que llevar: ___
