# Fase 3 — EXPORT: de ficha confirmada a "pegado en Wallapop y Vinted"

> Arranca aquí. **Reconcilia el estado contra el repo antes de tocar nada** (`change-loop.md` §D): `git log`, el código y los tests. Este seed destila la sesión del 2026-07-15/16, pero los seeds mienten y el código no.

## Prompt de arranque
```
Eres el orquestador ("papá oso") de RESELLERMASTER. Lee CLAUDE.md, .claude/rules/
(decision-making, truth-loop, change-loop, architecture, product, file-organization),
incident-ledger.md y docs/seeds/fase-3-export.md.
ATENCION: la seccion "DECISIONES DE DIEGO QUE CONTRADICEN LAS REGLAS ESCRITAS" de
ese seed es lo primero que debes leer — las reglas del repo estan desactualizadas
respecto a decisiones que Diego tomo el 2026-07-16, y si no lo sabes vas a
"corregir" cosas que estan bien a proposito.
Fase 3: construir el EXPORT. Es el 66% del tiempo de Diego y cuesta 0 EUR.
```

---

## Objetivo
Que Diego pase de "ficha confirmada en la app" a **anuncio pegado en Wallapop y Vinted en 2 clics**. Hoy ese paso **no existe**: lo hace 100% a mano.

## Por qué esta fase y no otra (MEDIDO por un panel de 4 agentes, 2026-07-16)
Desglose de segundos-hasta-publicar por producto (estimado con supuestos explícitos, 7 productos):

| Etapa | s/producto | % |
|---|---|---|
| Agrupar + revisar ficha + precio | ~90 s | ~33% |
| **EXPORT (copiar/pegar en las 2 plataformas)** | **~285 s** | **~66%** |

**Total ~6 min/producto, ~42 min el lote de 7.** 3 de los 4 agentes concluyeron, por separado, que el export es el cuello de botella y que todo lo demás es ruido comparado. **Cuesta 0 € (son tablas de datos, no IA).**

---

## ⚠️ DECISIONES DE DIEGO QUE CONTRADICEN LAS REGLAS ESCRITAS — LEER PRIMERO

Estas decisiones son de Diego (2026-07-16) y **están implementadas**, pero las reglas del repo **todavía dicen lo contrario**. Un `listing-audit` ya marcó una de ellas como "violación grave" citando las reglas viejas: **era un falso positivo por documentación desactualizada.** Si no lees esto, vas a repetirlo.

1. **El default se INVIRTIÓ: `null` → MEJOR INTENTO.** La extracción ahora **rellena TODOS los campos** con la mejor estimación del modelo, aunque no esté seguro. Motivo (de Diego): él revisa cada campo con su recorte delante antes de publicar, así que **en su flujo un campo vacío le cuesta teclearlo entero y un valor mal le cuesta 2 s corregirlo**. La asimetría es la contraria a la que asumía `truth-loop.md` §A.2 ("un campo null es un éxito") y la bitácora v0.5 ("el extractor NO AFIRMA").
   **LO QUE NO CAMBIÓ (y es innegociable):** la procedencia sigue honesta — `fuente="foto"` exige que el valor esté **contenido en el texto legible del recorte citado** (guard en `core/extract.py::_construir_campo_desde_sintesis`, con test); lo inferido sale `fuente="inferido"` + `confianza="baja"` y la UI lo pinta con badge **🧠 inferido — verifícalo** vs **📷 leído en foto**. `confianza="alta"` sigue siendo imposible salvo EAN con checksum.
   **PENDIENTE:** actualizar `truth-loop.md` §A.2/§A.3 y la bitácora vía `/optimize`, o Diego si toca el conjunto inmutable.

2. **`truth-loop.md` §D.2 exige match VISUAL para un comparable.** Tasar ropa genérica por **cohorte** (mediana de "parecidos" encontrados por texto) **no cumple** esa regla. Diego quiere el precio pre-rellenado. **Sin resolver — decisión suya.** Salida honesta propuesta: etiquetarlo como *"mediana de 15 artículos parecidos"*, nunca *"el precio de tu producto"*.

3. **`architecture.md` dice "no automatizar contra Wallapop/Vinted".** Esa regla se escribió para **PUBLICAR** (rellenar sus formularios = baneo = perder el negocio). **Leer su búsqueda pública** a 7 productos/mes es otro riesgo, y Diego lo juzgó despreciable **con razón** (un humano navegando hace más peticiones). **Sin escribir.** Límite que SÍ se mantiene: nada de auto-rellenar el DOM de sus formularios, ni herramientas *stealth*/anti-detección (no hacen falta a este volumen y convierten "mirar precios" en "evadir detección").

---

## Precondiciones
- **Fase 2 cerrada** (commits hasta `fix(ficha): valor pegado de session_state...`): extracción con síntesis + `ui/ficha.py` (campo junto a su recorte, todo relleno, badges de procedencia, confirmar → `fuente=diego` persistido) + `core/store.py` (`guardar_extraccion`, `confirmar_ficha` append-only).
- **De Diego: NADA nuevo.** Esta fase no necesita API keys ni gasta un céntimo.
- Entorno: `.env` tiene `ANTHROPIC_API_KEY` (solo para extraer). **AVG**: su "Protector web" hace MITM de TLS sobre `api.anthropic.com` y tumba las llamadas con "Connection error" — si pasa, excluir `api.anthropic.com` del análisis HTTPS. Ver la memoria del proyecto.

---

## Alcance / tareas

1. **`core/schema.py` — las TABLAS DE MAPEO (el bloqueante real).** Sin esto el export no tiene qué copiar. Los literales EXACTOS ya están verificados en `product.md`, **no los inventes**:
   - **estado**: enum canónico → literales de Vinted (`Nuevo/Como nuevo/Muy bueno/Bueno/Satisfactorio/Necesita reparación`) y de Wallapop (`Nuevo/Sin estrenar/Como nuevo/Buen estado/En condiciones aceptables` + los de "resto"). **Ya existe `EstadoCanonico` + `_WALLAPOP_MODA`/`_WALLAPOP_RESTO` + `VINTED_ESTADOS`** — revisar y usar.
   - **talla**: Wallapop = string combinado (`"XS / 34 / 6"`); Vinted = `size_id` de un *size group* por categoría. **`product.md` marca los size groups de Vinted como `[NO VERIFICADO]`** → NO inventarlos; si no se puede mapear, el export enseña el valor crudo y lo dice.
   - **categoría**: hoja obligatoria en Vinted y en Moda/Tecnología/Hogar de Wallapop. **No está resuelto** — decidir con Diego si se pide a mano (1 clic) o se infiere.
2. **`core/export.py` (NUEVO)** — campos confirmados → payload por plataforma: título, descripción, estructurados YA traducidos, y la lista de fotos seleccionadas/ordenadas. **DEBE llamar a `schema.es_exportable`/`validar_texto` y BLOQUEAR** si no pasa (defensa con dientes, `product.md` §7 / §12). Hoy el sanitizador solo se llama al confirmar la ficha (`ui/ficha.py::_problemas_de_texto`).
3. **`ui/export.py` (NUEVO)** — por producto y plataforma:
   - **Botón "copiar título+descripción"** (bloque único: se pegan de una vez en un textarea).
   - **Por cada campo estructurado, el VALOR YA TRADUCIDO** a esa plataforma, visible y copiable — lo que ahorra tiempo no es copiar el texto, es **no tener que re-decidir** "¿esto es Bueno o Buen estado?".
   - **Fotos con el límite y el orden de esa plataforma** (Wallapop ≤10, Vinted ≤20).
4. **Título/descripción compuestos desde los campos CONFIRMADOS**, no prosa libre del LLM (ver "Verdad").
5. `core/images.py` — selección/orden de fotos por plataforma (el módulo ya existe).

---

## Cómo
- Delegar la implementación a `engineer` (Sonnet) con contratos exactos; el orquestador valida **ejecutando**, no leyendo informes (`change-loop.md` §C1).
- **Las tablas son DATOS de `product.md`.** Donde `product.md` diga `[NO VERIFICADO]`, el export **no inventa**: enseña el valor crudo y avisa. Un campo obligatorio inventado = un anuncio que no se puede publicar, descubierto al pegarlo.
- **Límite duro:** botón de copiar al portapapeles ✅. Una extensión que auto-rellene el DOM de Wallapop/Vinted ❌ — eso es "automatizar la publicación" (`CLAUDE.md`, LO QUE NUNCA), aunque ahorre más tiempo.

## Verdad
Esta fase **no afirma nada nuevo** del producto: traduce lo que Diego ya confirmó. Pero **sí genera el texto que se publica**, así que:
- **El título y la descripción NO pueden ser prosa libre del LLM.** Los dos diseñadores ciegos del panel coincidieron: un LLM redactando libre **puede colar una marca ajena**, y en Vinted `MENTIONS_OTHER_BRAND` **oculta el anuncio**. Deben **componerse desde los campos ya confirmados** (plantilla determinista, o una llamada de redacción que reciba SOLO los campos confirmados como input).
- El **sanitizador BLOQUEA** en el export. No avisa: bloquea.
- Pasa por **`listing-audit`** lo que genere texto publicable.

## Coste
**0 €** si el título/descripción se componen por plantilla desde campos confirmados. Si se opta por una llamada de redacción, **medirlo y decirlo** (`change-loop.md` §C5). Contexto: la extracción actual cuesta **3,4 cts/producto** (23,6 cts el lote de 7), y **0 al reprocesar** (caché por hash).

## Verificación
- `python -m ruff check core/ ui/ tests/ app.py`
- `python -m pytest tests/ -q --ignore=tests/test_extract_golden.py --ignore=tests/test_grouping_golden.py` (baseline actual: **423 passed**)
- **`AppTest` obligatorio** para `ui/export.py` (`[INC-006]`: es superficie que Diego TOCA).
- **EL GATE REAL:** Diego **publica UN producto de verdad** copiando desde la app, y **cronometra**. Baseline a batir: **~285 s/producto** solo de export, ~6 min/producto en total.

## Commit + gate
`feat(export): payload por plataforma + tablas de mapeo + copiar en 2 clics`
Diego publica un producto real y dice si bajó de ~6 min. Después, generar el seed de la Fase 4.

---

## Esqueleto de fases posteriores (una línea; `change-loop.md` §E — no escribir ficción)
- **Fase 4 — Precio:** mediana automática de comparables buscando **por NOMBRE** (marca+tipo+talla, **NO por EAN**: Diego verificó que nadie pone el EAN en los anuncios) + botón de comprobar con los enlaces reales. `core/pricing.py` ya construye términos y URLs; falta leer resultados y calcular mediana/rango (n≥5 o `None`). Requiere resolver §D.2 y escribir la decisión sobre scraping. Ahorro: ~20 s/producto.
- **Fase 5 — `/optimize`:** promover a reglas las decisiones de hoy (null→mejor-intento, §D.2, scraping de búsqueda) y retirar lo que ya no aplica. **Sin esto, cada auditoría futura pelea contra el diseño vigente.**
- **Fase 6 — (APARCADA) visión en el agrupado:** solo si un gate real demuestra que los cortes de más cuestan. Medido: ahorra **~30 s en TODO el lote** y **auto-fusionar reintroduce el fallo catastrófico** (hoy la máquina nunca fusiona sola: 0 fusiones sobre las 33 fotos).

## NO REABRIR (medido y cerrado — no repetir el trabajo)
- **Búsqueda por imagen / Google Lens: NO.** Los 4 agentes coincidieron. Motivo duro: **todas** las APIs de Lens exigen una **URL pública** de la imagen (no aceptan subida ni base64) y esta app es local, sin hosting. Motivo blando: la caja ya se identifica por OCR gratis (EAN + `Model:LLLT-200`), y una sudadera gris usada **no tiene identidad única en internet**.
- **CLIP: NO** (medido 2×: 0.90 de similitud entre dos sudaderas DISTINTAS).
- **VLM local: NO** (RTX 3050, 4 GB VRAM).
- **Composición por búsqueda externa: NO construir tubería.** En **moda de Wallapop el campo material NI EXISTE**; en Vinted es **opcional**. Y no está en ninguna de las 33 fotos (0/33). El sensor más barato es **la mano de Diego**: chips de 1 clic (`algodón/poliéster/mezcla/no sé`), 2 s, con la prenda delante.
- **Precio del fabricante ≠ comparable de reventa.** El PVP nuevo (40 €) no tasa un usado (12 €). Del fabricante solo **specs**, jamás el precio.
- **Herramientas stealth/anti-detección** (obscura, crawl4ai con stealth, proxies): innecesarias a 7 productos/mes y convierten "mirar precios" en "evadir detección".
