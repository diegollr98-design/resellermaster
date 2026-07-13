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

_(sin incidentes todavía — el proyecto acaba de arrancar)_
