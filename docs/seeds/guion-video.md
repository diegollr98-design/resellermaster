# Guion del vídeo del recorrido — RESELLERMASTER · SEED (sesión FRESCA)

> PASO 0 OBLIGATORIO: invoca `/seed-review` sobre este SEED antes de tocar nada (regla global de Diego). Muestra el veredicto y procede según él.

> **Objetivo:** producir el **GUION + shot-list** para que Diego grabe un **screencast del dashboard de Streamlit REAL**, que rellenará el slot `#recorrido` del portfolio (`docs/portfolio/index.html`). **Objetivo real (upstream):** que quien contrata **VEA el producto funcionando** — es la única prueba visual que el portfolio no tiene incorporada (la app es local, sin demo pública desplegable) — y que el vídeo **refuerce la tesis** (anti-alucinación, procedencia, velocidad) **sin contradecir ni un caveat de honestidad**.

## Regla dura (es el corazón del repo)
El proyecto existe para que el pipeline **no mienta sobre el producto**. El vídeo hereda esa disciplina: **sólo se guioniza lo que la app HACE de verdad**, verificado corriéndola — no lo que la doc dice que hace. Una pantalla o un número inventado en el vídeo se autodestruye igual que en el portfolio.

## Qué ya existe (contexto, NO re-derivar de memoria)
- **El portfolio ya está construido y cerrado:** `docs/portfolio/index.html`. Su caption de `#recorrido` **ya define el flujo esperado** y el guion debe espejarlo:
  > ingesta del lote → **curar la agrupación (la cremallera con pestillo)** → **ficha con cada campo junto a su recorte y sus badges 📷/🧠** → **export copy-paste por plataforma** → **«5. Finanzas» (Subido → Vendido → beneficio → Excel)**. El export cronometrado (~210 s) es el clímax.
- **La app se levanta con el skill `/run`** (o `streamlit run app.py`). Necesita **un lote de fotos REAL de Diego** para el pase (no un fixture vacío).
- **Números medidos que el vídeo puede mostrar EN VIVO** (y así volverlos medición, no proyección): export cronometrado (~210 s con la app, **n=1**) vs ~285 s a mano (**estimado por un panel, NO cronometrado**); coste **3,4 cts/producto**; **0 al reprocesar** (caché por hash). En pantalla, cualquier número lleva su etiqueta de `n` y de origen (medido vs estimado) — nunca un número pelado.
- **El comparador de escala del portfolio usa `MAN=375, APP=210` como PROYECCIÓN** (`MAN=375 = 285 export estimado + 90 resto`; `APP=210` = export de 1 producto). Ver "oportunidad de oro" abajo — **corregida por `/seed-review`: el vídeo NO la vuelve "medido".**

## Realidad de la app HOY (verificada en el código `app.py` + `ui/`, 2026-08-05 — el pase en vivo la reconfirma)
- **5 pantallas en un `radio` de la barra lateral** (no pestañas superiores): **1. Ingesta · 2. Curar agrupación · 3. Ficha · 4. Export · 5. Finanzas**.
- **El precio (mediana de Wallapop) vive DENTRO de «4. Export»**, no es pantalla propia. En la Ficha solo hay `link_button` de búsqueda (Wallapop y Vinted).
- **NO hay cronómetro en la app** — el "210 vs 285 s" se mide con **reloj externo**. Si el vídeo afirma "cronometrado", necesita un cronómetro/reloj VISIBLE en pantalla y el export en **una toma continua sin cortes** (acelerar/cortar para clavar ~210 s = escenificar → prohibido).
- **`material`/`composición` YA NO está en la Ficha** (eliminado 2026-07-17); solo aparece como bloque de export en Vinted. El campo `null` que enseñe el vídeo debe ser uno que HOY esté en la ficha (`medidas`, `ean`, `modelo`, `talla` o `desperfectos` cuando no son legibles).
- **El precio de la pestaña Vinted del Export viene de la mediana de WALLAPOP** (Vinted no se tasa: Datadome). Si el vídeo lo enseña, **rotularlo** como "comparables de Wallapop", no como tasación de Vinted.

## Tarea de la sesión fresca
1. **Levantar la app real (`/run`) y hacer un pase COMPLETO** con un lote de fotos real, pantalla por pantalla, para saber **qué se ve HOY en cada paso** (el flujo real manda sobre este SEED, que pudo quedar desactualizado). Verificar cada pantalla existe y qué muestra. **Correr una extracción real ANTES de guionizar** (`[INC-031]` tumbaba la re-extracción con un 400 hasta el fix `5aa8a99` — confirmar que funciona hoy).
2. **Escribir el guion** como fichero `docs/portfolio/guion-video.md`: **shot-list con timestamps**, acción en pantalla en cada toma, y **texto de narración/caption**. Espejar **el ORDEN de etapas** del caption del portfolio (5 etapas), **NUNCA sus números/claims** — el caption es marketing, la app es la verdad (`change-loop §D`). **Guionizar SOBRE lo grabado en el pase real**, no sobre lo que la doc promete. Cada número o resultado que la narración afirme debe salir de la toma real; si el pase difiere del caption → se corrige el HTML, no se falsea la toma. Duración objetivo **60–90 s** (es un producto de *velocidad*: ritmo ágil).
   - **Plan B honesto obligatorio** para lo que varía en vivo: si la búsqueda de Wallapop devuelve `n<5` → `precio=None` + motivo → el guion enseña el **enlace de búsqueda**, no un número inventado.
3. **Marcar el clímax:** el export. Guionizar que Diego **cronometre de verdad** el export **con cronómetro/reloj visible en pantalla y en una toma continua sin cortes**. El resultado que salga es el que se narra (aunque no sea exactamente 210 s): es n=1, no se re-graba hasta clavar el número bonito.
4. **Idioma:** el portfolio es ES/EN. Decidir con Diego: narración ES + subtítulos EN, o vídeo mudo con captions en pantalla (bilingües). Dejarlo como pregunta si no está claro.

## ⚠️ La "oportunidad de oro" — CORREGIDA por `/seed-review` (era una trampa)
El SEED original proponía usar la grabación para volver el comparador `MAN=375/APP=210` de *"proyección"* a *"medido, n=10"*. **NO se hace, por tres razones que el panel (agente ciego + 2 críticos) convergió en refutar:**
1. **Un screencast NO mide el lado manual.** La app no tiene "modo manual" que filmar; `MAN` solo se mide si Diego lista ~10 productos ENTEROS a mano en Wallapop/Vinted con cronómetro — horas off-camera, otra tarea, no este vídeo.
2. **`MAN=375` no tiene ni un dato medido** (el 285 es estimado por un panel — lo dice el propio portfolio). Relabelarlo "medido n=10" es **falso de raíz** y borra los caveats de honestidad (`⚠ PROYECCIÓN LINEAL`) que el portfolio se ganó — el número plausible-sin-fuente que este proyecto existe para impedir (`§10`, `§19`).
3. **Grabar sesga a la baja** (best-take, no el martes representativo) y **APP=210 ya es un estiramiento** (era el export de 1 producto usado como tiempo total/producto).

**Lo que SÍ puede hacer el vídeo:** mostrar **un export real cronometrado** con su etiqueta honesta de `n=1` (afina/reconfirma `APP` con su n visible), y **dejar `MAN` y la nota del comparador como están** ("proyección", "estimado por un panel"). **Cambiar las constantes `MAN`/`APP` a "medido n=N" es un EXPERIMENTO APARTE** que exige cronometrar de verdad ≥N productos por AMBOS métodos, productos DISTINTOS de dificultad pareada, sin re-tomas descartadas. Si Diego lo quiere, es otra sesión; el vídeo no lo sustituye.

## Lo importante del portfolio a tener en cuenta (para no dispararse un tiro)
- **HONESTIDAD = la tesis.** El vídeo NO puede mostrar nada que contradiga los caveats del portfolio:
  - Es **app LOCAL de un usuario** — no simular cloud/multiusuario.
  - **NO automatiza la publicación** — mostrar **copy-paste** a Wallapop/Vinted, **jamás** autollenado del formulario (va contra sus ToS y contra la tesis).
  - El **precio = lo que otros PIDEN, no de venta**; y sale de **comparables reales con URL**.
- **Que se VEAN los badges** 📷 leído / 🧠 inferido **y un campo en `null`** — **NO `material`** (ya no está en la ficha): usa un campo que HOY exista (`medidas`, `ean`, `talla` o `desperfectos` cuando no es legible en la foto). Es la joya diferencial: el Loop de Verdad hecho pantalla. Que el null salga de la **pantalla real**, no de la ficha-mock dibujada a mano del portfolio.
- **Precio SIEMPRE etiquetado en pantalla:** "mediana de N parecidos · lo que otros PIDEN, no de venta", con las URLs de los comparables a la vista. Wallapop real; jamás una mediana de Vinted.
- **Finanzas con datos REALES del Gate B**, nunca cifras montadas para el vídeo (coste/precio/beneficio inventados en un portfolio = fabricación pura, el pecado que el proyecto existe para no cometer). Enseñar los márgenes reales de Diego es una **decisión de divulgación suya** (ver preguntas abajo).
- **Mostrar la cremallera** (curar): partir/fusionar fronteras — la operación donde meter una foto del producto A en la ficha del B es *inexpresable*.
- **Precio en Vinted:** la app **NO** lee mediana de Vinted (búsqueda tras Datadome; leerla exigiría stealth, prohibido por `architecture.md`). En el vídeo, **no mostrar una mediana de Vinted que no existe** — mostrar la de **Wallapop** (real) y/o el enlace de búsqueda de Vinted.

## Seguridad / PII al grabar (guionizar el "cómo grabar")
- **No mostrar el `.env` ni la API key** (ni la terminal donde aparezca), ni datos personales/PII en fotos o fichas. Si un producto de ejemplo lleva algo personal, usar uno neutro.
- **Ensayo previo sin errores:** un traceback de Streamlit en vivo (el 400 de `[INC-031]`, el MITM de AVG sobre `api.anthropic.com`) pinta **rutas absolutas del disco de Diego** y a veces el entorno DENTRO de la app. Ensayar el pase entero antes; si salta un error, cortar y re-tomar, no dejarlo en la grabación.
- **Barrer cada foto del lote de demo** por PII no obvia: reflejos (cara/interior de casa en superficies brillantes), direcciones en embalajes, nombres en etiquetas de envío.
- Grabar limpio: 16:9, cerrar notificaciones/pestañas, zoom de navegador legible.
- Decidir alojamiento del vídeo (YouTube *unlisted* o `.mp4` en Vercel) → el portfolio reemplaza el bloque `.video-ph` de `#recorrido` por `<iframe>` o `<video src poster>` (hay un comentario con la línea exacta en el HTML).

## Gate de cierre (§Cierre de `/seed-review`)
Antes de dar el guion por terminado: **cada pantalla que nombra el guion EXISTE de verdad** (verificada corriendo la app, no leída de la doc); **cada número que la narración afirma sale de la toma real** (no del caption); ningún paso contradice un caveat de honestidad; el clímax (export cronometrado con reloj visible, toma continua) está; y queda claro dónde se aloja el vídeo y cómo se incrusta. **Jerarquía de verdad: app > caption > SEED** — cualquier discrepancia corrige el documento (HTML/mock), nunca la grabación.

## NO hacer
- No inventar pantallas, campos o números que la app no muestre hoy.
- No guionizar autollenado de formularios de Wallapop/Vinted (ToS + tesis).
- No mostrar una mediana de precio de Vinted (no existe: Datadome).
- **No reetiquetar el comparador `MAN`/`APP` del portfolio como "medido n=10" a partir del vídeo** (el vídeo no mide el lado manual; sería falso — ver la sección corregida).
- **No acelerar/cortar el export** para que parezca más rápido de lo que fue.
- No exponer `.env`, API keys, tracebacks con rutas, ni PII.
