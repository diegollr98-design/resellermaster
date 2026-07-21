# Loop de Verdad — RESELLERMASTER

> **Tesis central:** el modo de fallo de este proyecto **no es que el código pete**. Es que el pipeline **afirme con total fluidez algo falso sobre el producto** — "Nike, talla M, algodón, unos 25€" cuando es Adidas, talla S, poliéster y se vende a 12€ — y Diego lo pegue en Wallapop sin mirar, porque para eso construimos la app: para no mirar.
>
> **La app está diseñada para que Diego confíe y copie rápido. Esa confianza es exactamente lo que convierte una alucinación en una venta perdida.** La velocidad es el producto; la verdad es la condición para que la velocidad no sea un pasivo.
>
> **Y su límite, que es lo que hace honesto a este loop:** ningún agente puede *verificar* que una prenda es de algodón. Lo único verificable por ejecución es (1) que el dato es **legible en un píxel concreto de una foto concreta**, y (2) que el precio procede de **comparables reales que existen y son del mismo producto**. Todo lo demás es una inferencia, y las inferencias se **etiquetan como tales** — no se publican como hechos.

---

## §A — La regla madre: procedencia obligatoria

**Ningún campo de una ficha existe sin decir de dónde salió.** Todo atributo que produce el pipeline lleva, en la estructura de datos y hasta la UI:

| Campo | Significado |
|---|---|
| `valor` | el dato (o `null`) |
| `fuente` | `foto` \| `diego` \| `comparable` \| `inferido` |
| `evidencia` | qué foto y qué región lo prueba (`IMG_0421.jpg`, crop de la etiqueta) — **obligatorio si `fuente=foto`** |
| `confianza` | `alta` \| `media` \| `baja` |

**Reglas duras:**
1. **`fuente=foto` sin `evidencia` es un bug, no un dato.** Si el modelo dice "talla M" pero no puede señalar dónde lo ve → el campo es `inferido`, no `foto`.
2. **El default es el MEJOR INTENTO, no `null`.** *(INVERTIDO el 2026-07-16 por decisión de Diego. Antes decía: "un campo null es un éxito; ante la duda, null".)* La extracción **rellena todos los campos** con su mejor estimación aunque no esté segura, **porque Diego revisa cada campo con su recorte delante antes de publicar**. En SU flujo la asimetría es **la contraria** a la que esta regla asumía: un campo vacío le cuesta **teclearlo entero**; un valor mal, **2 segundos** corregirlo. La regla vieja optimizaba para un Diego que NO mira; el real mira.

   **Por qué esto NO es licencia para mentir — y por qué el conjunto inmutable sigue intacto.** `CLAUDE.md` prohíbe *"afirmar un atributo que no sea legible en una foto"*, y sigue vigente **porque la app no AFIRMA: PROPONE**. Todo valor propuesto llega con las tres cosas a la vez:
   - **procedencia honesta** — `fuente="foto"` SÓLO si el valor está *contenido en el texto legible del recorte citado* (garantizado por un `if`, no por el prompt: `core/extract.py::_construir_campo_desde_sintesis`); si no, `inferido` + `confianza="baja"`;
   - **el píxel a la vista** — el recorte, junto al campo, en `ui/ficha.py`;
   - **un badge visible** — 📷 leído en foto vs 🧠 inferido — verifícalo.

   **Quien AFIRMA es Diego**, al confirmar (`fuente="diego"`). **Esta regla depende de eso:** si algún día se publica un campo sin que él lo haya visto (un "confirmar todo" a ciegas, un export automático), la premisa se cae y hay que volver a `null`. La defensa no es el default: **es su ojo con el píxel delante.**

3. **`inferido` SÍ puede pre-rellenar un campo estructurado, pero JAMÁS sin marcar.** *(Relajado el 2026-07-16. Antes: "nunca se pega tal cual en un campo estructurado".)* Un valor inferido se pre-rellena con badge 🧠 y `confianza="baja"` para que Diego lo corrija o lo confirme de un vistazo. Lo que sigue **PROHIBIDO**: que un `inferido` salga **sin badge**, con **confianza alta** (imposible por construcción: el `json_schema` de la síntesis sólo admite `media`/`baja`), o **sin que Diego lo vea** antes de publicar.
4. Los campos de **estado** (nuevo / muy bueno / bueno / aceptable) son SIEMPRE `inferido` y **siempre los confirma Diego**. Es el campo que más devoluciones causa y ningún modelo lo puede afirmar por una foto.
5. **Excepción MEDIDA al MEJOR INTENTO — campos de juicio imposible** `[INC-023][INC-025]`. Un campo que exige un juicio que el modelo no puede dar (*¿es esto un defecto? ¿es una dimensión del producto o del embalaje?*) **NO usa mejor intento**: el mejor intento produce ruido plausible (el nombre impreso del masajeador publicado como desperfecto; `medidas="SYSTEM"` desde `MEDICAL` en un logo). Se **transcribe una señal que Diego controla** — un marcador que él escribe en su nota (`DESPERFECTOS: cremallera rota`) → `if` determinista, `fuente="foto"` sólo si el valor está en el píxel citado, sin marcador → `null` (cae del lado barato: lo teclea). `medidas` se **quitó entero** del marcador: colisiona estructuralmente con packaging (palabra corta, sin señal que distinga caja de producto) y la ropa —el grueso del catálogo— ni las pide. Ver `decision-making.md` §18.

## §B — Superficies sensibles (calibrado de ceremonia)

El gate lo fija **qué superficie se toca**, no cuántas líneas cambian.

**Superficies sensibles** (→ ritual completo + `listing-audit` obligatorio + `/eval` antes de cerrar):
- **atributos** — cualquier cosa que afirme algo del producto (`core/extract.py`, prompts, `schema.py`)
- **precio** — `core/pricing.py`, comparables, matching por imagen
- **agrupacion** — `core/grouping.py`. Una foto en la ficha equivocada es el fallo más caro y el más silencioso.
- **coste** — `core/llm.py`, caché, estimación de lote
- **persistencia** — `core/store.py`. Perder el curado de un lote es perder horas de Diego.

**Superficie única no sensible** (→ ceremonia mínima: leer, cambiar, `pytest`, decirlo): copy de la UI, estilos, un fix de layout aislado, un botón de copiar.

> Precedente heredado de SEKURA: v0.12/v0.13/v0.14 fueron las tres "solo front" y **las tres cazaron bugs reales**. "Solo UI" ≠ "sin riesgo" — pero aquí la UI *sí* es mayormente inocua porque no afirma nada del producto. Lo que afirma, es sensible.

## §C — Tres capas de verificación

### Capa 1 — Golden set (determinista, es el SUELO)
`tests/golden/` — N productos reales fotografiados por Diego, con el **ground truth verificado por él** (la marca que pone la etiqueta, la talla real, el precio al que se vendió de verdad). El skill **`/eval`** corre el pipeline entero contra ese set y reporta:

```
n=<N>  proveedor=<...>  coste_total=<€>  coste/producto=<€>
accuracy por campo:   marca X%  talla X%  material X%  color X%  categoria X%
TASA DE ALUCINACIÓN:  X%   ← afirmó con confianza=alta y era FALSO. La métrica que manda.
tasa de abstención:   X%   ← dijo null. Coste: segundos de Diego. Aceptable.
```

**La métrica primaria es la TASA DE ALUCINACIÓN, no la accuracy.** Un pipeline que acierta el 70% y se abstiene en el 30% es **mejor** que uno que acierta el 85% e inventa el 15%: el primero le cuesta a Diego unos segundos, el segundo le cuesta ventas. No optimices accuracy. Optimiza *alucinación → 0*, y luego sube la cobertura.

**Gate con dientes:** un cambio en una superficie sensible **no se cierra** si `/eval` empeora la tasa de alucinación. No es un aviso: es un no.

### Capa 2 — `listing-audit` (agéntico, adversarial)
Subagente Opus, autocontenido, cuyo trabajo es **intentar pillar al pipeline mintiendo**: coge la ficha generada y las fotos originales, y para cada campo con `confianza=alta` **exige la evidencia y la mira**. Default escéptico: si no puede ver el dato en la foto, el campo es un hallazgo.

**Refutación asimétrica — y aquí este loop se INVIERTE respecto al de SEKURA:** allí el presupuesto se volcaba en refutar los *descartes* (un falso negativo en seguridad es catastrófico). **Aquí se vuelca en refutar las AFIRMACIONES CONFIADAS**, porque son las que se publican sin que nadie las mire. Un `null` lo caza Diego en el gate en 5 segundos; una alucinación con `confianza=alta` **no la caza nadie**. Presupuesto de auditoría → a los campos que el pipeline cree saber.

### Capa 3 — El gate de Diego (la única verdad)
Diego revisa el lote antes de publicar. La UI debe hacer que ese repaso cueste **segundos, no minutos**: los campos `confianza=baja` y `null` **saltan a la vista** (destacados, agrupados arriba); los de `confianza=alta` con evidencia se pueden pasar en bloque.

**Límite honesto:** las capas 1 y 2 verifican **legibilidad y procedencia**, no verdad. Que la etiqueta ponga "100% algodón" y la prenda sea sintética, eso no lo caza ningún agente. La capa 3 **no es opcional ni sustituible**.

**Segundo límite: LEGIBILIDAD ≠ PERTENENCIA** `[INC-011]`. Un gate que verifica que un dato es legible en un píxel **no verifica que ese dato pertenezca al producto**. La ficha Frankenstein (marca de una prenda + talla de otra, tras una fusión que Diego no cazó al curar) tiene los dos campos con evidencia **real y legible** → las Capas 1 y 2 la declaran LIMPIA; sólo la caza el ojo de Diego, que es justo lo que la app existe para no usar. Todo módulo que agregue datos de **VARIAS fotos** emite un **aviso de coherencia con DIENTES** (techo de confianza, no pie de foto — `decision-making.md` §12) cuando los campos provienen de fotos disjuntas, y el export lo propaga (no lo descarta: `[INC-017]`).

## §D — Precio: nunca desde el LLM

El precio **no es un atributo del producto**: es una observación del mercado. Por tanto:
1. El precio sale de **comparables reales** (anuncios del mismo producto), cada uno con su **URL** y su **precio observado**.
2. **Hay DOS niveles de comparable, y la ficha SIEMPRE dice cuál es.** *(Añadido el 2026-07-16 por decisión de Diego. Antes esta regla exigía match visual para TODO comparable, lo que dejaba la ropa genérica sin poder tasarse.)*
   - **EL MISMO producto** — exige confirmar la identidad: **EAN o modelo legible** (determinista, gratis vía OCR) o match visual. Sólo con eso se puede decir *"el precio de este producto"*.
   - **PARECIDOS (cohorte)** — para **ropa genérica usada, que NO TIENE identidad única en internet**: una sudadera gris usada no existe como "producto" en ningún catálogo, así que *"el mismo producto"* es una categoría que no aplica. Se buscan **por texto** (marca + tipo + talla) y se entrega **"mediana de N artículos parecidos"** con sus URLs a la vista. **Nunca** se etiqueta como "el precio de tu producto".

   **Lo que NO cambia (conjunto inmutable):** *sin comparables reales citados, no hay número*. La mediana de parecidos **cita comparables reales y verificables** — por eso es honesta. Un número salido del LLM, no. La diferencia no es la precisión: es que **Diego puede abrir los 15 enlaces y comprobarlo**.

   **La cohorte se valida por la FUERZA del identificador, no sólo por su presencia** `[INC-027]`. La cohorte es *marca + tipo + talla* — un identificativo DÉBIL solo (un `tipo` genérico: "sudadera", "masajeador") **no es una cohorte, es el catálogo entero**, y su mediana es un número plausible con URLs reales que NO representa el producto (el modo de fallo del proyecto en su forma más pura). Para dar un NÚMERO hace falta **marca o modelo** (identificativo fuerte); sin ellos → `precio=None` + motivo "cohorte demasiado amplia". *(Excepción: la vía EDITABLE de la UI, donde Diego escribe/edita cada término y ve los comparables — ahí el guardrail es su ojo, no el `if`; la disciplina estricta rige donde la máquina propone SOLA.)*

   **Límite honesto que hay que decir en la UI:** los anuncios publican lo que la gente **PIDE**, no por cuánto **VENDIÓ** (los precios de venta no son públicos en Wallapop/Vinted). Así que ni la mediana ni ninguna API dan "el precio óptimo": dan **el precio que otros piden**. El óptimo lo dice el mercado (si no se vende en 2 semanas, se baja).
3. Sin comparables suficientes (`n < k`, umbral a definir en el plan) → **`precio=None` + motivo**. Nunca un rango inventado.
4. Lo que el pipeline entrega es el **conjunto de comparables + un rango observado**, y Diego decide. La app no fija precios: informa.

> Precedente directo: en SEKURA un agente Opus **se inventó** un hecho legal ("el vigilante no tiene TIP", falso) y estuvo a punto de colarse en material de inversor `[INC-001]`. Un precio inventado es exactamente el mismo fallo con otra ropa: una **mentira plausible** que suena a dato.

## §E — Agrupación: cortar de más, nunca de menos

> **Diseño MEDIDO sobre las 33 fotos reales de Diego (`tests/golden/truth.json`, 2026-07-14). No es una hipótesis: son sus datos.** Ver `[INC-002]`, `[INC-003]`, `[INC-004]` para las tres versiones que fallaron por diseñar sin este dato.

### La asimetría, que es la regla madre
- **Partir un producto de más → Diego fusiona en ~5 segundos.** Barato.
- **Fusionar dos productos → una foto de otra prenda en el anuncio.** Nadie lo caza. **Una venta perdida.**

Por tanto la agrupación **no optimiza acierto: optimiza no-fusionar.** Sesgo siempre a cortar de más.

### La señal primaria: hueco temporal, sesgado a sobre-cortar
> **Corregido 2026-07-14** tras el barrido completo de umbrales (`tests/test_grouping_golden.py::test_barrido_de_umbrales`). La versión anterior de este párrafo daba dos rangos de huecos que eran **falsos contra el EXIF real** y proponía un umbral de 20 s. Ver `[INC-005]`.

Medido sobre el golden set, barriendo el umbral de 5 a 30 s:
- **Zona segura: 1-23 s → CERO fusiones.** Los cortes de más bajan de 18 (a 5 s) a 5 (a 20-23 s).
- **Acantilado: 24 s → fusiona** los productos 1 y 2 (la frontera entre ellos es de 23 s).
- **Umbral en uso: 15 s** → 6/6 fronteras, 0 fusiones, 6 cortes de más.

**Por qué 15 y no 20:** los dos están en la zona segura, pero 20 deja sólo **4 s de colchón** hasta el acantilado — **menos de dos jitters** (±2.4 s). 15 deja **9 s** (~3.7 jitters) y cuesta exactamente **un** corte de más: ~5 segundos de Diego, una vez, en todo el lote. Comprar el doble de colchón contra el fallo caro por 5 segundos del barato **es** la asimetría de esta sección.

**El colchón no es simétrico, y eso hace fácil la decisión:** hacia arriba hay un acantilado; **hacia abajo no hay ninguno** — bajar el umbral jamás fusiona, sólo corta de más. Por eso: **ante la duda, BAJAR. Nunca subir.**

**Los huecos intra-producto (1-94 s) e inter-producto (23-2735 s) SE SOLAPAN masivamente.** No existe separación bimodal. Cualquier diseño que busque "la frontera correcta" en la distribución de huecos **está condenado** — ya falló dos veces. El tiempo no decide: **propone, sobre-cortando.**

### El reloj puede PARTIR, pero no puede CONFIRMAR
> **Regla nueva, ganada con `[INC-005]`.** Es el hallazgo que hizo BLOQUEANTE la v4.

Una pausa larga es evidencia razonable de un cambio de producto. Pero **la ausencia de pausa no es evidencia de nada**: si Diego fotografía dos productos seguidos sin pararse, el reloj no ve ninguna diferencia. Y sus huecos intra-producto llegan a 19 s dentro de un mismo grupo, así que **ningún umbral puede cazar un cambio de producto de 9 s** sin triturar cada producto en pedazos.

**Consecuencia dura: mientras el tiempo sea la única señal, NINGÚN grupo puede salir con `confianza=alta`.** No es prudencia — es que la certeza **no es derivable** de esa señal. Y hay una razón perversa que lo hace crítico: **una fusión la causa un hueco pequeño**, y "hueco máximo pequeño" era exactamente lo que la v4 premiaba con `alta` → **la confianza estaba anti-correlacionada con el riesgo**, y `alta` es justo la que la UI confirma en bloque sin mirar.

`alta` sólo podrá volver cuando exista una señal **independiente del reloj**: el clasificador de tipo de foto.

### La señal de reparación: TIPO de foto (aquí, y sólo aquí, se paga)
Los cortes de más son **predecibles**: son la foto del **metro** (+94 s) y la del **papel con el desperfecto** (+71 s) — Diego se para a colocarlos — y fotos de detalle.

> **Regla dura: una foto de METRO, ETIQUETA o PAPEL nunca puede EMPEZAR un producto. Sólo un plano general puede.**

Clasificar el tipo de foto requiere un **modelo de visión de verdad**. Es el único punto del proyecto donde una API de pago está justificada. **Las dos alternativas gratuitas están MEDIDAS y DESCARTADAS — no las reintentes:**

- **CLIP falla** (medido 2×): dice "prenda entera 100%" ante un primer plano de etiqueta, y su similitud entre fotos consecutivas está casi **invertida** (dos prendas distintas colgadas = 0.90; el plano y la etiqueta del MISMO producto = 0.61). `[INC-004]`
- **OCR falla** (medido, `[INC-007]`): parecía la señal correcta (etiqueta/papel/metro *son* texto), pero los caracteres detectados en las fotos que **abren un producto real** `[5, 9, 9, 20, 28, 39, 72]` y en las que **abren un corte de más** `[0, 18, 32, 33, 36, 176]` **se solapan por completo**. Los productos de caja tienen el plano general lleno de texto impreso; las sudaderas casi no tienen texto, así que un plano general (`Reebok`, 9 chars) es indistinguible de una foto de detalle (0 chars). **El discriminante real es la COMPOSICIÓN de la imagen** — ¿se ve la prenda entera o un trozo? — y eso el texto no lo captura.

**Lo que el OCR SÍ da, gratis:** (1) el **metro** se delata solo — el OCR lee una ristra de dígitos (`69899995999291909698925959556575`): regla trivial que caza 1 de los 6 cortes de más sin pagar nada; (2) **lee marcas y tallas** (`Reebok`, `XXL`, `UMBRO`, `ORIGINAL MARINES`, `New Age`, `XXS`) → buena parte de la Fase 2 puede salir a coste cero.

**Coste medido del de pago:** Haiku 4.5 con visión, ~1.600 tokens/imagen a $1/MTok ≈ **0,2 cts/foto ≈ 1 ct/producto**, y con caché por hash de imagen **se paga una sola vez**. La suscripción Pro/Max de Claude **no vale** — Anthropic prohíbe explícitamente enrutar peticiones de apps de terceros por credenciales de plan de consumidor; hace falta API key de Console, facturada aparte.

**Degradación honesta:** si el modelo no está o falla, la app **sigue funcionando** — sólo hay más cortes de más. **Nunca** produce una ficha contaminada. El suelo determinista no depende de nadie.

### El humano cierra
El clustering **propone**; Diego **confirma** (así lo pidió). Reglas:
- La UI debe hacer el error **visible**: fotos del mismo grupo juntas y a tamaño suficiente para ver que una no pega.
- **Fusionar debe ser trivial** — es la operación que Diego hará más veces, por diseño.
- Un grupo compuesto **sólo de primeros planos** (sin ningún plano general) es una **alarma**, no un producto.
- Lo que el modelo no pueda casar va a un cajón de **INCIERTAS**, nunca al grupo que mejor cuadre.
- **Nunca** re-agrupar después de que Diego confirme. Su confirmación es un hecho.

### Sin EXIF no hay suelo
Si las fotos llegan **sin fecha** (WhatsApp la borra: medido, 0/59), la señal primaria **no existe** y todo recae en la visión, que ya falla sola. La app **avisa en la ingesta** y le dice que las pase por cable. No bloquea, pero no le deja no enterarse.

## §F — Retro y meta-mejora (cómo este loop se mejora sin derivar)

Dos leyes, heredadas y no negociables:
1. **El retro solo registra HECHOS → append al `incident-ledger.md`.** Cuando una ficha sale mal, se registra el incidente con su clase y su evidencia. **El retro NO edita reglas.** Una regla es una generalización sobre el futuro, y eso no se puede verificar por ejecución; auto-aplicarla desde n=1 es sobreajuste.
2. **Escritor único: `/optimize`.** Es el único que promueve incidente → regla, y solo con **≥2 incidencias independientes de la misma clase, o 1 de clase irreversible** (ficha publicada mal, precio inventado, foto cruzada, lote perdido, coste desbocado). Cada regla nacida así se **etiqueta con su `[id]`**, y `/optimize` la **retira** si el id no reaparece en >5 sesiones (convergencia, no acumulación).
3. **Conjunto INMUTABLE** (solo Diego lo toca): el §"LO QUE NUNCA DEBES HACER" de `CLAUDE.md`, la lista de **superficies sensibles** (§B), y las **reglas de oro**.
4. **Tope de meta-trabajo.** El retro es barato (2-3 líneas al ledger). Si mejorarse cuesta más que el trabajo real, la regla se rompió → para.

---

## Changelog
- **v1.2** (2026-07-16) — **Decisiones de Diego tras usar la app con sus fotos reales.** (1) §A.2: el default se **INVIERTE** — `null` → **mejor intento**. La regla vieja optimizaba para un Diego que no mira la ficha; el real la mira campo a campo con el recorte al lado, así que un hueco le cuesta teclearlo y un valor mal 2 s. La defensa deja de ser el default y pasa a ser **procedencia honesta + el píxel a la vista + su ojo** — y la regla lo dice explícitamente: si algún día se publica sin que él lo vea, se vuelve a `null`. (2) §A.3: `inferido` puede pre-rellenar un campo estructurado, **siempre marcado** (badge 🧠 + `confianza=baja`). (3) §D.2: **dos niveles de comparable** — "el mismo producto" (exige EAN/modelo o match visual) vs **"parecidos"** (cohorte, para ropa genérica usada, que no tiene identidad única en internet). Lo inmutable no se toca: sin comparables reales citados, no hay número. Contexto medido que lo respalda: Haiku 4.5 con 0 alucinaciones sobre las 33 fotos, y un panel de 4 agentes que midió que el **export es el 66% del tiempo** y que la búsqueda por imagen no sirve aquí. Ver `docs/seeds/fase-3-export.md` y la bitácora v0.6.
- **v1.1** (2026-07-14) — §E reescrita entera contra las **33 fotos reales** de Diego: el umbral (15 s), la zona segura (1-23 s) y el acantilado (24 s) son **medidos**, no supuestos; CLIP y OCR **descartados con dato** como clasificador de tipo de foto. Ya no son reglas a priori: `[INC-002]`…`[INC-009]` las ganaron. La regla nueva que más cara salió — *el default tiene que caer del lado barato de la asimetría, y hay que EJECUTARLO para saberlo* — vive en `decision-making.md` §16.
- **v1.0** (2026-07-13) — creado con la infra. Sin incidentes todavía: todas las reglas de aquí eran **a priori**, derivadas de los modos de fallo de los otros repos de Diego.
