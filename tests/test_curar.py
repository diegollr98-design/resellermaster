"""Tests de `ui/curar.py` — LA CREMALLERA CON PESTILLO (superficie
`agrupacion`, `truth-loop.md` §B y §E).

Tres capas, de la más fundamental a la más de extremo a extremo:

1. **`particion()`/`costuras_abiertas_de()` (core/grouping.py)** — funciones
   PURAS, property-based con `random` (sin `hypothesis`: no es una
   dependencia del repo — `requirements.txt` no la trae, y añadirla para
   esto no está pedido). Es TODA la lógica de negocio del nuevo modelo.
2. **`streamlit.testing.v1.AppTest`** contra `ui.curar.render` de verdad,
   igual que hacía `tests/test_ui_confirmacion.py` (eliminado, este fichero
   lo reemplaza). LÍMITE MEDIDO de `AppTest` en esta versión de Streamlit:
   un click en un widget DENTRO de un `@st.dialog` fuerza un rerun COMPLETO
   del script (no un rerun de fragmento — `AppTest.run()` no manda
   `fragment_id`, se comprobó ejecutando un caso mínimo), así que el botón
   que abrió el diálogo no vuelve a pulsarse y el diálogo no reaparece: el
   click "SÍ"/"NO" de dentro del modal no se puede ejercitar end-to-end vía
   `AppTest`. Por eso aquí se comprueba (a) que el diálogo ABRE sin
   excepción y con el contenido correcto (los dos grupos enteros) — eso sí
   lo ve `AppTest`, porque el primer render del diálogo ocurre en el MISMO
   script run que el click que lo abre — y (b) la mutación que hace el
   botón "SÍ" (`ui.curar._cerrar_costura`) se llama directamente, que es
   exactamente la función que ese botón invoca.
3. **EL GATE** — sobre las 33 fotos reales de Diego: cerrar EXACTAMENTE las
   costuras que el algoritmo cortó de más (y ninguna otra) reproduce los 7
   productos de su verdad (`tests/golden/truth.json`).
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import ExifTags, Image
from streamlit.testing.v1 import AppTest

from core.grouping import UMBRAL_HUECO_SEGUNDOS, agrupar, costuras_abiertas_de, particion
from core.images import leer_metadatos
from core.store import Foto, LoteStore, StoreError

import ui.curar as curar

_REPO = Path(__file__).resolve().parent.parent
_TRUTH = _REPO / "tests" / "golden" / "truth.json"
_FOTOS = _REPO / "fotos"


# ============================================================================
# 1. `particion()` / `costuras_abiertas_de()` — PURAS, property-based.
# ============================================================================
def _fotos_y_pares(n: int) -> tuple[list[str], list[tuple[str, str]]]:
    fotos = [f"foto_{i}" for i in range(n)]
    return fotos, list(zip(fotos, fotos[1:]))


def test_particion_lista_vacia():
    assert particion([], set()) == []


def test_particion_sin_costuras_abiertas_es_un_solo_grupo():
    fotos, _pares = _fotos_y_pares(7)
    assert particion(fotos, set()) == [fotos]


def test_particion_todas_las_costuras_abiertas_es_n_grupos_de_uno():
    fotos, pares = _fotos_y_pares(7)
    grupos = particion(fotos, set(pares))
    assert grupos == [[f] for f in fotos]


def test_particion_propiedades_basicas_property_based():
    """Ninguna foto se pierde ni se duplica; el orden se conserva; la suma
    de longitudes de los grupos es el número de fotos. Barrido aleatorio
    (semilla fija, reproducible) sobre tamaños y subconjuntos de costuras."""
    rng = random.Random(20260714)
    for _ in range(300):
        n = rng.randint(0, 15)
        fotos, pares = _fotos_y_pares(n)
        k = rng.randint(0, len(pares))
        costuras = set(rng.sample(pares, k)) if pares else set()

        grupos = particion(fotos, costuras)

        assert sum(len(g) for g in grupos) == n
        aplanado = [fid for g in grupos for fid in g]
        assert aplanado == fotos, "el orden no se conservó o se perdió/duplicó una foto"
        assert len(set(aplanado)) == n, "alguna foto se duplicó entre grupos"


def test_particion_solo_produce_grupos_contiguos_bajo_cualquier_secuencia_de_toggles():
    """EL ESPACIO DE ESTADOS ALCANZABLE SON SÓLO PARTICIONES CONTIGUAS.
    Se simula una secuencia aleatoria de `toggle(costura)` (abrir/cerrar) y,
    tras CADA paso, se comprueba que cada grupo devuelto es un *slice*
    contiguo real de la secuencia original — nunca "la foto del producto 2
    metida en el 7"."""
    rng = random.Random(7)
    for _ in range(150):
        n = rng.randint(1, 12)
        fotos, pares = _fotos_y_pares(n)
        costuras_abiertas: set[tuple[str, str]] = set()
        for _paso in range(rng.randint(0, 25)):
            if not pares:
                break
            par = rng.choice(pares)
            if par in costuras_abiertas:
                costuras_abiertas.discard(par)
            else:
                costuras_abiertas.add(par)

            grupos = particion(fotos, costuras_abiertas)

            # Contigüidad real: cada grupo es exactamente el siguiente
            # tramo de `fotos`, en orden, sin huecos ni solapes.
            cursor = 0
            for grupo in grupos:
                assert fotos[cursor : cursor + len(grupo)] == grupo
                cursor += len(grupo)
            assert cursor == n


def test_costuras_abiertas_de_es_inversa_de_particion_property_based():
    rng = random.Random(99)
    for _ in range(200):
        n = rng.randint(1, 10)
        fotos, pares = _fotos_y_pares(n)
        k = rng.randint(0, len(pares))
        costuras = set(rng.sample(pares, k)) if pares else set()

        grupos = particion(fotos, costuras)
        grupo_de_foto = {fid: i for i, g in enumerate(grupos) for fid in g}
        derivadas = costuras_abiertas_de(fotos, grupo_de_foto)

        assert derivadas == costuras
        assert particion(fotos, derivadas) == grupos


# --------------------------------------------------------------------------
# HALLAZGO 1 (`listing-audit`, 2026-07-14): fotos HUÉRFANAS (sin producto en
# `grupo_de_foto`) tenían que dejar la costura ABIERTA (degradar al lado
# seguro) y en vez de eso, con AMBAS huérfanas, `None != None` era `False`
# y la costura quedaba CERRADA -> `particion()` fusionaba TODO en un grupo.
# --------------------------------------------------------------------------
def test_costuras_abiertas_de_con_grupo_de_foto_vacio_todas_las_costuras_abiertas():
    """Reproduce EXACTAMENTE el caso del hallazgo:
    `costuras_abiertas_de(['a','b','c','d','e'], {})` debía dar TODAS las
    costuras abiertas (N-1 singletons), nunca `set()` (que fusiona las 5)."""
    fotos, pares = _fotos_y_pares(5)

    abiertas = costuras_abiertas_de(fotos, {})

    assert abiertas == set(pares)
    assert particion(fotos, abiertas) == [[f] for f in fotos]


def test_costuras_abiertas_de_caso_mixto_huerfanas_y_asignadas():
    """Unas fotos con producto asignado, otras huérfanas (todavía sin
    `producto_id`, p. ej. porque la propuesta automática falló al
    persistir). TODA costura que toque una huérfana debe quedar ABIERTA,
    incluida huérfana-huérfana (que es justo el caso `None != None` que
    fallaba)."""
    fotos = ["a", "b", "c", "d", "e"]
    # a y b ya están asignadas al MISMO producto -> costura a-b CERRADA.
    # c, d, e son huérfanas (sin producto todavía, en ningún caso el mismo).
    grupo_de_foto = {"a": "p1", "b": "p1"}

    abiertas = costuras_abiertas_de(fotos, grupo_de_foto)

    assert ("a", "b") not in abiertas
    assert ("b", "c") in abiertas  # asignada -> huérfana
    assert ("c", "d") in abiertas  # huérfana -> huérfana (el caso que fallaba)
    assert ("d", "e") in abiertas  # huérfana -> huérfana
    assert particion(fotos, abiertas) == [["a", "b"], ["c"], ["d"], ["e"]]


def test_costuras_abiertas_de_una_sola_foto_asignada_y_el_resto_huerfanas():
    """Caso límite: sólo la primera foto tiene producto; el resto son
    huérfanas consecutivas. Ninguna se debe fusionar entre sí."""
    fotos = ["x", "y", "z"]
    grupo_de_foto = {"x": "p1"}

    abiertas = costuras_abiertas_de(fotos, grupo_de_foto)

    assert abiertas == {("x", "y"), ("y", "z")}
    assert particion(fotos, abiertas) == [["x"], ["y"], ["z"]]


# ============================================================================
# 2. `AppTest` contra `ui.curar.render` real.
# ============================================================================
def _crear_foto_real(ruta: Path, color: tuple[int, int, int]) -> None:
    img = Image.new("RGB", (64, 64), color)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    img.save(ruta, format="JPEG")


def _crear_foto_con_exif(ruta: Path, color: tuple[int, int, int], fecha: datetime) -> None:
    """Igual que `_crear_foto_real`, pero con fecha `DateTimeOriginal`
    EMBEBIDA de verdad en el fichero (mismo patrón que
    `tests/test_grouping.py::_guardar_con_fecha`). Necesario para el caso
    "EXIF degenerado" (HALLAZGO 2): `core.grouping.agrupar()` lee el EXIF
    del FICHERO en disco, no el `timestamp_exif` guardado en el store —
    una imagen sin bloque EXIF real cae en "sin fecha", no en "EXIF
    idéntico"."""
    exif = Image.Exif()
    exif[274] = 1  # Orientation, IFD0 — sin esto Pillow no serializa el bloque.
    exif.get_ifd(ExifTags.IFD.Exif)[36867] = fecha.strftime("%Y:%m:%d %H:%M:%S")
    img = Image.new("RGB", (64, 64), color)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    img.save(ruta, format="JPEG", exif=exif)


def _script(data_dir: str, lote_id: str) -> None:
    from pathlib import Path as _Path

    from core.store import LoteStore as _LoteStore
    from ui import curar as _curar

    _store = _LoteStore(data_dir=_Path(data_dir))
    _curar.render(_store, lote_id)


def _preparar_lote_dos_grupos_y_suelta(tmp_path: Path) -> tuple[str, dict[str, str]]:
    """3 grupos ya guardados a mano (la PARTICIÓN es determinista, no
    depende de que `agrupar()` la reproduzca): G1=[A0,A1] (costura interna
    CERRADA), G2=[B0,B1] (ídem), S0 sola. Entre G1 y G2, y entre G2 y S0,
    hay costuras ABIERTAS. Devuelve `(lote_id, {nombre: foto_id})`.

    Los ficheros llevan EXIF REAL embebido con estos mismos timestamps
    (`_crear_foto_con_exif`, no `_crear_foto_real`): desde HALLAZGO 2,
    `_costuras_propuestas_inicialmente` vuelve a llamar `agrupar()` sobre
    el fichero en disco — si aquí sólo se fijara el `timestamp_exif` del
    store sin EXIF real, `agrupar()` leería "sin fecha" para las 5 y la
    propuesta recalculada no coincidiría con la partición ya guardada."""
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote cremallera", "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id

    nombres_colores = [
        ("A0", (200, 0, 0)),
        ("A1", (200, 50, 50)),
        ("B0", (0, 150, 0)),
        ("B1", (0, 180, 0)),
        ("S0", (0, 0, 200)),
    ]
    timestamps = {
        "A0": "2026-07-14T10:00:00",
        "A1": "2026-07-14T10:00:05",
        "B0": "2026-07-14T10:01:40",  # +95s desde A1: costura ABIERTA
        "B1": "2026-07-14T10:01:45",
        "S0": "2026-07-14T10:05:00",  # +195s desde B1: costura ABIERTA
    }
    fotos: list[Foto] = []
    for nombre, color in nombres_colores:
        ruta = carpeta / f"{nombre}.jpg"
        _crear_foto_con_exif(ruta, color, datetime.fromisoformat(timestamps[nombre]))
        fotos.append(Foto(ruta=str(ruta), hash=f"hash_{nombre}", timestamp_exif=timestamps[nombre]))
    ids = store.añadir_fotos(lote_id, fotos)
    por_nombre = dict(zip([n for n, _ in nombres_colores], ids))

    store.guardar_agrupacion(
        lote_id,
        [
            [por_nombre["A0"], por_nombre["A1"]],
            [por_nombre["B0"], por_nombre["B1"]],
            [por_nombre["S0"]],
        ],
    )
    return lote_id, por_nombre


def test_render_sin_excepcion(tmp_path):
    lote_id, _ = _preparar_lote_dos_grupos_y_suelta(tmp_path)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception


def test_descoser_separa_el_grupo_en_dos_sin_excepcion(tmp_path):
    """CICATRIZ: el botón "✂" de la costura INTERNA de un grupo (siempre
    cerrada, es la cicatriz) lo separa en dos, de un click, sin modal."""
    lote_id, por_nombre = _preparar_lote_dos_grupos_y_suelta(tmp_path)
    a0, a1 = por_nombre["A0"], por_nombre["A1"]

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    boton = next(b for b in at.button if b.key == f"descoser_{a0}_{a1}")
    boton.click().run()
    assert not at.exception, f"'Descoser' lanzó: {at.exception}"

    store = LoteStore(data_dir=tmp_path)
    estado = store.cargar_lote(lote_id)
    no_confirmados = [p for p in estado["productos"] if not p["confirmado"]]
    grupos = {frozenset(p["fotos"]) for p in no_confirmados}
    assert frozenset({a0}) in grupos
    assert frozenset({a1}) in grupos
    assert not any(frozenset({a0, a1}) == g for g in grupos)


def test_costura_abierta_abre_dialogo_con_los_dos_grupos_enteros(tmp_path):
    """EL PESTILLO: un click en la costura ABIERTA entre dos tarjetas nunca
    fusiona directamente — abre un modal con los DOS grupos completos.
    Límite medido de `AppTest` con `st.dialog` (ver docstring del módulo):
    esto comprueba que el modal ABRE sin excepción y con el contenido
    correcto; la mutación del botón "SÍ" se comprueba llamando a la función
    que invoca directamente, más abajo."""
    lote_id, por_nombre = _preparar_lote_dos_grupos_y_suelta(tmp_path)
    a1, b0 = por_nombre["A1"], por_nombre["B0"]

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    boton_costura = next(b for b in at.button if b.key == f"seam_{a1}_{b0}")
    boton_costura.click().run()
    assert not at.exception, f"abrir el diálogo del pestillo lanzó: {at.exception}"

    textos = " ".join([m.value for m in at.markdown] + [c.value for c in at.caption])
    assert "A0.jpg" in textos and "A1.jpg" in textos, "el diálogo no muestra el Grupo A entero"
    assert "B0.jpg" in textos and "B1.jpg" in textos, "el diálogo no muestra el Grupo B entero"
    assert any("SÍ" in b.label for b in at.button if b.label)
    assert any("NO" in b.label for b in at.button if b.label)


def test_cerrar_costura_fusiona_los_dos_grupos_en_el_store(tmp_path):
    """La mutación real del botón "SÍ, ES EL MISMO" dentro del pestillo:
    `ui.curar._cerrar_costura` es exactamente lo que ese botón llama."""
    lote_id, por_nombre = _preparar_lote_dos_grupos_y_suelta(tmp_path)
    a0, a1, b0, b1 = por_nombre["A0"], por_nombre["A1"], por_nombre["B0"], por_nombre["B1"]
    store = LoteStore(data_dir=tmp_path)

    ok = curar._cerrar_costura(store, lote_id, a1, b0)
    assert ok is True

    estado = store.cargar_lote(lote_id)
    no_confirmados = [p for p in estado["productos"] if not p["confirmado"]]
    grupo_fusionado = next(p for p in no_confirmados if a0 in p["fotos"])
    assert set(grupo_fusionado["fotos"]) == {a0, a1, b0, b1}


def test_confirmar_agrupacion_abre_dialogo_de_revision_sin_excepcion(tmp_path):
    lote_id, _ = _preparar_lote_dos_grupos_y_suelta(tmp_path)

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    boton = next(b for b in at.button if b.label and "Confirmar agrupación" in b.label)
    boton.click().run()
    assert not at.exception, f"abrir el modal de confirmación lanzó: {at.exception}"

    # Sin ninguna fusión hecha por Diego en este lote: el modal debe decirlo
    # (REGLA 4 de la tarea — sólo enseña los grupos TOCADOS, aquí ninguno).
    textos = " ".join([i.value for i in at.info])
    assert "No fusionaste ningún grupo" in textos


def test_accion_confirmar_todo_confirma_los_productos_no_ilegibles(tmp_path):
    lote_id, por_nombre = _preparar_lote_dos_grupos_y_suelta(tmp_path)
    store = LoteStore(data_dir=tmp_path)

    ok = curar._accion_confirmar_todo(store, lote_id)
    assert ok is True

    estado = store.cargar_lote(lote_id)
    assert all(p["confirmado"] for p in estado["productos"])
    assert len(estado["productos"]) == 3


# --------------------------------------------------------------------------
# Fichero ILEGIBLE: nunca entra en ningún grupo, sólo se puede descartar.
# --------------------------------------------------------------------------
def _preparar_lote_con_ilegible(tmp_path: Path) -> tuple[str, dict[str, str]]:
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote con ilegible", "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id
    carpeta.mkdir(parents=True, exist_ok=True)

    ruta_a0 = carpeta / "A0.jpg"
    ruta_a1 = carpeta / "A1.jpg"
    _crear_foto_real(ruta_a0, (200, 0, 0))
    _crear_foto_real(ruta_a1, (200, 50, 50))
    ruta_bad = carpeta / "BAD.jpg"
    ruta_bad.write_bytes(b"no soy una imagen de verdad")

    fotos = [
        Foto(ruta=str(ruta_a0), hash="hash_a0", timestamp_exif="2026-07-14T10:00:00"),
        Foto(ruta=str(ruta_a1), hash="hash_a1", timestamp_exif="2026-07-14T10:00:05"),
        Foto(
            ruta=str(ruta_bad),
            hash="hash_bad",
            legible=False,
            error_lectura="cannot identify image file",
        ),
    ]
    ids = store.añadir_fotos(lote_id, fotos)
    por_nombre = dict(zip(["A0", "A1", "BAD"], ids))
    store.guardar_agrupacion(lote_id, [[por_nombre["A0"], por_nombre["A1"]], [por_nombre["BAD"]]])
    return lote_id, por_nombre


def test_foto_ilegible_no_entra_en_ningun_grupo_y_se_puede_descartar(tmp_path):
    lote_id, por_nombre = _preparar_lote_con_ilegible(tmp_path)
    bad = por_nombre["BAD"]

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    # Ni checkbox ni costura la involucran — sólo el botón de descarte.
    assert not any(f"_{bad}" in (b.key or "") and "descartar" not in (b.key or "") for b in at.button)
    boton_descartar = next(b for b in at.button if b.key == f"descartar_{bad}")

    boton_descartar.click().run()
    assert not at.exception, f"'Descartar' lanzó: {at.exception}"

    store = LoteStore(data_dir=tmp_path)
    estado = store.cargar_lote(lote_id)
    assert all(f["id"] != bad for f in estado["fotos"])
    # Las legibles siguen intactas, en su grupo.
    no_confirmados = [p for p in estado["productos"] if not p["confirmado"]]
    assert any(set(p["fotos"]) == {por_nombre["A0"], por_nombre["A1"]} for p in no_confirmados)


# --------------------------------------------------------------------------
# Lote SIN EXIF: degrada a N grupos de 1, N-1 costuras — y es curable.
# --------------------------------------------------------------------------
def _preparar_lote_sin_exif(tmp_path: Path) -> tuple[str, list[str]]:
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote sin EXIF", "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id

    nombres_colores = [("F0", (200, 0, 0)), ("F1", (0, 150, 0)), ("F2", (0, 0, 200))]
    fotos: list[Foto] = []
    for nombre, color in nombres_colores:
        ruta = carpeta / f"{nombre}.jpg"
        _crear_foto_real(ruta, color)
        # timestamp_exif=None (default): así llega una foto sin EXIF real.
        fotos.append(Foto(ruta=str(ruta), hash=f"hash_{nombre}"))
    ids = store.añadir_fotos(lote_id, fotos)
    return lote_id, ids


def test_lote_sin_exif_renderiza_y_es_curable(tmp_path):
    lote_id, foto_ids = _preparar_lote_sin_exif(tmp_path)

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    # Aviso con dientes de EXIF ausente, en la cabecera.
    assert any("sin fecha" in e.value for e in at.error)

    store = LoteStore(data_dir=tmp_path)
    estado = store.cargar_lote(lote_id)
    no_confirmados = [p for p in estado["productos"] if not p["confirmado"]]
    # Degrada a N grupos de 1 (cajón de INCIERTAS de `core.grouping.agrupar`).
    assert len(no_confirmados) == 3
    assert all(len(p["fotos"]) == 1 for p in no_confirmados)

    # Es curable: hay costuras (todas abiertas) que se pueden cerrar.
    botones_costura = [b for b in at.button if b.key and b.key.startswith("seam_")]
    assert len(botones_costura) == 2  # N-1 = 2

    botones_costura[0].click().run()
    assert not at.exception, f"abrir el pestillo en un lote sin EXIF lanzó: {at.exception}"


# --------------------------------------------------------------------------
# HALLAZGO 1, end-to-end: si `_proponer_grupo_inicial` falla al persistir
# (StoreError transitorio — antivirus bloqueando el fichero, disco lleno un
# instante), la pantalla NUNCA debe presentar un MEGA-GRUPO con todas las
# fotos como si fuera un único producto confirmable.
# --------------------------------------------------------------------------
def test_h1_fallo_de_store_en_propuesta_inicial_no_presenta_mega_grupo_confirmable(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote con fallo transitorio de store", "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id

    nombres = ["A0", "A1", "B0", "B1", "S0"]
    fotos: list[Foto] = []
    for nombre in nombres:
        ruta = carpeta / f"{nombre}.jpg"
        _crear_foto_real(ruta, (10, 10, 10))
        # sin EXIF: da igual la señal temporal, lo que importa es que
        # NINGUNA quede con producto_id tras el fallo de persistencia.
        fotos.append(Foto(ruta=str(ruta), hash=f"hash_{nombre}"))
    store.añadir_fotos(lote_id, fotos)

    with patch.object(
        LoteStore, "guardar_agrupacion", side_effect=StoreError("disco bloqueado (simulado)")
    ):
        at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()

    assert not at.exception, f"el fallo de store no debe tumbar la pantalla: {at.exception}"

    # Nada se persistió (guardar_agrupacion siempre falló): CERO productos.
    estado_tras_fallo = store.cargar_lote(lote_id)
    assert estado_tras_fallo["productos"] == []

    # Lo que la pantalla HABRÍA renderizado (misma fuente que `render()`,
    # `_estado_cremallera`, sobre el estado real tras el fallo) NO puede ser
    # un único grupo con las 5 fotos — eso es la fusión total que el
    # HALLAZGO 1 prohíbe. Debe degradar a 5 singletons (huérfanas -> costura
    # SIEMPRE abierta).
    _, _fotos_ordenadas, grupos, _costuras = curar._estado_cremallera(store, lote_id)
    assert not any(len(g) == len(nombres) for g in grupos), (
        f"HALLAZGO 1: la pantalla fusionó las {len(nombres)} fotos huérfanas en un "
        f"mega-grupo: {grupos}"
    )
    assert grupos == [[fid] for fid in _fotos_ordenadas]

    # Y en el render real no aparece ninguna TARJETA de grupo con "5
    # foto(s)" (la cabecera del lote también dice "... — 5 foto(s)." — se
    # compara el valor EXACTO de cada caption, no un substring, para no
    # confundir la una con la otra).
    assert not any(c.value.strip() == "5 foto(s)" for c in at.caption)


# --------------------------------------------------------------------------
# HALLAZGO 2, end-to-end: con EXIF degenerado (timestamps idénticos), la
# propuesta inicial corta TODO. Diego fusiona a mano dos de esas fotos: el
# modal de revisión final DEBE listar ese grupo como tocado — nunca decir
# "No fusionaste ningún grupo" cuando sí fusionó.
# --------------------------------------------------------------------------
def test_h2_exif_degenerado_grupo_fusionado_a_mano_se_lista_en_revision(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote EXIF degenerado", "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id

    fecha_idéntica = datetime(2026, 7, 14, 10, 0, 0)
    mismo_timestamp = fecha_idéntica.isoformat()
    nombres = ["D0", "D1", "D2"]
    fotos: list[Foto] = []
    for nombre in nombres:
        ruta = carpeta / f"{nombre}.jpg"
        _crear_foto_con_exif(ruta, (40, 60, 80), fecha_idéntica)
        fotos.append(Foto(ruta=str(ruta), hash=f"hash_{nombre}", timestamp_exif=mismo_timestamp))
    ids = store.añadir_fotos(lote_id, fotos)
    por_nombre = dict(zip(nombres, ids))

    # Primer render: propone. EXIF degenerado -> cajón de INCIERTAS -> 3
    # singletons (`core.grouping._agrupar_por_tiempo`).
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    estado = store.cargar_lote(lote_id)
    no_confirmados = [p for p in estado["productos"] if not p["confirmado"]]
    assert len(no_confirmados) == 3, "EXIF degenerado debe cortar TODO en la propuesta inicial"

    # Diego fusiona D0 y D1 a mano — la costura entre ellas SÍ estaba
    # abierta en la propuesta inicial (EXIF degenerado corta todo), así que
    # cerrarla es una fusión de verdad hecha por él.
    d0, d1 = por_nombre["D0"], por_nombre["D1"]
    assert curar._cerrar_costura(store, lote_id, d0, d1) is True

    # Unitario: `_grupo_fue_fusionado` (vía `_costuras_propuestas_inicialmente`,
    # que NO re-thresholdea nada) debe detectar el grupo fusionado.
    _, fotos_ordenadas, grupos_actuales, _ = curar._estado_cremallera(store, lote_id)
    fotos_por_id = {f["id"]: f for f in store.cargar_lote(lote_id)["fotos"]}
    costuras_propuestas = curar._costuras_propuestas_inicialmente(fotos_ordenadas, fotos_por_id)

    grupo_fusionado = next(g for g in grupos_actuales if set(g) == {d0, d1})
    assert curar._grupo_fue_fusionado(grupo_fusionado, costuras_propuestas) is True

    # End-to-end: el modal de revisión LISTA el grupo fusionado, no dice
    # "No fusionaste ningún grupo".
    at2 = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at2.exception
    boton = next(b for b in at2.button if b.label and "Confirmar agrupación" in b.label)
    boton.click().run()
    assert not at2.exception, f"abrir el modal de revisión lanzó: {at2.exception}"

    textos_info = " ".join(i.value for i in at2.info)
    assert "No fusionaste ningún grupo" not in textos_info

    textos_warning = " ".join(w.value for w in at2.warning)
    assert "que fusionaste" in textos_warning


# ============================================================================
# 3. EL GATE — sobre las 33 fotos reales de Diego.
# ============================================================================
@pytest.fixture(scope="module")
def golden() -> tuple[list[Path], dict[str, int]]:
    if not _TRUTH.exists():
        pytest.skip(f"No existe el golden set ({_TRUTH})")
    import json

    truth = json.loads(_TRUTH.read_text(encoding="utf-8"))
    esperado = {nombre: producto["id"] for producto in truth["productos"] for nombre in producto["fotos"]}

    fotos = sorted(_FOTOS.glob("IMG_20260714_*.jpg"))
    if len(fotos) != truth["n_fotos"]:
        pytest.skip(
            f"Las fotos reales de Diego no están disponibles en {_FOTOS} "
            f"(encontradas {len(fotos)}, esperadas {truth['n_fotos']}). Sin ellas este "
            "gate no se puede evaluar."
        )
    return fotos, esperado


def test_el_gate_cerrar_las_costuras_que_sobran_reproduce_la_verdad_de_diego(golden, capsys):
    """Cerrar EXACTAMENTE las costuras que `agrupar()` cortó de más (y
    NINGUNA otra) sobre las 33 fotos reales produce EXACTAMENTE los 7
    productos de la verdad de Diego. Es la demostración, sobre datos
    reales, de que el modelo de la cremallera (orden total + costuras +
    `particion()`) es equivalente al de `agrupar()` cuando Diego cierra
    justo los cortes de más — ni uno más, ni uno menos."""
    fotos_paths, esperado = golden

    metadatos = {p: leer_metadatos(p) for p in fotos_paths}
    fotos_ordenadas_paths = sorted(fotos_paths, key=lambda p: (metadatos[p].fecha_captura_exif, p.name))
    fotos_ordenadas = [p.stem for p in fotos_ordenadas_paths]

    propuestos = agrupar(fotos_ordenadas_paths)
    grupo_inicial: dict[str, int] = {}
    for i, g in enumerate(propuestos):
        for p in g.fotos:
            grupo_inicial[p.stem] = i
    costuras_iniciales = costuras_abiertas_de(fotos_ordenadas, grupo_inicial)
    costuras_correctas = costuras_abiertas_de(fotos_ordenadas, esperado)

    # Ya demostrado en `tests/test_grouping_golden.py` (FUSIONES == 0): toda
    # frontera real ya estaba abierta en la propuesta de `agrupar()`.
    assert costuras_correctas <= costuras_iniciales, (
        "una frontera real NO estaba abierta en la propuesta inicial: "
        "eso sería una fusión, y el gate de agrupar() la prohíbe."
    )

    costuras_a_cerrar = costuras_iniciales - costuras_correctas
    with capsys.disabled():
        print(
            f"\n--- CREMALLERA sobre el golden set: {len(costuras_a_cerrar)} "
            f"costura(s) a cerrar (umbral={UMBRAL_HUECO_SEGUNDOS:.0f}s) ---"
        )

    costuras_finales = costuras_iniciales - costuras_a_cerrar
    assert costuras_finales == costuras_correctas

    grupos_finales = particion(fotos_ordenadas, costuras_finales)

    assert len(grupos_finales) == esperado_n_productos(esperado)
    reconstruido: dict[int, set[str]] = defaultdict(set)
    for grupo in grupos_finales:
        ids_reales = {esperado[fid] for fid in grupo}
        assert len(ids_reales) == 1, f"un grupo final mezcla productos distintos: {ids_reales}"
        reconstruido[ids_reales.pop()] = set(grupo)

    fotos_por_producto_esperado: dict[int, set[str]] = defaultdict(set)
    for nombre, pid in esperado.items():
        fotos_por_producto_esperado[pid].add(nombre)

    assert reconstruido == fotos_por_producto_esperado


def esperado_n_productos(esperado: dict[str, int]) -> int:
    return len(set(esperado.values()))
