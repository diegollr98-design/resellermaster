# Arquitectura — RESELLERMASTER

> **Estado: las costuras están DECIDIDAS, la implementación NO.** Este fichero define los contratos que cualquier plan debe respetar. El detalle (proveedor, esquema exacto, librerías) se cierra en la sesión de planificación.

## Las 3 costuras

### Costura 1 — `LLMEngine` (`core/llm.py`)
**Toda** llamada a cualquier proveedor de visión/texto (Claude, Gemini, GPT, o local) pasa por aquí. Nadie llama a un SDK de proveedor directamente. Nunca.

**Por qué:** la elección de proveedor **no está tomada** y no debe tomarse por intuición — se decide con `/eval` sobre el golden set (precisión, tasa de alucinación, coste/producto, latencia). Si el proveedor está esparcido por 8 ficheros, cambiarlo cuesta una tarde; si vive detrás de una costura, cuesta una línea. **Y se cambiará**: los precios y los modelos se mueven cada pocos meses.

**Responsabilidades:**
- Interfaz única `analizar(imagenes, schema) -> dict` — el proveedor es un parámetro de config, no una decisión de código.
- **Contabilidad de coste**: tokens in/out y € por llamada, agregados por producto y por lote. Es la fuente del "coste estimado" que se muestra ANTES de lanzar un lote.
- **Caché por hash de imagen + hash de prompt.** Reprocesar el mismo lote no debe costar dos veces. **La caché NUNCA se borra sin permiso explícito de Diego** — cada entrada es dinero ya gastado (`decision-making.md` §15).
- Reintentos y errores **ruidosos**: un fallo de API nunca degrada en silencio a un campo inventado.

### Costura 2 — `PriceEngine` (`core/pricing.py`)
**El precio nunca sale del LLM.** Sale de comparables observados. Ver `truth-loop.md` §D para la doctrina completa.

**Contrato:** `tasar(producto) -> {comparables: [{url, precio, similitud_visual}], rango: (min,max) | None, motivo_si_none: str}`.
Sin comparables suficientes → `None` + motivo. **Nunca un número sin fuente.**

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
- **No visión local.** Medido: RTX 3050 Laptop con **4 GB de VRAM**. Un VLM que lea logos y etiquetas de composición no entra. Decidido con dato, no con intuición.
- **No multi-usuario, no cloud, no auth.** Es una app local de un solo usuario. Cualquier propuesta que meta una capa de servidor tiene que justificar por qué.
