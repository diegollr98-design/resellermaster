# Fase 1 — Costuras, ingesta y agrupación

## Objetivo
Que Diego pueda arrastrar una carpeta con las fotos mezcladas de varios productos, la app las agrupe **por producto** (proponiendo, no decidiendo), él confirme los grupos en dos vistazos, y ese trabajo **no se pierda nunca**. Cero LLM, cero API keys, cero coste.

## Precondiciones
- Python 3.13 (verificado). Sin `.env` todavía: esta fase no llama a ningún servicio.
- Fotos de ejemplo: las del golden set que está haciendo Diego (Fase 0). Mientras no existan, se prueba con fotos sintéticas + EXIF fabricado.

## Alcance
1. **`core/schema.py`** — la costura 3. Enum canónico interno de estado + **dos tablas de mapeo** (Wallapop / Vinted, literales exactos de `rules/product.md`). Dataclass `Campo` con `valor / fuente / evidencia / confianza` (`truth-loop.md` §A). Definición de qué campos pide cada plataforma × categoría.
2. **`core/images.py`** — leer EXIF (timestamp, orientación), normalizar rotación, hash perceptual, detectar duplicados exactos, ordenar y renombrar para export (20 fotos Vinted / 10 Wallapop).
3. **`core/grouping.py`** — agrupar por **timestamp EXIF** (señal primaria: las fotos de un producto se disparan seguidas) + hash perceptual como confirmación. Devuelve grupos **con su confianza**; los dudosos se marcan, no se cuelan.
4. **`core/store.py`** — SQLite + disco. El lote es **reanudable**: cerrar la app y volver continúa donde estaba. `st.session_state` es caché, nunca la verdad.
5. **`app.py` + `ui/`** — pantalla de ingesta y pantalla de confirmación de grupos. Sólo renderizan.

## Cómo
- Sonnet `engineer` en paralelo: `schema` / `images` / `store` son independientes. `grouping` depende de `images`; la UI depende de todo.
- **Sin dependencias pesadas**: Pillow para EXIF/imagen, `imagehash` o dHash a mano, `sqlite3` de stdlib, Streamlit. Nada de torch en esta fase.

## Verdad
Esta fase **no afirma nada del producto todavía** — no hay atributos. Pero sí toca dos superficies sensibles:
- **`agrupacion`** — una foto en el grupo equivocado es el fallo más caro y silencioso. La UI tiene que hacer el error **visible**: fotos del grupo juntas y grandes. El clustering **propone**; Diego **confirma**. Nunca se re-agrupa después de que confirme.
- **`persistencia`** — perder el curado de un lote es perder horas suyas.
→ Ambas pasan por `listing-audit` antes de cerrar.

## Coste
**0 €.** Ninguna llamada a ningún servicio. Si algún cambio de esta fase introduce una, es el cambio equivocado.

## Verificación
- `pytest` + `ruff check` limpios.
- `/run` — la app arranca sin traceback y carga un lote de prueba.
- **El gate de Diego:** arrastra fotos reales suyas y comprueba (a) que los grupos que propone son correctos, (b) que corregirlos es rápido, (c) que cierra la app, la reabre, y **no ha perdido nada**.

## Commit + gate
`feat(fase-1): ingesta, agrupacion por EXIF y persistencia del lote` → **PARAR** → Diego prueba → OK → generar `docs/seeds/fase-2.md`.
