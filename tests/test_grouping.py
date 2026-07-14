"""Tests de core/grouping.py -- costura de AGRUPACION (superficie sensible). v4.

Reescrito de raiz tras la reescritura completa de `core/grouping.py` a v4
(hueco temporal >= 20s, cajon de INCIERTAS para fotos sin EXIF, cero
pixeles). El fichero anterior testeaba CLIP, pHash, `cache_dir`,
`_umbral_corte`, `TECHO_CONFIANZA`, `_componentes_conexas` -- nada de eso
existe ya en v4. Este fichero es UNITARIO, con fixtures sinteticas
(PIL + EXIF escrito a mano, igual que `tests/test_images.py`). El gate
contra las 33 fotos REALES de Diego vive aparte, en
`tests/test_grouping_golden.py`, y no se reimplementa aqui.

## Por que estos tests son distintos de los que rompieron 3 veces
Las v1-v3 de `core/grouping.py` tenian TODOS sus tests en verde y las tres
estaban rotas (`[INC-002]`, `[INC-003]`, `[INC-004]`): barrian el eje
comodo (ratio inter/intra "limpio") y nunca el eje donde vivia el fallo
(jitter fuerte, gente real que se para a hacer una foto del metro). La
seccion "LA PROPIEDAD QUE IMPORTA" de abajo es la que intenta, con lotes
sinteticos variados y semillas distintas, ROMPER la invariante de
no-fusionar -- no confirmar que el codigo hace lo que ya hace.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import ExifTags, Image

import core.grouping as grouping
from core.grouping import FRACCION_MARGEN_ALTA, UMBRAL_HUECO_SEGUNDOS, agrupar
from core.images import MetadatosImagen

# --------------------------------------------------------------------------
# Helpers de fabricacion de imagenes sinteticas -- mismo patron que
# `tests/test_images.py` (Orientation en IFD0 obligatorio para que Pillow
# serialice el bloque EXIF entero al guardar).
# --------------------------------------------------------------------------
BASE = datetime(2026, 7, 10, 12, 0, 0)


def _exif_con_fecha(fecha: datetime) -> Image.Exif:
    exif = Image.Exif()
    exif[274] = 1
    exif.get_ifd(ExifTags.IFD.Exif)[36867] = fecha.strftime("%Y:%m:%d %H:%M:%S")
    return exif


def _guardar_con_fecha(ruta: Path, fecha: datetime) -> None:
    img = Image.new("RGB", (8, 8), (200, 30, 30))
    img.save(ruta, format="JPEG", quality=90, exif=_exif_con_fecha(fecha))


def _guardar_sin_exif(ruta: Path) -> None:
    img = Image.new("RGB", (8, 8), (30, 30, 200))
    img.save(ruta, format="JPEG", quality=90)


def _meta(fecha: datetime | None) -> MetadatosImagen:
    """`MetadatosImagen` fabricado a mano, para probar `_agrupar_por_tiempo`
    y `_construir_grupo` con precision de SUB-segundo. Un fichero EXIF real
    solo guarda segundos enteros (`strftime('%Y:%m:%d %H:%M:%S')`), asi que
    la frontera exacta del umbral (19.9s vs 20.0s) no se puede fabricar
    pasando por disco -- hay que inyectar el dato directamente."""
    return MetadatosImagen(
        ruta=Path("placeholder.jpg"),
        legible=True,
        formato="JPEG",
        ancho=8,
        alto=8,
        orientacion_exif=1,
        fecha_captura_exif=fecha,
        mtime_fichero=None,
        error=None,
    )


# --------------------------------------------------------------------------
# 1. Corte exactamente en el umbral -- el operador es `>=`, no `>`.
# --------------------------------------------------------------------------
def test_hueco_justo_por_debajo_del_umbral_no_corta():
    a, b = Path("a.jpg"), Path("b.jpg")
    metadatos = {a: _meta(BASE), b: _meta(BASE + timedelta(seconds=UMBRAL_HUECO_SEGUNDOS - 0.1))}

    grupos = grouping._agrupar_por_tiempo([a, b], metadatos)

    assert len(grupos) == 1
    assert grupos[0].fotos == [a, b]


def test_hueco_exactamente_en_el_umbral_si_corta_operador_es_mayor_o_igual():
    a, b = Path("a.jpg"), Path("b.jpg")
    metadatos = {a: _meta(BASE), b: _meta(BASE + timedelta(seconds=UMBRAL_HUECO_SEGUNDOS))}

    grupos = grouping._agrupar_por_tiempo([a, b], metadatos)

    assert len(grupos) == 2
    assert grupos[0].fotos == [a]
    assert grupos[1].fotos == [b]


def test_hueco_justo_por_encima_del_umbral_tambien_corta():
    a, b = Path("a.jpg"), Path("b.jpg")
    metadatos = {a: _meta(BASE), b: _meta(BASE + timedelta(seconds=UMBRAL_HUECO_SEGUNDOS + 0.1))}

    grupos = grouping._agrupar_por_tiempo([a, b], metadatos)

    assert len(grupos) == 2


# --------------------------------------------------------------------------
# 2. Lote vacio.
# --------------------------------------------------------------------------
def test_lote_vacio_devuelve_lista_vacia():
    assert agrupar([]) == []


# --------------------------------------------------------------------------
# 3. Una sola foto -> un grupo, confianza "baja".
# --------------------------------------------------------------------------
def test_una_sola_foto_con_exif_es_un_grupo_confianza_baja(tmp_path):
    ruta = tmp_path / "unica.jpg"
    _guardar_con_fecha(ruta, BASE)

    grupos = agrupar([ruta])

    assert len(grupos) == 1
    assert grupos[0].fotos == [ruta]
    assert grupos[0].confianza == "baja"
    assert grupos[0].motivo


def test_una_sola_foto_sin_exif_es_un_grupo_confianza_baja(tmp_path):
    ruta = tmp_path / "unica_sin_exif.jpg"
    _guardar_sin_exif(ruta)

    grupos = agrupar([ruta])

    assert len(grupos) == 1
    assert grupos[0].fotos == [ruta]
    assert grupos[0].confianza == "baja"
    assert grupos[0].motivo


# --------------------------------------------------------------------------
# 4. Fotos SIN EXIF: cada una su propio grupo "baja", NUNCA mezcladas con
#    las que si tienen fecha (cajon de INCIERTAS).
# --------------------------------------------------------------------------
def test_fotos_sin_exif_cada_una_su_propio_grupo_confianza_baja(tmp_path):
    a = tmp_path / "sin_a.jpg"
    b = tmp_path / "sin_b.jpg"
    _guardar_sin_exif(a)
    _guardar_sin_exif(b)

    grupos = agrupar([a, b])

    assert len(grupos) == 2
    for g in grupos:
        assert len(g.fotos) == 1
        assert g.confianza == "baja"
        assert "no tiene fecha" in g.motivo
    assert {g.fotos[0] for g in grupos} == {a, b}


def test_lote_mixto_exif_y_sin_exif_nunca_se_mezclan(tmp_path):
    con_a = tmp_path / "con_a.jpg"
    con_b = tmp_path / "con_b.jpg"
    _guardar_con_fecha(con_a, BASE)
    _guardar_con_fecha(con_b, BASE + timedelta(seconds=4))
    sin_a = tmp_path / "sin_a.jpg"
    sin_b = tmp_path / "sin_b.jpg"
    _guardar_sin_exif(sin_a)
    _guardar_sin_exif(sin_b)

    grupos = agrupar([con_a, sin_a, con_b, sin_b])

    # las dos con EXIF, misma rafaga (hueco 4s < 20s) -> un solo grupo junto
    grupos_con_exif = [g for g in grupos if set(g.fotos) == {con_a, con_b}]
    assert len(grupos_con_exif) == 1

    # cada foto sin EXIF va sola, nunca mezclada con las de EXIF ni entre si
    for foto_sin in (sin_a, sin_b):
        grupo = next(g for g in grupos if foto_sin in g.fotos)
        assert grupo.fotos == [foto_sin]
        assert grupo.confianza == "baja"
        assert "no tiene fecha" in grupo.motivo

    assert sum(len(g.fotos) for g in grupos) == 4
    assert sorted(f.name for g in grupos for f in g.fotos) == sorted(
        f.name for f in (con_a, con_b, sin_a, sin_b)
    )


# --------------------------------------------------------------------------
# 5. Confianza: hueco interno maximo < 10s (mitad del umbral) -> "alta";
#    entre 10s y 20s -> "media"; grupo de 1 foto -> "baja" (siempre).
# --------------------------------------------------------------------------
def test_confianza_alta_rafaga_limpia_sin_pausas_dentro(tmp_path):
    rutas = []
    for i, seg in enumerate((0, 4, 8)):  # huecos internos: 4s, 4s -- muy por debajo de 10s
        ruta = tmp_path / f"p_{i}.jpg"
        _guardar_con_fecha(ruta, BASE + timedelta(seconds=seg))
        rutas.append(ruta)

    grupos = agrupar(rutas)

    assert len(grupos) == 1
    assert grupos[0].confianza == "alta"


def test_confianza_media_cuando_una_pausa_interna_se_acerca_al_umbral(tmp_path):
    rutas = []
    for i, seg in enumerate((0, 15)):  # hueco interno 15s: entre 10s y 20s
        ruta = tmp_path / f"p_{i}.jpg"
        _guardar_con_fecha(ruta, BASE + timedelta(seconds=seg))
        rutas.append(ruta)

    grupos = agrupar(rutas)

    assert len(grupos) == 1
    assert grupos[0].confianza == "media"


def test_confianza_baja_siempre_para_grupo_de_una_sola_foto(tmp_path):
    # aunque este perfectamente aislado en el tiempo (sin pausas que juzgar)
    ruta = tmp_path / "sola.jpg"
    _guardar_con_fecha(ruta, BASE)

    grupos = agrupar([ruta])

    assert grupos[0].confianza == "baja"


def test_frontera_alta_media_justo_por_debajo_de_la_mitad_del_umbral_es_alta():
    limite = UMBRAL_HUECO_SEGUNDOS * FRACCION_MARGEN_ALTA  # 10.0
    a, b = Path("a.jpg"), Path("b.jpg")
    metadatos = {a: _meta(BASE), b: _meta(BASE + timedelta(seconds=limite - 0.1))}

    grupo = grouping._construir_grupo([a, b], metadatos)

    assert grupo.confianza == "alta"


def test_frontera_alta_media_exactamente_en_la_mitad_del_umbral_es_media():
    limite = UMBRAL_HUECO_SEGUNDOS * FRACCION_MARGEN_ALTA  # 10.0
    a, b = Path("a.jpg"), Path("b.jpg")
    metadatos = {a: _meta(BASE), b: _meta(BASE + timedelta(seconds=limite))}

    grupo = grouping._construir_grupo([a, b], metadatos)

    assert grupo.confianza == "media"


# --------------------------------------------------------------------------
# 6. Orden cronologico dentro de cada grupo, aunque la entrada venga
#    desordenada.
# --------------------------------------------------------------------------
def test_orden_cronologico_dentro_del_grupo_aunque_entrada_desordenada(tmp_path):
    rutas_cronologicas = []
    for i, seg in enumerate((0, 3, 6, 9)):
        ruta = tmp_path / f"p_{i}.jpg"
        _guardar_con_fecha(ruta, BASE + timedelta(seconds=seg))
        rutas_cronologicas.append(ruta)

    desordenadas = [
        rutas_cronologicas[2],
        rutas_cronologicas[0],
        rutas_cronologicas[3],
        rutas_cronologicas[1],
    ]

    grupos = agrupar(desordenadas)

    assert len(grupos) == 1
    assert grupos[0].fotos == rutas_cronologicas


def test_orden_cronologico_entre_grupos_tambien_con_entrada_desordenada(tmp_path):
    """El orden de entrada no debe afectar ni la particion en grupos ni el
    orden interno de cada uno -- ni siquiera cuando el desorden mezcla
    fotos de dos rafagas distintas en la lista de entrada."""
    a0 = tmp_path / "a0.jpg"
    a1 = tmp_path / "a1.jpg"
    b0 = tmp_path / "b0.jpg"
    b1 = tmp_path / "b1.jpg"
    _guardar_con_fecha(a0, BASE)
    _guardar_con_fecha(a1, BASE + timedelta(seconds=4))
    inicio_b = BASE + timedelta(seconds=4 + 40)  # hueco 40s >= 20s: nueva rafaga
    _guardar_con_fecha(b0, inicio_b)
    _guardar_con_fecha(b1, inicio_b + timedelta(seconds=4))

    grupos = agrupar([b1, a0, b0, a1])  # completamente desordenado

    assert len(grupos) == 2
    conjuntos = {tuple(g.fotos) for g in grupos}
    assert (a0, a1) in conjuntos
    assert (b0, b1) in conjuntos


# --------------------------------------------------------------------------
# 7. LA PROPIEDAD QUE IMPORTA: con jitter fuerte y numero variable de
#    rafagas/fotos por rafaga, el algoritmo puede partir de mas (barato --
#    Diego fusiona en segundos) pero JAMAS debe unir dos rafagas separadas
#    por un hueco >= UMBRAL_HUECO_SEGUNDOS (caro -- nadie lo caza). No hay
#    `hypothesis` instalado en este entorno: se hace a mano con 40 semillas
#    distintas, cada una fabricando un lote sintetico distinto.
# --------------------------------------------------------------------------
def _lote_de_rafagas(rnd: random.Random, tmp_path: Path) -> tuple[list[Path], dict[Path, int]]:
    """Fabrica un lote de N rafagas (2 a 6), cada una de 1 a 5 fotos, con
    jitter FUERTE dentro de la rafaga (huecos intra siempre estrictamente
    por debajo del umbral) y separadas entre si por huecos siempre por
    encima o iguales al umbral -- la unica frontera "valida" es la de
    rafaga. Devuelve las rutas y a que rafaga pertenece cada una, para que
    el test pueda comprobar la invariante sin conocer el algoritmo."""
    n_rafagas = rnd.randint(2, 6)
    t = 0
    contador = 0
    rutas: list[Path] = []
    rafaga_por_ruta: dict[Path, int] = {}

    for r in range(n_rafagas):
        n_fotos = rnd.randint(1, 5)
        for f in range(n_fotos):
            ruta = tmp_path / f"foto_{contador:04d}.jpg"
            _guardar_con_fecha(ruta, BASE + timedelta(seconds=t))
            rutas.append(ruta)
            rafaga_por_ruta[ruta] = r
            contador += 1
            if f < n_fotos - 1:
                # jitter intra-rafaga: 0..19s, SIEMPRE por debajo del umbral
                t += rnd.randint(0, int(UMBRAL_HUECO_SEGUNDOS) - 1)
        if r < n_rafagas - 1:
            # separacion inter-rafaga: 20..320s, SIEMPRE >= umbral
            t += rnd.randint(int(UMBRAL_HUECO_SEGUNDOS), int(UMBRAL_HUECO_SEGUNDOS) + 300)

    return rutas, rafaga_por_ruta


@pytest.mark.parametrize("semilla", range(40))
def test_property_dos_rafagas_separadas_por_el_umbral_nunca_caen_en_el_mismo_grupo(
    tmp_path, semilla
):
    rnd = random.Random(semilla)
    sub = tmp_path / f"lote_{semilla}"
    sub.mkdir()
    rutas, rafaga_por_ruta = _lote_de_rafagas(rnd, sub)

    orden_de_entrada = list(rutas)
    rnd.shuffle(orden_de_entrada)  # el orden de entrada no deberia importar

    grupos = agrupar(orden_de_entrada)

    # nada se pierde ni se duplica por el camino
    fotos_en_grupos = sorted((f.name for g in grupos for f in g.fotos))
    assert fotos_en_grupos == sorted(f.name for f in rutas)

    # LA INVARIANTE: ningun grupo mezcla fotos de dos rafagas distintas.
    # Partir una rafaga en varios grupos (sobre-cortar) esta PERMITIDO;
    # fusionar dos rafagas en un grupo NUNCA lo esta.
    for g in grupos:
        rafagas_del_grupo = {rafaga_por_ruta[f] for f in g.fotos}
        assert len(rafagas_del_grupo) == 1, (
            f"semilla={semilla}: un grupo mezcla las rafagas {sorted(rafagas_del_grupo)} "
            f"(deberian estar separadas por >= {UMBRAL_HUECO_SEGUNDOS:.0f}s) -- "
            f"fotos: {[f.name for f in g.fotos]}"
        )
