# Fase 2 — Extracción de atributos + precio por comparables

> **SESIÓN FRESCA. Arranca aquí.** La Fase 1 (agrupación + curado) está CERRADA y probada con un lote real de Diego. No hace falta releer la sesión anterior: su destilado está aquí + en las reglas del repo.

## Prompt de arranque
```
Eres el orquestador ("papá oso") de RESELLERMASTER. Lee CLAUDE.md, .claude/rules/
(decision-making, truth-loop, change-loop, architecture, product), incident-ledger.md
(INC-001 a INC-009 — memoria de por qué el diseño es como es) y docs/seeds/fase-2.md.
Fase 1 cerrada. Vamos a la Fase 2: extraer atributos y precio de los 7 productos reales.
```

## Qué está HECHO (no lo toques)
`core/schema.py`, `core/images.py`, `core/store.py`, `core/grouping.py`, `app.py`, `ui/` (ingesta + curar) — **280 tests en verde**, auditados. La cremallera de curado pasó 5 rondas de `listing-audit`. Golden set real en `tests/golden/` (33 fotos, 7 productos, EXIF intacto; **verdad de AGRUPACIÓN fijada**, verdad de ATRIBUTOS deliberadamente NO — ver decisión D1).

## Qué NO existe todavía (esto es la Fase 2, greenfield)
`core/extract.py` (Costura 1, `ExtractorEngine`) y `core/pricing.py` (Costura 2, `PriceEngine`). Contratos definidos en `rules/architecture.md`.

## DECISIONES DE DIEGO — inmutables para esta fase (no las relitigues)

- **D1 — Diego NO da la verdad de atributos. El modelo extrae el máximo de la imagen.** No es un capricho: es coherente con `truth-loop.md`. La mayoría de atributos (marca, talla, composición) son **texto en una etiqueta** → el OCR los lee del píxel, no se inventan. Donde la etiqueta no se lea → `null` + `confianza=baja`, nunca un valor plausible.

- **D2 — OCR primero (gratis, no alucina), modelo de visión sólo para lo que el OCR no alcanza.** "Extraer el máximo" empuja hacia un VLM, que SÍ puede alucinar una marca — por eso el OCR es la señal primaria y el VLM rellena huecos VIENDO la salida del OCR, nunca adivina. Ver `architecture.md` §Costura 1 e `[INC-001]`. Medido en Fase 1: el OCR ya lee `Reebok`, `XXL`, `UMBRO`, `ORIGINAL MARINES`, `New Age`, `XXS` de las fotos reales, y **el metro se delata solo** (ristra de dígitos) → regla gratis que caza 1 corte de más.

- **D3 — Precio desde comparables por BÚSQUEDA POR IMAGEN, con guardia de MISMO producto.** Petición explícita de Diego, y él mismo señaló el riesgo: un comparable que se *parece* pero no es el mismo producto **NO cuenta** (match visual del mismo, no por texto del título). = `truth-loop.md` §D. Sin comparables suficientes del mismo → `precio=None` + motivo, nunca un rango inventado.

## Reconciliación del GATE (léelo: el `/eval` actual asume algo que D1 elimina)
El skill `/eval` está escrito para comparar contra "ground truth verificado por Diego". D1 lo quita. **Reconciliación (coherente con `truth-loop.md` §C, que dice que las capas verifican LEGIBILIDAD y PROCEDENCIA, no verdad):**
- La **métrica que manda sigue siendo la tasa de alucinación**, medida **adversarialmente**: `listing-audit` coge cada campo con `confianza=alta` y comprueba **si ese dato es LEGIBLE en el recorte de la foto**. "¿Este texto está de verdad en este píxel?" es comprobable sin saber la marca real. Un campo confiado que no se ve → alucinación.
- **Límite honesto, no escondido:** eso caza lo INVENTADO (marca que no está en ninguna foto), NO el "OCR leyó mal una etiqueta que sí está" (Reebok vs Reobok). Ese último tramo lo caza el **ojo de Diego en la revisión en pantalla (Capa 3)**, que hace igual antes de publicar.
- **Tarea para esta fase:** actualizar el skill `/eval` para que su premisa sea "abstención + legibilidad adversarial", no "accuracy vs verdad de Diego". Opción que la sesión puede sopesar sin forzarla: un mini-set de *qué campos SON legibles* por producto (no sus valores) — comprobable por cualquiera, permite medir bien la abstención. Diego dijo no a dar valores; esto no son valores.

## Realidades de coste (ANTES de gastar un euro, estimar y mostrar — `decision-making.md` §15)
- **Búsqueda inversa de imagen = API de pago** (Google Lens vía SerpAPI). No hay gratuita (Bing Visual Search se retiró ago-2025). Precio v1 puede empezar SIN tasar: `pricing.py` construye la CONSULTA/enlace y Diego ve los comparables con un click (`architecture.md` §Costura 2, v1). El match visual automático es v2 (de pago).
- **VLM de visión = céntimos/producto** (Haiku 4.5 visión ~0,2 cts/foto, cacheado por hash). Suscripción de Claude NO vale (prohibido programático). Toda llamada pasa por `core/llm.py` (Costura 1) — conteo de coste y caché por hash obligatorios.
- La elección de proveedor se decide con `/eval` sobre el golden set, no por intuición — pero D1 cambia qué mide `/eval` (ver arriba). Resolver eso ANTES de elegir proveedor.

## El orden sugerido (no encadenar sin OK de Diego — phase gates)
1. `ExtractorEngine` con **OCR local** (gratis) contra el schema → medir cobertura real sobre los 7 productos. Puede que cubra más de lo esperado (D2, medido en Fase 1).
2. Actualizar `/eval` a la métrica adversarial de legibilidad (reconciliación de arriba).
3. Sólo si `/eval` muestra que el OCR se queda corto → añadir el VLM detrás de la costura, viendo la salida del OCR.
4. `PriceEngine` v1 (enlace de búsqueda, 0 €) → Diego prueba → decidir si el match visual de pago (v2) merece la pena.
5. Sanitizador de texto antes de exportar (defensa con dientes: sin emails/enlaces/MAYÚSCULAS/marcas ajenas — `product.md` §7).

## Verificación
`listing-audit` sobre cada afirmación del pipeline (obligatorio, superficie `atributos`+`precio`). Doble pase si da LIMPIA. El gate real NO es `pytest` verde: es la tasa de alucinación adversarial + el ojo de Diego.
```
