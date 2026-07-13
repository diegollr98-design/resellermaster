"""Tests de core/grouping.py -- costura de AGRUPACION (superficie sensible).

Reescrito de raiz tras [INC-002] v2 (SEGUNDO `listing-audit` BLOQUEANTE,
"cosmetico respecto a la clase de fallo"). Cubre los DOS EJES donde el
modulo se rompia y que ningun test anterior tocaba:

  1. **Outlier temporal**: un cambio de prenda anomalamente lento
     (30-180s) en medio de un lote a ritmo normal. Barrido de 5/6/8
     prendas. Invariante duro: ningun lote puede acabar con dos productos
     REALES en el mismo grupo.
  2. **Particion equitativa**: 3 fotos de un producto + 3 de otro en el
     mismo segmento temporal (bimodalidad, no "el intruso solitario").
     Debe salir "baja" + sugerencia de corte.

Mas los casos que YA cubria v1 (reescritos contra la API nueva) y el caso
especifico de [INC-002] que colorhash NO cazaba (rojo vs NARANJA,
adyacentes en el circulo cromatico).

## Por que las imagenes sinteticas de v1 (elipse solida sobre fondo gris)
no sirven aqui
Se MIDIO (ver docstring de `core/grouping.py`, Cambio 2): CLIP casi no
discrimina color sobre una elipse solida sin textura -- "roja" vs
"naranja" media 0.965, PRACTICAMENTE IGUAL que "roja" vs una variante de
brillo de si misma. Una foto sin contenido semantico no le da a un modelo
entrenado con fotografias reales nada de lo que tirar. `_imagen_prenda`
(abajo) fabrica algo con mas se{ñ}al real: una silueta de prenda + ruido
de tela + fondo neutro -- con eso la separacion SI aparece (medido, mismo
sitio). Genera imagenes sinteticas con EXIF fabricado a mano -- sigue sin
depender de fotos reales ni de red (aparte de la descarga UNICA del
modelo CLIP, cacheada tras la primera ejecucion en esta maquina).

Cero coste de API: CLIP es local. El modelo se descarga una vez (ver
docstring de `core/grouping.py`) y se reutiliza entre tests via la cache
de embeddings por sha256 (fixture `cache_clip`, con scope de modulo).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from PIL import ExifTags, Image, ImageDraw, ImageFilter

import core.grouping as grouping
from core.grouping import TECHO_CONFIANZA, Grupo, UMBRAL_CLIP_SIMILITUD_MINIMA, agrupar

# --------------------------------------------------------------------------
# Fixture de cache CLIP compartida entre tests del modulo -- evita
# recalcular el mismo embedding varias veces y evita ensuciar la cache
# real del proyecto (`data/cache/embeddings_clip/`, nunca se toca desde
# los tests -- `.claude/rules/decision-making.md` SS15).
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def cache_clip(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("clip_cache")


# --------------------------------------------------------------------------
# Helpers de fabricacion de imagenes sinteticas
# --------------------------------------------------------------------------
def _exif_con_fecha(fecha: datetime) -> Image.Exif:
    """Orientation en IFD0 (1 = normal) + DateTimeOriginal en la sub-IFD
    "Exif". Pillow solo serializa el bloque EXIF si IFD0 tiene al menos un
    tag propio -- por eso siempre se fuerza Orientation, igual que en
    `tests/test_images.py`."""
    exif = Image.Exif()
    exif[274] = 1
    exif.get_ifd(ExifTags.IFD.Exif)[36867] = fecha.strftime("%Y:%m:%d %H:%M:%S")
    return exif


_FONDO_PRENDA = (150, 150, 145)


def _imagen_prenda(
    color: tuple[int, int, int], seed: int = 0, size: tuple[int, int] = (224, 224), silueta: str = "camiseta"
) -> Image.Image:
    """Fabrica algo con suficiente contenido semantico para que CLIP saque
    senal de el (ver docstring del fichero, y de `core/grouping.py` Cambio
    2, para la medicion que justifica este diseño frente a una elipse
    solida): una silueta de prenda rellena de `color`, con ruido de tela
    por pixel (`seed` varia el ruido -- simula "otro disparo" del mismo
    producto) y un desenfoque leve, sobre un fondo neutro que no colisiona
    con ningun color de la paleta usada en este fichero."""
    img = Image.new("RGB", size, _FONDO_PRENDA)
    d = ImageDraw.Draw(img)
    w, h = size
    if silueta == "camiseta":
        cuerpo = [
            (w * 0.25, h * 0.25),
            (w * 0.75, h * 0.25),
            (w * 0.85, h * 0.4),
            (w * 0.7, h * 0.45),
            (w * 0.7, h * 0.9),
            (w * 0.3, h * 0.9),
            (w * 0.3, h * 0.45),
            (w * 0.15, h * 0.4),
        ]
    else:  # "pantalon" -- una silueta claramente distinta, para el caso sin-EXIF
        cuerpo = [(w * 0.3, h * 0.15), (w * 0.7, h * 0.15), (w * 0.7, h * 0.9), (w * 0.3, h * 0.9)]
    d.polygon(cuerpo, fill=color)

    arr = np.array(img).astype(np.int16)
    ruido = np.random.RandomState(seed).randint(-12, 12, size=(h, w, 3))
    arr = np.clip(arr + ruido, 0, 255).astype(np.uint8)
    return Image.fromarray(arr).filter(ImageFilter.GaussianBlur(0.5))


def _imagen_intrusa(seed: int = 999) -> Image.Image:
    """Contenido sin ninguna relacion visual con `_imagen_prenda()`: un
    tablero de ajedrez, deliberadamente muy distinto de cualquier prenda."""
    img = Image.new("RGB", (224, 224))
    d = ImageDraw.Draw(img)
    for x in range(0, 224, 28):
        for y in range(0, 224, 28):
            color = (0, 0, 0) if (x // 28 + y // 28) % 2 == 0 else (255, 255, 255)
            d.rectangle((x, y, x + 28, y + 28), fill=color)
    return img


def _guardar_con_fecha(ruta: Path, img: Image.Image, fecha: datetime) -> None:
    img.save(ruta, format="JPEG", quality=90, exif=_exif_con_fecha(fecha))


def _guardar_sin_exif(ruta: Path, img: Image.Image) -> None:
    img.save(ruta, format="JPEG", quality=90)


BASE = datetime(2026, 7, 10, 12, 0, 0)

# Paleta de colores usada en todo el fichero -- ver docstring de
# `core/grouping.py` Cambio 2 para la medicion que la calibra: rojo vs
# naranja (adyacentes, el caso que colorhash NO cazaba) midio 0.924 de
# similitud CLIP, bien por debajo de `UMBRAL_CLIP_SIMILITUD_MINIMA=0.97`.
_ROJO = (200, 30, 30)
_NARANJA = (230, 126, 34)
_AZUL = (30, 30, 200)
_NEGRO = (35, 35, 35)


# --------------------------------------------------------------------------
# Invariantes duros de la superficie sensible -- deben cumplirse en
# CUALQUIER escenario, no solo en los ejemplos concretos de abajo.
# --------------------------------------------------------------------------
def _grupos_alta(grupos: list[Grupo]) -> list[Grupo]:
    return [g for g in grupos if g.confianza == "alta"]


def _grupos_mezclan_productos(
    grupos: list[Grupo], producto_por_ruta: dict[Path, int]
) -> list[tuple[Grupo, set[int]]]:
    rotos = []
    for g in grupos:
        productos = {producto_por_ruta[f] for f in g.fotos}
        if len(productos) > 1:
            rotos.append((g, productos))
    return rotos


def test_techo_confianza_es_media_mientras_este_activo() -> None:
    # Guardarrail de la propia constante -- si alguien la cambia sin
    # querer, que un test lo note antes que Diego en produccion.
    assert TECHO_CONFIANZA == "media"


def test_invariante_duro_ningun_grupo_es_alta_nunca(tmp_path, cache_clip) -> None:
    """Mientras `TECHO_CONFIANZA="media"` este activo, NINGUN grupo puede
    salir "alta" -- ni siquiera el caso mas limpio posible (EXIF perfecto,
    fotos identicas). Cambio 4."""
    fotos = []
    for i, seg in enumerate((0, 4, 8)):
        ruta = tmp_path / f"p_{i}.jpg"
        _guardar_con_fecha(ruta, _imagen_prenda(_ROJO, seed=i), BASE + timedelta(seconds=seg))
        fotos.append(ruta)
    inicio_b = BASE + timedelta(seconds=8 + 60)
    for i, seg in enumerate((0, 4, 8)):
        ruta = tmp_path / f"q_{i}.jpg"
        _guardar_con_fecha(ruta, _imagen_prenda(_AZUL, seed=i + 10), inicio_b + timedelta(seconds=seg))
        fotos.append(ruta)

    grupos = agrupar(fotos, cache_dir=cache_clip)
    assert not _grupos_alta(grupos), [
        (g.confianza, [f.name for f in g.fotos]) for g in grupos
    ]


def test_ningun_grupo_mezcla_productos_sin_estar_marcado_baja(tmp_path, cache_clip) -> None:
    """Invariante duro pedido explicitamente: si un grupo contiene fotos
    de dos productos REALES distintos, tiene que estar marcado "baja".
    Construye un caso adversarial a proposito: 3+3 en el mismo segmento
    temporal (sin hueco que lo separe) -- la unica defensa posible es la
    bimodalidad visual (Cambio 3)."""
    fotos = []
    producto_por_ruta: dict[Path, int] = {}
    colores = [_ROJO, _ROJO, _ROJO, _AZUL, _AZUL, _AZUL]
    for i, color in enumerate(colores):
        ruta = tmp_path / f"f_{i}.jpg"
        _guardar_con_fecha(ruta, _imagen_prenda(color, seed=i), BASE + timedelta(seconds=i * 3))
        fotos.append(ruta)
        producto_por_ruta[ruta] = 0 if color == _ROJO else 1

    grupos = agrupar(fotos, cache_dir=cache_clip)
    for g in _grupos_mezclan_productos(grupos, producto_por_ruta):
        assert g[0].confianza == "baja", (g[0].confianza, [f.name for f in g[0].fotos])


# --------------------------------------------------------------------------
# Caso 1 -- outlier temporal: un cambio de prenda anomalamente lento en
# medio de un lote a ritmo normal. [INC-002] v2, requerido explicitamente.
# --------------------------------------------------------------------------
def _lote_con_outlier_temporal(
    tmp_path: Path,
    n_productos: int,
    fotos_por_producto: int,
    intra: float,
    inter_normal: float,
    inter_outlier: float,
    idx_outlier: int,
    seed: int,
) -> tuple[list[Path], dict[Path, int]]:
    """Fabrica un lote a ritmo normal (`intra` entre fotos de un mismo
    producto, `inter_normal` entre productos) salvo en la transicion
    `idx_outlier`, donde el hueco es `inter_outlier` (30-180s: Diego se
    enredo, sono el telefono). Cada producto tiene un color DISTINTO
    (ciclando 4 colores bien separados) para poder verificar despues que
    ningun grupo los mezcla."""
    rnd = random.Random(seed)
    paleta = [_ROJO, _AZUL, _NEGRO, _NARANJA]
    rutas: list[Path] = []
    producto_por_ruta: dict[Path, int] = {}
    t = 0.0
    for p in range(n_productos):
        color = paleta[p % len(paleta)]
        for f in range(fotos_por_producto):
            ruta = tmp_path / f"prod{p}_{f}.jpg"
            _guardar_con_fecha(
                ruta, _imagen_prenda(color, seed=p * 100 + f), BASE + timedelta(seconds=t)
            )
            rutas.append(ruta)
            producto_por_ruta[ruta] = p
            if f < fotos_por_producto - 1:
                t += max(0.1, intra + rnd.uniform(-1.0, 1.0))
        if p < n_productos - 1:
            inter = inter_outlier if p == idx_outlier else inter_normal
            t += max(0.1, inter + rnd.uniform(-1.0, 1.0))
    return rutas, producto_por_ruta


@pytest.mark.parametrize("n_productos", [5, 6, 8])
@pytest.mark.parametrize("cambio_lento", [30, 90, 180])
def test_outlier_temporal_ningun_lote_mezcla_dos_productos(
    tmp_path, cache_clip, n_productos, cambio_lento
) -> None:
    """El caso EXACTO de [INC-002]: ritmo normal (4s intra, 12s inter) con
    UN cambio de prenda anomalamente lento (30-180s) en medio del lote.
    Barrido de 5/6/8 prendas x 3 duraciones de outlier. Invariante duro:
    ningun lote puede acabar con dos productos REALES en el mismo grupo
    -- ni "alta" (imposible con el techo activo) ni de ninguna confianza."""
    sub = tmp_path / f"n{n_productos}_lento{cambio_lento}"
    sub.mkdir()
    idx_outlier = n_productos // 2  # el cambio lento cae a mitad del lote
    rutas, producto_por_ruta = _lote_con_outlier_temporal(
        sub, n_productos, 3, intra=4.0, inter_normal=12.0, inter_outlier=cambio_lento, idx_outlier=idx_outlier, seed=cambio_lento * 10 + n_productos
    )
    grupos = agrupar(rutas, cache_dir=cache_clip)

    fotos_en_grupos = [f for g in grupos for f in g.fotos]
    assert sorted(fotos_en_grupos) == sorted(rutas)  # nada se pierde ni se duplica

    rotos = _grupos_mezclan_productos(grupos, producto_por_ruta)
    assert not rotos, (
        f"n={n_productos} outlier={cambio_lento}s: grupo(s) mezclando productos reales: "
        f"{[(prods, [f.name for f in g.fotos]) for g, prods in rotos]}"
    )
    assert not _grupos_alta(grupos)


# --------------------------------------------------------------------------
# Caso 2 -- particion equitativa (bimodalidad, no "el intruso solitario")
# --------------------------------------------------------------------------
def test_particion_equitativa_3_mas_3_sale_baja_con_sugerencia_de_corte(tmp_path, cache_clip) -> None:
    """3 fotos de un producto + 3 de otro en el MISMO segmento temporal
    (huecos uniformes, sin ninguna base para cortar por tiempo): la unica
    defensa posible es la bimodalidad CLIP (Cambio 3). Debe salir "baja" +
    el motivo debe sugerir el corte concreto (nombrar las dos sub-
    agrupaciones)."""
    fotos = []
    colores = [_ROJO, _ROJO, _ROJO, _AZUL, _AZUL, _AZUL]
    for i, color in enumerate(colores):
        ruta = tmp_path / f"f_{i}.jpg"
        _guardar_con_fecha(ruta, _imagen_prenda(color, seed=i), BASE + timedelta(seconds=i * 3))
        fotos.append(ruta)

    grupos = agrupar(fotos, cache_dir=cache_clip)

    assert len(grupos) == 1  # el timestamp (huecos uniformes) no separa nada
    grupo = grupos[0]
    assert set(grupo.fotos) == set(fotos)
    assert grupo.confianza == "baja"
    # El motivo tiene que dar pistas del corte: los nombres de las fotos
    # "rojas" y "azules" tienen que aparecer, indicando la particion.
    assert "sub-grupos" in grupo.motivo or "separa" in grupo.motivo
    for f in fotos:
        assert f.name in grupo.motivo


# --------------------------------------------------------------------------
# Caso 3 -- foto cruzada de color ADYACENTE (rojo vs naranja): el caso que
# colorhash NO cazaba (distancia 2, dentro de su propio umbral).
# --------------------------------------------------------------------------
def test_color_adyacente_rojo_naranja_se_caza_por_clip(tmp_path, cache_clip) -> None:
    secuencia = [(0, _ROJO), (3, _ROJO), (6, _NARANJA), (9, _ROJO)]
    rutas = []
    for i, (seg, color) in enumerate(secuencia):
        ruta = tmp_path / f"foto_{seg}.jpg"
        _guardar_con_fecha(ruta, _imagen_prenda(color, seed=i + 20), BASE + timedelta(seconds=seg))
        rutas.append(ruta)

    grupos = agrupar(rutas, cache_dir=cache_clip)

    assert len(grupos) == 1  # huecos uniformes de 3s: nada que cortar por tiempo
    grupo = grupos[0]
    assert set(grupo.fotos) == set(rutas)
    assert grupo.confianza == "baja"
    assert rutas[2].name in grupo.motivo  # la foto naranja, señalada


# --------------------------------------------------------------------------
# Caso 4 -- degradacion honesta si CLIP no esta disponible: TODO el lote
# sale "baja", nunca en silencio, nunca un fallback a pHash.
# --------------------------------------------------------------------------
@pytest.fixture
def clip_no_disponible(monkeypatch):
    """Fuerza `_cargar_modelo_clip` a devolver `(None, None)` sin tocar la
    cache real del modulo entre tests (usa monkeypatch, que revierte
    automaticamente al terminar el test)."""
    monkeypatch.setitem(grouping._estado_modelo_clip, "cargado", True)
    monkeypatch.setitem(grouping._estado_modelo_clip, "modelo", None)
    monkeypatch.setitem(grouping._estado_modelo_clip, "preprocess", None)
    yield


def test_clip_no_disponible_todo_el_lote_sale_baja_nunca_silencio(
    tmp_path, cache_clip, clip_no_disponible, caplog
) -> None:
    fotos = []
    for i, seg in enumerate((0, 4, 8)):
        ruta = tmp_path / f"p_{i}.jpg"
        _guardar_con_fecha(ruta, _imagen_prenda(_ROJO, seed=i), BASE + timedelta(seconds=seg))
        fotos.append(ruta)

    grupos = agrupar(fotos, cache_dir=cache_clip)

    assert len(grupos) >= 1
    for g in grupos:
        assert g.confianza == "baja"
        assert "CLIP no disponible" in g.motivo  # nunca en silencio


def test_clip_no_disponible_registra_el_fallo_al_cargar(caplog) -> None:
    """El fallo de carga EN SI (no solo la degradacion de confianza que
    prueba el test anterior) se registra con traceback -- log ruidoso, no
    un aviso mudo (`decision-making.md` SS13). Se fuerza el fallo real
    sustituyendo `open_clip.create_model_and_transforms` por una funcion
    que revienta, y se comprueba (a) que `_cargar_modelo_clip` devuelve
    `(None, None)` y (b) que el log de error existe de verdad."""
    import logging

    import open_clip

    original_estado = dict(grouping._estado_modelo_clip)
    original_crear = open_clip.create_model_and_transforms
    grouping._estado_modelo_clip.clear()

    def _reventar(*args, **kwargs):
        raise RuntimeError("modelo no descargado (simulado)")

    open_clip.create_model_and_transforms = _reventar
    caplog.set_level(logging.ERROR, logger="core.grouping")
    try:
        modelo, preprocess = grouping._cargar_modelo_clip()
    finally:
        open_clip.create_model_and_transforms = original_crear
        grouping._estado_modelo_clip.clear()
        grouping._estado_modelo_clip.update(original_estado)

    assert modelo is None and preprocess is None
    assert any("No se pudo cargar el modelo CLIP" in rec.message for rec in caplog.records)


# --------------------------------------------------------------------------
# Casos heredados de v1 (reescritos contra la API/fixtures nuevas): un
# solo producto a ritmo constante, lote sin EXIF, foto intrusa (el caso
# "1 rara" que la bimodalidad tambien cubre), grupos adyacentes y
# lote vacio/una sola foto.
# --------------------------------------------------------------------------
def test_dos_productos_seguidos_con_hueco_pequeno_se_separan(tmp_path, cache_clip) -> None:
    fotos_a = []
    for i, seg in enumerate((0, 4, 8)):
        ruta = tmp_path / f"a_{i}.jpg"
        _guardar_con_fecha(ruta, _imagen_prenda(_ROJO, seed=i), BASE + timedelta(seconds=seg))
        fotos_a.append(ruta)

    inicio_b = BASE + timedelta(seconds=8 + 30)
    fotos_b = []
    for i, seg in enumerate((0, 4, 8)):
        ruta = tmp_path / f"b_{i}.jpg"
        _guardar_con_fecha(ruta, _imagen_prenda(_AZUL, seed=i + 10), inicio_b + timedelta(seconds=seg))
        fotos_b.append(ruta)

    grupos = agrupar(fotos_a + fotos_b, cache_dir=cache_clip)

    assert len(grupos) == 2
    conjuntos = [set(g.fotos) for g in grupos]
    assert set(fotos_a) in conjuntos
    assert set(fotos_b) in conjuntos
    for g in grupos:
        assert g.motivo
        assert g.confianza != "alta"  # techo global -- Cambio 4


def test_grupo_unico_disparado_a_ritmo_constante_no_se_corte_de_mas(tmp_path, cache_clip) -> None:
    fotos = []
    for i, seg in enumerate((0, 3, 6, 9, 12)):
        ruta = tmp_path / f"p_{i}.jpg"
        _guardar_con_fecha(ruta, _imagen_prenda(_ROJO, seed=i), BASE + timedelta(seconds=seg))
        fotos.append(ruta)

    grupos = agrupar(fotos, cache_dir=cache_clip)

    assert len(grupos) == 1
    assert set(grupos[0].fotos) == set(fotos)
    assert grupos[0].confianza == "media"
    assert "huecos suficientes" in grupos[0].motivo


def test_lote_sin_exif_agrupa_solo_por_similitud_clip(tmp_path, cache_clip) -> None:
    base_img = _imagen_prenda(_ROJO, seed=1)

    casi_1 = tmp_path / "whatsapp_1.jpg"
    casi_2 = tmp_path / "whatsapp_2.jpg"
    _guardar_sin_exif(casi_1, base_img)
    _guardar_sin_exif(casi_2, _imagen_prenda(_ROJO, seed=2))  # "otro disparo" del mismo producto

    suelta = tmp_path / "captura_pantalla.jpg"
    _guardar_sin_exif(suelta, _imagen_intrusa())

    grupos = agrupar([casi_1, casi_2, suelta], cache_dir=cache_clip)

    todas = [foto for g in grupos for foto in g.fotos]
    assert sorted(todas) == sorted([casi_1, casi_2, suelta])

    grupo_por_foto = {foto: g for g in grupos for foto in g.fotos}
    assert grupo_por_foto[casi_1] is grupo_por_foto[casi_2]
    assert grupo_por_foto[suelta] is not grupo_por_foto[casi_1]

    grupo_pareja = grupo_por_foto[casi_1]
    assert grupo_pareja.confianza == "media"  # nunca "alta" sin EXIF
    assert grupo_pareja.motivo

    grupo_suelta = grupo_por_foto[suelta]
    assert len(grupo_suelta.fotos) == 1
    assert grupo_suelta.confianza == "baja"
    assert "no tiene fecha" in grupo_suelta.motivo


def test_lote_sin_exif_cuatro_colores_distintos_no_se_funden_en_uno(tmp_path, cache_clip) -> None:
    colores = [_ROJO, _AZUL, _NEGRO, _NARANJA]
    rutas = []
    for i, color in enumerate(colores):
        ruta = tmp_path / f"prenda_{i}.jpg"
        _guardar_sin_exif(ruta, _imagen_prenda(color, seed=i + 30))
        rutas.append(ruta)

    grupos = agrupar(rutas, cache_dir=cache_clip)

    fotos_en_grupos = [f for g in grupos for f in g.fotos]
    assert sorted(fotos_en_grupos) == sorted(rutas)

    grupo_por_foto = {foto: g for g in grupos for foto in g.fotos}
    # Ninguna de las 4 prendas de color distinto termina en el mismo grupo
    # que otra (incluye el par ROJO/NARANJA, el caso que colorhash fallaba).
    grupos_distintos = {id(grupo_por_foto[r]) for r in rutas}
    assert len(grupos_distintos) == len(rutas)
    for g in grupos:
        assert g.confianza == "baja"


def test_foto_intrusa_en_medio_de_la_sesion_baja_la_confianza_pero_no_se_expulsa(tmp_path, cache_clip) -> None:
    base_img = _imagen_prenda(_ROJO, seed=1)
    secuencia = [
        (0, base_img),
        (3, _imagen_prenda(_ROJO, seed=2)),
        (6, _imagen_intrusa()),
        (9, _imagen_prenda(_ROJO, seed=3)),
    ]

    rutas = []
    for seg, img in secuencia:
        ruta = tmp_path / f"foto_{seg}.jpg"
        _guardar_con_fecha(ruta, img, BASE + timedelta(seconds=seg))
        rutas.append(ruta)

    grupos = agrupar(rutas, cache_dir=cache_clip)

    assert len(grupos) == 1
    grupo = grupos[0]
    assert set(grupo.fotos) == set(rutas)  # la intrusa NO se expulsa
    assert grupo.confianza == "baja"
    assert rutas[2].name in grupo.motivo


def test_lote_de_una_sola_foto_con_exif_es_grupo_dudoso(tmp_path, cache_clip) -> None:
    ruta = tmp_path / "unica.jpg"
    _guardar_con_fecha(ruta, _imagen_prenda(_ROJO), BASE)

    grupos = agrupar([ruta], cache_dir=cache_clip)

    assert len(grupos) == 1
    assert grupos[0].fotos == [ruta]
    assert grupos[0].confianza == "baja"
    assert grupos[0].motivo


def test_lote_de_una_sola_foto_sin_exif_es_grupo_dudoso(tmp_path, cache_clip) -> None:
    ruta = tmp_path / "unica_sin_exif.jpg"
    _guardar_sin_exif(ruta, _imagen_prenda(_ROJO))

    grupos = agrupar([ruta], cache_dir=cache_clip)

    assert len(grupos) == 1
    assert grupos[0].fotos == [ruta]
    assert grupos[0].confianza == "baja"
    assert grupos[0].motivo


def test_lote_vacio_devuelve_lista_vacia() -> None:
    assert agrupar([]) == []


def test_ninguna_foto_se_pierde_ni_se_duplica_entre_grupos(tmp_path, cache_clip) -> None:
    fotos_a = []
    for i, seg in enumerate((0, 5, 10)):
        ruta = tmp_path / f"a_{i}.jpg"
        _guardar_con_fecha(ruta, _imagen_prenda(_ROJO, seed=i), BASE + timedelta(seconds=seg))
        fotos_a.append(ruta)

    fotos_b = []
    inicio_b = BASE + timedelta(minutes=10)
    for i, seg in enumerate((0, 5, 10)):
        ruta = tmp_path / f"b_{i}.jpg"
        _guardar_con_fecha(ruta, _imagen_prenda(_AZUL, seed=i + 10), inicio_b + timedelta(seconds=seg))
        fotos_b.append(ruta)

    base_sin_exif = _imagen_prenda(_NEGRO, seed=40)
    casi_1 = tmp_path / "c_1.jpg"
    casi_2 = tmp_path / "c_2.jpg"
    _guardar_sin_exif(casi_1, base_sin_exif)
    _guardar_sin_exif(casi_2, _imagen_prenda(_NEGRO, seed=41))

    suelta = tmp_path / "suelta.jpg"
    _guardar_sin_exif(suelta, _imagen_intrusa())

    todas_las_fotos = fotos_a + fotos_b + [casi_1, casi_2, suelta]
    grupos = agrupar(todas_las_fotos, cache_dir=cache_clip)

    fotos_en_grupos = [foto for g in grupos for foto in g.fotos]
    assert sorted(fotos_en_grupos) == sorted(todas_las_fotos)
    assert len(fotos_en_grupos) == len(set(fotos_en_grupos))


def test_todos_los_motivos_son_texto_no_vacio_en_todos_los_grupos(tmp_path, cache_clip) -> None:
    fotos = []
    for i, seg in enumerate((0, 4, 8)):
        ruta = tmp_path / f"p_{i}.jpg"
        _guardar_con_fecha(ruta, _imagen_prenda(_ROJO, seed=i), BASE + timedelta(seconds=seg))
        fotos.append(ruta)
    suelta = tmp_path / "suelta.jpg"
    _guardar_sin_exif(suelta, _imagen_intrusa())

    grupos = agrupar(fotos + [suelta], cache_dir=cache_clip)

    for g in grupos:
        assert isinstance(g, Grupo)
        assert isinstance(g.motivo, str) and g.motivo.strip()
        assert g.confianza in ("alta", "media", "baja")
        assert g.confianza != "alta"  # techo global -- Cambio 4


def test_umbral_clip_similitud_minima_esta_documentado_y_es_razonable() -> None:
    # Guardarraíl minimo: si alguien cambia la constante sin querer a algo
    # sin sentido (fuera de [0,1], o demasiado bajo para discriminar nada),
    # que un test lo note.
    assert 0.5 < UMBRAL_CLIP_SIMILITUD_MINIMA < 1.0
