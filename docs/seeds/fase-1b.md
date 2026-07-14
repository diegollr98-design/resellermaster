# Fase 1b — Reescribir la agrupación sobre datos reales

> **SESIÓN FRESCA. Arranca aquí.** La sesión anterior acumuló mucho contexto y su análisis está destilado en este seed + en las reglas del repo. **No hace falta releerla.**

## Prompt de arranque

```
Eres el orquestador ("papá oso") de RESELLERMASTER. Lee CLAUDE.md, .claude/rules/
(decision-making, truth-loop §E, change-loop, architecture, product, file-organization),
.claude/incident-ledger.md (INC-001 a INC-004 — son la memoria de por qué el diseño es
como es) y docs/seeds/fase-1b.md.

Vamos a reescribir core/grouping.py sobre el diseño MEDIDO en truth-loop.md §E, usando
el golden set real de Diego (tests/golden/truth.json + carpeta fotos/).
```

## Qué está HECHO y funciona (no lo toques)
`core/schema.py`, `core/images.py`, `core/store.py`, `app.py`, `ui/` — **198 tests en verde**, auditados. La UI ya avisa si un lote llega sin EXIF.

## Qué está ROTO
**`core/grouping.py`.** Tres versiones, tres `listing-audit` BLOQUEANTE. **Bórralo y reescríbelo** — no lo parchees: los tres intentos fallaron por la misma clase de error (ver `[INC-002]`, `[INC-003]`).

## Por qué falló, en una línea
Se diseñó durante tres rondas **sin mirar una sola foto de Diego**, calibrando umbrales contra polígonos sintéticos. Cuando por fin se miraron sus fotos, resultó que las señales sobre las que se construía **no existían o estaban invertidas**.

## El diseño, ya medido (detalle en `truth-loop.md` §E)

**La asimetría es la regla madre:** partir de más cuesta 5 segundos (Diego fusiona); fusionar dos productos cuesta una venta. **No optimices acierto — optimiza no-fusionar.**

1. **Corte por hueco temporal, sesgado a SOBRE-CORTAR.** Medido en el golden set: `hueco ≥ 20 s` → **6/6 fronteras, CERO fusiones**, 5 cortes de más. `≥ 25 s` ya fusiona → **el margen es estrecho: ante la duda, BAJA el umbral, nunca lo subas.**
   ⚠️ **No intentes "encontrar el umbral óptimo" en la distribución de huecos.** El jitter real es ±2.4 s y los huecos intra (4-8 s) e inter (14-36 s) **se solapan**. No hay separación bimodal. Buscarla ya falló dos veces (`[INC-002]`, `[INC-003]`).
2. **Reparación por TIPO de foto:** una foto de **metro / etiqueta / papel** nunca puede EMPEZAR un producto. Sólo un plano general puede. Eso mata los cortes de más predecibles (el metro a +94 s, el papel a +71 s).
3. **CLIP NO sirve para esto** (medido dos veces): dice "prenda entera 100%" ante un primer plano de etiqueta, y su similitud consecutiva está invertida (dos prendas distintas = 0.90; el plano y la etiqueta del mismo producto = 0.61). El paso 2 necesita un **modelo de visión de pago**, detrás de la costura `ExtractorEngine`. **Diego lo ha aprobado, sólo para esto.**
4. **Degradación honesta:** sin modelo (o si falla), la app **funciona igual** — sólo hay más cortes de más. **Nunca** una ficha contaminada. El suelo determinista no depende de nadie.
5. **Sin EXIF no hay suelo.** WhatsApp borra la fecha (medido: 0/59). La UI ya lo avisa.

## Verificación — no vale `pytest` en verde
Los tests pasaron en verde en **las tres versiones rotas**. La única señal que cuenta:

```
Correr agrupar() sobre las 33 fotos de `fotos/` (las IMG_20260714_*, que SÍ tienen EXIF)
y comparar contra tests/golden/truth.json:
  - FUSIONES (dos productos en un grupo): debe ser CERO. Es el gate. Si hay una, no se cierra.
  - Cortes de más: reportarlos. Son el coste aceptable (Diego fusiona).
```
Escribe eso como un test de verdad, no como un script suelto.

Después: `listing-audit` (superficie sensible `agrupacion`), y **doble pase** si da LIMPIA — es lo que exige `change-loop.md` §C2 y es lo que cazó los tres fallos anteriores.

## Gate de Diego
Que arrastre sus fotos a la app, vea los 7 productos propuestos, y compruebe que fusionar los cortes sobrantes es cuestión de segundos. Ése es el criterio real, no los tests.

## Lo que viene después (no ahora)
Fase 2: extracción de atributos. Falta pedirle a Diego la **verdad de atributos** de los 7 productos (marca, talla, composición, estado, precio real de venta) para que `/eval` pueda medir la **tasa de alucinación**, que es la métrica que manda.
