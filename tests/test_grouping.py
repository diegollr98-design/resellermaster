"""Tests de core/grouping.py -- costura de AGRUPACION (superficie sensible).

Casos REALES, no de laboratorio (pedido explicito de la tarea):
  1. Dos productos fotografiados seguidos con poco hueco (absoluto) entre
     ellos -- el caso donde un umbral de tiempo FIJO fallaria y uno
     RELATIVO a la mediana de huecos del lote acierta.
  2. Un lote sin ningun EXIF.
  3. Una foto intrusa metida en medio de la sesion de otro producto (el
     caso que la confirmacion por pHash existe para cazar).
  4. Un lote de una sola foto.

Mas invariantes generales de una superficie sensible: ninguna foto se
pierde, ninguna se duplica entre grupos, y todo motivo es texto no vacio
en espanol para que Diego lo lea.

Genera imagenes sinteticas con EXIF fabricado a mano -- no depende de
fotos reales ni de red. Cero coste. El fabricado de EXIF replica el
patron ya usado en `tests/test_images.py` (Orientation en IFD0,
DateTimeOriginal en la sub-IFD "Exif").
"""

from __future__ import annotations

import io
import random
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import ExifTags, Image, ImageDraw, ImageEnhance

from core.grouping import UMBRAL_COLORHASH_MUY_DISTINTA, UMBRAL_PHASH_MUY_DISTINTA, Grupo, agrupar

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


def _imagen_producto(color_fondo=(240, 240, 240), color_forma=(200, 30, 30)) -> Image.Image:
    img = Image.new("RGB", (64, 64), color_fondo)
    d = ImageDraw.Draw(img)
    d.ellipse((10, 10, 54, 54), fill=color_forma)
    return img


def _variante_leve(img: Image.Image, factor_brillo: float) -> Image.Image:
    """Simula otra foto del MISMO producto (angulo/luz ligeramente distinta,
    mismo encuadre y color de fondo) -- debe quedar POR DEBAJO de
    `UMBRAL_PHASH_MUY_DISTINTA` frente a la foto base."""
    return ImageEnhance.Brightness(img).enhance(factor_brillo)


def _imagen_intrusa() -> Image.Image:
    """Contenido sin ninguna relacion visual con `_imagen_producto()`:
    patron de tablero, deliberadamente muy por encima de
    `UMBRAL_PHASH_MUY_DISTINTA` (calibrado en el docstring de
    core/grouping.py: un tablero midio 31 de distancia frente al producto
    base, el umbral es 24)."""
    img = Image.new("RGB", (64, 64))
    d = ImageDraw.Draw(img)
    for x in range(0, 64, 8):
        for y in range(0, 64, 8):
            color = (0, 0, 0) if (x // 8 + y // 8) % 2 == 0 else (255, 255, 255)
            d.rectangle((x, y, x + 8, y + 8), fill=color)
    return img


def _imagen_producto_muy_distinta() -> Image.Image:
    """Otra "foto de producto", pero con forma, posicion Y color distintos
    (no solo el color: pHash aqui es sobre luminancia -- dos siluetas
    identicas en distinto color pueden dar distancia 0, medido). Sirve
    para simular "otro producto" real, no solo una variante del mismo."""
    img = Image.new("RGB", (64, 64), (30, 30, 200))
    d = ImageDraw.Draw(img)
    d.rectangle((4, 4, 40, 60), fill=(30, 200, 30))
    return img


def _imagen_triangulo() -> Image.Image:
    """Otra forma mas, para tener varios "productos" mutuamente distintos
    entre si por pHash (medido: >26 de distancia frente a las demas
    helpers de este fichero)."""
    img = Image.new("RGB", (64, 64), (250, 250, 200))
    d = ImageDraw.Draw(img)
    d.polygon([(32, 2), (2, 60), (62, 60)], fill=(10, 10, 10))
    return img


def _recomprimir_jpeg(img: Image.Image, calidad: int) -> Image.Image:
    """Simula una foto "casi duplicada" real: la misma imagen, recomprimida
    a otra calidad JPEG (distancia de pHash medida: 4, por debajo del
    umbral de casi-duplicado de `core.images` que es 5)."""
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=calidad)
    buf.seek(0)
    return Image.open(buf)


def _guardar_con_fecha(ruta: Path, img: Image.Image, fecha: datetime) -> None:
    img.save(ruta, format="JPEG", quality=90, exif=_exif_con_fecha(fecha))


def _guardar_sin_exif(ruta: Path, img: Image.Image) -> None:
    img.save(ruta, format="JPEG", quality=90)


BASE = datetime(2026, 7, 10, 12, 0, 0)


# --------------------------------------------------------------------------
# Caso 1 -- dos productos seguidos, hueco pequeno EN ABSOLUTO entre ellos
# --------------------------------------------------------------------------


def test_dos_productos_seguidos_con_hueco_pequeno_se_separan(tmp_path):
    # Producto A: 3 fotos a ritmo de rafaga (4 s entre disparos).
    fotos_a = []
    for i, seg in enumerate((0, 4, 8)):
        ruta = tmp_path / f"a_{i}.jpg"
        _guardar_con_fecha(ruta, _imagen_producto(), BASE + timedelta(seconds=seg))
        fotos_a.append(ruta)

    # Hueco entre A y B: 30 s. Pequeno en terminos absolutos (medio
    # minuto), pero >7x el ritmo intra-producto (4 s) -- justo el caso
    # donde un umbral fijo tipo "5 minutos" NO cortaria (30s < 300s) pero
    # el umbral relativo a la mediana (4s * FACTOR_CORTE_MEDIANA=3 = 12s)
    # si lo hace (30s > 12s).
    inicio_b = BASE + timedelta(seconds=8 + 30)
    fotos_b = []
    for i, seg in enumerate((0, 4, 8)):
        ruta = tmp_path / f"b_{i}.jpg"
        _guardar_con_fecha(
            ruta,
            _imagen_producto(color_forma=(30, 30, 200)),
            inicio_b + timedelta(seconds=seg),
        )
        fotos_b.append(ruta)

    grupos = agrupar(fotos_a + fotos_b)

    assert len(grupos) == 2
    conjuntos = [set(g.fotos) for g in grupos]
    assert set(fotos_a) in conjuntos
    assert set(fotos_b) in conjuntos
    for g in grupos:
        assert g.confianza == "alta"
        assert g.motivo


def test_grupo_unico_disparado_a_ritmo_constante_no_se_corte_de_mas(tmp_path):
    # Un solo producto, 5 fotos a ritmo PERFECTAMENTE constante (huecos
    # todos iguales, sin ninguna variacion): no debe partirse en falso --
    # pero tampoco hay ninguna frontera que buscar en una distribucion sin
    # variacion, asi que `_umbral_corte` devuelve None [C2] y el techo de
    # confianza es "media", nunca "alta" (sin senal primaria que respalde
    # la certeza maxima, aunque las fotos sean visualmente consistentes).
    fotos = []
    for i, seg in enumerate((0, 3, 6, 9, 12)):
        ruta = tmp_path / f"p_{i}.jpg"
        _guardar_con_fecha(ruta, _imagen_producto(), BASE + timedelta(seconds=seg))
        fotos.append(ruta)

    grupos = agrupar(fotos)

    assert len(grupos) == 1
    assert set(grupos[0].fotos) == set(fotos)
    assert grupos[0].confianza == "media"
    assert "huecos suficientes" in grupos[0].motivo


# --------------------------------------------------------------------------
# Caso 2 -- un lote sin ningun EXIF
# --------------------------------------------------------------------------


def test_lote_sin_exif_agrupa_solo_por_phash_casi_duplicado(tmp_path):
    base = _imagen_producto()

    # Dos fotos SIN exif que son casi-duplicadas de verdad (la misma
    # recomprimida a otra calidad JPEG, como reenviarla por WhatsApp):
    # deben terminar en el mismo grupo.
    casi_1 = tmp_path / "whatsapp_1.jpg"
    casi_2 = tmp_path / "whatsapp_2.jpg"
    _guardar_sin_exif(casi_1, base)
    _guardar_sin_exif(casi_2, _recomprimir_jpeg(base, calidad=60))

    # Una tercera foto sin exif, completamente distinta: no debe unirse.
    suelta = tmp_path / "captura_pantalla.jpg"
    _guardar_sin_exif(suelta, _imagen_intrusa())

    grupos = agrupar([casi_1, casi_2, suelta])

    # Ninguna foto se pierde ni se duplica entre grupos.
    todas = [foto for g in grupos for foto in g.fotos]
    assert sorted(todas) == sorted([casi_1, casi_2, suelta])

    grupo_por_foto = {foto: g for g in grupos for foto in g.fotos}
    assert grupo_por_foto[casi_1] is grupo_por_foto[casi_2]
    assert grupo_por_foto[suelta] is not grupo_por_foto[casi_1]

    grupo_pareja = grupo_por_foto[casi_1]
    assert grupo_pareja.confianza == "media"  # nunca "alta" sin EXIF (senal primaria)
    assert grupo_pareja.motivo

    grupo_suelta = grupo_por_foto[suelta]
    assert len(grupo_suelta.fotos) == 1
    assert grupo_suelta.confianza == "baja"
    assert "no tiene fecha" in grupo_suelta.motivo


def test_lote_sin_exif_todas_distintas_cada_foto_es_su_propio_grupo_dudoso(tmp_path):
    # Cuatro formas mutuamente distintas por pHash (no solo color -- ver
    # docstring de `_imagen_producto_muy_distinta`), ninguna por debajo del
    # umbral de casi-duplicado: ninguna debe unirse con otra.
    imagenes = [_imagen_producto(), _imagen_producto_muy_distinta(), _imagen_intrusa(), _imagen_triangulo()]
    rutas = []
    for i, img in enumerate(imagenes):
        ruta = tmp_path / f"foto_{i}.jpg"
        _guardar_sin_exif(ruta, img)
        rutas.append(ruta)

    grupos = agrupar(rutas)

    assert len(grupos) == len(rutas)  # cada una sola, ninguna se coló con otra
    for g in grupos:
        assert len(g.fotos) == 1
        assert g.confianza == "baja"
        assert g.motivo


# --------------------------------------------------------------------------
# Caso 3 -- foto intrusa metida en medio de la sesion de otro producto
# --------------------------------------------------------------------------


def test_foto_intrusa_en_medio_de_la_sesion_baja_la_confianza_pero_no_se_expulsa(tmp_path):
    # 4 fotos del MISMO producto (variantes de brillo leve, todas por
    # debajo de UMBRAL_PHASH_MUY_DISTINTA entre si) disparadas seguidas...
    base = _imagen_producto()
    secuencia = [
        (0, base),
        (3, _variante_leve(base, 1.1)),
        (6, _imagen_intrusa()),  # <- la intrusa, EXIF perfectamente en medio
        (9, _variante_leve(base, 0.9)),
    ]

    rutas = []
    for seg, img in secuencia:
        ruta = tmp_path / f"foto_{seg}.jpg"
        _guardar_con_fecha(ruta, img, BASE + timedelta(seconds=seg))
        rutas.append(ruta)

    grupos = agrupar(rutas)

    # El timestamp no da ningun motivo para cortar (huecos uniformes de
    # 3s) -- las 4 fotos siguen en UN solo grupo temporal.
    assert len(grupos) == 1
    grupo = grupos[0]
    assert set(grupo.fotos) == set(rutas)  # la intrusa NO se expulsa

    # Pero la confirmacion por pHash la detecta y baja la confianza.
    assert grupo.confianza == "baja"
    intrusa_nombre = rutas[2].name
    assert intrusa_nombre in grupo.motivo
    assert "cruzada" in grupo.motivo


def test_umbral_phash_muy_distinta_esta_documentado_y_es_positivo():
    # Guardarraíl minimo: si alguien cambia la constante sin querer a algo
    # sin sentido (negativo, cero), que un test lo note.
    assert UMBRAL_PHASH_MUY_DISTINTA > 0


# --------------------------------------------------------------------------
# Caso 4 -- un lote de una sola foto
# --------------------------------------------------------------------------


def test_lote_de_una_sola_foto_con_exif_es_grupo_dudoso(tmp_path):
    ruta = tmp_path / "unica.jpg"
    _guardar_con_fecha(ruta, _imagen_producto(), BASE)

    grupos = agrupar([ruta])

    assert len(grupos) == 1
    assert grupos[0].fotos == [ruta]
    assert grupos[0].confianza == "baja"
    assert grupos[0].motivo


def test_lote_de_una_sola_foto_sin_exif_es_grupo_dudoso(tmp_path):
    ruta = tmp_path / "unica_sin_exif.jpg"
    _guardar_sin_exif(ruta, _imagen_producto())

    grupos = agrupar([ruta])

    assert len(grupos) == 1
    assert grupos[0].fotos == [ruta]
    assert grupos[0].confianza == "baja"
    assert grupos[0].motivo


def test_lote_vacio_devuelve_lista_vacia():
    assert agrupar([]) == []


# --------------------------------------------------------------------------
# Invariantes generales de una superficie sensible
# --------------------------------------------------------------------------


def test_ninguna_foto_se_pierde_ni_se_duplica_entre_grupos(tmp_path):
    # Lote mixto: dos productos con EXIF (uno con hueco de sobra para
    # cortar), una pareja sin EXIF casi-duplicada, y una foto suelta sin
    # EXIF y sin parecido con nada.
    fotos_a = []
    for i, seg in enumerate((0, 5, 10)):
        ruta = tmp_path / f"a_{i}.jpg"
        _guardar_con_fecha(ruta, _imagen_producto(), BASE + timedelta(seconds=seg))
        fotos_a.append(ruta)

    fotos_b = []
    inicio_b = BASE + timedelta(minutes=10)
    for i, seg in enumerate((0, 5, 10)):
        ruta = tmp_path / f"b_{i}.jpg"
        _guardar_con_fecha(
            ruta, _imagen_producto(color_forma=(30, 30, 200)), inicio_b + timedelta(seconds=seg)
        )
        fotos_b.append(ruta)

    base_sin_exif = _imagen_producto(color_forma=(30, 200, 30))
    casi_1 = tmp_path / "c_1.jpg"
    casi_2 = tmp_path / "c_2.jpg"
    _guardar_sin_exif(casi_1, base_sin_exif)
    _guardar_sin_exif(casi_2, _recomprimir_jpeg(base_sin_exif, calidad=60))

    suelta = tmp_path / "suelta.jpg"
    _guardar_sin_exif(suelta, _imagen_intrusa())

    todas_las_fotos = fotos_a + fotos_b + [casi_1, casi_2, suelta]
    grupos = agrupar(todas_las_fotos)

    fotos_en_grupos = [foto for g in grupos for foto in g.fotos]
    assert sorted(fotos_en_grupos) == sorted(todas_las_fotos)  # nada se pierde
    assert len(fotos_en_grupos) == len(set(fotos_en_grupos))  # nada se duplica


def test_todos_los_motivos_son_texto_no_vacio_en_todos_los_grupos(tmp_path):
    fotos = []
    for i, seg in enumerate((0, 4, 8)):
        ruta = tmp_path / f"p_{i}.jpg"
        _guardar_con_fecha(ruta, _imagen_producto(), BASE + timedelta(seconds=seg))
        fotos.append(ruta)
    suelta = tmp_path / "suelta.jpg"
    _guardar_sin_exif(suelta, _imagen_intrusa())

    grupos = agrupar(fotos + [suelta])

    for g in grupos:
        assert isinstance(g, Grupo)
        assert isinstance(g.motivo, str) and g.motivo.strip()
        assert g.confianza in ("alta", "media", "baja")


# --------------------------------------------------------------------------
# [INC-002] -- regresion de la mediana como umbral de corte, y de la
# ceguera al color de pHash. Cada test de aqui abajo reproduce, EJECUTANDO
# el codigo real (no en el papel), uno de los casos rotos del incidente.
# --------------------------------------------------------------------------

_PALETA_COLORES: list[tuple[int, int, int]] = [
    (200, 30, 30),  # roja
    (30, 30, 200),  # azul
    (25, 25, 25),  # negra
    (225, 225, 225),  # blanca
]
"""Paleta de colores CALIBRADA (ver docstring de
`UMBRAL_COLORHASH_MUY_DISTINTA` en `core/grouping.py`): distancia MINIMA
medida entre cualquier par de estos 4 colores = 3, por encima del umbral
(2). Otros colores (verde, marron...) se probaron y algunos pares dieron
distancia 0 -- `colorhash` usa solo 6 cubos de tono y colores adyacentes
en el circulo cromatico pueden colisionar (limite documentado, no un bug
de este test). Esta paleta es la que la evidencia real de [INC-002] usa,
asi que ademas de segura es la mas representativa."""


def _lote_multiples_productos(
    tmp_path: Path,
    n_productos: int,
    fotos_por_producto: int,
    intra: float,
    inter: float,
    jitter: float,
    seed: int,
) -> tuple[list[Path], dict[Path, int]]:
    """Fabrica un lote de `n_productos`, cada uno con `fotos_por_producto`
    fotos a un ritmo intra-producto `intra` s y un hueco entre productos
    `inter` s -- ambos con jitter humano UNIFORME +-`jitter` s (nunca <=0,
    un disparo no puede ir hacia atras en el tiempo). Cada producto tiene
    un color DISTINTO (ciclando `_PALETA_COLORES`, misma silueta para
    todos -- pHash por si solo NO puede distinguirlos, ver [INC-002]) para
    poder comprobar despues que ningun grupo mezcla productos. Devuelve
    las rutas en orden de disparo y el indice de producto de cada una."""
    rnd = random.Random(seed)
    rutas: list[Path] = []
    producto_por_ruta: dict[Path, int] = {}
    t = 0.0
    for p in range(n_productos):
        color = _PALETA_COLORES[p % len(_PALETA_COLORES)]
        for f in range(fotos_por_producto):
            ruta = tmp_path / f"prod{p}_{f}.jpg"
            _guardar_con_fecha(
                ruta, _imagen_producto(color_forma=color), BASE + timedelta(seconds=t)
            )
            rutas.append(ruta)
            producto_por_ruta[ruta] = p
            if f < fotos_por_producto - 1:
                t += max(0.1, intra + rnd.uniform(-jitter, jitter))
        if p < n_productos - 1:
            t += max(0.1, inter + rnd.uniform(-jitter, jitter))
    return rutas, producto_por_ruta


def _grupos_alta_mezclan_productos(
    grupos: list[Grupo], producto_por_ruta: dict[Path, int]
) -> list[tuple[Grupo, set[int]]]:
    """Grupos `confianza="alta"` cuyas fotos pertenecen a MAS DE UN
    producto real -- si esta lista no esta vacia, el invariante duro del
    modulo se ha roto."""
    rotos = []
    for g in grupos:
        if g.confianza != "alta":
            continue
        productos = {producto_por_ruta[f] for f in g.fotos}
        if len(productos) > 1:
            rotos.append((g, productos))
    return rotos


@pytest.mark.parametrize("ratio", [2.0, 2.5, 3.0, 3.5, 4.0])
def test_barrido_ratio_inter_intra_2_a_4_ningun_grupo_alta_mezcla_productos(tmp_path, ratio):
    # El rango que el diseño ORIGINAL (mediana * 3, ratio de corte
    # efectivo 3.0x) fallaba por debajo de -- y donde vivia el bug real de
    # [INC-002] (ratio 3.0x con 8 prendas de 3 fotos ya mezclaba 8
    # productos en un grupo "alta"). El invariante duro tiene que
    # aguantar en TODO este rango, con jitter humano +-1s.
    intra = 4.0
    inter = intra * ratio
    for seed in range(15):
        sub = tmp_path / f"ratio_{ratio}_seed_{seed}"
        sub.mkdir()
        rutas, producto_por_ruta = _lote_multiples_productos(
            sub,
            n_productos=6,
            fotos_por_producto=3,
            intra=intra,
            inter=inter,
            jitter=1.0,
            seed=seed,
        )
        grupos = agrupar(rutas)

        # Ninguna foto se pierde ni se duplica -- invariante general de
        # una superficie sensible, valido tambien aqui.
        fotos_en_grupos = [f for g in grupos for f in g.fotos]
        assert sorted(fotos_en_grupos) == sorted(rutas)

        rotos = _grupos_alta_mezclan_productos(grupos, producto_por_ruta)
        assert not rotos, (
            f"ratio={ratio} seed={seed}: grupo(s) 'alta' mezclando productos: "
            f"{[(productos, [f.name for f in g.fotos]) for g, productos in rotos]}"
        )


def test_invariante_duro_confianza_alta_nunca_mezcla_productos_distintos(tmp_path):
    """El contrato no negociable del modulo (`.claude/rules/truth-loop.md`
    SS E): un grupo `confianza="alta"` JAMAS puede tener fotos de dos
    productos distintos. Se comprueba contra varios escenarios adversos a
    la vez, incluida la reproduccion EXACTA de [INC-002] (8 prendas, 3
    fotos c/u, 4s intra-producto / 12s inter-producto, SIN jitter -- el
    caso que rompia el diseño anterior: mediana=4, umbral=4*3=12, y
    `12 > 12` es falso, cero cortes, 8 productos en un solo grupo
    "alta")."""
    # 1) La reproduccion exacta del incidente, sin jitter.
    sub1 = tmp_path / "inc002_exacto"
    sub1.mkdir()
    rutas1, productos1 = _lote_multiples_productos(
        sub1, n_productos=8, fotos_por_producto=3, intra=4.0, inter=12.0, jitter=0.0, seed=0
    )
    grupos1 = agrupar(rutas1)
    assert not _grupos_alta_mezclan_productos(grupos1, productos1)
    # Ademas, en este caso limpio (sin jitter) el diseño nuevo SI debe
    # separar los 8 productos correctamente (no solo evitar la mezcla a
    # "alta" -- de hecho aqui deben poder llegar a "alta" porque el margen
    # es holgado, ver docstring de FACTOR_MARGEN_ALTA).
    assert len(grupos1) == 8
    assert all(g.confianza == "alta" for g in grupos1)

    # 2) Con jitter humano +-1s y ratio ajustado (2.0x, el mas dificil).
    sub2 = tmp_path / "ratio_2_jitter"
    sub2.mkdir()
    rutas2, productos2 = _lote_multiples_productos(
        sub2, n_productos=10, fotos_por_producto=4, intra=4.0, inter=8.0, jitter=1.0, seed=7
    )
    grupos2 = agrupar(rutas2)
    assert not _grupos_alta_mezclan_productos(grupos2, productos2)

    # 3) Rafaga: muchas fotos de productos distintos con hueco 0 (reloj
    # de camara desincronizado o disparo multiple). Ver test dedicado mas
    # abajo para el caso minimo; aqui se repite a mayor escala como parte
    # del invariante combinado.
    sub3 = tmp_path / "rafaga"
    sub3.mkdir()
    rutas3, productos3 = _lote_multiples_productos(
        sub3, n_productos=4, fotos_por_producto=3, intra=0.0, inter=0.0, jitter=0.0, seed=0
    )
    grupos3 = agrupar(rutas3)
    assert not _grupos_alta_mezclan_productos(grupos3, productos3)


def test_dos_prendas_mismo_corte_distinto_color_en_medio_de_la_sesion_se_cazan_por_color(
    tmp_path,
):
    # El caso EXACTO de la evidencia de [INC-002]: pHash es ciego al
    # color (misma silueta, distinta camiseta -> distancia de Hamming de
    # pHash = 0, medido). Una foto de OTRO color, MISMA forma, colada en
    # medio de una sesion de fotos de un producto: si la confirmacion
    # visual dependiera solo de pHash, esto pasaria colado en silencio.
    roja = _imagen_producto(color_forma=(200, 30, 30))
    azul_intrusa = _imagen_producto(color_forma=(30, 30, 200))  # MISMA forma, otro color
    secuencia = [(0, roja), (3, roja), (6, azul_intrusa), (9, roja)]

    rutas = []
    for seg, img in secuencia:
        ruta = tmp_path / f"foto_{seg}.jpg"
        _guardar_con_fecha(ruta, img, BASE + timedelta(seconds=seg))
        rutas.append(ruta)

    grupos = agrupar(rutas)

    # El timestamp (huecos uniformes de 3s) no da ningun motivo para
    # cortar -- las 4 fotos siguen en UN solo grupo temporal.
    assert len(grupos) == 1
    grupo = grupos[0]
    assert set(grupo.fotos) == set(rutas)

    # Pero la confirmacion por COLOR (no por forma) la caza.
    assert grupo.confianza == "baja"
    assert rutas[2].name in grupo.motivo
    assert "color" in grupo.motivo


def test_umbral_corte_none_un_solo_hueco_no_puede_ser_alta(tmp_path):
    # Dos fotos, MISMA forma y MISMO color (para aislar el fallo: aqui NO
    # hay ninguna senal visual que las distinga, la UNICA red de
    # seguridad posible es la ausencia de senal de tiempo), separadas por
    # un unico hueco de 10 minutos. `_umbral_corte` no tiene distribucion
    # de la que derivar "grande" con un solo dato -- devuelve `None` -- y
    # el grupo NUNCA puede salir "alta" sin esa senal primaria [C2],
    # aunque bien podrian ser dos productos distintos que comparten
    # silueta y color (un patron muy comun en ropa basica).
    producto = _imagen_producto()
    ruta_1 = tmp_path / "p1.jpg"
    ruta_2 = tmp_path / "p2.jpg"
    _guardar_con_fecha(ruta_1, producto, BASE)
    _guardar_con_fecha(ruta_2, producto, BASE + timedelta(minutes=10))

    grupos = agrupar([ruta_1, ruta_2])

    assert len(grupos) == 1
    assert set(grupos[0].fotos) == {ruta_1, ruta_2}
    assert grupos[0].confianza != "alta"
    assert "huecos suficientes" in grupos[0].motivo


def test_umbral_corte_none_rafaga_huecos_cero_con_producto_distinto_nunca_alta(tmp_path):
    # Varias fotos con el MISMO timestamp EXIF exacto (rafaga extrema, o
    # un reloj de camara mal calibrado que redondea al segundo) -- TODOS
    # los huecos son 0. `_umbral_corte` no tiene ninguna base para cortar
    # [C2]. Se cuela una foto de un producto claramente distinto (misma
    # silueta, otro color): el invariante duro exige que, pase lo que
    # pase con la confirmacion visual, esto NUNCA pueda salir "alta".
    roja = _imagen_producto(color_forma=(200, 30, 30))
    azul_distinta = _imagen_producto(color_forma=(30, 30, 200))
    rutas = []
    for i, img in enumerate([roja, roja, roja, azul_distinta, roja]):
        ruta = tmp_path / f"foto_{i}.jpg"
        _guardar_con_fecha(ruta, img, BASE)  # el mismo segundo para todas
        rutas.append(ruta)

    grupos = agrupar(rutas)

    assert len(grupos) == 1
    assert set(grupos[0].fotos) == set(rutas)
    assert grupos[0].confianza != "alta"
    # Con el color distinto colandose, la confirmacion visual SI la caza
    # (ademas de que el techo por None ya lo impedia).
    assert grupos[0].confianza == "baja"


def test_lote_sin_exif_cuatro_colores_distintos_no_se_funden_en_uno(tmp_path):
    # [INC-002], ruta sin EXIF: con distancia de pHash 0 (misma silueta),
    # cuatro camisetas de colores CLARAMENTE distintos pasaban el umbral
    # de casi-duplicado (5) y salian como UN producto, motivo
    # "probablemente el mismo producto". Ahora hace falta TAMBIEN
    # parecerse en color (`UMBRAL_COLORHASH_MUY_DISTINTA`).
    colores = [(200, 30, 30), (30, 30, 200), (25, 25, 25), (225, 225, 225)]
    rutas = []
    for i, color in enumerate(colores):
        ruta = tmp_path / f"prenda_{i}.jpg"
        _guardar_sin_exif(ruta, _imagen_producto(color_forma=color))
        rutas.append(ruta)

    grupos = agrupar(rutas)

    fotos_en_grupos = [f for g in grupos for f in g.fotos]
    assert sorted(fotos_en_grupos) == sorted(rutas)  # nada se pierde ni se duplica

    # Ninguna de las 4 camisetas de color distinto termina en el mismo
    # grupo que otra -- cada una es su propio grupo dudoso, nunca "alta".
    assert len(grupos) == 4
    for g in grupos:
        assert len(g.fotos) == 1
        assert g.confianza == "baja"


def test_umbral_colorhash_muy_distinta_esta_documentado_y_es_positivo():
    # Guardarraíl minimo, igual que el de pHash: si alguien cambia la
    # constante sin querer a algo sin sentido, que un test lo note.
    assert UMBRAL_COLORHASH_MUY_DISTINTA > 0
