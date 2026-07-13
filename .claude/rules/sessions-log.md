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

## v0.1 — 2026-07-13 — Infra agéntica instalada ✅
**Qué se hizo:** repo `git init` en `D:\Resellermaster`. Se replicó el harness de Diego (extraído de ecxm-ops, pumpfun-bot y SEKURA) y se adaptó al modo de fallo de este proyecto: `CLAUDE.md` (portada + índice), `.claude/settings.json` (permisos), `incident-ledger.md` (activa el detector global `optimize-nudge.sh` por opt-in), 4 agentes (`engineer`, `bug-hunter`, `listing-audit`, `flow-qa`), 3 skills (`/optimize`, `/eval`, `/run`), 7 ficheros de reglas. Pieza nueva respecto a los otros repos: **`truth-loop.md`** — el loop que impide que el pipeline invente atributos o precios, con el golden set como gate con dientes.
**Incidentes:** ninguno.
**Verificación:** hardware medido (`RTX 3050 Laptop, 4 GB VRAM, 16 GB RAM, i5-11400H`) → **visión local descartada con dato**, no por intuición. `git status` limpio tras el commit inicial.
**Pendiente:** sesión de planificación del producto (2ª tanda de preguntas a Diego). Sin decidir: proveedor de visión (se decide con `/eval`, no antes), estrategia de precio por comparables, taxonomía de campos Wallapop/Vinted (`rules/product.md` está vacío a propósito). **Cero código de producto todavía** — así lo pidió Diego.
