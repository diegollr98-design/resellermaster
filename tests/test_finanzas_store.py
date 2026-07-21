"""Tests de la Fase 5 FINANZAS en core/store.py (superficie sensible: dinero).

Cubre la migración additiva v2->v3, la referencia humana (assign-once,
AUTOINCREMENT que no reutiliza tras DELETE), el "Subido" idempotente por
plataforma, la venta con snapshot congelado del coste (la prueba clave),
el undo reversible con rastro, la devolución, la agrupación por lote y el
ledger CROSS-LOTE.

Test de `store.py` PURO: replica el contrato de `campos` sin importar
`core.extract`.
"""

from __future__ import annotations

import sqlite3

import pytest

import core.store as store_mod
from core.store import (
    Foto,
    LoteStore,
    ProductoNoEncontradoError,
)


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


def _lote_con_productos(store: LoteStore, n: int, nombre: str = "Lote") -> tuple[str, list[str]]:
    """Crea un lote con `n` fotos, cada una en su propio producto (grupo de 1)."""
    lote_id = store.crear_lote(nombre, "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id
    fotos = []
    for i in range(n):
        ruta = carpeta / f"IMG_{i:04d}.jpg"
        ruta.write_bytes(b"contenido-falso-de-foto")
        fotos.append(Foto(ruta=str(ruta), hash=f"{nombre}-hash{i}", timestamp_exif=None))
    foto_ids = store.añadir_fotos(lote_id, fotos)
    productos = store.guardar_agrupacion(lote_id, [[fid] for fid in foto_ids])
    return lote_id, productos


def _extraccion_con_titulo(titulo: str) -> dict:
    return {
        "campos": {
            "titulo": {
                "valor": titulo,
                "fuente": "inferido",
                "confianza": "media",
                "evidencia": None,
                "propuesta": None,
            },
            "marca": {
                "valor": "Reebok",
                "fuente": "foto",
                "confianza": "media",
                "evidencia": {"fichero": "IMG_0001.jpg", "bbox": [1, 2, 3, 4]},
                "propuesta": None,
            },
        },
        "coste_usd": 0.004,
        "fallos": [],
        "aviso_coherencia": None,
    }


# --------------------------------------------------------------------------
# Migración additiva v2 -> v3
# --------------------------------------------------------------------------


def test_migracion_v2_a_v3_es_additiva_sin_perder_filas(tmp_path, monkeypatch):
    # Monta una DB REAL en v2 (la DDL real, no una copia a mano), poblada como
    # el flujo real: un lote, fotos, un producto agrupado y confirmado.
    monkeypatch.setattr(store_mod, "SCHEMA_VERSION", 2)
    store_v2 = LoteStore(data_dir=tmp_path)
    lote_id, productos = _lote_con_productos(store_v2, n=3)
    store_v2.confirmar_producto(productos[0])
    db_path = store_v2.db_path
    del store_v2

    # A v2 las columnas/tablas de finanzas todavía NO existen.
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(productos)")}
        assert "referencia" not in cols
        assert "coste_cents" not in cols
        tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "ventas" not in tablas and "publicaciones" not in tablas
    finally:
        conn.close()

    # Reabrir con el SCHEMA_VERSION real (3) migra additivamente.
    monkeypatch.setattr(store_mod, "SCHEMA_VERSION", 3)
    store_v3 = LoteStore(data_dir=tmp_path)

    # Ninguna fila v1/v2 se perdió y el lote sigue cargando.
    estado = store_v3.cargar_lote(lote_id)
    assert len(estado["fotos"]) == 3
    assert len(estado["productos"]) == 3
    confirmados = [pr for pr in estado["productos"] if pr["confirmado"]]
    assert len(confirmados) == 1

    # Las tablas nuevas ya funcionan sobre la DB migrada.
    n = store_v3.asignar_referencia(productos[0])
    assert n == 1
    store_v3.guardar_coste(productos[0], 500)

    conn = sqlite3.connect(store_v3.db_path)
    try:
        version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        assert version == 3
        cols = {r[1] for r in conn.execute("PRAGMA table_info(productos)")}
        assert {"referencia", "coste_cents"} <= cols
    finally:
        conn.close()


def test_lote_v2_viejo_default_coste_cents_es_cero(tmp_path, monkeypatch):
    monkeypatch.setattr(store_mod, "SCHEMA_VERSION", 2)
    store_v2 = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store_v2, n=1)
    del store_v2

    monkeypatch.setattr(store_mod, "SCHEMA_VERSION", 3)
    store_v3 = LoteStore(data_dir=tmp_path)
    conn = sqlite3.connect(store_v3.db_path)
    try:
        coste = conn.execute(
            "SELECT coste_cents FROM productos WHERE id = ?", (productos[0],)
        ).fetchone()[0]
        assert coste == 0
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Referencia humana: assign-once + nunca se reutiliza tras DELETE
# --------------------------------------------------------------------------


def test_asignar_referencia_assign_once_idempotente(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=1)

    n1 = store.asignar_referencia(productos[0])
    n2 = store.asignar_referencia(productos[0])
    assert n1 == n2

    conn = sqlite3.connect(store.db_path)
    try:
        filas = conn.execute(
            "SELECT COUNT(*) FROM referencia_seq WHERE producto_id = ?", (productos[0],)
        ).fetchone()[0]
        assert filas == 1  # una sola fila en la secuencia, no dos
    finally:
        conn.close()


def test_referencia_nunca_se_reutiliza_tras_delete(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=3)

    assert store.asignar_referencia(productos[0]) == 1
    assert store.asignar_referencia(productos[1]) == 2
    assert store.asignar_referencia(productos[2]) == 3

    # Borra el del medio "a mano" (raw connection, foreign_keys OFF por defecto),
    # como haría un futuro borrado de producto.
    conn = sqlite3.connect(store.db_path)
    try:
        conn.execute("DELETE FROM productos WHERE id = ?", (productos[1],))
        conn.commit()
    finally:
        conn.close()

    # Un producto nuevo (en otro lote) obtiene 4, jamás 2 ni 3 reciclados.
    _, mas = _lote_con_productos(store, n=1, nombre="Lote2")
    assert store.asignar_referencia(mas[0]) == 4


def test_asignar_referencia_producto_inexistente_falla(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    with pytest.raises(ProductoNoEncontradoError):
        store.asignar_referencia("producto-fantasma")


# --------------------------------------------------------------------------
# Coste (céntimos enteros, columna propia)
# --------------------------------------------------------------------------


def test_guardar_coste_persiste_y_sobrevive_a_extraccion(tmp_path):
    # El dinero NO puede vivir en `campos`: `guardar_extraccion` lo pisaría.
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=1)
    store.guardar_coste(productos[0], 750)
    store.guardar_extraccion(productos[0], _extraccion_con_titulo("Sudadera Reebok XXL"))

    conn = sqlite3.connect(store.db_path)
    try:
        coste = conn.execute(
            "SELECT coste_cents FROM productos WHERE id = ?", (productos[0],)
        ).fetchone()[0]
        assert coste == 750  # sigue ahí tras sobreescribir `campos`
    finally:
        conn.close()


def test_guardar_coste_negativo_falla(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=1)
    with pytest.raises(ValueError):
        store.guardar_coste(productos[0], -1)


def test_guardar_coste_producto_inexistente_falla(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    with pytest.raises(ProductoNoEncontradoError):
        store.guardar_coste("producto-fantasma", 100)


# --------------------------------------------------------------------------
# "Subido" por plataforma (idempotente)
# --------------------------------------------------------------------------


def test_registrar_subido_idempotente_no_duplica_ni_re_fecha(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=1)

    store.registrar_subido(productos[0], "wallapop", precio_elegido_cents=2000)
    conn = sqlite3.connect(store.db_path)
    try:
        subido_en_1 = conn.execute(
            "SELECT subido_en FROM publicaciones WHERE producto_id = ?", (productos[0],)
        ).fetchone()[0]
    finally:
        conn.close()

    # Segunda pulsada: NO duplica, NO re-fecha, pero refresca el precio.
    store.registrar_subido(productos[0], "wallapop", precio_elegido_cents=1800)

    conn = sqlite3.connect(store.db_path)
    try:
        filas = conn.execute(
            "SELECT subido_en, precio_elegido_cents FROM publicaciones WHERE producto_id = ?",
            (productos[0],),
        ).fetchall()
        assert len(filas) == 1
        assert filas[0][0] == subido_en_1  # subido_en del primero
        assert filas[0][1] == 1800  # snapshot refrescado
        movs = conn.execute(
            "SELECT COUNT(*) FROM movimientos WHERE producto_id = ? AND tipo = 'subido'",
            (productos[0],),
        ).fetchone()[0]
        assert movs == 1  # un solo movimiento 'subido'
    finally:
        conn.close()


def test_registrar_subido_dos_plataformas_conviven(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=1)
    store.registrar_subido(productos[0], "wallapop")
    store.registrar_subido(productos[0], "vinted")

    ventas = store.cargar_ventas()
    fila = next(f for f in ventas if f["producto_id"] == productos[0])
    plataformas = {p["plataforma"] for p in fila["publicaciones"]}
    assert plataformas == {"wallapop", "vinted"}


def test_registrar_subido_plataforma_invalida_falla(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=1)
    with pytest.raises(ValueError):
        store.registrar_subido(productos[0], "milanuncios")


def test_registrar_subido_congela_tasacion_como_json(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=1)
    tasacion = {"mediana_cents": 2000, "n": 12, "urls": ["https://wallapop.com/x"]}
    store.registrar_subido(productos[0], "wallapop", precio_elegido_cents=2000, tasacion=tasacion)

    ventas = store.cargar_ventas()
    fila = next(f for f in ventas if f["producto_id"] == productos[0])
    assert fila["publicaciones"][0]["tasacion"] == tasacion


def test_registrar_subido_producto_inexistente_falla(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    with pytest.raises(ProductoNoEncontradoError):
        store.registrar_subido("producto-fantasma", "wallapop")


# --------------------------------------------------------------------------
# Venta: el snapshot del coste CONGELADO (la prueba clave)
# --------------------------------------------------------------------------


def test_vendido_congela_el_coste_al_vender(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=1)
    p = productos[0]

    store.guardar_coste(p, 500)
    store.marcar_vendido(p, precio_final_cents=2000, plataforma_venta="wallapop")

    # Diego edita el coste DESPUÉS de vender: no debe mover el beneficio ya cerrado.
    store.guardar_coste(p, 900)

    ventas = store.cargar_ventas()
    fila = next(f for f in ventas if f["producto_id"] == p)
    # beneficio = 2000 - 500 (snapshot), NO 2000 - 900.
    assert fila["beneficio_bruto_cents"] == 1500


def test_marcar_vendido_re_marca_no_recongela_coste(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=1)
    p = productos[0]

    store.guardar_coste(p, 500)
    store.marcar_vendido(p, precio_final_cents=2000, plataforma_venta="wallapop")
    # Sube el coste y re-marca a otro precio: el coste snapshot NO se re-congela.
    store.guardar_coste(p, 900)
    store.marcar_vendido(p, precio_final_cents=2500, plataforma_venta="vinted")

    ventas = store.cargar_ventas()
    fila = next(f for f in ventas if f["producto_id"] == p)
    assert fila["venta"]["precio_final_cents"] == 2500
    assert fila["venta"]["plataforma_venta"] == "vinted"
    assert fila["beneficio_bruto_cents"] == 2000  # 2500 - 500 (primer snapshot manda)


def test_marcar_vendido_congela_titulo_y_referencia(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=1)
    p = productos[0]
    ref = store.asignar_referencia(p)
    store.guardar_extraccion(p, _extraccion_con_titulo("Sudadera Reebok XXL gris"))

    store.marcar_vendido(p, precio_final_cents=2000, plataforma_venta="wallapop")

    conn = sqlite3.connect(store.db_path)
    try:
        fila = conn.execute(
            "SELECT titulo_snap, referencia_snap FROM ventas WHERE producto_id = ?", (p,)
        ).fetchone()
        assert fila[0] == "Sudadera Reebok XXL gris"
        assert fila[1] == ref
    finally:
        conn.close()


def test_marcar_vendido_negativo_falla(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=1)
    with pytest.raises(ValueError):
        store.marcar_vendido(productos[0], precio_final_cents=-1, plataforma_venta="wallapop")


def test_marcar_vendido_producto_inexistente_falla(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    with pytest.raises(ProductoNoEncontradoError):
        store.marcar_vendido("producto-fantasma", precio_final_cents=100, plataforma_venta="wallapop")


# --------------------------------------------------------------------------
# Undo reversible con rastro / devolución
# --------------------------------------------------------------------------


def test_deshacer_venta_es_reversible_y_conserva_el_rastro(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=1)
    p = productos[0]

    store.registrar_subido(p, "wallapop")  # para que siga apareciendo en el ledger
    store.marcar_vendido(p, precio_final_cents=2000, plataforma_venta="wallapop")
    store.deshacer_venta(p)

    ventas = store.cargar_ventas()
    fila = next(f for f in ventas if f["producto_id"] == p)
    assert fila["venta"] is None  # ya no está vendida
    assert fila["beneficio_bruto_cents"] is None

    # El historial en `movimientos` (append-only) conserva ambos eventos.
    conn = sqlite3.connect(store.db_path)
    try:
        tipos = [
            r[0]
            for r in conn.execute(
                "SELECT tipo FROM movimientos WHERE producto_id = ? ORDER BY id", (p,)
            )
        ]
        assert "vendido" in tipos and "undo_venta" in tipos
    finally:
        conn.close()


def test_deshacer_venta_sin_venta_previa_falla(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=1)
    with pytest.raises(ProductoNoEncontradoError):
        store.deshacer_venta(productos[0])


def test_marcar_devuelta_conserva_la_fila(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=1)
    p = productos[0]

    store.marcar_vendido(p, precio_final_cents=2000, plataforma_venta="wallapop")
    store.marcar_devuelta(p)

    ventas = store.cargar_ventas()
    fila = next(f for f in ventas if f["producto_id"] == p)
    assert fila["venta"] is not None  # la fila se CONSERVA
    assert fila["venta"]["estado"] == "devuelta"
    assert fila["beneficio_bruto_cents"] is None  # devuelta no cuenta como beneficio

    conn = sqlite3.connect(store.db_path)
    try:
        movs = conn.execute(
            "SELECT COUNT(*) FROM movimientos WHERE producto_id = ? AND tipo = 'devuelta'", (p,)
        ).fetchone()[0]
        assert movs == 1
    finally:
        conn.close()


def test_marcar_devuelta_sin_venta_falla(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=1)
    with pytest.raises(ProductoNoEncontradoError):
        store.marcar_devuelta(productos[0])


# --------------------------------------------------------------------------
# Agrupación por lote de venta + ledger CROSS-LOTE
# --------------------------------------------------------------------------


def test_lote_de_venta_agrupa_varios_productos(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=2)

    store.marcar_vendido(
        productos[0], precio_final_cents=1500, plataforma_venta="wallapop", lote_venta_id="LV1"
    )
    store.marcar_vendido(
        productos[1], precio_final_cents=2500, plataforma_venta="wallapop", lote_venta_id="LV1"
    )

    ventas = store.cargar_ventas()
    lote_ids = {
        f["producto_id"]: f["venta"]["lote_venta_id"]
        for f in ventas
        if f["producto_id"] in productos
    }
    assert lote_ids[productos[0]] == "LV1"
    assert lote_ids[productos[1]] == "LV1"


def test_cargar_ventas_es_cross_lote(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_a, prods_a = _lote_con_productos(store, n=1, nombre="LoteA")
    lote_b, prods_b = _lote_con_productos(store, n=1, nombre="LoteB")

    store.registrar_subido(prods_a[0], "wallapop")
    store.marcar_vendido(prods_b[0], precio_final_cents=3000, plataforma_venta="vinted")

    ventas = store.cargar_ventas()
    por_id = {f["producto_id"]: f for f in ventas}
    assert prods_a[0] in por_id and prods_b[0] in por_id
    # Cada uno arrastra su lote de origen (distintos): es CROSS-LOTE de verdad.
    assert por_id[prods_a[0]]["lote_id"] == lote_a
    assert por_id[prods_b[0]]["lote_id"] == lote_b


def test_cargar_ventas_solo_incluye_productos_con_publicacion_o_venta(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=3)
    # Sólo el primero tiene actividad financiera.
    store.registrar_subido(productos[0], "wallapop")

    ventas = store.cargar_ventas()
    ids = {f["producto_id"] for f in ventas}
    assert ids == {productos[0]}


def test_cargar_ventas_incluye_referencia_titulo_y_coste(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=1)
    p = productos[0]
    ref = store.asignar_referencia(p)
    store.guardar_coste(p, 800)
    store.guardar_extraccion(p, _extraccion_con_titulo("Jersey Umbro M"))
    store.registrar_subido(p, "wallapop")

    ventas = store.cargar_ventas()
    fila = next(f for f in ventas if f["producto_id"] == p)
    assert fila["referencia"] == ref
    assert fila["titulo"] == "Jersey Umbro M"
    assert fila["coste_cents"] == 800
    assert fila["venta"] is None


def test_cargar_ventas_ordena_mas_reciente_primero(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=2)
    store.registrar_subido(productos[0], "wallapop")
    store.registrar_subido(productos[1], "wallapop")
    # El segundo producto tiene una venta posterior -> es el más reciente.
    store.marcar_vendido(productos[1], precio_final_cents=1000, plataforma_venta="wallapop")

    ventas = store.cargar_ventas()
    assert ventas[0]["producto_id"] == productos[1]


def test_cargar_ventas_vacio_sin_actividad(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    _lote_con_productos(store, n=2)  # productos sin subir ni vender
    assert store.cargar_ventas() == []


def test_venta_sobrevive_al_borrado_del_producto(tmp_path):
    # `ventas` no tiene FK a `productos`: una venta es dinero y debe sobrevivir
    # a un futuro borrado del producto, cayendo a sus snapshots congelados.
    store = LoteStore(data_dir=tmp_path)
    _, productos = _lote_con_productos(store, n=1)
    p = productos[0]
    store.asignar_referencia(p)
    store.guardar_coste(p, 400)
    store.guardar_extraccion(p, _extraccion_con_titulo("Camiseta Nike S"))
    store.marcar_vendido(p, precio_final_cents=1200, plataforma_venta="wallapop")

    # Borra el producto "a mano" (raw connection).
    conn = sqlite3.connect(store.db_path)
    try:
        conn.execute("DELETE FROM publicaciones WHERE producto_id = ?", (p,))
        conn.execute("DELETE FROM productos WHERE id = ?", (p,))
        conn.commit()
    finally:
        conn.close()

    ventas = store.cargar_ventas()
    fila = next(f for f in ventas if f["producto_id"] == p)
    assert fila["lote_id"] is None  # el producto ya no está
    assert fila["titulo"] == "Camiseta Nike S"  # snapshot congelado
    assert fila["coste_cents"] == 400
    assert fila["beneficio_bruto_cents"] == 800  # 1200 - 400, del snapshot
