"""Tests de core/store.py — persistencia del lote.

Cubre el ciclo completo (crear -> añadir fotos -> agrupar -> confirmar ->
cerrar -> reabrir -> todo sigue ahí) y una interrupción real a mitad de
una escritura (simula el proceso muriendo dentro de una transacción).
"""

from __future__ import annotations

import sqlite3

import pytest

from core.store import (
    AgrupacionBloqueadaError,
    Foto,
    FotoDuplicadaError,
    LoteNoEncontradoError,
    LoteStore,
    ProductoNoEncontradoError,
    RutaInvalidaError,
    SCHEMA_VERSION,
)


def _crear_lote_con_fotos(store: LoteStore, n: int = 4) -> tuple[str, list[str]]:
    lote_id = store.crear_lote("Lote de prueba", "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id
    fotos = []
    for i in range(n):
        ruta = carpeta / f"IMG_{i:04d}.jpg"
        ruta.write_bytes(b"contenido-falso-de-foto")
        fotos.append(
            Foto(ruta=str(ruta), hash=f"hash{i}", timestamp_exif=f"2026-07-13T10:0{i}:00")
        )
    foto_ids = store.añadir_fotos(lote_id, fotos)
    return lote_id, foto_ids


def _crear_lote_con_fotos_exif_mixto(
    store: LoteStore, timestamps: list[str | None]
) -> tuple[str, list[str]]:
    """Como `_crear_lote_con_fotos`, pero con control explícito de qué foto
    trae `timestamp_exif` y cuál llega `None` (el caso real: fotos pasadas
    por WhatsApp, EXIF borrado)."""
    lote_id = store.crear_lote("Lote de prueba", "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id
    fotos = []
    for i, ts in enumerate(timestamps):
        ruta = carpeta / f"IMG_{i:04d}.jpg"
        ruta.write_bytes(b"contenido-falso-de-foto")
        fotos.append(Foto(ruta=str(ruta), hash=f"hash{i}", timestamp_exif=ts))
    foto_ids = store.añadir_fotos(lote_id, fotos)
    return lote_id, foto_ids


# --------------------------------------------------------------------------
# Ciclo completo
# --------------------------------------------------------------------------


def test_ciclo_completo_crear_agrupar_confirmar_reabrir(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id, foto_ids = _crear_lote_con_fotos(store, n=4)

    # Agrupar en dos productos de 2 fotos cada uno.
    grupo_a = foto_ids[:2]
    grupo_b = foto_ids[2:]
    productos = store.guardar_agrupacion(lote_id, [grupo_a, grupo_b])
    assert len(productos) == 2

    # Diego confirma sólo el primer producto.
    store.confirmar_producto(productos[0])

    estado_antes = store.cargar_lote(lote_id)
    assert estado_antes["productos"][0]["confirmado"] is True
    assert estado_antes["productos"][1]["confirmado"] is False

    # --- "Cerrar la app": se destruye el objeto Python, nada vive ya en
    # memoria. "Reabrir": una instancia LoteStore nueva contra el mismo
    # directorio de datos, como haría un nuevo proceso de Streamlit.
    del store
    store2 = LoteStore(data_dir=tmp_path)

    estado_despues = store2.cargar_lote(lote_id)
    assert estado_despues["lote"]["id"] == lote_id
    assert len(estado_despues["fotos"]) == 4
    assert {p["id"] for p in estado_despues["productos"]} == set(productos)

    prod_a = next(p for p in estado_despues["productos"] if p["id"] == productos[0])
    prod_b = next(p for p in estado_despues["productos"] if p["id"] == productos[1])
    assert sorted(prod_a["fotos"]) == sorted(grupo_a)
    assert sorted(prod_b["fotos"]) == sorted(grupo_b)
    assert prod_a["confirmado"] is True
    assert prod_a["confirmado_en"] is not None
    assert prod_b["confirmado"] is False
    assert prod_b["confirmado_en"] is None

    # Las fotos del producto confirmado quedan marcadas como confirmadas.
    fotos_confirmadas = {f["id"] for f in estado_despues["fotos"] if f["confirmada"]}
    assert fotos_confirmadas == set(grupo_a)

    # Queda el rastro append-only de la confirmación.
    assert len(estado_despues["confirmaciones"]) == 1
    assert estado_despues["confirmaciones"][0]["producto_id"] == productos[0]
    assert estado_despues["confirmaciones"][0]["tipo"] == "agrupacion"


def test_listar_lotes_resume_correctamente(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id, foto_ids = _crear_lote_con_fotos(store, n=3)
    productos = store.guardar_agrupacion(lote_id, [foto_ids])
    store.confirmar_producto(productos[0])

    resumen = store.listar_lotes()
    assert len(resumen) == 1
    fila = resumen[0]
    assert fila["id"] == lote_id
    assert fila["n_fotos"] == 3
    assert fila["n_productos"] == 1
    assert fila["n_confirmados"] == 1


# --------------------------------------------------------------------------
# Agrupación: el humano cierra (truth-loop.md §E)
# --------------------------------------------------------------------------


def test_reagrupar_antes_de_confirmar_reemplaza_la_propuesta(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id, foto_ids = _crear_lote_con_fotos(store, n=4)

    store.guardar_agrupacion(lote_id, [foto_ids[:2], foto_ids[2:]])
    # Diego ajusta la propuesta antes de confirmar: junta todo en un grupo.
    nuevos = store.guardar_agrupacion(lote_id, [foto_ids])

    estado = store.cargar_lote(lote_id)
    assert len(estado["productos"]) == 1
    assert estado["productos"][0]["id"] == nuevos[0]
    assert sorted(estado["productos"][0]["fotos"]) == sorted(foto_ids)


def test_no_se_puede_reagrupar_una_foto_de_producto_confirmado(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id, foto_ids = _crear_lote_con_fotos(store, n=4)

    productos = store.guardar_agrupacion(lote_id, [foto_ids[:2], foto_ids[2:]])
    store.confirmar_producto(productos[0])

    with pytest.raises(AgrupacionBloqueadaError):
        # Intenta mover una foto ya confirmada a un grupo distinto.
        store.guardar_agrupacion(lote_id, [[foto_ids[0], foto_ids[2]], [foto_ids[1], foto_ids[3]]])

    # El intento bloqueado no ha tocado NADA: el estado sigue siendo el de
    # antes del intento (todo o nada).
    estado = store.cargar_lote(lote_id)
    assert len(estado["productos"]) == 2
    prod_confirmado = next(p for p in estado["productos"] if p["id"] == productos[0])
    assert sorted(prod_confirmado["fotos"]) == sorted(foto_ids[:2])


def test_confirmar_producto_inexistente_falla_ruidosamente(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    with pytest.raises(ProductoNoEncontradoError):
        store.confirmar_producto("producto-que-no-existe")


def test_guardar_agrupacion_lote_inexistente_falla(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    with pytest.raises(LoteNoEncontradoError):
        store.guardar_agrupacion("lote-fantasma", [["foto-x"]])


# --------------------------------------------------------------------------
# Nunca la foto original de Diego
# --------------------------------------------------------------------------


def test_anadir_foto_fuera_de_la_carpeta_del_lote_falla(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote", "C:/fotos/origen")

    original = tmp_path / "original_de_diego.jpg"
    original.write_bytes(b"foto-original")

    with pytest.raises(RutaInvalidaError):
        store.añadir_fotos(
            lote_id, [Foto(ruta=str(original), hash="h1", timestamp_exif=None)]
        )

    # Nada se ha insertado: el rechazo es todo o nada.
    estado = store.cargar_lote(lote_id)
    assert estado["fotos"] == []


def test_anadir_foto_duplicada_falla_de_forma_tipada(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote", "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id
    ruta = carpeta / "IMG_0001.jpg"
    ruta.write_bytes(b"contenido")

    store.añadir_fotos(lote_id, [Foto(ruta=str(ruta), hash="h1", timestamp_exif=None)])
    with pytest.raises(FotoDuplicadaError):
        store.añadir_fotos(lote_id, [Foto(ruta=str(ruta), hash="h1", timestamp_exif=None)])

    estado = store.cargar_lote(lote_id)
    assert len(estado["fotos"]) == 1


def test_guardar_agrupacion_rechaza_grupo_vacio(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id, foto_ids = _crear_lote_con_fotos(store, n=2)
    with pytest.raises(ValueError):
        store.guardar_agrupacion(lote_id, [foto_ids, []])

    # Rechazo todo o nada: no se creó ningún producto de la llamada fallida.
    estado = store.cargar_lote(lote_id)
    assert estado["productos"] == []


def test_crear_lote_deja_la_carpeta_lista_antes_de_anadir_fotos(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote", "C:/fotos/origen")
    # La carpeta de trabajo existe inmediatamente después de crear_lote,
    # sin depender de ningún paso intermedio.
    assert (store.lotes_dir / lote_id).is_dir()


# --------------------------------------------------------------------------
# Aviso de EXIF ausente (`resumen_exif_lote`)
# --------------------------------------------------------------------------


def test_resumen_exif_lote_todo_con_exif(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id, _ = _crear_lote_con_fotos_exif_mixto(
        store, ["2026-07-13T10:00:00", "2026-07-13T10:01:00", "2026-07-13T10:02:00"]
    )

    resumen = store.resumen_exif_lote(lote_id)

    assert resumen == {"total": 3, "con_exif": 3, "sin_exif": 0}


def test_resumen_exif_lote_todo_sin_exif_caso_whatsapp(tmp_path):
    # El caso real que pidió Diego: 13/13 fotos por WhatsApp, cero EXIF.
    store = LoteStore(data_dir=tmp_path)
    lote_id, _ = _crear_lote_con_fotos_exif_mixto(store, [None] * 13)

    resumen = store.resumen_exif_lote(lote_id)

    assert resumen == {"total": 13, "con_exif": 0, "sin_exif": 13}


def test_resumen_exif_lote_mixto(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id, _ = _crear_lote_con_fotos_exif_mixto(
        store, ["2026-07-13T10:00:00", None, None, "2026-07-13T10:03:00"]
    )

    resumen = store.resumen_exif_lote(lote_id)

    assert resumen == {"total": 4, "con_exif": 2, "sin_exif": 2}


def test_resumen_exif_lote_sobrevive_a_cierre_y_reapertura(tmp_path):
    # Es un cálculo sobre lo persistido en `fotos`, no un caché en memoria:
    # tiene que seguir siendo correcto tras "cerrar la app" y reabrirla.
    store = LoteStore(data_dir=tmp_path)
    lote_id, _ = _crear_lote_con_fotos_exif_mixto(store, [None, None, "2026-07-13T10:00:00"])
    del store

    store2 = LoteStore(data_dir=tmp_path)
    resumen = store2.resumen_exif_lote(lote_id)

    assert resumen == {"total": 3, "con_exif": 1, "sin_exif": 2}


def test_resumen_exif_lote_sin_fotos(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote vacío", "C:/fotos/origen")

    resumen = store.resumen_exif_lote(lote_id)

    assert resumen == {"total": 0, "con_exif": 0, "sin_exif": 0}


def test_resumen_exif_lote_inexistente_falla(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    with pytest.raises(LoteNoEncontradoError):
        store.resumen_exif_lote("lote-fantasma")


# --------------------------------------------------------------------------
# Migraciones
# --------------------------------------------------------------------------


def test_tabla_de_version_de_esquema_existe_y_es_correcta(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    conn = sqlite3.connect(store.db_path)
    try:
        version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        assert version == SCHEMA_VERSION
    finally:
        conn.close()

    # Reabrir sobre una base ya migrada no falla y no repite la migración.
    store2 = LoteStore(data_dir=tmp_path)
    conn = sqlite3.connect(store2.db_path)
    try:
        filas = conn.execute("SELECT version FROM schema_version").fetchall()
        assert [f[0] for f in filas] == [1]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Interrupción a mitad de escritura: el crash real, no sólo validación previa
# --------------------------------------------------------------------------


def test_crash_a_mitad_de_guardar_agrupacion_no_deja_estado_a_medias(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id, foto_ids = _crear_lote_con_fotos(store, n=4)

    # sqlite3.Connection es un tipo C inmutable: no se le puede hacer
    # monkeypatch.setattr directo a un método. Se sub-clasea para
    # interceptar `execute` y forzar un crash real a mitad de la
    # transacción (no una validación previa a que empiece a escribir).
    contador = {"updates_producto_id": 0}

    class ConexionQueRevienta(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if isinstance(sql, str) and sql.strip().startswith("UPDATE fotos SET producto_id = ?"):
                contador["updates_producto_id"] += 1
                if contador["updates_producto_id"] == 2:
                    # Simula el proceso muriendo justo después de reasignar
                    # la primera foto y antes de terminar de escribir el resto.
                    raise RuntimeError("crash simulado a mitad de escritura")
            return super().execute(sql, *args, **kwargs)

    original_connect = sqlite3.connect

    def connect_con_crash(*args, **kwargs):
        kwargs["factory"] = ConexionQueRevienta
        return original_connect(*args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sqlite3, "connect", connect_con_crash)
        with pytest.raises(RuntimeError, match="crash simulado"):
            store.guardar_agrupacion(lote_id, [foto_ids[:2], foto_ids[2:]])

    # El "proceso" vuelve a arrancar: nueva conexión, nueva instancia.
    store_recuperada = LoteStore(data_dir=tmp_path)
    estado = store_recuperada.cargar_lote(lote_id)

    # Nada de la agrupación fallida se ha aplicado: cero productos, todas
    # las fotos siguen sin asignar. La transacción se deshizo entera.
    assert estado["productos"] == []
    assert all(f["producto_id"] is None for f in estado["fotos"])
    assert len(estado["fotos"]) == 4


def test_crash_a_mitad_de_confirmar_producto_no_deja_estado_a_medias(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id, foto_ids = _crear_lote_con_fotos(store, n=2)
    productos = store.guardar_agrupacion(lote_id, [foto_ids])
    producto_id = productos[0]

    class ConexionQueRevienta(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if isinstance(sql, str) and sql.strip().startswith("INSERT INTO confirmaciones"):
                raise RuntimeError("crash simulado justo antes de dejar el rastro de auditoria")
            return super().execute(sql, *args, **kwargs)

    original_connect = sqlite3.connect

    def connect_con_crash(*args, **kwargs):
        kwargs["factory"] = ConexionQueRevienta
        return original_connect(*args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sqlite3, "connect", connect_con_crash)
        with pytest.raises(RuntimeError, match="crash simulado"):
            store.confirmar_producto(producto_id)

    store_recuperada = LoteStore(data_dir=tmp_path)
    estado = store_recuperada.cargar_lote(lote_id)
    producto = next(p for p in estado["productos"] if p["id"] == producto_id)

    # La confirmación no se aplicó a medias: ni el flag, ni las fotos, ni
    # el log de auditoría. O todo, o nada.
    assert producto["confirmado"] is False
    assert producto["confirmado_en"] is None
    assert all(not f["confirmada"] for f in estado["fotos"])
    assert estado["confirmaciones"] == []
