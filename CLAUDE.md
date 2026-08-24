# CLAUDE.md — RESELLERMASTER

## v0.12 | Actualizado: 2026-08-05 | App local (Streamlit): fotos de reventa en masa → agrupar por producto → campos de Wallapop y Vinted → copiar-pegar.

**FASE 2 CERRADA ✅ (extracción + ficha)** — Haiku 4.5 **medido con la key real** sobre las 33 fotos: **0 alucinaciones**, se abstiene en las 6 trampas ilegibles, **3,4 cts/producto** (0 al reprocesar). `ui/ficha.py` pinta cada campo **junto a su recorte** con badge 📷 leído / 🧠 inferido; Diego confirma → `fuente=diego` persistido. **423 tests verdes**, ruff limpio, la app arranca.

**EL GIRO (Diego, 2026-07-16) — ya ESCRITO en las reglas (`/optimize` 2026-07-17):** el default es `null` → **MEJOR INTENTO**. La extracción **rellena todos los campos** con su mejor estimación, porque Diego revisa cada uno con el píxel delante: un hueco le cuesta teclearlo, un valor mal 2 s. **La procedencia sigue intacta** (`fuente=foto` exige que el valor esté *contenido en el texto legible del recorte citado* — `§17`, no sólo que la cita exista; `alta` solo por checksum de EAN). Vive en `truth-loop.md` §A.2/§A.3. **Excepción**: los campos de juicio imposible (`desperfectos`) NO usan mejor intento — se transcriben de un marcador que Diego escribe, o `null` (`truth-loop.md` §A.5, `decision-making.md` §18; `medidas` se quitó).

**FASE 3 CERRADA ✅ (el EXPORT) — commit `c1d0c73`.** `core/export.py` + `ui/export.py` (pestaña "4. Export"): traduce estado a los literales exactos por plataforma (con guarda de traducción-con-signo, `§16`), bloquea ficha sin confirmar / marca ajena / email / enlace, fotos por plataforma. Extraer TODO el lote de una (14 clics → 2); confirmar todas las fichas de golpe SIN mentir la procedencia; desperfectos desde marcador. El export era el **~66% del tiempo de Diego** (~285 s/producto ESTIMADO por un panel de 4 agentes; nunca cronometrado) y no existía; ahora existe.

**FASE 4 (export + precio) CERRADA ✅** — categoría candidata (Diego elige la hoja, nunca la máquina), envío sugerido sesgado hacia arriba, ubicación silenciada, y **PRECIO v2** (mediana de comparables PARECIDOS leyendo la búsqueda pública de Wallapop por TEXTO, con palabras clave editables, auto-búsqueda al abrir, y `link_button` a la búsqueda). El export se pinta en el **orden del formulario de cada plataforma** (Wallapop ≠ Vinted, verbatim de Diego). Todo verificado con Diego usándolo en vivo.

**FASE 4 (`tipo` + `género`) CERRADA ✅ — commits `86fc757`…`0ecac2c` (2026-07-21).** La síntesis ya produce **qué ES el producto** (`tipo`, texto corto libre, `fuente=inferido`) y su **`género`** (enum cerrado hombre/mujer/niño/niña/unisex, inferido) — 0 llamadas VLM extra, ambos espejo de `categoria`/`estado`. `tipo` alimenta las TRES cosas que faltaban: **el título** (que ahora LIDERA con el tipo, prompt v2 + backstop determinista `_asegurar_titulo_lidera_con_tipo` con dedup por núcleo), **el precio** (`_tipo_para_busqueda` prefiere el `tipo` confirmado sobre el escaneo de `_TIPOS_PRENDA`; guarda `[INC-027]` intacta) y **las candidatas de categoría** (`tipo`+`género` al `texto_busqueda`, medido: género hombre vs mujer da secciones distintas del árbol real). Botón nuevo **"Re-confirmar seleccionados"** para regenerar títulos en bloque sin re-extraer. Todo verificado con Diego usándolo en vivo; **813 tests verdes**. Dos bugs que cazó Diego, cerrados: la síntesis se caía por un **429** (los ~20 crops en ráfaga saturaban el rate-limit → la síntesis, la llamada 21, la más valiosa, agotaba `max_retries=2`) → fix `max_retries=8` en la costura + UI honesta; y la ficha se **vaciaba al cambiar de tab** (Streamlit GC de las keys de widget, marcador de firma sobrevive → siembra saltada) → re-sembrar por key (`[INC-029]`/`[INC-030]`, ambos con el disco SIEMPRE intacto).

**FASE 5 (FINANZAS) CERRADA ✅ — commits `6bb68de`…`27299ca` (2026-07-21/23); gate presencial MEDIDO por Diego el 2026-08-05 (ver abajo).** 5ª pantalla "5. Finanzas": registro de ventas + beneficio + export a `.xlsx`. **`core/store.py` v3** cierra los 3 riesgos de corrupción del seed **estructuralmente** (no con validación posterior): `referencia`/`coste_cents` = **columnas propias** (no dentro del JSON `campos` que `guardar_extraccion` sobreescribe), contador de referencia = **marca de agua persistente** (tabla `referencia_seq` con AUTOINCREMENT, nunca `MAX()+1` — borrar/archivar no reutiliza número; `movimientos` es el log append-only de ventas), y **`ventas` = tabla aparte con snapshot inmutable** que sobrevive al borrado del producto/lote. Migración additiva (v2→v3, sin DROP). UI: **coste + nº de referencia** en Ficha (ref idempotente, sólo si aún es NULL); botón **"Subido" por plataforma** en Export (registra publicación + imprime la ref en la descripción = la llave); marcar **"Vendido"** (precio final auto desde Export + editable + plataforma) → beneficio = venta − coste; dashboard con export Excel. **`borrar_lote`** con guarda de dinero (bloquea si el lote tiene ventas, antes de tocar disco/DB) + borrado transaccional hijos→padres. **904 tests verdes, 1 skipped** (el único skip es el test VLM-real opt-in que gasta API, `§15`; el `test_curar` flaky quedó **resuelto** subiendo el `timeout` de `AppTest`). `[INC-030b]` cerrado: confirmar A reseteaba B sin confirmar (GC de keys a mitad del bucle de rerun) → sombra `_shadow_{key}` que sobrevive al GC; disco SIEMPRE intacto (clase §19).

**LOS GATES REALES: MEDIDOS ✅ (2026-08-05, cierre por auditoría).** La métrica primaria del proyecto (`§19`, "minimizar segundos-hasta-publicar") por fin instrumentada tras 5 fases: **export real de 1 producto ~210 s con la app vs ~285 s a mano → la app SÍ ahorra tiempo** (el número que faltaba desde la Fase 1). **Gate presencial de la Fase 5** también hecho: **venta real end-to-end** (Subido→Vendido→Excel) + prueba anti-corrupción PASADA — editar `coste_cents` tras vender NO altera el beneficio histórico (lee de `coste_snap_cents`, snapshot inmutable). En el Gate A Diego cazó `[INC-031]` (síntesis 400 de esquema en toda re-extracción, `genero` con `type`-array+`enum`; fix `anyOf`) — verde local ≠ funciona: los tests mockean el motor y descartan el `json_schema`. **Cabos abiertos:** **portfolio HTML** (en curso, con la narrativa medida 210 vs 285 s), precio en **Vinted** (Datadome). Descartado y medido, **no reabrir**: búsqueda por imagen/Lens, visión en el agrupado, composición por búsqueda externa.

**FASE 1 CERRADA ✅** — Diego curó y confirmó su primer lote real. `schema`/`images`/`store`/`grouping`/`ui` hechos y auditados: **280 tests verdes**, ruff limpio, la app arranca. `core/grouping.py` es la **v5** ("el reloj puede PARTIR, pero no puede CONFIRMAR"): hueco temporal a **15 s**, cero fusiones sobre las 33 fotos reales del golden set (`tests/golden/truth.json`, 7 productos). La pantalla de curado es **`ui/curar.py`, la CREMALLERA CON PESTILLO** — la unidad es la **frontera**, no la foto, así que meter una foto del producto A en el grupo del B es **inexpresable**, no sólo desaconsejado (elegida por un panel adversarial de 21 agentes tras 4 `listing-audit` BLOQUEANTE contra el diseño anterior, `[INC-008]`).

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
`ListingSchema` ya existe (`core/schema.py`). `LLMEngine` y `PriceEngine` **se construyen en la Fase 2**. Las tres son innegociables:
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
Elección del proveedor de visión: **se decide MIDIENDO sobre las fotos reales, nunca por intuición** — y antes de proponer nada de pago, `decision-making.md` §7 (lleva 3 incidencias). Hardware de Diego: RTX 3050 Laptop **4 GB VRAM** / 16 GB RAM → **visión local descartada** (no entra un VLM que lea logos y etiquetas). La suscripción Pro/Max **no vale** (Anthropic prohíbe enrutar apps de terceros por credenciales de consumidor): hace falta API key de Console.

**Coste — MEDIDO sobre las 33 fotos reales (2026-07-14), no estimado.** El "≈1 ct/producto" que decía aquí antes **era falso**: sale de `LLMEngine.estimar_coste_lote` sobre el pipeline real (`core/extract.py` manda **un recorte por región de texto**, no una llamada por foto):

**Recalculado el 2026-07-16** tras añadir la llamada de **síntesis** (1 por producto: 3 fotos generales + los textos detectados → commitea todos los campos + redacta título/descripción). Delta: **+7 llamadas, +4,4 cts el lote, +0,63 cts/producto** respecto a la tabla anterior (62 llamadas / 19,2 cts / 2,75 cts-producto).

| | llamadas | coste |
|---|---|---|
| Lote entero (33 fotos, 7 productos) | 69 | **23,6 cts** |
| Media por producto | 9,9 | **3,4 cts** |
| Peor caso (producto 1, caja con texto denso) | 22 | **7,1 cts** |
| Ropa (productos 3-7) | 4-11 | 1,6-3,7 cts |

**Medido de verdad con la API (no estimado):** el lote de recortes costó **14,5 cts / 62 llamadas** con `0` alucinaciones; el `lufthous` completo con síntesis, **0,95 cts** (y **0** al repetirlo: caché).

Con **caché por hash de imagen** se paga **una sola vez**: reprocesar el mismo lote cuesta **0 €**. La caché **nunca se borra sin permiso** — cada entrada es dinero ya gastado.

**Toda cifra de coste de este fichero sale de `estimar_coste_lote`, no de una estimación a ojo.** Si el pipeline cambia cuántas llamadas hace por producto, esta tabla se recalcula y se dice — un cambio de prompt que duplica el coste es un cambio de arquitectura disfrazado (`change-loop.md` §C5).

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
