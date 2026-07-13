# Arquitectura — RESELLERMASTER

> **Estado: las costuras están DECIDIDAS, la implementación NO.** Este fichero define los contratos que cualquier plan debe respetar. El detalle (proveedor, esquema exacto, librerías) se cierra en la sesión de planificación.

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

**v2 (si algún día se paga):** SerpAPI/Google Lens devuelve comparables con `{url, precio, similitud_visual}` y el motor propone un rango observado. Mismo contrato, mismo sitio.

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
- **No automatizamos la publicación.** Ni Selenium ni Playwright contra Wallapop/Vinted. Va contra sus términos y el riesgo es el baneo de la cuenta — que *es* el negocio. La app llega hasta "copiar y pegar en 2 clicks".
- **No corremos un VLM local.** Medido: RTX 3050 Laptop con **4 GB de VRAM** — un VLM que lea logos y etiquetas no entra. Pero eso **no** implica pagar una API: el OCR local en CPU cubre lo que un VLM haría, sin alucinar. Ver `[INC-001]`.
- **No pagamos por identificar el producto (de momento).** No existe reverse image search gratuita (Bing Visual Search se retiró en ago-2025; Google Lens sólo vía SerpAPI, de pago). La app genera el **enlace de búsqueda** a Wallapop/Vinted y Diego ve los comparables reales con un click. Su criterio sobre el precio es mejor que el de cualquier modelo, y cuesta 0 €.
- **No multi-usuario, no cloud, no auth.** Es una app local de un solo usuario. Cualquier propuesta que meta una capa de servidor tiene que justificar por qué.
