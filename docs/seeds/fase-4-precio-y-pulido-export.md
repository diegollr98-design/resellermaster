# Fase 4 — Pulir el EXPORT (quitar la fricción evitable) + PRECIO por comparables

> Arranca aquí. **Reconcilia el estado contra el repo antes de tocar nada** (`change-loop.md` §D): `git log`, el código y los tests. Este seed destila la sesión del 2026-07-17, pero los seeds mienten y el código no.

## Prompt de arranque
```
Eres el orquestador ("papá oso") de RESELLERMASTER. Lee CLAUDE.md, .claude/rules/
(decision-making, truth-loop, change-loop, architecture, product, file-organization),
.claude/incident-ledger.md y docs/seeds/fase-4-precio-y-pulido-export.md.
ATENCION: la seccion "DECISIONES DE DIEGO QUE CONTRADICEN LAS REGLAS ESCRITAS" de
docs/seeds/fase-3-export.md sigue vigente — las reglas del repo estan desactualizadas
respecto a decisiones del 2026-07-16/17 y si no lo sabes vas a "corregir" cosas que
estan bien a proposito. CORRE /optimize PRONTO: hay ~18 incidentes sin promover.
Dos frentes: (1) quitar la friccion EVITABLE del export; (2) el PRECIO por comparables.
```

---

## Estado real al cerrar la sesión del 2026-07-17 (último commit `c1d0c73`)

**El flujo de la ficha va RÁPIDO y MEDIDO:** Diego revisó y confirmó su lote de 7 productos en **~1 minuto** (antes: ~90 s/producto solo de agrupar+revisar). La app de ficha ya cumple su objetivo. Lo construido y verde esta sesión:
- **Extraer TODO el lote de una** (14 clics → 2). `estado` y `categoria` salen de un ENUM cerrado (no prosa), `inferido`+`baja`, Diego confirma.
- **Confirmar todas las fichas de golpe**, SIN mentir la procedencia: un campo que Diego no tocó mantiene su `fuente` original (`foto`/`inferido`), nunca pasa a `diego`. El export avisa de los `inferido`.
- **Re-extraer productos SELECCIONADOS** (multiselect + gate que exige casilla si hay fichas ya confirmadas — no perder curado).
- **Ficha compacta**: recortes en miniatura + popover; candidatas plegadas; obligatorios con badge naranja/rojo; título/descripción de sólo lectura antes de confirmar (se generan AL confirmar, desde los campos confirmados, con una llamada de texto SIN fotos → 0,12 cts/producto).
- **Desperfectos desde el PAPEL** (`DESPERFECTOS: cremallera rota`): marcador determinista, no un juicio del modelo. **Medidas SÓLO del metro** (el marcador `MEDIDAS` se quitó: `MEDIDA:` singular colisiona con packaging — `[INC-025]`).
- **Estado del export** (Fase 3, commit `c1d0c73` y anteriores): `core/export.py` + `ui/export.py` (pestaña "4. Export") construidos y auditados. Traduce estado a los literales exactos por plataforma; bloquea ficha sin confirmar / marca ajena / email / enlace; fotos por plataforma con `images.exportar_producto`.

**Baseline de tests:** ~646 passed (suite sin golden), ruff limpio, golden set 8 passed sobre las 33 fotos reales. **Reconfírmalo con `git log`/`pytest`, no te fíes de este número.**

---

## FRENTE 1 — Reducir la fricción del export (AUDITADO CON FUENTES PRIMARIAS, 2026-07-17)

Diego llegó al Export, vio la pila de avisos y **desconfió de la primera clasificación del orquestador** ("¿seguro que los que dices que NO no se pueden quitar?"). Pidió un análisis 100% objetivo sin el sesgo del orquestador. Se lanzaron **3 agentes independientes** (2 ciegos a la conclusión + 1 auditor de sesgo), con WebSearch/WebFetch contra fuentes primarias. **Diego tenía razón: el orquestador falló o fue parcial en 5 de los 7.**

**El patrón del sesgo (confirmado):** trató `[NO VERIFICADO]` y "requiere token" como **"imposible"**, cuando ambos datos son **públicos y obtenibles** — y la app YA tiene permiso para leer dato público (`architecture.md`). La prueba objetiva de que fue inercia, no análisis: clasificó `package_size` (necesario) y `tramo_peso` (evitable) en **direcciones opuestas siendo el mismo tipo de campo** (un enum de envío que se elige por producto). Y los DOS de más peso temporal (categoría, precio) estaban mal etiquetados como "necesarios".

### Veredicto objetivo por aviso (ordenado por segundos-de-Diego en juego)

| # | Aviso | Dijo el orq. | Veredicto auditado | Evidencia (fuente primaria) |
|---|---|---|---|---|
| 4 | **categoría / catalog_id** | necesario | **REDUCIBLE** — no eliminable, pero de "busca en 349 hojas a mano" → "elige entre 2-3 candidatas" | El árbol de AMBAS es PÚBLICO: `GET api.wallapop.com/api/v3/categories` (200, sin auth; 859 hojas, `leafMandatory` en Moda/Tec/Hogar/Cine ✓). Vinted: `www.vinted.es/` SSR embebe `catalogTree` JSON (200, cookie anónima; 2482 hojas). **El `[NO VERIFICADO]` que citó el orq. NO EXISTE para categoría** — `product.md` ni la lista entre los huecos. Y "Vinted requiere token" es FALSO para el árbol (solo la API Pro `/api/v1/ontologies` exige token; el SSR no). |
| 7 | **precio** | necesario | **EVITABLE-DIFERIDO** (mal etiquetado) | No es imposible, es la Fase 4 (Frente 2 abajo). Meterlo en "necesarios/no se puede" ocultó el 2º mayor ahorro tras una etiqueta de imposibilidad. Etiqueta correcta: "evitable, fuera del alcance de la sesión del 17". |
| 5 | **package_size_id (Vinted)** | necesario | **REDUCIBLE** (mismo sesgo que #4) | El `id` numérico SÍ requiere token (el orq. acertó AHÍ) — pero **la app no publica por API, copia-pega en el formulario**, donde el desplegable usa etiquetas PÚBLICAS (`vinted.com/help/51`: Pequeño/Mediano/Grande/pesado, ~4). Inferible del tipo de prenda con default seguro. Baja de "campo bloqueado" a "1 tap sobre 4 opciones". |
| 2 | tramo_peso_kg (Wallapop) | evitable | **EVITABLE — pero vía CHIP, no auto** | Acertó la dirección. PERO "pre-rellena por categoría" es FALSO: el peso lo fija el producto físico, no la categoría (una "moda" es camiseta 2kg o abrigo 5kg). Tachar esa vía; el chip de 5 (1 tap fiable) sí. |
| 6 | filtro marca ajena (aviso) | necesario | **NECESARIO como defensa, FRECUENCIA reducible** | Correcto que es best-effort y el silencio no es garantía (no gatear sobre "no encontró nada" — es circular). PERO un aviso que sale SIEMPRE (incondicional, `export.py:465`) choca con su propia `decision-making.md` §12 ("defensa always-on = se muere en semanas"). Reducible: dispararlo fuerte solo si hay un token-mayúscula-sospechoso en el texto, tenue si no. |
| 1 | ubicacion (Wallapop) | evitable | **EVITABLE** ✓ | Config-una-vez, correcto. (Verificar con Diego si Wallapop ya la autocompleta de su cuenta y ni hace falta). |
| 3 | texto "N campos sin revisar" | evitable/cosmético | **EVITABLE/cosmético** ✓ | Cambiar el texto, correcto. **CUIDADO:** es el rastro de procedencia que `truth-loop.md` §A.2 exige que se VEA — cambiar el texto sí, diluirlo hasta que no se note NO (rompe §A.2). |

### El límite honesto que los agentes SÍ confirman (no sobre-corregir)
**"Reducible" ≠ "eliminable".** Para categoría: auto-elegir LA hoja exacta NO es fiable (349-2482 hojas; las distinciones que separan hojas hermanas —capucha vs cremallera, casual vs deportiva, hombre vs mujer en unisex— son justo las que un modelo confunde, y **una hoja mal = anuncio OCULTO = venta perdida silenciosa**, el modo de fallo caro del `truth-loop`). El pick final de la hoja **sigue siendo del ojo de Diego** — auto-commitearla sería la enésima "confianza anti-correlacionada con el riesgo". Lo reducible es el TRABAJO (de navegar el árbol → confirmar 2-3 candidatas), no la decisión. Es el patrón de siempre: la máquina propone y enseña, Diego cierra.

### Plan del Frente 1 (por orden de ahorro; superficie sensible → `listing-audit`)
1. **Categoría (mayor ahorro):** descargar y versionar los dos árboles como tablas (Wallapop 1 GET; Vinted parsear `catalogTree` del SSR — job de refresco MANUAL, no en caliente: el SSR de Vinted está tras Datadome, a 1 fetch ocasional es despreciable). Añadir un campo estructurado **`tipo_prenda`** (+ género) a la extracción — hoy NO existe (`extract.py` produce categoria/marca/modelo/ean/talla/color/estado/titulo/descripcion/desperfectos, sin tipo/género); es barato (lista corta o keyword-match sobre el título). Tabla `(amplia + tipo + género) → [hojas candidatas]`. En `ui/export.py`, mostrar 2-3 rutas-hoja para elegir con 1 clic. **NUNCA** auto-rellenar `catalog_id`/`categoria` con una hoja única.
2. **package_size (Vinted) + tramo_peso (Wallapop):** son el MISMO patrón (enum de envío corto, elegido por producto). Un `CampoExportado` con valor SUGERIDO (badge 🧠 inferido, `confianza=baja`) que Diego confirma con 1 tap. **Sesgar la sugerencia hacia arriba** (sub-dimensionar cuesta un recargo de envío al vendedor; sobre-dimensionar solo encarece un poco al comprador → mismo principio que "sobre-cortar" del agrupado).
3. **ubicacion:** config una vez. El aviso desaparece.
4. **Aviso "N campos" y filtro de marca:** reescribir el texto del primero (sin diluir el rastro §A.2); condicionar la frecuencia del segundo sin usar `_MARCAS_COMUNES_HEURISTICA` (evitar el gate circular).

### Correcciones FACTUALES a las reglas (hacer al pasar — `product.md` tiene datos falsos)
- **`product.md` (sección VINTED / HUECOS):** el ÁRBOL de categorías de Vinted **SÍ es obtenible** del SSR de `www.vinted.es` sin token; **solo la API Pro** (`/api/v1/ontologies`) exige token. Corregir la afirmación de que "requiere token de la API de ontologías" — es cierta solo para el `size_group`/`package_size` *numérico*, no para el árbol ni las etiquetas.
- **`product.md`:** el árbol de Wallapop es un endpoint público estable (`api.wallapop.com/api/v3/categories`), descargable y versionable.
- Ojo: las ontologías de Vinted **varían por país+divisa y están versionadas por fecha** → el árbol cambia; el job de descarga es periódico, no "congelar para siempre".

**Regla dura corregida del Frente 1:** un aviso se reduce si el dato es **obtenible sin inventarlo** — y "obtenible" incluye **leer un endpoint/SSR público** (permitido por `architecture.md`), no solo "lo tenemos ya en el repo". `[NO VERIFICADO]` significa "nadie lo ha mirado AÚN", no "es imposible". El único aviso genuinamente irreducible en su decisión (no en su frecuencia) es el de marca best-effort, y aun ése baja de frecuencia.

---

## FRENTE 2 — El PRECIO por comparables (Fase 4)

`core/pricing.py` ya existe (v1, 0 €): construye la consulta y las URLs de búsqueda; `precio=None` es inexpresable de violar. Falta la v2.

### Lo decidido (2026-07-16, ya escrito en `truth-loop.md` §D.2 y `architecture.md` — NO reabrir):
- **Búsqueda por TEXTO (marca+tipo+talla), NUNCA por EAN** — Diego verificó que nadie pone el EAN en los anuncios.
- **DOS niveles de comparable, la ficha SIEMPRE dice cuál:** "el mismo producto" (exige EAN/modelo legible o match visual) vs **"parecidos"** (cohorte, para ropa genérica usada que NO tiene identidad única en internet). Se entrega **"mediana de N parecidos" + rango + las URLs**, nunca "el precio de tu producto".
- Sin `n≥5` → `precio=None` + motivo. **Nunca un número sin fuente.**
- **Google Lens / búsqueda por imagen: DESCARTADA, no reabrir** (`architecture.md`: todas las APIs exigen URL pública, la app es local; y la ropa genérica no tiene identidad única). CLIP tampoco (`[INC-004]`).

### LA DECISIÓN QUE FALTA (bloquea el Frente 2 — resuélvela con Diego ANTES de construir):
**Leer la búsqueda pública de Wallapop/Vinted** para sacar la mediana. `architecture.md` (actualizado 2026-07-16) ya distingue **LEER ≠ PUBLICAR** y lo permite con condiciones: volumen doméstico (~7 productos/lote, pocos lotes/mes = menos peticiones que un humano navegando → riesgo de baneo despreciable, lo juzgó Diego), **PROHIBIDAS las herramientas stealth/anti-detección**, y NADA de escribir (leer resultados públicos sí; tocar una cuenta jamás). **PERO** `truth-loop.md` §D.2 sigue marcado como "sin resolver — decisión de Diego" respecto al match visual, y el conjunto inmutable de `CLAUDE.md` ("no automatizar la publicación") hay que releerlo con Diego para confirmar que la lectura de búsqueda pública no lo viola. **Corre el Loop de Cambios y que Diego confirme el approach de scraping ANTES de escribir una línea.** Ahorro estimado: ~20 s/producto (`fase-3-export.md`).

---

## ORDEN RECOMENDADO
1. **`/optimize` PRIMERO** (ver abajo — urgente y ya se arrastra).
2. **Frente 1** (barato, acotado, ahorro inmediato y en cada producto): ubicacion + tramo_peso + reescribir el aviso de procedencia. Pasa por `listing-audit`.
3. **EL GATE REAL, que sigue pendiente desde la Fase 3:** Diego **exporta un producto de verdad, lo pega en Wallapop, y CRONOMETRA.** Baseline a batir: ~285 s/producto de export. **Sin este número, no sabemos si algo de esto ahorra tiempo.** Hazlo antes de meterte en el Frente 2 — puede que el precio no sea el siguiente cuello.
4. **Frente 2** (precio) — sólo tras resolver la decisión de scraping con Diego.

---

## `/optimize` — URGENTE, ya se arrastra
El ledger tiene **~18 incidentes sin promover** (`[INC-013]` … `[INC-025]`), y las reglas escritas **contradicen** decisiones vigentes de Diego (el giro null→mejor-intento de §A.2/§A.3, la excepción de `desperfectos`/`medidas`, la lectura de búsqueda pública). **Mientras no se escriban, cada `listing-audit` futuro peleará contra el diseño vigente** — ya pasó esta sesión (un audit citó reglas viejas). Reglas candidatas destacadas de esta sesión:
- `[INC-017]` traducción con pérdida tiene SIGNO: presentar el producto mejor de lo que es es mentira, peor es seguro; el badge "ya traducido" nunca sobre un mapeo que sube de nivel.
- `[INC-019]`/`[INC-024]` una afirmación de "esto es imposible/seguro" en un comentario o commit **se ejecuta antes de escribirla**; y un umbral fuzzy se verifica contra el vocabulario real que puede colisionar, no contra la longitud de una palabra.
- `[INC-021]` antes de juzgar la CALIDAD de un output, verifica que el usuario lo ha EJECUTADO sobre su caso real; un flujo que cuesta N clics arrancar no se ha probado hasta que alguien pagó esos N clics (los tests arrancan de un fixture ya poblado, nunca los pagan).
- `[INC-023]`/`[INC-025]` un campo que exige un JUICIO que el modelo no puede dar se convierte en TRANSCRIPCIÓN de una señal que el humano controla, o se deja vacío; y antes del 3er parche sobre el mismo campo, la pregunta es "¿vale la pena esta FUENTE?", no "¿cómo arreglo el match?".

---

## NO REABRIR (medido y cerrado esta sesión)
- **Medidas desde texto/papel: NO** (`[INC-025]`, `MEDIDA:` colisiona estructuralmente; ropa no pide medidas). Sólo del metro.
- **Proveedor de visión ≠ Haiku: medido, NO cambiar por ahorro** — gasto TOTAL de Diego desde el día 1 = **$0,057** (5,7 cts). Qwen3-Max es MÁS caro que Haiku ($1,20/$6 vs $1/$5). Cambiar arriesga las 0 alucinaciones medidas sobre las 33 fotos por ahorrar ~7 €/año. Sólo re-evaluar si el gasto sube un orden de magnitud.
- **Título/descripción como prosa libre del LLM: NO** — se generan desde los campos confirmados, con una llamada de texto SIN fotos (garantía estructural anti-marca-ajena). Ya está.
