# Fase 5 — FINANZAS (registro de ventas + beneficio + Excel) · SEED DE PLANIFICACIÓN

> **Este seed es para PLANEAR CON RE-AUDITORÍA, no para implementar a ciegas.**
> Contiene dos cosas de naturaleza distinta, no las confundas:
> 1. **REQUISITOS de Diego** — FIJOS. Ya los decidió. No se re-litigan.
> 2. **HALLAZGOS DE UN PANEL PREVIO** (4 agentes, 2026-07-21) — **CANDIDATOS**.
>    Pueden estar mal, ser bloat, o dejarse huecos. Diego aprobó el CONCEPTO de
>    todos (incl. los opcionales), pero **la FORMA de cada uno, el MODELO DE
>    DATOS y los CASOS LÍMITE están sin verificar**.
>
> **PASO 0 OBLIGATORIO (lo pidió Diego, literal): re-auditar los hallazgos de
> abajo con un panel multi-agente en paralelo, con el MÍNIMO sesgo posible.** No
> los asumas por venir en el seed: re-derívalos desde cero y atácalos; quédate
> solo con lo que sobreviva. El objetivo es 100% objetividad, no confirmar el
> trabajo previo.
>
> **Reconcilia el estado contra el repo antes de nada** (`change-loop.md` §D):
> `git log`, el código (**`core/store.py` sobre todo** — es donde viven los 3
> riesgos de corrupción), `pytest`. Los seeds mienten; el código no.

## Prompt de arranque
```
Eres el orquestador ("papá oso") de RESELLERMASTER. Lee CLAUDE.md, .claude/rules/
(decision-making, truth-loop, change-loop, architecture, product, file-organization,
sessions-log), .claude/incident-ledger.md y docs/seeds/fase-5-finanzas.md.
Es una sesión de PLANIFICACIÓN CON RE-AUDITORÍA (Loop de Cambios §A). Superficie
SENSIBLE de PERSISTENCIA (core/store.py: son ventas = dinero, perder datos es
catastrófico). PRIMERO ejecuta el PASO 0 (re-auditar los hallazgos del seed con un
panel multi-agente en paralelo, mínimo sesgo). Con lo que sobreviva, decide el plan
CON DIEGO antes de escribir código. No asumas los hallazgos como ciertos por venir
en el seed.
```

---

## EL OBJETIVO (requisitos de Diego — FIJOS, no re-litigar)

Una **5ª pantalla "5. Finanzas"**: un registro/CRM de ventas para llevar el
control de lo que se va vendiendo y **calcular el beneficio**. Detrás, una tabla
persistida **exportable a Excel**.

1. **En Export, un botón "Subido" POR plataforma** (Wallapop, Vinted) → registra
   el producto en Finanzas con sus campos y en qué plataforma(s) se subió.
2. **Número de referencia único**, generado en la **Ficha**, que **no se repite
   jamás** (ni con productos pasados ni futuros), impreso en la **descripción de
   AMBAS plataformas**. Es la LLAVE: cuando algo se vende, Diego copia el número
   del anuncio, lo busca en Finanzas y marca "Vendido".
3. **Coste**: campo manual en la **Ficha**, **no obligatorio**, **0 por defecto**,
   editable a mano cuando un producto sí tuvo coste.
4. **Al venderse**: marcar "Vendido" con el **precio de venta final** (que
   **auto-rellena con el precio elegido en Export**, pero es **editable** — se
   puede vender más barato o en lote) y **en qué plataforma se vendió**.
5. **Beneficio** = precio de venta − coste. **Dashboard**: ver/filtrar/seleccionar
   productos, total vendido, beneficio, **exportar a `.xlsx`**.
6. Diego aprobó también las **features y opcionales** del panel (sección B y C de
   abajo). La duda no es SI se hacen, es CÓMO y qué casos límite resolver.

**Encaje con el proyecto:** aquí NO hay riesgo de alucinación — precio de venta,
coste y plataforma los mete Diego, no el pipeline. Pero **sí es superficie
sensible de PERSISTENCIA** (`truth-loop.md` §B): son sus ventas y su dinero →
nada vive solo en `session_state`, todo a disco (SQLite).

---

## PASO 0 — RE-AUDITAR los hallazgos (multi-agente, paralelo, MÍNIMO SESGO)

Lo pidió Diego. Lanza **agentes autocontenidos en paralelo** para reauditar la
sección de HALLAZGOS (A–F). Para minimizar el sesgo, **mezcla dos tipos**:

- **Ciegos (re-derivan desde cero, SIN ver los hallazgos del panel previo):**
  dales solo el OBJETIVO de Diego + las restricciones + el código real, y pídeles
  que propongan el modelo de datos correcto y las features que faltan. **Si
  convergen con el panel previo → confianza alta; si divergen → ahí está lo que
  no era obvio** (`change-loop.md` §A).
- **Adversariales (SÍ ven los hallazgos y los ATACAN):** que intenten refutar
  cada riesgo del modelo de datos (¿es real contra `store.py`?), cada feature
  (¿es bloat? ¿viola una restricción? ¿hay una forma mejor?), y **que busquen lo
  que el panel previo se DEJÓ** (casos límite nuevos).

**Quédate solo con lo que sobreviva la re-auditoría, re-derivado por ejecución
(leyendo `store.py`/`product.md`, corriendo probes), no por informe.** Los 3
riesgos de la sección A vienen marcados como "verificados contra el código" por el
panel previo — **vuelve a verificarlos tú**, no los des por buenos.

> No re-litigues los REQUISITOS de Diego (arriba). El PASO 0 audita los
> HALLAZGOS (el CÓMO, el modelo de datos, los huecos), no el QUÉ.

---

## HALLAZGOS DEL PANEL PREVIO (2026-07-21) — CANDIDATOS a re-auditar

> Origen: 4 agentes ciegos entre sí con lentes distintas (flujo del revendedor ·
> valor compuesto del dato · fricción/velocidad · red-team de integridad).
> Convergencia entre agentes = señal fuerte, PERO re-verificar igual.

### A. MODELO DE DATOS — 3 riesgos de corrupción (verificar contra `core/store.py`)
Marcados por el red-team como los que, con el `store.py` de hoy, **garantizan
pérdida de datos**. Son estructurales (se cierran decidiendo DÓNDE vive el dato,
no con validación posterior). **Re-verifica cada uno leyendo `store.py`:**

1. **`referencia` y `coste` = COLUMNAS PROPIAS de `productos` (o de una tabla
   nueva), NUNCA dentro del JSON `campos`.** Motivo alegado: `store.guardar_
   extraccion()` hace `UPDATE productos SET campos = ?` (sobreescribe `campos`
   entero) → re-extraer un producto **borraría** su referencia (ya impresa en el
   anuncio real) y su coste. La ref se asigna **una vez**, idempotente
   (`confirmar_ficha` es re-invocable → asignar solo si aún es NULL).
2. **Contador de referencias = marca de agua persistente (high-water-mark),
   NUNCA `MAX(referencia)+1`.** Motivo: si sale del máximo de las filas VIVAS,
   borrar/archivar un producto vendido **reutiliza su número** → dos ventas con
   la misma llave. Incrementar en la MISMA transacción que asigna (`_transaccion`
   ya da atomicidad). Verifica cómo hace `store.py` las migraciones/secuencias.
3. **`ventas` = TABLA APARTE con snapshot INMUTABLE** (copia ref, título,
   plataforma, precio, coste, fecha) que **sobrevive al borrado del
   producto/lote**. Motivo: si la venta es un FK-join vivo a `productos` y lee de
   `campos` en vivo → (a) borrar un lote viejo **destruye el historial de
   ventas**; (b) editar la ficha de un producto ya vendido **muta su beneficio
   retroactivamente**. Migración **additiva** (nueva versión de schema, p.ej.
   `_MIGRATIONS[3]` con `ALTER ADD COLUMN`/`CREATE TABLE`, **nunca DROP**) — mira
   cómo se hizo la v2 en `store.py`.

### B. FEATURES CONVERGENTES (varios agentes independientes — Diego las aprobó)
1. **Beneficio HONESTO, no `venta − coste`** (3 de 4 agentes). Dos correcciones:
   (a) **`coste = NULL` ("no informado") ≠ `0` ("gratis")** — con 0 por defecto,
   subir N productos sin coste da beneficio = 100% margen, un número plausible y
   FALSO (el pecado que el proyecto existe para evitar, `§10`/`truth-loop`). Las
   filas sin coste **se marcan y NO inflan el total en silencio** (`§12`, defensa
   con dientes, no un pie de foto). *(Ojo: esto matiza el requisito de Diego "0
   por defecto" — a nivel de UI puede MOSTRAR 0, pero el AGREGADO de beneficio
   debe distinguir 0-real de no-informado. Resolver con Diego.)*
   (b) **Comisiones + envío + destacados** como campos opcionales (manuales, NO
   calculados — cambian cada mes) O etiquetar SIEMPRE **"beneficio bruto (sin
   comisiones ni envío)"**. En Wallapop pagas envío/tarifas; en Vinted, los
   destacados/bumps cuestan.
2. **Estado por plataforma + recordatorio de RETIRAR del otro** (3 de 4). Es el
   error nº1 del revendedor bi-plataforma: se vende en Vinted, sigue vivo en
   Wallapop, otro lo compra → cancelas → **rating quemado** (el activo). Al
   marcar vendido, aviso "🔴 retíralo de Wallapop". **Checklist, NO
   automatización** (retirar por API está prohibido).
3. **Tiempo en venta + inventario que envejece** (2 de 4). Con la fecha de
   "Subido", una **lista ordenada por días parados** que avisa cerca de la
   caducidad de Wallapop (2 meses, `product.md`) y sugiere bajar el precio —
   operacionaliza la única regla de precio que `truth-loop.md` §D admite como
   verdad. **Una lista, CERO gráficas** (bloat a ~7 productos/lote).
4. **Congelar la EVIDENCIA de precio al "Subido"** (2 de 4). La mediana pública
   que ve hoy Diego **caduca en semanas** (son anuncios vivos). Si al pulsar
   "Subido" se guarda ese snapshot (`Tasacion`: mediana+min+max+los ~15
   comparables con URL+precio pedido+términos+timestamp+plataforma) + el precio
   que Diego eligió, y luego el precio de venta REAL → en N ventas tiene el factor
   "lo que se PIDE → lo que se VENDE" que **hoy nadie tiene** (los precios de
   venta no son públicos). **Barato de capturar ahora, imposible de reconstruir
   después.** A futuro podría mejorar la sugerencia de precio con SUS datos
   (construir-luego; capturar-ya). Hoy `tasar()` calcula el `Tasacion` en la UI y
   **no lo persiste** — verifícalo.

### C. OPCIONALES (Diego los quiere; confirmar la FORMA)
- **Ubicación física** ("caja 3 / estante B") — campo de texto en la ficha, para
  encontrar el objeto en casa y enviarlo en 24-48h.
- **Fecha de adquisición** (default hoy) → "velocidad de capital" (8€ en 5 días >
  15€ en 90) y detectar stock muerto.
- **Re-exportar un producto YA procesado** sin rehacer ficha/agrupación (para
  re-listar tras caducar; la persistencia ya existe, falta la puerta de entrada).
- **Precio mínimo / suelo de negociación** por producto (Wallapop se regatea
  siempre) — depende de tener los costes itemizados (B.1) para ser honesto.

### D. NÚMERO DE REFERENCIA — detalles a respetar
- **Formato que pase el sanitizador de Vinted POR CONSTRUCCIÓN** (`schema.
  validar_texto`: `UNALLOWED_SYMBOLS`, `LONG_WORDS`, `EXCESSIVE_SYMBOLS`) — dígitos
  simples, prefijo corto sin símbolos raros, no una "palabra larga". **Testearlo
  contra `validar_texto` en AMBAS plataformas** (`§17`: la garantía es un `if`, no
  la prosa). Discreto ("Ref. 142"), inyectado **después** de sanitizar/construir
  la descripción, en posición estable.
- **Buscable por su CAMPO, no full-text** — "142" a pelo choca con un precio, una
  talla o un año.
- **Red de seguridad:** Wallapop **autogenera** su descripción y la nuestra se
  pega encima (`product.md`); si Diego no pega la nuestra, la ref no aterriza →
  el producto debe ser localizable en Finanzas **también por título/foto**, no
  solo por número.
- **Cuándo se asigna:** decisión abierta (¿al confirmar la ficha? ¿al primer
  "Subido"?). Que una extracción de usar-y-tirar NO queme números.

### E. BONUS fuera de Finanzas (fricción trivial)
- Al "Preparar fotos", **abrir la carpeta automáticamente** (`os.startfile` en
  Windows) — hoy muestra la ruta como texto y Diego navega a mano, ×14/lote
  (~1,5 min). Trivial, no roza la prohibición (abre una carpeta local, no toca la
  plataforma). *(Verificar en `ui/ingesta.py`/`export.py` dónde se copian las
  fotos.)*

### F. DESCARTADO por el panel (NO re-proponer — bloat/prohibido)
Auto-publicar / auto-bump / auto-despublicar (baneo), calcular comisiones
automáticamente (frágil, cambian cada mes), gestión de chat/mensajes (on-platform,
términos), **leer métricas de la cuenta** (visitas/favoritos — prohibido tocar la
cuenta, no solo publicar), gráficas de analítica/tendencias (bloat), escáner
EAN/códigos de barras (el EAN ya se descartó como identificador), IVA/fiscal/
contabilidad, edición de fotos, multi-usuario/cloud, export de todo el lote en un
solo scroll (el propio agente de fricción lo auto-mató).

---

## LO QUE YA EXISTE (reusar; verificar en el código, no reinventar)
- **`core/store.py`** — SQLite + migraciones (`_MIGRATIONS`, versionado additivo;
  mira cómo se añadió la v2), `_transaccion` (atomicidad), `guardar_extraccion`/
  `confirmar_ficha` (append-only, el log `confirmaciones`). **Aquí viven los 3
  riesgos de la sección A y la migración de la tabla `ventas`.**
- **`core/schema.py`** — Costura 3; `validar_texto` (el sanitizador que la ref
  debe pasar). Un posible sitio para el enum de plataformas / estados de venta.
- **`core/export.py`** — `construir_payload`, cómo se compone la descripción (para
  inyectar la ref) y cómo pasa por `validar_texto`. El precio elegido vive hoy en
  la UI del export (`ui/export.py`) — hay que **persistirlo** para el auto-relleno.
- **`ui/export.py`** — donde van los botones "Subido" por plataforma.
- **`ui/ficha.py`** — donde va el campo `coste` (patrón: un campo editable no
  obligatorio, como los que ya existen) y donde se genera/muestra la ref.
- **`app.py`** — la navegación `st.sidebar.radio` con las 4 pantallas; añadir la
  5ª "5. Finanzas" es un `_PANTALLA_FINANZAS` + una rama (mira cómo están las 4).
- **`ui/`** — una pantalla nueva `ui/finanzas.py` (un módulo por pantalla,
  `file-organization.md`).

---

## SUPERFICIE SENSIBLE + GATE
- **Persistencia** (`truth-loop.md` §B): ventas = dinero. Toda escritura a disco,
  migración **additiva** verificada **contra lotes viejos** (que un lote de antes
  de la tabla `ventas` siga abriéndose sin romper). Nada vive solo en
  `session_state`.
- **`[INC-028]`/change-loop §C4:** **todo botón nuevo** ("Subido", "Vendido",
  "Exportar Excel", "Deshacer venta") lleva un **`AppTest` que lo PULSA** (ejecuta
  su `on_click`), no solo que renderiza. El arranque headless no paga el clic.
- **`§17`:** la ref en la descripción pasa por `validar_texto` en un test real
  (ambas plataformas), no "debería pasar".
- **`listing-audit`** NO es central aquí (Finanzas no AFIRMA atributos del
  producto). El guardián aquí es la **integridad de datos** (los tests de
  persistencia + los casos límite de la sección "DECISIONES ABIERTAS").
- El **GATE REAL** del proyecto sigue pendiente y es ORTOGONAL: Diego cronometra
  un export de verdad (baseline ~285 s). No lo tapa esta fase.

---

## DECISIONES ABIERTAS (resolver CON Diego tras el PASO 0)
Casos límite del ciclo de vida que, si no se deciden, corrompen o dan números
falsos (los enumeró el red-team; el PASO 0 puede añadir más):
1. **`coste` 0-real vs no-informado** en el beneficio agregado (B.1a) — ¿NULL con
   marca, o Diego asume 0 = 0?
2. **Beneficio bruto vs neto** — ¿campos de comisión/envío, o etiqueta "bruto"?
3. **Deshacer un "Vendido"** por error — reversible, con rastro append-only.
4. **Devoluciones** — estado que revierte la venta (con envío de vuelta = pérdida
   neta), el producto vuelve a "listado".
5. **Multi-cantidad / venta en lote** — ¿1 ref = 1 unidad física (dos idénticos =
   dos productos)? ¿un precio repartido entre N refs? ¿o solo documentar que no se
   soporta?
6. **Producto vendido SIN "Subido"** — ¿la fila nace al confirmar la ficha (cuando
   se genera la ref) y "Subido" solo marca plataforma, o "Vendido" puede crear la
   fila?
7. **Qué precio se congela** — "Subido" congela un snapshot (precio+plataforma+
   ref+coste); si Diego baja el precio después (en Export o en la propia
   plataforma, que la app no ve), ¿cuál queda?
8. **Excel: `.xlsx` vs CSV**, y **regenerar vs acumular** — el .xlsx es un INFORME
   generado (nombre con fecha), **SQLite es la verdad**; las ediciones en el .xlsx
   se pierden (decirlo en la UI).
9. **Cuándo se asigna la ref** (sección D).
10. **Idempotencia** de "Subido" (upsert por producto×plataforma, no dos filas).

---

## NO REABRIR / PROHIBIDO
- **Automatizar la publicación o el retirado** (Selenium/scraping de formularios,
  extensiones que autorellenen o borren anuncios) → baneo de la cuenta que ES el
  negocio. Todo lo de Finanzas es registro LOCAL + recordatorios; jamás toca las
  plataformas.
- **Leer la cuenta de Diego** (visitas, favoritos, mensajes, ventas) — prohibido
  tocar la cuenta, no solo publicar. El precio de venta lo teclea él.
- **Calcular comisiones/tarifas automáticamente** — frágil, cambian; manual y
  opcional.
- Búsqueda por imagen/Lens, VLM local, multi-usuario/cloud — ya descartados y
  medidos en fases anteriores.

## Pendientes menores (no bloquean el plan)
- El GATE real (cronometrar un export, ~285 s) — la métrica que dice si esto
  ahorra tiempo, aún sin medir.
- Precio en Vinted (búsqueda tras Datadome).
- `test_curar::test_render_sin_excepcion` flaky (timeout `AppTest`).
- `género` → título/descripción, si Diego lo quiere ahí.
