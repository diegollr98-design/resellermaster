# Ledger de Incidentes — RESELLERMASTER

**Append-only.** Registro FACTUAL de incidentes/near-misses cazados por el Loop de Cambios (`.claude/rules/change-loop.md` §F) y por el Loop de Verdad (`rules/truth-loop.md`). Vive en `.claude/` → **NO se auto-carga** (sin coste de tokens por sesión). **No se editan entradas pasadas; solo se añaden** (más reciente arriba).

**Para qué:** materia prima de la auto-mejora. **Solo `/optimize` (Paso 2)** promueve una entrada a regla, y solo con **≥2 incidencias independientes** de la misma clase, **o 1 de clase irreversible** (ficha publicada mal, precio inventado, foto cruzada, coste desbocado, pérdida de un lote). n=1 no irreversible → se queda aquí, no toca reglas. Al promover, la regla nueva se **etiqueta con el/los id** de esta lista; `/optimize` (Paso 5) retira las reglas cuyos ids no reaparecen en >5 sesiones.

**Formato:** `- [id] fecha · clase · qué pasó · evidencia (comando/veredicto) · estado`

**Clases:**
- `alucinacion` — el pipeline afirmó un atributo (marca/talla/material/medida) que no era legible en la foto.
- `precio` — precio dado sin comparables, o con comparables que no eran del mismo producto.
- `agrupacion` — foto asignada al producto equivocado, o producto partido en dos fichas.
- `coste` — gasto de API no estimado, no mostrado, o disparado respecto a lo previsto.
- `persistencia` — trabajo de curado perdido (crash, rerun de Streamlit, estado sólo en memoria).
- `verificacion` — un agente reportó sin verificar (dijo "OK" sin ejecutar/mirar la foto).
- `plataforma` — un campo generado no encaja con lo que Wallapop/Vinted acepta realmente.
- `proceso` — fallo de método (saltarse un gate, no pedir confirmación, encadenar fases sin OK).

**Estado:** `pendiente (≥2)` = registrado, aún no promovido · `promovido → <regla>` = ya es regla dura.

**Nota:** la existencia de este fichero es lo que **da de alta a este proyecto** en el detector global `C:\Users\diego\.claude\hooks\optimize-nudge.sh` (opt-in por presencia del ledger). El hook avisará en `SessionStart` si hay incidentes pendientes, si los ficheros auto-cargados pasan de 3000 líneas, o si han pasado >21 días desde el último `/optimize`.

---

<!-- entradas nuevas debajo, más reciente arriba -->

- [INC-002] 2026-07-13 · agrupacion · `core/grouping.py` metía **8 prendas distintas en UN grupo con `confianza="alta"`** con el ritmo de fotografiado real de un lote (3 fotos/prenda cada 4 s, 12 s para cambiar de prenda): el umbral de corte `mediana×3` da exactamente 12, y `12 > 12` es falso → cero cortes. El confirmador por pHash que debía cazarlo es **ciego al color** (phash promedia a luminancia): camiseta roja vs azul = distancia **0**, indistinguibles. Los dos fallos se refuerzan y el output sale marcado `alta`, la vía por la que la UI está diseñada para que Diego NO mire. · evidencia: `listing-audit` (barrido de 8 ritmos + medición de distancias pHash), re-derivado por el orquestador ejecutando el caso: `mediana=4 umbral=12 cortes=0`; `phash(roja) - phash(azul) = 0`. **Los 11 tests de `test_grouping.py` pasaban en verde** — sólo cubrían ratio inter/intra 7,5× cuando el fallo vive por debajo de 3,5×. · estado: pendiente (≥2) — reglas candidatas: (a) "un umbral derivado de la MEDIANA de una distribución no separa outliers: usar p75+1.5·IQR o separación bimodal"; (b) "si la señal primaria no pudo aplicarse (sin umbral derivable), la confianza NUNCA puede ser `alta` — el techo es `media`"; (c) "pHash es luminancia: no es una señal válida de identidad de producto cuando el color es el discriminante".

- [INC-001] 2026-07-13 · proceso · Propuse un plan con dos APIs de pago (LLM de visión + SerpAPI) **sin haber explorado en serio la alternativa gratuita**. Encuadré el problema como "visión" (→ VLM, → 4 GB VRAM insuficientes, → API de pago) cuando el problema real era **leer texto en una etiqueta** (→ OCR local en CPU, gratis). Lo cazó Diego preguntando "¿seguro no hay mejor opción?". Peor aún: el stack gratis (OCR) está **más alineado con el `truth-loop` que el de pago** — un OCR no puede alucinar una marca, un VLM sí — o sea que escribí una regla que argumentaba contra mi propia recomendación y no lo vi. · evidencia: `rules/truth-loop.md` §A ("ningún atributo sin que sea legible en una foto") vs. el plan propuesto en la misma sesión. · estado: pendiente (≥2) — regla candidata: "antes de proponer una dependencia de pago, enunciar el problema en términos de la CAPACIDAD mínima que resuelve el caso (¿leer texto? ¿clasificar? ¿inferir?), no del producto que primero viene a la cabeza; y comprobar si alguna regla del propio repo ya argumenta en contra".
- [INC-001] 2026-07-13 · proceso · Propuse un plan con dos APIs de pago (LLM de visión + SerpAPI) **sin haber explorado en serio la alternativa gratuita**. Encuadré el problema como "visión" (→ VLM, → 4 GB VRAM insuficientes, → API de pago) cuando el problema real era **leer texto en una etiqueta** (→ OCR local en CPU, gratis). Lo cazó Diego preguntando "¿seguro no hay mejor opción?". Peor aún: el stack gratis (OCR) está **más alineado con el `truth-loop` que el de pago** — un OCR no puede alucinar una marca, un VLM sí — o sea que escribí una regla que argumentaba contra mi propia recomendación y no lo vi. · evidencia: `rules/truth-loop.md` §A ("ningún atributo sin que sea legible en una foto") vs. el plan propuesto en la misma sesión. · estado: pendiente (≥2) — regla candidata: "antes de proponer una dependencia de pago, enunciar el problema en términos de la CAPACIDAD mínima que resuelve el caso (¿leer texto? ¿clasificar? ¿inferir?), no del producto que primero viene a la cabeza; y comprobar si alguna regla del propio repo ya argumenta en contra".
