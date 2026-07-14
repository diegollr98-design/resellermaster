# Sessions Log — RESELLERMASTER

Bitácora por hito. **Más reciente arriba.** Mantener ≤ 100 líneas: las entradas viejas se archivan en `docs/sessions-log-archive.md` (fuera del contexto) vía `/optimize` Paso 5c.

**Plantilla de entrada:**
```
## vX.Y — fecha — <título> <✅|🔴>
**Qué se hizo:** ...
**Incidentes:** [id] del ledger, si los hubo
**Verificación:** qué se ejecutó y qué demostró (salida real, no "reportó que OK")
**Pendiente:** ...
```

---

## v0.4 — 2026-07-14 — Fase 1 CERRADA: agrupación + curado, lote real confirmado ✅
**Qué se hizo:** `core/grouping.py` reescrito de cero (v5, "el reloj puede PARTIR pero no CONFIRMAR" — cero fusiones sobre las 33 fotos reales, umbral 15 s). La pantalla de curado pasó por 5 rondas de `listing-audit`: el layout de 2 columnas de Diego cazó 4 BLOQUEANTE seguidos (el fallo estaba en el DISEÑO, no en el código, `[INC-008]`), un panel adversarial de 21 agentes eligió **la CREMALLERA CON PESTILLO** (la frontera como unidad; fusionar dos productos lejanos es INEXPRESABLE, no sólo avisado). Features de curado: dividir grupo, unir vecinos, eliminar foto (mover a `descartadas/`, recuperable). **Diego curó y confirmó su primer lote real.**
**Incidentes:** INC-005 (confianza anti-correlacionada con el riesgo), INC-006 (el bug que crashea es el ruidoso; el gemelo silencioso es el peligroso), INC-007 (OCR medido y descartado como clasificador de tipo de foto; 3ª vez del encuadre visión-vs-texto), INC-008 (4 rondas de parcheo = rediseñar), INC-009 (degradación hacia fusionar en el caso de fallo).
**Verificación:** 280 tests verdes, ruff limpio, la app arranca sin traceback. El gate (cerrar 6 costuras → 7 productos) verde sobre las fotos reales. Doble `listing-audit` + verificación en cada superficie sensible. Golden set recuperado tras un borrado accidental de `fotos/` (copias en `data/lotes/`).
**Pendiente:** **Fase 2** (extracción de atributos + precio) en SESIÓN FRESCA con `docs/seeds/fase-2.md`. Decisión clave de Diego: NO da ground truth de atributos → el `/eval` pasa a medir alucinación adversarialmente (legibilidad en el píxel) + su ojo en la revisión. Correr `/optimize` (varios incidentes ya ganaron su regla). Falta preguntar a Diego los segundos-hasta-publicar reales del lote curado.

## v0.1 — 2026-07-13 — Infra agéntica instalada ✅
**Qué se hizo:** repo `git init` en `D:\Resellermaster`. Se replicó el harness de Diego (extraído de ecxm-ops, pumpfun-bot y SEKURA) y se adaptó al modo de fallo de este proyecto: `CLAUDE.md` (portada + índice), `.claude/settings.json` (permisos), `incident-ledger.md` (activa el detector global `optimize-nudge.sh` por opt-in), 4 agentes (`engineer`, `bug-hunter`, `listing-audit`, `flow-qa`), 3 skills (`/optimize`, `/eval`, `/run`), 7 ficheros de reglas. Pieza nueva respecto a los otros repos: **`truth-loop.md`** — el loop que impide que el pipeline invente atributos o precios, con el golden set como gate con dientes.
**Incidentes:** ninguno.
**Verificación:** hardware medido (`RTX 3050 Laptop, 4 GB VRAM, 16 GB RAM, i5-11400H`) → **visión local descartada con dato**, no por intuición. `git status` limpio tras el commit inicial.
**Pendiente:** sesión de planificación del producto (2ª tanda de preguntas a Diego). Sin decidir: proveedor de visión (se decide con `/eval`, no antes), estrategia de precio por comparables, taxonomía de campos Wallapop/Vinted (`rules/product.md` está vacío a propósito). **Cero código de producto todavía** — así lo pidió Diego.
