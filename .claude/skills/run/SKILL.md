---
name: run
description: Levanta la app Streamlit de RESELLERMASTER y verifica que arranca limpia. Úsalo para comprobar un cambio en la app real, no sólo en los tests.
allowed-tools: Bash, Read, Glob, Grep
---

# /run — Levantar la app

Verificar un cambio en la app **real**, no sólo en los tests. `pytest` verde y la app rota es un estado perfectamente posible en Streamlit.

## Pasos

1. **Comprobar sintaxis primero** (barato, caza el 90%):
   ```bash
   python -m compileall -q core ui app.py && ruff check .
   ```

2. **Arrancar en headless con timeout** y capturar la salida:
   ```bash
   streamlit run app.py --server.headless true --server.port 8501
   ```
   Correr en background. Un arranque limpio no imprime tracebacks. **Un `Traceback` en el arranque = fallo, aunque la página cargue** (Streamlit renderiza parcialmente y esconde el error abajo).

3. **Verificar el flujo mínimo** si el cambio lo afecta: cargar un lote de prueba de `tests/golden/`, comprobar que agrupa, que muestra los campos con su procedencia y que los botones de copiar existen.

4. **Reportar la salida REAL**, no un "arranca bien". Pega el log.

## Gotchas de este entorno (Windows)
- **Encoding:** prefijar `PYTHONUTF8=1` si aparecen `UnicodeDecodeError` con nombres de fichero de fotos con tildes o emojis. Los móviles ponen de todo en los nombres.
- **PowerShell:** `;` no `&&` para encadenar. El Bash tool sí acepta `&&`.
- **No dejar el servidor colgado:** si arrancas Streamlit en background para verificar, **páralo al terminar**. Un puerto ocupado hace que el siguiente arranque falle con un error que no tiene nada que ver con tu cambio (y se diagnostica mal — ver el precedente `[INC-006]` de SEKURA: un `build` sobre un dev server levantado produjo un `ReferenceError` **falso** y costó una sesión de depuración de un bug que no existía).

## REGLAS
- **Nunca** reportar "la app funciona" sin haber visto la salida del arranque.
- **Nunca** dejar procesos de Streamlit huérfanos.
- Si la app arranca pero el cambio toca una superficie sensible → **`/run` no basta**: corre `/eval`.
