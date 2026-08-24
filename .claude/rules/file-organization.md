# Organización de Archivos — RESELLERMASTER

Cualquier archivo nuevo va a su carpeta. Si no existe, créala. **Nunca dejar archivos sueltos en la raíz.**

```
app.py                    ← entrypoint Streamlit (SOLO renderiza; cero lógica de negocio)
core/
  llm.py                  ← COSTURA 1: único punto de llamada a cualquier proveedor LLM/visión
  pricing.py              ← COSTURA 2: precio desde comparables. Nunca desde el LLM
  schema.py               ← COSTURA 3: campos por plataforma/categoría. Fuente de verdad
  grouping.py             ← clustering de fotos por producto
  extract.py              ← foto(s) → atributos, contra el schema
  images.py               ← orden, rotación, dedup, renombrado, export
  store.py                ← persistencia del lote (SQLite + disco). Nada vive solo en session_state
ui/                       ← componentes Streamlit, un módulo por pestaña/paso
tests/
  golden/                 ← GOLDEN SET: productos reales con ground truth verificado por Diego
  test_*.py
data/
  lotes/                  ← lotes de trabajo (gitignored)
  cache/                  ← caché de llamadas LLM por hash de imagen (gitignored, NUNCA borrar sin permiso)
  exports/                ← CSV/JSON generados (gitignored)
docs/
  seeds/                  ← seeds por fase (handoff a sesión fresca)
  sessions-log-archive.md ← bitácora fría
.claude/
  rules/                  ← instrucciones modulares
  agents/                 ← engineer, bug-hunter, listing-audit, flow-qa
  skills/                 ← /optimize, /eval, /run
```

**Reglas:**
- Toda llamada a un proveedor LLM pasa por `core/llm.py` (costura 1). Todo precio sale de `core/pricing.py` (costura 2). Todo campo de ficha está declarado en `core/schema.py` (costura 3). **Nada las saltea.**
- `app.py` y `ui/` son **desechables**: sólo renderizan estado. Si hay una decisión de negocio ahí, está en el sitio equivocado.
- El estado de un lote se escribe en `core/store.py` → disco. **`st.session_state` es una caché, no la verdad** — un rerun de Streamlit no puede costarle a Diego 2 horas de curado.
- Fotos y datos del usuario **nunca** se commitean (ver `.gitignore`). El golden set es la excepción **parcial**: el ground truth (`tests/golden/*.json`) sí se versiona — es el gate —; las fotos que describe **no** (viven en `fotos/`, gitignored). En un clon, los tests que las necesitan skipean diciendo por qué.
- Secretos sólo en `.env` (gitignored). Hay un `.env.example` con las claves vacías.
- Referencia voluminosa (taxonomías de categorías, tablas de tallas) → `docs/`, no en `CLAUDE.md`.
