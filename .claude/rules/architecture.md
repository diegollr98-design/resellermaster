# Arquitectura — RESELLERMASTER

> **Estado (2026-07-14):** `ListingSchema` (`core/schema.py`) **implementado**; `ExtractorEngine` y `PriceEngine` **se construyen en la Fase 2**. Este fichero define los contratos que cualquier plan debe respetar.

## Las 3 costuras

### Costura 1 — `ExtractorEngine` (`core/extract.py`)
**Todo** lo que produzca un atributo del producto pasa por aquí, sea OCR local o una API de pago. Nadie llama a un motor directamente. Nunca.

**Decisión tomada (2026-07-13, tras `[INC-001]`): se arranca con el stack GRATUITO y local.** El de pago es un proveedor más detrás de la costura, que sólo entra si `/eval` demuestra que hace falta.

**Por qué el stack gratuito es el default, y no por ahorrar:** el problema real no es "visión", es **leer texto en una etiqueta**. Un **OCR no puede alucinar una marca** — lee píxeles o devuelve nada. Un VLM ve una forma curva y afirma "Nike" con confianza alta, que es exactamente el fallo que este proyecto existe para evitar. El OCR está **estructuralmente alineado con el `truth-loop`**; el LLM no. El único valor añadido real de un LLM es *inferir lo que no se ve*, que es justo lo que §A prohíbe afirmar.

**Proveedores (todos detrás de la misma interfaz):**
| Proveedor | Qué extrae | Coste |
|---|---|---|
| `local` (**default**) | OCR (marca, talla, composición, modelo) · color por píxeles · categoría por CLIP | **0 €** |
| `hibrido` | `local` primero; el LLM sólo rellena huecos, **y ve la salida del OCR** (no adivina: la tiene delante) | céntimos |
| `llm` | Un modelo de visión hace todo | ~1-2 cts/producto |

**Responsabilidades:**
- Interfaz única `extraer(fotos, schema) -> dict[campo, Procedencia]`. El proveedor es config, no código.
- **Contabilidad de coste** (0 € en `local`): tokens y € por producto y por lote. Fuente del coste estimado que se muestra **antes** de lanzar un lote.
- **Caché por hash de imagen.** Reprocesar un lote no cuesta dos veces. **La caché NUNCA se borra sin permiso** — en `hibrido`/`llm` cada entrada es dinero ya gastado (`decision-making.md` §15).
- Errores **ruidosos**: un fallo del motor nunca degrada en silencio a un campo inventado. Falla → `null` + marca.

**El límite honesto del stack gratuito:** se abstendrá mucho (etiqueta arrugada, logo bordado sin texto → OCR no lee nada). Abstenerse es correcto según el gate, pero **hay un umbral de cobertura por debajo del cual la app deja de ahorrar tiempo y pierde su razón de ser**. Ese umbral no lo sabemos: **lo mide `/eval`, no se estima.**

### Costura 2 — `PriceEngine` (`core/pricing.py`)
**El precio nunca sale de un modelo.** Sale de comparables observados. Ver `truth-loop.md` §D.

**v1 (gratis):** el motor no tasa — **construye la consulta**. `buscar(producto) -> {urls_busqueda: {wallapop, vinted}, terminos: str, precio: None}`. Diego abre el enlace, ve los comparables reales y fija el precio. La app **informa, no tasa**; y su criterio vale más que el de cualquier modelo.

**v2 (decidida el 2026-07-16 — NO es Google Lens):** el motor **lee la búsqueda pública por TEXTO** de Wallapop/Vinted (marca+tipo+talla, **nunca por EAN**: Diego verificó que nadie lo pone en los anuncios), coge los primeros ~15 resultados y devuelve **mediana + rango + las URLs**, que pre-rellenan un precio **editable**. Etiquetado según `truth-loop.md` §D.2: *"mediana de N parecidos"*, nunca "el precio de tu producto". Sin `n≥5` → `precio=None` + motivo. Coste 0 €.

> **Google Lens / búsqueda por imagen: DESCARTADA, no reabrir.** Motivo duro: **todas** las APIs de Lens exigen una **URL pública de la imagen** (no aceptan subida ni base64) — esta app es local y no tiene hosting; habría que subir las fotos de Diego a un tercero. Motivo de fondo: **hace falta justo donde no funciona**. Un producto de caja ya se identifica **gratis** por OCR (EAN con checksum + `Model:XXX`), así que basta texto; y una **sudadera gris usada no tiene identidad única en internet**, así que Lens tampoco la encuentra. Lo confirmaron por separado 4 agentes (2 diseños ciegos + 2 ataques) el 2026-07-16. Y `[INC-004]`: CLIP tampoco vale para el match visual (0.90 de similitud entre dos sudaderas DISTINTAS).

En ambos casos: sin comparables suficientes → `precio=None` + motivo. **Nunca un número sin fuente.**

### Costura 3 — `ListingSchema` (`core/schema.py`)
Los campos obligatorios y opcionales de **cada plataforma × categoría** viven en UN sitio, declarativo. El LLM **rellena un esquema**; no inventa campos ni se olvida de los obligatorios.

**Por qué:** Wallapop y Vinted piden cosas distintas, y cada categoría pide cosas distintas. Si el prompt lleva los campos embebidos, cada categoría nueva es una edición de prompt y una regresión silenciosa. Con el esquema declarativo, añadir una categoría es añadir datos, no código.

**Cada campo lleva su estructura de procedencia** (`valor`, `fuente`, `evidencia`, `confianza`) — ver `truth-loop.md` §A. Esto no es opcional: es lo que hace auditable la ficha.

## Persistencia (`core/store.py`)
- **`st.session_state` es una caché, no la verdad.** Todo el estado de curado de un lote se escribe a disco (SQLite + ficheros). Streamlit rerunea por diseño; un rerun, un crash o un cierre de pestaña **no pueden costarle a Diego dos horas de trabajo**.
- El lote es reanudable: cerrar la app y volver a abrirla continúa donde estaba.

## El flujo (5 etapas)
```
ingerir  →  agrupar  →  extraer  →  tasar  →  exportar
 (fotos)   (clusters,  (schema +   (compa-  (copy-paste
            Diego      procedencia) rables)   + CSV/JSON)
            confirma)
```
Un bug en cualquier etapa hay que buscarlo en **las cinco** (`change-loop.md` §C3).

## Lo que NO hacemos (y por qué)
- **No automatizamos la publicación.** Ni Selenium ni Playwright contra los FORMULARIOS de Wallapop/Vinted, ni una extensión que autorellene su DOM. Va contra sus términos y el riesgo es el baneo de la cuenta — que *es* el negocio. La app llega hasta "copiar y pegar en 2 clicks".

  **LEER su búsqueda pública NO es lo mismo que PUBLICAR** *(distinción añadida el 2026-07-16 por decisión de Diego; antes esta regla se leía como una prohibición total y bloqueaba el precio por comparables).* Pedir la búsqueda pública para leer precios **está permitido**, con estas condiciones:
  - **Volumen doméstico.** Son ~7 productos por lote y pocos lotes al mes: **menos peticiones que un humano navegando** en una sola sesión. A ese ritmo el riesgo de baneo por tasa es **despreciable** — lo juzgó Diego y es correcto. Si algún día el volumen sube en un orden de magnitud, esta condición se re-evalúa.
  - **PROHIBIDAS las herramientas *stealth*/anti-detección** (navegadores con fingerprints rotatorios, proxies rotativos, `navigator.webdriver=undefined`). A este volumen **no hacen falta**, y meterlas convierte "mirar precios" en "evadir deliberadamente su protección anti-bot" — que sí es un problema, y contra la plataforma que *es* el negocio.
  - **Nada de escribir.** Leer resultados públicos, sí. Tocar una cuenta, publicar, editar o mensajear: **jamás**.
- **No corremos un VLM local.** Medido: RTX 3050 Laptop con **4 GB de VRAM** — un VLM que lea logos y etiquetas no entra. Pero eso **no** implica pagar una API: el OCR local en CPU cubre lo que un VLM haría, sin alucinar. Ver `[INC-001]`.
- **No pagamos por identificar el producto (de momento).** No existe reverse image search gratuita (Bing Visual Search se retiró en ago-2025; Google Lens sólo vía SerpAPI, de pago). La app genera el **enlace de búsqueda** a Wallapop/Vinted y Diego ve los comparables reales con un click. Su criterio sobre el precio es mejor que el de cualquier modelo, y cuesta 0 €.
- **No multi-usuario, no cloud, no auth.** Es una app local de un solo usuario. Cualquier propuesta que meta una capa de servidor tiene que justificar por qué.
