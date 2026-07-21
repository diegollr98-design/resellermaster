# Producto — RESELLERMASTER

> Verificado el **2026-07-13** contra fuentes primarias: OpenAPI oficial de Vinted Pro (`pro-docs.svc.vinted.com/downloads/api.yml`), payload SSR de `vinted.es/help/*`, API pública de Wallapop (`api.wallapop.com/api/v3/*`) y su centro de ayuda vía la API de Zendesk.
>
> **Lo marcado `[NO VERIFICADO]` NO se rellena a ojo.** Un campo obligatorio inventado = un anuncio que no se puede publicar, descubierto al pegarlo.

---

## VINTED

### Campos (fuente: OpenAPI `ItemProperties` + help/375)
| Campo | Oblig. | Tipo | Límite |
|---|---|---|---|
| `title` | **Sí** | texto libre | **5–100 chars** |
| `description` | **Sí** | texto libre | **5–2000 chars** |
| fotos | **Sí** | imágenes | mín. 1, **máx. 20**, ≤5 MB c/u |
| `catalog_id` (categoría) | **Sí** | árbol, **hoja obligatoria** (prof. 3–4) | — |
| `brand` | **Sí** | lista + crear marca; "Sin marca" existe | 256 chars |
| `status_id` (estado) | **Sí** | lista cerrada | 6 valores |
| `price` + `currency` | **Sí** | número + EUR | mín. 1 |
| `package_size_id` | **Sí** | lista cerrada | — |
| `size_id` (talla) | **Condicional** | lista cerrada | oblig. si la categoría tiene *size group* |
| colores | No | lista cerrada | **máx. 2** |
| materiales | No | lista cerrada | **máx. 3** |
| medidas largo/ancho | No | número | — |

**Vinted NO es solo moda:** también Hogar, Electrónica, Entretenimiento, Deporte, Hobbies. No admite vehículos ni inmuebles.

### Escala de estado — LITERALES EXACTOS (help/50)
`Nuevo` · `Como nuevo` · `Muy bueno` · `Bueno` · `Satisfactorio` · `Necesita reparación` (solo electrónica)

> ⚠️ **"Nuevo con etiquetas" / "Nuevo sin etiquetas" NO EXISTEN en Vinted.** Es un error común. Regla oficial: *"si no sabes qué estado elegir, elige el más bajo"*; un estado incorrecto puede hacer que **oculten el anuncio**.

### Reglas de contenido que atan al generador (validación dura del backend)
Códigos reales de `ItemValidationError`: `CONTAINS_EMAIL`, `EXCESSIVE_UPPERCASE`, `EXCESSIVE_SYMBOLS`, `UNALLOWED_SYMBOLS`, `LONG_WORDS`, `TOO_SHORT`, `TOO_LONG`, `PRICE_TOO_LOW`.
→ El texto generado **nunca** lleva emails, MAYÚSCULAS excesivas, ristras de símbolos/emojis, ni palabras larguísimas.

Además, prohibido y sancionable: **enlaces externos**; **mencionar o hashtaggear una marca distinta a la seleccionada**; **"parecido a" / "inspirado en" / "réplica"** + marca; duplicar el mismo artículo en varios anuncios.

---

## WALLAPOP

### Campos (fuente: ayuda oficial + `type_attributes` de la API en vivo)
Título · Categoría (18 raíces, hasta **5 niveles**; hoja obligatoria solo en Moda, Tecnología, Hogar y Cine/libros) · **Fotos: máx. 10 (50 con PRO)** · Descripción · Medidas · Estado · Marca · **Hashtags (campo estructurado propio)** · Precio · Envío + tramo de peso (2/5/10/20/30 kg) · Ubicación.

**Atributos por tipo (empírico, API en vivo):**
- **moda**: `brand`, `size`, `color`, `condition` — **SIN material**
- **electrónica**: `brand`, `model`, `storage_capacity`, `color`, `condition`
- **hogar/muebles**: `height_cm`, `width_cm`, `length_cm`, `material`, `color`, `is_bulky`
- **libros**: `isbn`, `author`, `publisher`, `language`, `book_format`

### Escala de estado — LITERALES EXACTOS (empírico, 149 anuncios vivos)
**Moda:** `Nuevo` · `Sin estrenar` · `Como nuevo` · `Buen estado` · `En condiciones aceptables`
**Resto:** los anteriores + `Sin abrir` · `En su caja` · `Lo ha dado todo`

> ⚠️ Es **"Buen estado"**, no "Bueno". Y **"En condiciones aceptables"**, no "Aceptable". La lista visible depende de la categoría. Observado, no enum garantizado.

### ⚠️ Wallapop YA autogenera la descripción con IA
La ayuda oficial dice literal: *"Rellenaremos la descripción del producto por ti. Revísala y edítala"*.
**Consecuencia de producto:** nuestra descripción se pega **encima** de un borrador que Wallapop ya escribió. El valor de la app en Wallapop **no está en la prosa** — está en la agrupación de fotos, los atributos estructurados, el precio con comparables y la velocidad. Recalibrar expectativas: no vendemos redacción, vendemos velocidad y verdad.

### Otras reglas
Duplicados = spam (se elimina el anuncio repetido; prohibido **reusar la misma foto en varios anuncios de Wallapop** — entre plataformas distintas no aplica). Anuncios caducan a los **2 meses** sin interacciones. Hashtags **sí** permitidos.

---

## IMPLICACIONES DIRECTAS PARA `core/schema.py`

1. **`condition` NO puede ser un campo compartido.** Los literales no coinciden en ningún nivel. Hace falta un **enum canónico interno** + **dos tablas de mapeo** (una por plataforma). El estado siempre lo confirma Diego (`truth-loop.md` §A.4).
2. **`size` tampoco.** Wallapop usa un string combinado (`"XS / 34 / 6"`); Vinted usa un `size_id` de un *size group* por categoría → **tabla de mapeo obligatoria**.
3. **`material` es opcional-por-plataforma:** hasta 3 en moda de Vinted, **inexistente** en moda de Wallapop.
4. **`brand` es obligatoria en Vinted** (o "Sin marca") y opcional en Wallapop. Si el pipeline no ve la marca → `"Sin marca"` en Vinted, **nunca una marca plausible**.
5. **Título:** el límite duro es el de Vinted (**100 chars**). Generar uno que sirva para ambas.
6. **Descripción:** dos longitudes. Vinted hasta 2000; para Wallapop **ceñirse a ≤600 chars** (ver hueco abajo).
7. **Un sanitizador de texto obligatorio** antes de exportar: sin emails, sin enlaces, sin MAYÚSCULAS excesivas, sin ristras de símbolos/emojis, sin marcas ajenas a la seleccionada. Es una **defensa con dientes**: si no pasa, no se exporta.
8. **Fotos:** 20 en Vinted, 10 en Wallapop → `core/images.py` selecciona y ordena distinto por plataforma.

---

## HUECOS — `[NO VERIFICADO]`, no rellenar a ojo
- **Wallapop, límite de caracteres de título y descripción.** El "650" que repiten los blogs **es falso o está desactualizado**: hay anuncios vivos con 668 chars. Empírico: máx. 68 en título, máx. 668 en descripción sobre 1.399 anuncios. Recomendación operativa: **≤600 chars**, y verificar al pegar.
- Wallapop: formato, peso y resolución de imagen aceptados (el "1 MB" de los blogs no aparece en ninguna fuente oficial).
- Wallapop: enum cerrado y completo de `condition` por categoría.
- Wallapop: si prohíbe explícitamente enlaces/emails/teléfonos en la descripción (práctica común asumirlo, **no lo encontré escrito**).
- Vinted: si los límites 100/2000 de la API Pro aplican idénticos al formulario consumer (coinciden en todo lo comprobable, pero el formulario no publica sus límites).
- Vinted: enums completos de `package_size`, colores y *size groups* **numéricos** (`id`) requieren token de la API Pro de ontologías. **OJO — corrección factual (2026-07-17, `[INC-016]`/seed fase-4):** el **ÁRBOL de categorías** (`catalogTree`, 2482 hojas) **SÍ es obtenible sin token** del SSR de `www.vinted.es` (cookie anónima, 200); y sus **etiquetas** de `package_size` son públicas — la app copia-pega en el formulario, no publica por API, así que le basta la etiqueta, no el `id`. **VERIFICADO por Diego contra el formulario real (2026-07-21):** las opciones del envío estándar son **Pequeño** (cabe en un sobre grande) · **Mediano** (caja de zapatos, *recomendado*) · **Grande** (caja de mudanza), + **Voluminoso** (grandes/pesados) aparte. `core/export.py::_campo_envio` sugiere una de las tres, sesgada hacia arriba. "Requiere token" es cierto **sólo** para el `id` numérico, no para el árbol ni las etiquetas. El árbol varía por país+divisa y está versionado por fecha → job de descarga **periódico**, no congelar para siempre.
- Wallapop: el **árbol de categorías** es un endpoint **público y estable** (`GET api.wallapop.com/api/v3/categories`, 200 sin auth, 859 hojas, `leafMandatory` en Moda/Tec/Hogar/Cine) — descargable y versionable de un GET.
- **Ambas: el algoritmo de ranking.** Ninguna lo documenta. Cualquier afirmación tipo "el algoritmo penaliza X" que leas en un blog es **especulación** — no la conviertas en regla.
