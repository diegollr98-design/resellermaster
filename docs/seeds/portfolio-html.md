# Portfolio HTML — RESELLERMASTER · SEED (sesión FRESCA)

> PASO 0 OBLIGATORIO: invoca `/seed-review` sobre este SEED antes de tocar nada (regla global de Diego). Muestra el veredicto y procede según él.

> **Objetivo:** un **portfolio HTML autocontenido** que presente RESELLERMASTER al **mismo nivel de acabado** que los dos de referencia. **Objetivo real (upstream): impresionar a quien contrata y conseguir que contraten a Diego** — mostrando que coge un problema difuso, lo descompone y orquesta IA para construir cada pieza SIN perder el rigor. El repo ya está CLEAN y verificado (cierre por auditoría hecho, ver `sessions-log.md` v0.12).
>
> **Regla dura, y es el corazón de este repo:** el proyecto existe para que el pipeline **no mienta sobre el producto**. Un portfolio sobre anti-alucinación que **invente una cifra** se autodestruye. **Cada número del portfolio se verifica contra el repo ANTES de escribirlo** (git log, CLAUDE.md, el ledger, la suite). Si un dato no está medido, no se pone o se marca como estimado. Esto no es opcional: es la tesis del proyecto aplicada a su propia vitrina.

## Los dos HTML de referencia (LÉELOS ENTEROS antes de empezar)
Viven en la raíz del repo (Diego los dejó ahí como ejemplo de OTROS dos proyectos suyos):
- `d:\Resellermaster\portfolio-fable-ultra.html` (pumpfun-bot / quant research) — **la referencia primaria de estilo.**
- `d:\Resellermaster\portfolio-sekura.html` (seguridad privada) — segunda referencia, mismo chasis, otro dominio.

**Adapta el CHASIS al repo, no el repo al chasis.** El sistema de diseño (CSS + JS vanilla) es reutilizable casi verbatim; lo que cambia es la NARRATIVA. Copia el chasis, sustituye el contenido. Concretamente reutiliza: paleta dark premium + neón, tipografías (Space Grotesk / Inter / JetBrains Mono), aurora+grano, scroll-progress, nav sticky, toggle de idioma ES/EN (i18n por `t-es`/`t-en`, sin FOUC), ticker/marquee, sección de vídeo con placeholder, el "cockpit" orbital animado, los callouts de "verdad", `prefers-reduced-motion`, cero dependencias externas. **Un solo fichero, todo inline** (es un requisito de la herramienta Artifact: CSP estricta, sin CDN/fuentes remotas — inlina las fuentes o usa un stack de sistema equivalente si el peso base64 es prohibitivo; decídelo al construir).

## Herramienta y forma de entrega
- Publícalo con **Artifact** (autocontenido, favicon emoji — sugerencia 📸 o 🏷️, `<title>` estilo "Diego · RESELLERMASTER · …", theme-aware pero el diseño de referencia es dark-first, así que puede comprometerse a dark).
- **Antes de escribir la página, carga el skill `artifact-design`** (lo exige la herramienta) para calibrar. Y `dataviz` si metes cualquier gráfico.
- Escribe el HTML a un fichero (p.ej. `docs/portfolio/index.html` o el scratchpad) y publícalo con Artifact. El body va directo (sin `<html>/<head>/<body>` propios: los envuelve Artifact) — OJO: los de referencia SON documentos completos; al portarlos a Artifact hay que mover el `<style>` y el `<script>` al cuerpo y quitar el andamiaje `<!DOCTYPE>/<head>`. Alternativa si Diego prefiere un `.html` suelto como los de referencia (para subirlo a su propio hosting): entrégalo como fichero completo. **Pregúntale cuál quiere** si no está claro.

## EL VÍDEO (Diego lo grabará — deja el hueco preparado)
Habrá que grabar un vídeo mostrando el **dashboard/app en funcionamiento**. La sección "Recorrido/Walkthrough" de la referencia (`#recorrido`) es exactamente eso: un slot de vídeo 16:9 con placeholder (play + poster) y una nota. **Replícalo**: deja el `<video>`/iframe vacío con un placeholder elegante y una nota tipo "demo en vivo del pipeline". El guion que el vídeo debe mostrar (déjalo escrito como caption/nota para Diego): **Ingesta de un lote de fotos → curar la agrupación (la cremallera con pestillo) → ficha con cada campo junto a su recorte y sus badges 📷/🧠 → export copy-paste por plataforma → «5. Finanzas» (Subido→Vendido→beneficio→Excel).** El export cronometrado (~210 s) es el clímax del vídeo.

## Arco de secciones (adaptado a ESTE repo)
Espeja la estructura de las referencias (hero → recorrido/vídeo → cockpit → flagship → decisiones medidas → 2 historias de juicio → método → mapa → contacto), con este contenido:

1. **Hero.** h1 con palabra-acento en `grad-text`. Tesis: *fotos de reventa en masa → fichas listas para copiar-pegar en Wallapop/Vinted, sin inventar nada del producto. Solo, con IA.* Fila de **stats REALES** (ver §Números). Ticker con hitos.
2. **Recorrido (VÍDEO).** El slot de demo del dashboard (arriba).
3. **Cockpit / el loop.** El flywheel de **5 etapas**: `ingerir → agrupar → extraer → tasar → exportar` (arquitectura real, `rules/architecture.md`). Orbital animado como en la referencia. Callout de "verdad": **el Loop de Verdad** (la máquina PROPONE y enseña el píxel; Diego CONFIRMA; el fallo caro se vuelve inexpresable).
4. **Flagship — el Loop de Verdad (anti-alucinación), el corazón.** Procedencia obligatoria por campo (`foto`/`inferido`/`diego` + evidencia + confianza), badges 📷 leído / 🧠 inferido, `fuente=foto` SÓLO si el valor está en el píxel citado (garantizado por un `if`, no por el prompt). El precio NUNCA sale del LLM: mediana de comparables reales con URL, o `None`+motivo. Las 3 costuras (`ExtractorEngine`/`PriceEngine`/`ListingSchema`).
5. **Decisiones MEDIDAS, no intuidas (equivalente al "cementerio").** Lo que se descartó CON DATO: Google Lens / búsqueda por imagen (todas exigen URL pública; una sudadera usada no tiene identidad única — 4 agentes lo confirmaron); CLIP como clasificador (0.90 de similitud entre dos sudaderas distintas); OCR como clasificador de tipo de foto (los caracteres se solapan por completo, medido); VLM local (RTX 3050, 4 GB VRAM → no entra, medido, no intuido). Y el umbral de agrupación (15 s, zona segura 1-23 s medida sobre las 33 fotos reales de Diego).
6. **Juicio 1 — un bug que cacé en mi propio sistema.** Historia fuerte y RECIENTE (del cierre): **la síntesis fallaba con un 400 de esquema en toda re-extracción y 904 tests verdes no lo veían** (`[INC-031]`) — porque los tests mockean el motor y nunca mandan el `json_schema` a la API real; lo cazó Diego USANDO la app. Moraleja: *verde local ≠ funciona; el bug vive donde el test no paga el camino real.* (Alternativa igual de buena: `[INC-005]`, la confianza anti-correlacionada con el riesgo — "el reloj puede PARTIR pero no CONFIRMAR". Elige una; no metas las dos aquí.)
7. **Juicio 2 — cuando me cuestioné, revisé mi propio plan.** Meta-historia del cierre: el `/seed-review` con **agente ciego** cazó que mi propio SEED de cierre cometía un PROXY (auditar código con 900 tests verdes mientras la métrica primaria del proyecto —segundos-hasta-publicar— NUNCA se había medido en 5 fases, `[INC-016]`). Reordené: **medir primero**. Resultado: **~210 s con la app vs ~285 s a mano**, el número que corona el proyecto. *El rigor es cuestionar tu propio plan, no defenderlo.*
8. **Método — validación adversarial multi-agente.** El modelo de orquestación real: **Opus orquestador** ("papá oso", gasta poco contexto, valida lo crítico por EJECUCIÓN) + **Sonnet en paralelo** (implementación) + subagentes **`bug-hunter`** (causa raíz sin contexto), **`listing-audit`** (intenta pillar al pipeline mintiendo contra las fotos reales), **`flow-qa`**, y **`/seed-review`** (des-ancla el plan con un agente ciego). El propio cierre de esta semana: seed-review → gates medidos → 3 bug-hunters contra la DB real → fixes con test red→green. La IA construye; un panel escéptico ataca; Diego juzga.
9. **Mapa — un sistema, no piezas sueltas.** Las 3 costuras + los 5 loops (Verdad, Cambios, meta-loop `/optimize`) + el ledger de 32 incidentes como memoria que se convierte en reglas.
10. **Contacto — CTA.** "Disponible para trabajar", mismo tono que las referencias.

## Números REALES (con su fuente — verifícalos antes de usarlos; NO inventes)
- **~210 s export con la app vs ~285 s baseline a mano** (medido este cierre, Gate A; el baseline ~285 s lo estimó un panel de 4 agentes — dilo así, no como medición). Fuente: `sessions-log.md` v0.12, memoria `gate-real-medido`.
- **0 alucinaciones de Haiku 4.5 sobre las 33 fotos reales**; se abstiene en las 6 trampas ilegibles. Fuente: CLAUDE.md (Fase 2), `truth-loop.md`.
- **3,4 cts/producto** de coste de extracción (medido con la API real; 0 al reprocesar por caché). Fuente: CLAUDE.md tabla de coste.
- **904 tests verdes, 1 skipped** (el skip es un test VLM-real opt-in, no un fallo). Fuente: la suite (re-córrela para confirmar el número exacto antes de publicarlo).
- **5 fases** cerradas (agrupación → extracción+ficha → export → precio+categoría+tipo/género → finanzas).
- **32 incidentes** en el ledger (aprendizaje sistemático que `/optimize` promueve a reglas). Fuente: `incident-ledger.md`.
- **La cremallera con pestillo** elegida por un **panel adversarial de 21 agentes** tras **4 veredictos BLOQUEANTE** contra el diseño anterior. Fuente: CLAUDE.md (Fase 1), `[INC-008]`.
- Hardware: **RTX 3050 Laptop, 4 GB VRAM** → visión local descartada MIDIENDO, no por intuición.

## Caveats de HONESTIDAD (obligatorios — el portfolio los declara, no los esconde)
El acabado impresiona; las referencias también son honestas sobre sus límites (SEKURA dice "es un MVP, Stripe test, licencia en trámite"). Espeja ese tono. Declara:
- Es una **app LOCAL de un solo usuario** (Diego), sin cloud/multiusuario/auth. No es un SaaS. Es una decisión de diseño, no una carencia.
- **No automatiza la publicación** en Wallapop/Vinted (va contra sus términos, arriesga el baneo de la cuenta que ES el negocio) — llega hasta "copiar y pegar en 2 clics". A propósito.
- El precio es **lo que otros PIDEN, no el precio de venta** (los precios de venta no son públicos en esas plataformas) — límite honesto que la propia app declara en su UI.
- El baseline de ~285 s es una **estimación de un panel**, no un cronometraje; el ~210 s SÍ es medido. No los presentes al mismo nivel de certeza.

## Gate de cierre (§Cierre de `/seed-review`)
Antes de dar el portfolio por terminado: careo de fidelidad — **cada cifra publicada existe en el repo** (haz el grep/lectura, no confíes en este seed, que pudo copiar mal un número); el vídeo tiene su hueco; los caveats de honestidad están; y el resultado se ve bien en móvil (el body no hace scroll horizontal). Enséñale el Artifact a Diego y PARA hasta su OK (es external-facing: su cara ante quien contrata).

## NO hacer
- No inventar métricas, logos de clientes, testimonios, ni "usado por X empresas" (es un proyecto personal). 
- No prometer lo que la app no hace (publicación automática, precio de venta real, multiusuario).
- No romper la CSP de Artifact (nada de CDNs, fuentes remotas, fetch externo). Todo inline.
- No copiar el TEXTO de pumpfun-bot/SEKURA: el chasis sí, la narrativa es de RESELLERMASTER.
