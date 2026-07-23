"""Tests de `app.py` — la barra lateral (superficie PERSISTENCIA cuando se
borra un lote: `change-loop.md` §C4 / `[INC-028]` — todo BOTÓN nuevo lleva un
`AppTest` que lo PULSA de verdad, no sólo que renderiza la pantalla inicial).

`app._get_store` está decorado con `@st.cache_resource` y clava
`DEFAULT_DATA_DIR`; para el test se reasigna esa referencia del módulo a un
`LoteStore` sobre `tmp_path` — así el `AppTest` corre `app.main()` real contra
una base de datos aislada.
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from core.store import Foto, LoteStore


def _crear_lote_con_producto(store: LoteStore, nombre: str) -> tuple[str, str]:
    lote_id = store.crear_lote(nombre, "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id
    ruta = carpeta / "IMG_0000.jpg"
    ruta.write_bytes(b"contenido-falso-de-foto")
    (foto_id,) = store.añadir_fotos(lote_id, [Foto(ruta=str(ruta), hash=f"h-{nombre}")])
    (producto_id,) = store.guardar_agrupacion(lote_id, [[foto_id]])
    return lote_id, producto_id


def _script(data_dir: str) -> None:
    from pathlib import Path as _Path

    import app as _app
    from core.store import LoteStore as _LoteStore

    # Sortea el @st.cache_resource + DEFAULT_DATA_DIR: apunta el store a la
    # base de datos aislada del test.
    _app._get_store = lambda: _LoteStore(data_dir=_Path(data_dir))
    _app.main()


def _seleccionar_lote(at: AppTest, lote_id: str) -> AppTest:
    """Deja `lote_id` seleccionado en el selectbox de la barra lateral (el
    último lote creado sale primero, así que un lote concreto puede no ser el
    default)."""
    sel = next(s for s in at.selectbox if s.key == "sb_lote_id")
    if sel.value != lote_id:
        at = sel.set_value(lote_id).run()
    return at


def test_boton_eliminar_lote_borra_de_verdad_y_desaparece_del_selector(tmp_path: Path):
    store = LoteStore(data_dir=tmp_path)
    lote_a, _ = _crear_lote_con_producto(store, "LoteA")
    lote_b, _ = _crear_lote_con_producto(store, "LoteB")

    at = AppTest.from_function(_script, args=(str(tmp_path),)).run()
    assert not at.exception, at.exception

    at = _seleccionar_lote(at, lote_a)

    # Dos pasos: marcar el checkbox de confirmación habilita el botón.
    checkbox = next(c for c in at.checkbox if c.key == f"del_confirm_{lote_a}")
    at = checkbox.set_value(True).run()
    assert not at.exception, at.exception

    boton = next(b for b in at.button if b.key == f"del_btn_{lote_a}")
    at = boton.click().run()
    assert not at.exception, at.exception

    # El lote se borró de verdad en el store...
    ids_restantes = {lote["id"] for lote in store.listar_lotes()}
    assert lote_a not in ids_restantes
    assert lote_b in ids_restantes

    # ...y ya no aparece en el selectbox de la barra lateral.
    sel = next(s for s in at.selectbox if s.key == "sb_lote_id")
    assert lote_a not in sel.options
    assert sel.value == lote_b  # la guarda reasignó a un lote válido restante


def test_boton_eliminar_lote_con_venta_avisa_y_no_borra(tmp_path: Path):
    store = LoteStore(data_dir=tmp_path)
    lote_id, producto_id = _crear_lote_con_producto(store, "ConVenta")
    store.confirmar_producto(producto_id)
    store.marcar_vendido(producto_id, precio_final_cents=2000, plataforma_venta="wallapop")

    at = AppTest.from_function(_script, args=(str(tmp_path),)).run()
    assert not at.exception, at.exception
    at = _seleccionar_lote(at, lote_id)

    checkbox = next(c for c in at.checkbox if c.key == f"del_confirm_{lote_id}")
    at = checkbox.set_value(True).run()
    boton = next(b for b in at.button if b.key == f"del_btn_{lote_id}")
    at = boton.click().run()
    assert not at.exception, at.exception

    # No se borró (guardia de dinero) y se avisa con un error, no un traceback.
    assert lote_id in {lote["id"] for lote in store.listar_lotes()}
    assert at.error
    assert "venta" in " ".join(e.value for e in at.error).lower()


def test_borrar_el_unico_lote_deja_el_sidebar_sin_romper(tmp_path: Path):
    store = LoteStore(data_dir=tmp_path)
    lote_id, _ = _crear_lote_con_producto(store, "Unico")

    at = AppTest.from_function(_script, args=(str(tmp_path),)).run()
    assert not at.exception, at.exception

    checkbox = next(c for c in at.checkbox if c.key == f"del_confirm_{lote_id}")
    at = checkbox.set_value(True).run()
    boton = next(b for b in at.button if b.key == f"del_btn_{lote_id}")
    at = boton.click().run()

    # Sin lotes, el sidebar muestra el info de "no hay lote" y no revienta.
    assert not at.exception, at.exception
    assert store.listar_lotes() == []
    assert at.info
