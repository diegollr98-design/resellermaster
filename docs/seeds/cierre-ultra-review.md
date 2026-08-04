# Cierre — Ultra-review multiagente + hardening · SEED (sesión FRESCA)

> PASO 0 OBLIGATORIO: invoca `/seed-review` sobre este SEED antes de tocar nada.

> **Objetivo:** dejar RESELLERMASTER **cerrado — sin bugs, lo más óptimo
> posible** — con un análisis adversarial multi-agente, ANTES del portfolio HTML.
> NO es una fase de features nuevas: es un **gate de calidad sobre lo ya
> construido**. No re-litigues lo hecho; intenta ROMPERLO y, lo que sobreviva,
> déjalo documentado como sólido.

## Prompt de arranque
```
Eres el orquestador ("papá oso") de RESELLERMASTER. Lee CLAUDE.md, .claude/rules/
(decision-making, truth-loop, change-loop, architecture, product, file-organization,
sessions-log) y .claude/incident-ledger.md. RECONCILIA el estado contra el repo
(git log, el código, pytest) antes de nada — no te fíes de este seed.

PASO 0 (ver la primera línea del cuerpo): invoca /seed-review antes de tocar nada,
muestra el veredicto y procede según él. Lo dispara también la regla global
~/.claude/CLAUDE.md; el PASO 0 embebido es belt-and-suspenders por si esa config no
está cargada.

Después: es una sesión de CIERRE POR AUDITORÍA ADVERSARIAL (no features). Orquesta
listing-audit/bug-hunter en paralelo sobre las superficies sensibles para intentar
PILLAR bugs/mentiras del pipeline, valida cada veredicto por EJECUCIÓN, y corre /eval
como gate antes de cerrar cualquier cambio. El objetivo es 100% objetividad, romper
lo que puedas.
```

## Estado reconciliado (contra el repo @ `beb8973`, 2026-08-04 — RE-VERIFÍCALO)
- **5 fases implementadas.** Fase 5 (Finanzas) + **borrado de lotes** DONE. `core/store.py` en **v3** (migración additiva, `ventas`/`referencia`/`coste`/`publicaciones`/`movimientos`).
- **Suite: 899 passed, 1 skipped.** El skip es `test_extract_golden.py:523` (medición VLM-real, opt-in que gasta API, `§15`) — **no es un fallo**. El `test_curar` flaky quedó resuelto (`timeout=30` en `AppTest`).
- **Árbol limpio.** CLAUDE.md v0.11, ledger al día (`[INC-030b]` como hecho de refuerzo de `§19`, no promueve regla nueva).
- Coste/producto sin cambios (Finanzas y borrado hacen 0 llamadas al LLM).

## Vehículos del cierre (complementarios)
1. **`/code-review ultra`** — lo dispara **Diego** (facturado), revisión multi-agente en la nube del diff de la rama. El orquestador NO lo lanza.
2. **Auditoría por superficie** — el orquestador orquesta `listing-audit`/`bug-hunter` en paralelo. Es un audit del **ESTADO**, no del diff. Todo hallazgo → `bug-hunter` (causa raíz con evidencia) → el orquestador VALIDA por ejecución → fix → test red→green → `/eval`.

## Qué auditar por superficie SENSIBLE (`truth-loop.md §B`)
- **PERSISTENCIA — `store.py` v3 + Finanzas (la más nueva, la más crítica):** migración additiva contra un lote VIEJO (que abra sin romper); el **snapshot inmutable de `ventas`** (editar coste/ficha tras vender NO muta el beneficio histórico); idempotencia de Subido/Vendido/undo/devolución; `borrar_lote` (orden FK hijos→padres, **guarda de dinero** que bloquea si hay ventas, reset del contador solo con DB vacía); que **NADA de dinero viva en `campos`** (lo sobreescriben `guardar_extraccion`/`confirmar_ficha`); atomicidad (`_transaccion`) y crash-a-mitad.
- **EXTRACCIÓN / truth-loop — `extract.py`, prompts:** `fuente=foto` SOLO si el valor está en el píxel citado (`§17`); ningún atributo afirmado sin píxel; badges 📷/🧠; `/eval` mide la **tasa de alucinación** (métrica primaria).
- **PRECIO — `pricing.py`:** cohorte débil = catálogo entero disfrazado (`[INC-027]`); "mediana de N parecidos", nunca "el precio de tu producto"; sin `n≥5` → `None` + motivo.
- **EXPORT — `export.py`:** sanitizador con dientes (`validar_texto`); la **ref inyectada** pasa en AMBAS plataformas; traducción con SIGNO (`[INC-017]`: presentar peor es seguro, mejor es mentira).
- **AGRUPACIÓN — `grouping.py`:** el reloj puede PARTIR pero no CONFIRMAR; sesgo a sobre-cortar; sin EXIF avisa.

## Gates
- **`/eval` (golden set)** ANTES de cerrar cualquier cambio sensible que salga de la auditoría. Gasta API (`§15`) — opt-in explícito.
- **Verde local ≠ ficha correcta** (`change-loop §C4`): la señal que cuenta es `/eval` + el ojo de Diego. Todo botón nuevo → `AppTest` que lo PULSA.

## Cabos conocidos (NO son bugs — decisiones/mediciones pendientes)
- **EL GATE REAL (el más importante):** Diego cronometra un export real (baseline ~285 s) — la métrica primaria del proyecto (segundos-hasta-publicar), **sin medir tras 5 fases**. + **gate presencial de la Fase 5**: registrar una venta real de punta a punta (Subido → Vendido → Excel).
- Precio en **Vinted** (búsqueda tras Datadome).
- `género` → título/descripción, si Diego lo quiere ahí.
- Idea de producto de Diego (FUTURO, no ahora): "solo envíos, quitar venta mano a mano".

## NO REABRIR (medido y descartado)
Búsqueda por imagen/Lens · VLM local · visión en el agrupado · composición por búsqueda externa · automatizar publicación/retirada (baneo) · leer la cuenta de Diego · calcular comisiones automáticamente.

## DESPUÉS del cierre: PORTFOLIO HTML
Una vez el repo esté CLEAN y verificado, crear un HTML de portfolio (herramienta **Artifact**, autocontenido) al estilo del **`portfolio-fable-ultra`** de pumpfun-bot. **Diego aporta ese HTML de referencia** para clavar el formato (vive en otro repo). Narrativa de sobra: el Loop de Verdad (anti-alucinación), las decisiones MEDIDAS (0 alucinaciones de Haiku sobre 33 fotos, la cremallera con pestillo, las 5 fases), y la propia orquestación multi-agente Opus/Sonnet.
