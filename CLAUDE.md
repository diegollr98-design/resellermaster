# CLAUDE.md — RESELLERMASTER

## v0.1 | Actualizado: 2026-07-13 | Infra agéntica instalada, producto SIN planear todavía. App local (Streamlit) para subir fotos de productos de reventa en masa → agrupar por producto → generar título/descripción/campos de Wallapop y Vinted → copiar-pegar y publicar rápido. Fase actual: **harness listo, esperando la sesión de planificación**. Nada de código de producto hasta que el plan esté aprobado por Diego.

## ⚠️ CÓMO TRABAJAR CON EL USUARIO (Diego) — LEER SIEMPRE PRIMERO
Diego pregunta/propone POCO; cuando lo hace es señal de ALTA prioridad y suele tener razón (detecta patrones bien). Detalle en `.claude/rules/decision-making.md`.
1. **Ancla en SU plan, no en la cultura del repo.** Los defaults conservadores (diffs mínimos, "validar antes de actuar", diferir código, no over-engineer) son para situaciones AMBIGUAS — **NO overrides de un plan explícito**. Si declaró un plan, el trabajo que implica (incl. código nuevo) SE HACE; ayúdale a ejecutarlo, no le ofrezcas la alternativa más barata/sin-código como contra-recomendación.
2. **No re-litigues premisas que él no planteó.** Si ves un riesgo real, dilo en UNA línea con dato y sigue.
3. **Verifica con datos/código REALES y la herramienta correcta ANTES de afirmar.** Lee el código, no la doc. Si te equivocas, **corrige y avanza — NO compenses con más caveats/hedging**.
4. **Si pushea, repite, o dice "¿seguro?"/"siento que no colaboras" → PARA.** Ha detectado algo que se te escapó. Reanaliza desde SU marco, sin defender tu posición previa.
5. **No tengas miedo al código.** Escribirlo bien es tu fuerte; "no tocar nada" está mal calibrado cuando él pide acción.
6. Si el contexto es muy largo y notas que empiezas a desvariar/repetir errores → **avísale y sugiere sesión fresca** (no lo escondas).

## ⚙️ MODELO DE ORQUESTACIÓN
- **Opus (orquestador, "papá oso"): gasta poco contexto.** Delega la implementación, revisa veredictos/resúmenes, **valida lo crítico** y actúa directo solo en lo crítico.
- **Sonnet en paralelo = implementación.** Tareas independientes → varios subagentes `engineer` lanzados a la vez (un mensaje, varias tool calls).
- **Phase gates:** tras cada fase, **Diego prueba con fotos reales**. Entregar checklist "Cómo probar la Fase X" y **PARAR** hasta su OK. No encadenar fases sin su visto bueno.
- **Protocolo de bug:** cada bug → subagente **`bug-hunter`** (opus, autocontenido, **sin contexto**, en paralelo) localiza la causa raíz con evidencia → el orquestador **valida** el veredicto **antes** de aplicar el fix.
- **Todo lo que el pipeline AFIRMA sobre un producto** (atributos, precio, agrupación de fotos) → subagente **`listing-audit`** (opus) intenta refutarlo contra las fotos reales. Velocidad del flujo copy-paste → **`flow-qa`** (sonnet).

## 🔁 EL LOOP QUE IMPORTA AQUÍ
El modo de fallo de este proyecto **no** es el código: es que **el pipeline mienta sobre el producto con total fluidez** (marca equivocada, talla inventada, precio sin comparables, foto del producto A en la ficha del B) y Diego lo publique. Una ficha mala no es un bug: es una venta perdida, una devolución y reputación quemada en una plataforma donde el rating es el activo.
- **Loop de Verdad** (`rules/truth-loop.md`) — cómo impedimos que el pipeline invente. Es el loop central del proyecto.
- **Loop de Cambios** (`rules/change-loop.md`) — protocolo ante cualquier cambio: verificación por ejecución, escritor único de reglas.
- **Meta-loop** — `/optimize` promueve incidentes del ledger a reglas. Es el **único** que escribe reglas.

## QUÉ ES
App **local** (Streamlit, un solo usuario: Diego) que ingiere un lote de fotos mezcladas de varios productos de segunda mano, las agrupa por producto (con confirmación humana), y produce para cada uno la ficha completa lista para **copiar y pegar** en **Wallapop** y **Vinted**: título, descripción, categoría, marca, talla, estado, color, material y precio. Objetivo primario: **minimizar segundos-hasta-publicar por producto**. Export CSV/JSON para inventario. **No automatiza la publicación** (ambas plataformas lo prohíben en sus términos).

## ARQUITECTURA — LAS 3 COSTURAS (detalle en `rules/architecture.md`)
Aún no hay código. Cuando lo haya, estas tres costuras son innegociables:
1. **`LLMEngine`** — TODA llamada a cualquier proveedor (Claude/Gemini/GPT/local) pasa por un único módulo. Contabiliza coste por producto y cachea por hash de imagen. El proveedor es una **decisión reversible**, no una dependencia esparcida por el código.
2. **`PriceEngine`** — el precio **nunca** sale de la imaginación del LLM. Sale de comparables observados con su URL. Sin comparables suficientes → `precio=None` + motivo. Nunca un número inventado.
3. **`ListingSchema`** — los campos obligatorios de cada plataforma/categoría viven en UN sitio. El LLM **rellena un esquema**, no inventa campos.

## REGLA DE ORO
- Antes de tocar código: `git commit -m "pre-fix [desc]"`. Después: `git commit -m "fix [desc]"`.
- **Ningún atributo del producto se afirma sin que sea VISIBLE en una foto** o lo haya introducido Diego. Si no se ve la etiqueta, el campo va `null` + `confianza=baja` — nunca un valor plausible.
- **Ningún precio sin comparables citados.** Sin datos → dilo, no inventes un rango.
- Cambios que tocan **superficies sensibles** (atributos, precio, agrupación de fotos, coste API, persistencia del lote) → pasan por `listing-audit` antes de darse por buenos.
- Ejecuta el gate `/eval` (golden set) **antes de cerrar** cualquier cambio en el pipeline de extracción. Verde local ≠ fichas correctas.

## MODELOS
```
Opus 4.8:   arquitectura, auditoría de fichas (listing-audit), bugs sutiles, elección de proveedor
Sonnet 4.6: implementación, UI Streamlit, pipeline, cambios rutinarios
Sonnet tiende a: no verificar supuestos, dar por hecho que el LLM "acertó" sin mirar la foto
Opus tiende a: conservadurismo excesivo — recordar "Anclar en el plan del usuario"
```
Elección del proveedor de visión: **pendiente, se decide con `/eval` sobre el golden set, no por intuición.** Hardware de Diego: RTX 3050 Laptop **4 GB VRAM** / 16 GB RAM → **visión local descartada** (no entra un VLM con calidad suficiente para leer logos y etiquetas).

## LO QUE NUNCA DEBES HACER
- **Afirmar un atributo del producto (marca, talla, material, medidas) que no sea legible en una foto.** Un campo vacío es recuperable; una ficha con la talla equivocada es una devolución.
- **Dar un precio sin comparables reales citados.** "Unos 20-25€" sin fuente es una mentira plausible.
- **Cerrar una agrupación de fotos sin que Diego la confirme.** Una foto del producto A en la ficha del B es el fallo más caro y el más silencioso.
- **Llamar a la API de un proveedor fuera de `LLMEngine`.** Rompe el conteo de coste y clava el proveedor.
- **Procesar un lote grande sin estimar y mostrar el coste ANTES.** Diego decide si lo lanza.
- **Perder el trabajo de curado de un lote** — el estado se persiste en disco, no sólo en `st.session_state`.
- **Automatizar la publicación en Wallapop/Vinted** (scraping de formularios, Selenium sobre sus webs). Va contra sus términos y arriesga el baneo de la cuenta que es el negocio.
- **Silenciar errores** — log + propagar. Una defensa que "solo avisa" no es defensa.
- **Commitear `.env`** o cualquier API key.
- **`git push` sin que Diego lo pida** en ese mismo mensaje.

## DETALLES → ver .claude/
- `rules/decision-making.md` — **cómo trabajar y decidir** (meta-regla expandida). Agnóstico de dominio.
- `rules/truth-loop.md` — **Loop de Verdad**: cómo impedimos que el pipeline invente atributos o precios. El loop central.
- `rules/change-loop.md` — **Loop de Cambios**: protocolo ante toda propuesta de cambio.
- `rules/architecture.md` — las 3 costuras (`LLMEngine`, `PriceEngine`, `ListingSchema`), coste, caché, persistencia.
- `rules/product.md` — campos de Wallapop/Vinted, categorías, taxonomía. **Se rellena en la sesión de plan.**
- `rules/file-organization.md` — estructura de carpetas.
- `rules/sessions-log.md` — bitácora por hito (histórico en `docs/sessions-log-archive.md`).
- `incident-ledger.md` — ledger append-only de incidentes; **solo `/optimize` promueve** a regla.
- `agents/` — `engineer` (Sonnet) · `bug-hunter` (Opus) · `listing-audit` (Opus) · `flow-qa` (Sonnet).
- `skills/eval` — **el gate**: golden set, tasa de alucinación · `skills/run` — levantar la app.
- **`/optimize` es el GLOBAL** (`C:\Users\diego\.claude\skills\optimize\`), a propósito: es el que conoce el ledger. Este proyecto **no** define uno propio — en `ecxm-ops` el `/optimize` de proyecto sombreaba al global y no sabía del ledger, así que el loop de auto-mejora nunca llegó a activarse. No repetir ese fallo aquí.
