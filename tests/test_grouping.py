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
from datetime import datetime, timedelta
from pathlib import Path

from PIL import ExifTags, Image, ImageDraw, ImageEnhance

from core.grouping import UMBRAL_PHASH_MUY_DISTINTA, Grupo, agrupar

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
    # Un solo producto, 5 fotos a ritmo perfectamente constante: la
    # mediana de huecos es el propio ritmo, ningun hueco la triplica ->
    # no debe partirse en falso.
    fotos = []
    for i, seg in enumerate((0, 3, 6, 9, 12)):
        ruta = tmp_path / f"p_{i}.jpg"
        _guardar_con_fecha(ruta, _imagen_producto(), BASE + timedelta(seconds=seg))
        fotos.append(ruta)

    grupos = agrupar(fotos)

    assert len(grupos) == 1
    assert set(grupos[0].fotos) == set(fotos)
    assert grupos[0].confianza == "alta"


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
