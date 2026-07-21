"""Tests de `ui/finanzas.py` — la 5ª PANTALLA (superficie PERSISTENCIA: los
importes son dinero; pantalla que Diego TOCA CON LAS MANOS -> `AppTest`
obligatorio, `[INC-006]`, `change-loop.md` §C4/`[INC-028]`: todo botón nuevo
lleva un test que lo PULSA de verdad, no sólo que renderiza).

Qué se prueba, y por qué así:
- El dashboard: total vendido + beneficio bruto, con la etiqueta HONESTA fija
  ("Beneficio bruto (sin comisiones ni envío...)").
- El botón "Vendido" (con precio auto-rellenado desde la publicación con
  precio elegido en Export) -> `store.marcar_vendido` de verdad.
- "Deshacer venta" -> `store.deshacer_venta` de verdad (la venta desaparece).
- "Devolución" -> `store.marcar_devuelta` de verdad (la fila se conserva,
  estado='devuelta').
- El recordatorio B2 de retirar del OTRO sitio cuando hay publicación en
  ambas plataformas.
- "Exportar a Excel" -> genera un `.xlsx` de verdad en `data/exports/`.
- Filtro por estado y buscador de texto.

Cada test monta un `LoteStore` real sobre `tmp_path` (nunca un mock del
store: la persistencia real es la superficie que se está probando) con 1-2
productos ya con AGRUPACIÓN confirmada, algunos "Subidos" -- igual que
`tests/test_export_ui.py`.
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from core.store import Foto, LoteStore


# ============================================================================
# Setup: lote + producto(s) con agrupación confirmada + "Subido".
# ============================================================================


def _crear_producto(store: LoteStore, lote_id: str, nombre_foto: str) -> str:
    carpeta = store.lotes_dir / lote_id
    ruta = carpeta / nombre_foto
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(b"contenido-falso-de-foto")
    (foto_id,) = store.añadir_fotos(lote_id, [Foto(ruta=str(ruta), hash=f"hash-{nombre_foto}")])
    (producto_id,) = store.guardar_agrupacion(lote_id, [[foto_id]])
    store.confirmar_producto(producto_id)
    return producto_id


def _extraccion_con_titulo(titulo: str) -> dict:
    return {
        "campos": {
            "titulo": {
                "valor": titulo,
                "fuente": "inferido",
                "confianza": "media",
                "evidencia": None,
                "propuesta": None,
            }
        },
        "coste_usd": 0.0,
        "fallos": [],
        "aviso_coherencia": None,
    }


def _script(data_dir: str) -> None:
    from pathlib import Path as _Path

    from core.store import LoteStore as _LoteStore
    from ui import finanzas as _finanzas

    _finanzas.render(_LoteStore(data_dir=_Path(data_dir)))


# ============================================================================
# 1. Dashboard vacío (sin actividad financiera todavía).
# ============================================================================
def test_sin_actividad_pinta_dashboard_a_cero(tmp_path: Path):
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote finanzas", "C:/fotos/origen")
    _crear_producto(store, lote_id, "IMG_0.jpg")  # ni subido ni vendido

    at = AppTest.from_function(_script, args=(str(tmp_path),)).run()
    assert not at.exception, at.exception

    metricas = {m.label: m.value for m in at.metric}
    assert metricas["Total vendido"] == "0.00 €"
    assert metricas["Beneficio bruto total"] == "0.00 €"
    captions = " ".join(c.value for c in at.caption)
    assert "Beneficio bruto (sin comisiones ni envío" in captions
    assert at.info  # "No hay ningún producto que mostrar con este filtro"


# ============================================================================
# 2. Botón "Vendido" -> marca de verdad en el store, con el precio
#    auto-rellenado desde la publicación de Export.
# ============================================================================
def test_boton_vendido_marca_la_venta_con_precio_auto_rellenado(tmp_path: Path):
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote finanzas", "C:/fotos/origen")
    pid = _crear_producto(store, lote_id, "IMG_0.jpg")
    store.guardar_extraccion(pid, _extraccion_con_titulo("Sudadera Reebok XXL"))
    store.registrar_subido(pid, "wallapop", precio_elegido_cents=2000)

    at = AppTest.from_function(_script, args=(str(tmp_path),)).run()
    assert not at.exception, at.exception

    # Auto-rellenado: el precio ya sale a 20.00 sin que Diego teclee nada.
    campo_precio = next(t for t in at.text_input if t.key == f"finanzas_{pid}_precio")
    assert campo_precio.value == "20.00"

    boton = next(b for b in at.button if b.key == f"finanzas_{pid}_btn_vendido")
    at = boton.click().run()
    assert not at.exception, at.exception

    ventas = store.cargar_ventas()
    fila = next(f for f in ventas if f["producto_id"] == pid)
    assert fila["venta"] is not None
    assert fila["venta"]["estado"] == "vendida"
    assert fila["venta"]["precio_final_cents"] == 2000
    assert fila["venta"]["plataforma_venta"] == "wallapop"

    # El dashboard ya refleja la venta.
    metricas = {m.label: m.value for m in at.metric}
    assert metricas["Total vendido"] == "20.00 €"


def test_boton_vendido_con_precio_invalido_no_rompe_y_avisa(tmp_path: Path):
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote finanzas", "C:/fotos/origen")
    pid = _crear_producto(store, lote_id, "IMG_0.jpg")
    # Sin publicación ni venta el producto NI SIQUIERA aparece en el ledger
    # (`store.cargar_ventas`, contrato "al menos una publicación o venta") --
    # se sube sin precio elegido para que el campo nazca vacío.
    store.registrar_subido(pid, "wallapop")

    at = AppTest.from_function(_script, args=(str(tmp_path),)).run()
    assert not at.exception

    campo_precio = next(t for t in at.text_input if t.key == f"finanzas_{pid}_precio")
    at = campo_precio.set_value("no soy un numero").run()

    boton = next(b for b in at.button if b.key == f"finanzas_{pid}_btn_vendido")
    at = boton.click().run()
    assert not at.exception, at.exception
    assert at.error
    assert "inválido" in " ".join(e.value for e in at.error).lower()

    # No se marcó nada en el store: sigue sin venta (sólo la publicación).
    ventas = store.cargar_ventas()
    fila = next(f for f in ventas if f["producto_id"] == pid)
    assert fila["venta"] is None


# ============================================================================
# 3. "Deshacer venta" -> el store ya no tiene la venta.
# ============================================================================
def test_boton_deshacer_venta_revierte_de_verdad(tmp_path: Path):
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote finanzas", "C:/fotos/origen")
    pid = _crear_producto(store, lote_id, "IMG_0.jpg")
    store.marcar_vendido(pid, precio_final_cents=1500, plataforma_venta="wallapop")

    at = AppTest.from_function(_script, args=(str(tmp_path),)).run()
    assert not at.exception, at.exception
    assert at.success  # "✅ Vendido por 15.00 € en Wallapop"

    boton = next(b for b in at.button if b.key == f"finanzas_{pid}_btn_deshacer")
    at = boton.click().run()
    assert not at.exception, at.exception

    ventas = store.cargar_ventas()
    assert ventas == []  # sin publicación ni venta, ya no aparece en el ledger


# ============================================================================
# 4. "Devolución" -> la fila se CONSERVA con estado devuelta.
# ============================================================================
def test_boton_devolucion_conserva_la_fila_como_devuelta(tmp_path: Path):
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote finanzas", "C:/fotos/origen")
    pid = _crear_producto(store, lote_id, "IMG_0.jpg")
    store.marcar_vendido(pid, precio_final_cents=1500, plataforma_venta="wallapop")

    at = AppTest.from_function(_script, args=(str(tmp_path),)).run()
    assert not at.exception, at.exception

    boton = next(b for b in at.button if b.key == f"finanzas_{pid}_btn_devolucion")
    at = boton.click().run()
    assert not at.exception, at.exception

    ventas = store.cargar_ventas()
    fila = next(f for f in ventas if f["producto_id"] == pid)
    assert fila["venta"]["estado"] == "devuelta"
    assert fila["beneficio_bruto_cents"] is None

    # El dashboard ahora lo muestra como DEVUELTA (no como venta activa) y ya
    # no ofrece un segundo botón de "Devolución" para la misma fila.
    assert any("DEVUELTA" in w.value for w in at.warning)
    assert not any(b.key == f"finanzas_{pid}_btn_devolucion" for b in at.button)


# ============================================================================
# 5. B2 — recordatorio de retirar del OTRO sitio.
# ============================================================================
def test_recordatorio_retirar_del_otro_sitio(tmp_path: Path):
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote finanzas", "C:/fotos/origen")
    pid = _crear_producto(store, lote_id, "IMG_0.jpg")
    store.registrar_subido(pid, "wallapop", precio_elegido_cents=2000)
    store.registrar_subido(pid, "vinted", precio_elegido_cents=2000)
    store.marcar_vendido(pid, precio_final_cents=2000, plataforma_venta="wallapop")

    at = AppTest.from_function(_script, args=(str(tmp_path),)).run()
    assert not at.exception, at.exception

    textos_warning = " ".join(w.value for w in at.warning)
    assert "RETIRAR" in textos_warning
    assert "Vinted" in textos_warning


def test_sin_publicar_en_dos_plataformas_no_hay_recordatorio(tmp_path: Path):
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote finanzas", "C:/fotos/origen")
    pid = _crear_producto(store, lote_id, "IMG_0.jpg")
    store.registrar_subido(pid, "wallapop", precio_elegido_cents=2000)
    store.marcar_vendido(pid, precio_final_cents=2000, plataforma_venta="wallapop")

    at = AppTest.from_function(_script, args=(str(tmp_path),)).run()
    assert not at.exception, at.exception

    textos_warning = " ".join(w.value for w in at.warning)
    assert "RETIRAR" not in textos_warning


# ============================================================================
# 6. "Exportar a Excel" -> genera de verdad un .xlsx en disco.
# ============================================================================
def test_boton_exportar_excel_genera_fichero_real(tmp_path: Path):
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote finanzas", "C:/fotos/origen")
    pid = _crear_producto(store, lote_id, "IMG_0.jpg")
    store.marcar_vendido(pid, precio_final_cents=1500, plataforma_venta="wallapop")

    at = AppTest.from_function(_script, args=(str(tmp_path),)).run()
    assert not at.exception, at.exception

    boton = next(b for b in at.button if b.key == "finanzas_btn_exportar")
    at = boton.click().run()
    assert not at.exception, at.exception

    assert at.success
    codigos = [c.value for c in at.code]
    assert len(codigos) == 1
    ruta_generada = Path(codigos[0])
    assert ruta_generada.exists()
    assert ruta_generada.suffix == ".xlsx"
    assert ruta_generada.parent == tmp_path / "exports"


# ============================================================================
# 7. Filtro por estado + buscador de texto.
# ============================================================================
def test_filtro_no_vendido_oculta_los_ya_vendidos(tmp_path: Path):
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote finanzas", "C:/fotos/origen")
    p_vendido = _crear_producto(store, lote_id, "IMG_0.jpg")
    p_pendiente = _crear_producto(store, lote_id, "IMG_1.jpg")
    store.marcar_vendido(p_vendido, precio_final_cents=1000, plataforma_venta="wallapop")
    store.registrar_subido(p_pendiente, "wallapop", precio_elegido_cents=1200)

    at = AppTest.from_function(_script, args=(str(tmp_path),)).run()
    assert not at.exception

    selector = next(s for s in at.selectbox if s.key == "finanzas_filtro_estado")
    at = selector.set_value("No vendido").run()
    assert not at.exception, at.exception

    assert not any(b.key == f"finanzas_{p_vendido}_btn_deshacer" for b in at.button)
    assert any(b.key == f"finanzas_{p_pendiente}_btn_vendido" for b in at.button)


def test_buscador_por_titulo_filtra(tmp_path: Path):
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote finanzas", "C:/fotos/origen")
    p1 = _crear_producto(store, lote_id, "IMG_0.jpg")
    p2 = _crear_producto(store, lote_id, "IMG_1.jpg")
    store.guardar_extraccion(p1, _extraccion_con_titulo("Sudadera Reebok XXL"))
    store.guardar_extraccion(p2, _extraccion_con_titulo("Camiseta Nike M"))
    store.registrar_subido(p1, "wallapop")
    store.registrar_subido(p2, "wallapop")

    at = AppTest.from_function(_script, args=(str(tmp_path),)).run()
    assert not at.exception

    buscador = next(t for t in at.text_input if t.key == "finanzas_buscar")
    at = buscador.set_value("reebok").run()
    assert not at.exception, at.exception

    assert any(b.key == f"finanzas_{p1}_btn_vendido" for b in at.button)
    assert not any(b.key == f"finanzas_{p2}_btn_vendido" for b in at.button)
