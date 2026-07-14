"""Tests de core/store.py — persistencia del lote.

Cubre el ciclo completo (crear -> añadir fotos -> agrupar -> confirmar ->
cerrar -> reabrir -> todo sigue ahí) y una interrupción real a mitad de
una escritura (simula el proceso muriendo dentro de una transacción).
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from core.store import (
    AgrupacionBloqueadaError,
    Foto,
    FotoDuplicadaError,
    FotoNoEncontradaError,
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

    # Reabrir sobre una base ya migrada no falla y no repite ninguna
    # migración (una fila por versión aplicada, 1..SCHEMA_VERSION, nunca
    # duplicada).
    store2 = LoteStore(data_dir=tmp_path)
    conn = sqlite3.connect(store2.db_path)
    try:
        filas = conn.execute("SELECT version FROM schema_version").fetchall()
        assert [f[0] for f in filas] == list(range(1, SCHEMA_VERSION + 1))
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


# --------------------------------------------------------------------------
# `archivar_foto` — quitar una foto mala/casi-duplicada, RECUPERABLE
# (nunca un borrado del disco), pedido explícitamente por Diego.
# --------------------------------------------------------------------------


def test_archivar_foto_mueve_el_fichero_y_borra_la_fila(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id, foto_ids = _crear_lote_con_fotos(store, n=3)
    ruta_original = store.lotes_dir / lote_id / "IMG_0000.jpg"
    assert ruta_original.exists()

    store.guardar_agrupacion(lote_id, [foto_ids])

    ruta_destino = store.archivar_foto(lote_id, foto_ids[0])

    # El fichero se movió de verdad: ya no está en el origen, sí en destino.
    assert not ruta_original.exists()
    assert ruta_destino.exists()
    assert ruta_destino.parent == store.lotes_dir / lote_id / "descartadas"

    # La foto ya no está en el lote (fila borrada), pero el producto sigue
    # existiendo con las otras dos fotos (no se vació).
    estado = store.cargar_lote(lote_id)
    assert all(f["id"] != foto_ids[0] for f in estado["fotos"])
    assert len(estado["fotos"]) == 2
    assert len(estado["productos"]) == 1
    assert sorted(estado["productos"][0]["fotos"]) == sorted(foto_ids[1:])


def test_archivar_foto_limpia_el_producto_huerfano_si_se_queda_vacio(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id, foto_ids = _crear_lote_con_fotos(store, n=1)
    store.guardar_agrupacion(lote_id, [foto_ids])

    store.archivar_foto(lote_id, foto_ids[0])

    estado = store.cargar_lote(lote_id)
    assert estado["fotos"] == []
    assert estado["productos"] == []


def test_archivar_foto_no_machaca_si_el_destino_ya_existe(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id, foto_ids = _crear_lote_con_fotos(store, n=2)

    # Un fichero previo con el MISMO nombre ya vive en 'descartadas/' (p.
    # ej. de un lote reabierto tras mover a mano). No debe machacarse.
    carpeta_descartadas = store.lotes_dir / lote_id / "descartadas"
    carpeta_descartadas.mkdir(parents=True, exist_ok=True)
    colisión = carpeta_descartadas / "IMG_0000.jpg"
    colisión.write_bytes(b"contenido-preexistente")

    ruta_destino = store.archivar_foto(lote_id, foto_ids[0])

    assert colisión.read_bytes() == b"contenido-preexistente"
    assert ruta_destino != colisión
    assert ruta_destino.exists()


def test_archivar_foto_rechaza_producto_ya_confirmado(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id, foto_ids = _crear_lote_con_fotos(store, n=2)
    productos = store.guardar_agrupacion(lote_id, [foto_ids])
    store.confirmar_producto(productos[0])

    ruta_original = store.lotes_dir / lote_id / "IMG_0000.jpg"

    with pytest.raises(AgrupacionBloqueadaError):
        store.archivar_foto(lote_id, foto_ids[0])

    # Nada se tocó: ni el fichero se movió, ni la fila desapareció.
    assert ruta_original.exists()
    estado = store.cargar_lote(lote_id)
    assert len(estado["fotos"]) == 2
    assert estado["productos"][0]["confirmado"] is True


def test_archivar_foto_inexistente_falla_ruidosamente(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id, _ = _crear_lote_con_fotos(store, n=1)

    with pytest.raises(FotoNoEncontradaError):
        store.archivar_foto(lote_id, "foto-que-no-existe")


def test_archivar_foto_si_falla_el_move_no_toca_la_db(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id, foto_ids = _crear_lote_con_fotos(store, n=2)
    store.guardar_agrupacion(lote_id, [foto_ids])

    # El fallo del move (disco lleno, antivirus de Windows) se envuelve en
    # StoreError — NO se propaga el OSError crudo — para que la UI lo capture
    # con su `except StoreError` y nunca le pinte un traceback a Diego
    # ([INC-006]). Se cazó en un listing-audit: era la única garantía de UI
    # que este flujo rompía.
    from core.store import StoreError

    with patch("core.store.shutil.move", side_effect=OSError("disco lleno (simulado)")):
        with pytest.raises(StoreError):
            store.archivar_foto(lote_id, foto_ids[0])

    # La DB no se tocó: la foto sigue en el lote, en su producto.
    estado = store.cargar_lote(lote_id)
    assert len(estado["fotos"]) == 2
    assert any(f["id"] == foto_ids[0] for f in estado["fotos"])
    assert sorted(estado["productos"][0]["fotos"]) == sorted(foto_ids)


@pytest.mark.parametrize(
    "metodo, construir_args",
    [
        ("guardar_agrupacion", lambda ids: (lambda lote: [[ids[0]], [ids[1]]])),
        ("archivar_foto", lambda ids: (lambda lote: ids[0])),
        ("descartar_foto", lambda ids: (lambda lote: ids[0])),
        ("confirmar_producto", lambda ids: (lambda lote: ids[0])),
    ],
)
def test_un_error_crudo_de_sqlite_sale_como_StoreError(tmp_path, metodo, construir_args):
    """Un fallo CRUDO de SQLite ('database is locked' con dos pestañas, disco
    lleno) desde CUALQUIER método de mutación debe salir como StoreError —
    nunca un sqlite3.Error crudo, que la UI (`except StoreError`) no captura y
    acabaría en un traceback en la pantalla de Diego ([INC-006]). Lo cazó un
    listing-audit sobre `archivar_foto`; el predicado vive en `_transaccion` y
    lo comparten TODAS las escrituras (`decision-making.md` §11)."""
    from core.store import StoreError

    store = LoteStore(data_dir=tmp_path)
    lote_id, foto_ids = _crear_lote_con_fotos(store, n=2)
    args = construir_args(foto_ids)(lote_id)
    llamada = (lote_id, args) if metodo != "confirmar_producto" else (args,)

    with patch.object(store, "_conectar", side_effect=sqlite3.OperationalError("database is locked")):
        with pytest.raises(StoreError):
            getattr(store, metodo)(*llamada)
