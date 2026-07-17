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

## FRENTE 1 — Quitar la fricción EVITABLE del export

Diego llegó al Export y vio la pila de avisos. Su instinto es correcto: **los avisos SON el tiempo del export** (cada uno es algo que rellena a mano = el ~66% que mide `[INC-016]`). Clasificados contra el código (`core/export.py::_avisos_obligatorios_sin_cubrir` línea ~333, `_avisos_procedencia_no_revisada` línea ~367):

### EVITABLES — se pueden quitar SIN inventar nada (esto es el Frente 1):
1. **`ubicacion` (Wallapop)** — es SIEMPRE la de Diego. Config una vez (en `store` o un `.env`/settings), se rellena sola. **CAVEAT a verificar con Diego:** puede que Wallapop ya la autocomplete de su cuenta y ni haga falta tocarla → preguntar antes de construir.
2. **`tramo_peso_kg` (Wallapop)** — enum CERRADO verificado `(2,5,10,20,30)` (`schema.WALLAPOP_TRAMOS_PESO_KG`). Diego lo elige con un chip rápido, o se pre-rellena por categoría (ropa→2, caja→según). Es una elección de 1 clic, no teclear.
3. **El aviso "N campos se publican sin que los hayas revisado tú"** (`_avisos_procedencia_no_revisada`) — es medio ENGAÑOSO y lo escribió el orquestador. Dice "no los has revisado" cuando Diego SÍ los miró (1 min haciendo los 7), sólo que no los *tecleó*. Confirmar-en-bloque marca `fuente="inferido"` a todo lo no-tecleado, sin distinguir "mirado y aceptado" de "no mirado". **NO lo borres** (el rastro de procedencia es lo que sostiene `truth-loop.md` §A.2 — ver el comentario en `export.py:353`): **cambia el TEXTO** para que no acuse. Algo como *"N campos van con el valor que propuso el modelo (no tecleado por ti); si los revisaste en la ficha, adelante"*. Y considera: ¿se puede reducir a sólo los campos que de verdad importan (estado, marca, talla), no los 10?

### NECESARIOS — NO se pueden quitar (quitarlos = inventar = el fallo que la app evita):
- **`categoria`/`catalog_id` (la hoja real del árbol)** — Wallapop y Vinted exigen una hoja de profundidad 3-5; el pipeline sólo produce la categoría interna amplia (`moda`/`electronica`/...). El árbol real está `[NO VERIFICADO]` en `product.md`. El buscador de la propia plataforma se la da a Diego en 1 clic. **Inventar la hoja = un anuncio que no publica.** Déjalo como aviso; si acaso, mejora el aviso para que sugiera el término de búsqueda.
- **`package_size_id` (Vinted)** — su enum real requiere un token de la API de ontologías de Vinted que no tenemos (`product.md` HUECOS). Mismo motivo.
- **filtro de marca ajena best-effort** — defensa honesta, aviso permanente por diseño (`_MARCAS_COMUNES_HEURISTICA` cubre ~31 marcas; el auditor midió que 4/5 marcas del golden set NO lo disparan — `[INC-017]`). NO lo vendas como garantía.

**Regla dura del Frente 1** (`change-loop.md`, `truth-loop.md`): un aviso sólo se quita si el dato se puede **rellenar sin inventarlo**. Config-de-Diego (ubicacion) y enum-cerrado-verificado (tramo_peso) sí. Un enum `[NO VERIFICADO]` (categoria hoja, package_size), NO. Superficie sensible (es lo que se publica) → pasa por `listing-audit`.

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
