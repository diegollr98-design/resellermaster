# Fase 2 — Continuación (extracción + precio). SESIÓN FRESCA

> Arranca aquí. La sesión anterior (2026-07-14/15) construyó las 3 costuras nuevas y las auditó a fondo. Este seed destila el estado REAL contra el repo, no la memoria de esa sesión. **Reconcilia igualmente contra el repo antes de tocar nada** (`change-loop.md` §D): lee el código y `git log`, no este fichero.

## Prompt de arranque
```
Eres el orquestador ("papá oso") de RESELLERMASTER. Lee CLAUDE.md, .claude/rules/
(decision-making, truth-loop, change-loop, architecture, product), incident-ledger.md
(INC-001 a INC-012) y docs/seeds/fase-2-continuacion.md.
Fase 2: las costuras estan hechas y auditadas. Falta MEDIR con la API key de Diego,
la pantalla de la ficha, y cerrar el gate /eval.
```

## QUÉ ESTÁ HECHO Y VERIFICADO (no lo rehagas; commits `d21972d`, `8bc15c3`, `df25f36`)
- **`core/llm.py`** (Costura 1) — único punto de llamada a un proveedor. Caché por hash (imagen+modelo+**prompt**; un hit no llama a la API, cuesta 0), contabilidad de coste en USD, `estimar_coste_lote` (funciona SIN key y SIN red). Errores tipados y ruidosos, jamás fallback silencioso. Modelo configurable: `claude-haiku-4-5` por defecto ($1/$5 MTok), `sonnet-5`, `opus-4-8` para comparar.
- **`core/pricing.py`** (Costura 2, v1, 0 €) — no tasa: **construye la consulta**. `precio=None` es inexpresable de violar. Atajo EAN = comparables del mismo producto garantizados (con checksum GS1). Consulta sin término IDENTIFICATIVO (marca/modelo/tipo/ean) → se abstiene. v2 (SerpAPI) dejada con `NotImplementedError`.
- **`core/extract.py`** (ExtractorEngine) — **PROPONE y ENSEÑA EL PÍXEL, no afirma** (`[INC-012]`, decisión de Diego tras 3 `listing-audit` BLOQUEANTE). `Propuesta(campo, valor, recorte, evidencia, lecturas, alternativas, motivo)`. `confianza=alta` sólo la da un EAN con checksum válido — ninguna lectura de modelo puede producirla. OCR localiza → recorte a resolución nativa → VLM lee el recorte (Haiku reescala a 1568px: mandar la foto entera reproduce el cuello de botella y se paga). Un crop rinde VARIOS campos. Conflictos → todas las candidatas con su recorte, nunca elige.
- **`tests/golden/legibilidad.json`** — EL GATE. Qué es legible en qué píxel (7×8 celdas, verificado a ojo). Las 3 trampas del lote: estampado≠marca (P4 Jack&Jones), dos marcas (P5 UMBRO/RAMI JALAB), palabra tapada por cable (P2 `.Pocket__`).
- **406 tests verdes, ruff limpio.** Coste medido: **19,2 cts el lote (2,75 cts/producto)**, 0 al reprocesar.

## COBERTURA MEDIDA (VLM oráculo, sin key) — la cifra que valida el diseño
Prendas (P3-P7): **marca 5/5 confirmable, talla 5/5**. P4/P5 salen como CONFLICTO con ambas candidatas + recorte → eso ES cobertura (elegir en 2s es el producto). Cajas (P1/P2): marca no publicable por etiqueta (el logo no está en etiqueta de cuello) pero se enseña el píxel y resuelven por EAN/modelo. Baseline del diseño viejo (roto): marca 2/5, talla 1/5.

## LO QUE FALTA — en orden, con phase gate de Diego entre cada uno

### 1. MEDIR CON LA API KEY REAL (bloqueante; es la única medición que queda y la que decide si el VLM vale)
Todo lo auditado hasta ahora prueba **cómo el CÓDIGO trata lo que el modelo diga**. NO está medido **con qué frecuencia Haiku 4.5 MIENTE en los crops reales de Diego.** Ningún test verde lo sustituye.
- Diego pone su key de Console en `.env` (raíz): `ANTHROPIC_API_KEY=sk-ant-...`. **`.env` YA está en `.gitignore`** (verificado). NUNCA commitear una key.
- Correr el extractor real sobre las 33 fotos (coste ~19 cts, una vez; la caché lo fija). Para CADA campo propuesto: ¿el `valor` del VLM coincide con lo que se lee en el `recorte`? Es la tasa de alucinación real, medida contra el píxel (Capa 2 del `truth-loop`), no contra una tabla de verdad (Diego no la da, D1).
- Reportar: alucinación por campo, cobertura real, coste real (vs los 19,2 cts estimados), y **si el crop que se enseña es de verdad legible a ojo** (si no, el pipeline propone sobre un píxel que Diego tampoco puede juzgar → inútil).

### 2. PANTALLA DE LA FICHA (`ui/ficha.py`, no existe) — aquí vive TODO el valor ahora
El extractor propone; **la pantalla es la que hace que Diego confirme en 2 segundos**. Sin ella, la Propuesta es una estructura de datos que nadie ve. Requisito central: **cada campo se pinta JUNTO A SU RECORTE, ampliado y legible.** Los conflictos (P4/P5) muestran las candidatas con sus dos recortes y Diego elige (un click). Al confirmar, el `Campo` pasa a `fuente="diego"`. Es superficie sensible (atributos) + es la que Diego TOCA → `AppText` obligatorio (`change-loop.md` §C4, `[INC-006]`). Persistencia por `core/store.py`: la confirmación es un hecho, no puede perderse en un rerun.

### 3. GATE `/eval` — reescribir a la métrica nueva
El skill actual compara contra una tabla de verdad. D1 la elimina, y el diseño v2 la hace irrelevante: la métrica ya no es "cuántos acierta" sino **"cuántos campos llega a poner delante de Diego con un recorte útil"** (cobertura) + **tasa de alucinación adversarial** (¿el valor propuesto está en el recorte?). El test `test_medir_cobertura_con_vlm_oraculo` en `tests/test_extract_golden.py` ya es el esqueleto: /eval debe correr eso + medir alucinación con la key real.

### 4. `PriceEngine` v1 → Diego prueba los enlaces
Ya construido. Falta enchufarlo a la ficha (botón "buscar comparables" → abre los enlaces de Wallapop/Vinted) y que Diego confirme que los comparables que salen son del mismo producto. Sólo entonces decidir si la v2 de pago (SerpAPI) merece la pena.

### 5. Sanitizador en el export (`schema.py::es_exportable`, ya escrito) — falta que el exportador lo LLAME y BLOQUEE. No es fase, es un cabo suelto barato.

## AVISOS QUE NO DEBES PERDER
- **`decision-making.md` §15 no está cableada:** nadie llama a `estimar_coste_lote` fuera de los tests. Antes de que la app procese un lote real, la UI DEBE mostrar el coste estimado y que Diego lo autorice. Cablearlo en el paso 2.
- **La ficha Frankenstein (`[INC-011]`):** `extract.py` tiene un aviso de coherencia (fotos disjuntas → no puede salir alta). Pero la defensa de verdad es la pantalla: Diego VE los recortes y nota si una etiqueta es de otra prenda. El aviso baja el techo; el ojo cierra.
- **Meta (`[INC-012]`):** la regla anti-bucle (`decision-making.md` §6) aplica a los veredictos adversariales: **2 `listing-audit` BLOQUEANTE de la misma clase = rediseñar, no un tercer parche.** El orquestador anterior lo disparó en la ronda 3, tarde.
- **Correr `/optimize`:** el ledger ganó `[INC-010][INC-011][INC-012]`, varios con ≥2 incidencias de la misma clase (confianza anti-correlacionada con el riesgo: INC-005/009/010; "quién decide": INC-008/012). Toca promover.
```
