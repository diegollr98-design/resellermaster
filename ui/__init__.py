"""RESELLERMASTER — ui/: pantallas Streamlit.

Regla dura (`.claude/rules/file-organization.md`): este paquete y `app.py`
son DESECHABLES — sólo renderizan estado que ya vive en `core/`. Ninguna
decisión de negocio (qué es un grupo, qué vale un campo, qué precio tiene
un producto) se toma aquí; sólo se pinta y se llama a `core/`.
"""
