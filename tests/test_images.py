"""Tests de core/images.py — capa de imagen (EXIF, orientación, hashing,
dedup, miniaturas, orden sugerido y export por producto).

Genera imágenes sintéticas con EXIF fabricado a mano (incluye una SIN EXIF
y una corrupta) — no depende de fotos reales ni de red. Cero coste.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFilter

from core.images import (
    EXTENSIONES_SOPORTADAS,
    LIMITE_FOTOS_PLATAFORMA,
    PlataformaDesconocidaError,
    SlugInvalidoError,
    calcular_phash,
    duplicados_exactos,
    duplicados_similares,
    es_soportada,
    exportar_producto,
    hashear_lote,
    leer_metadatos,
    normalizar_orientacion,
    obtener_o_crear_miniatura,
    puntuacion_nitidez,
    sha256_de_fichero,
    sugerir_orden,
)

# --------------------------------------------------------------------------
# Helpers de fabricación de imágenes sintéticas
# --------------------------------------------------------------------------


def _exif_con(orientacion: int | None = None, fecha_original: str | None = None) -> Image.Exif:
    """Construye un bloque EXIF a mano: Orientation en IFD0 (tag 274, donde
    vive de verdad), DateTimeOriginal en la sub-IFD "Exif" (tag 36867,
    donde vive de verdad — NO en IFD0, error común que produciría lecturas
    siempre-None).

    Nota de fabricación: Pillow sólo serializa el bloque EXIF a PNG/JPEG si
    IFD0 tiene al menos un tag asignado directamente; si sólo se toca la
    sub-IFD "Exif" (p. ej. sólo se pide `fecha_original`, sin orientación),
    el bloque se queda vacío al guardar y la lectura da siempre `None` — no
    es un bug de `core/images.py`, es cómo serializa `PIL.Image.Exif`. Una
    cámara real siempre trae Orientation en IFD0, así que aquí se fuerza lo
    mismo (por defecto 1 = normal) cuando el llamador sólo quiere la fecha."""
    from PIL import ExifTags

    exif = Image.Exif()
    exif[274] = orientacion if orientacion is not None else 1
    if fecha_original is not None:
        exif.get_ifd(ExifTags.IFD.Exif)[36867] = fecha_original
    return exif


def _guardar_png_con_marca(
    ruta: Path,
    tamano: tuple[int, int] = (60, 30),
    orientacion: int | None = None,
    fecha_original: str | None = None,
) -> None:
    """PNG blanco con un píxel rojo en la esquina superior-izquierda (0,0),
    para poder verificar exactamente a dónde se mueve tras normalizar la
    orientación. PNG es sin pérdida: los píxeles se pueden comparar exactos."""
    img = Image.new("RGB", tamano, (255, 255, 255))
    img.putpixel((0, 0), (255, 0, 0))
    exif = _exif_con(orientacion, fecha_original) if (orientacion or fecha_original) else None
    if exif is not None:
        img.save(ruta, format="PNG", exif=exif)
    else:
        img.save(ruta, format="PNG")


def _imagen_producto(color_fondo=(240, 240, 240), color_forma=(200, 30, 30)) -> Image.Image:
    """Imagen "tipo producto": forma sólida sobre fondo plano. Suficiente
    contenido de frecuencia para que pHash y la nitidez tengan algo que medir."""
    img = Image.new("RGB", (64, 64), color_fondo)
    d = ImageDraw.Draw(img)
    d.ellipse((10, 10, 54, 54), fill=color_forma)
    return img


def _imagen_producto_muy_distinta() -> Image.Image:
    """Otra "foto de producto", pero deliberadamente distinta en forma,
    posición Y color (no sólo el color) para que el pHash caiga lejos del
    de `_imagen_producto()` — sólo cambiar el color de la misma silueta no
    basta: el pHash pondera sobre todo la estructura de luminancia."""
    img = Image.new("RGB", (64, 64), (30, 30, 200))
    d = ImageDraw.Draw(img)
    d.rectangle((4, 4, 40, 60), fill=(30, 200, 30))
    return img


def _guardar_jpeg(ruta: Path, img: Image.Image, calidad: int = 90) -> None:
    img.save(ruta, format="JPEG", quality=calidad)


def _recomprimir_jpeg(img: Image.Image, calidad: int) -> Image.Image:
    """Simula una foto "casi duplicada" real: la misma imagen, recomprimida
    a otra calidad JPEG (mismo sha256 NO, pHash SÍ muy cercano)."""
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=calidad)
    buf.seek(0)
    return Image.open(buf)


def _escribir_corrupto(ruta: Path) -> None:
    ruta.write_bytes(b"esto no es una foto de verdad, son bytes basura 1234567890")


# --------------------------------------------------------------------------
# es_soportada / extensiones
# --------------------------------------------------------------------------


def test_extensiones_soportadas_incluye_heic_y_formatos_comunes():
    assert EXTENSIONES_SOPORTADAS == {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


@pytest.mark.parametrize(
    "nombre,esperado",
    [
        ("foto.jpg", True),
        ("foto.JPG", True),
        ("foto.jpeg", True),
        ("foto.png", True),
        ("foto.webp", True),
        ("IMG_0421.heic", True),
        ("IMG_0421.HEIC", True),
        ("nota.txt", False),
        ("sin_extension", False),
    ],
)
def test_es_soportada(nombre, esperado):
    assert es_soportada(Path(nombre)) is esperado


def test_limites_de_plataforma_son_los_verificados_en_product_md():
    # Vinted 20, Wallapop 10 — `rules/product.md`, verificado contra fuente
    # primaria. Si esto cambia algún día, tiene que cambiar aquí a propósito.
    assert LIMITE_FOTOS_PLATAFORMA == {"vinted": 20, "wallapop": 10}


# --------------------------------------------------------------------------
# EXIF: fecha de disparo real vs. mtime del fichero, orientación
# --------------------------------------------------------------------------


def test_leer_metadatos_con_exif_completo(tmp_path):
    ruta = tmp_path / "con_exif.png"
    _guardar_png_con_marca(ruta, orientacion=6, fecha_original="2024:05:01 10:00:00")

    meta = leer_metadatos(ruta)

    assert meta.legible is True
    assert meta.error is None
    assert meta.orientacion_exif == 6
    assert meta.fecha_captura_exif is not None
    assert meta.fecha_captura_exif.isoformat() == "2024-05-01T10:00:00"
    # mtime del fichero es un dato DISTINTO, nunca el mismo campo.
    assert meta.mtime_fichero is not None
    assert meta.ancho == 60 and meta.alto == 30


def test_leer_metadatos_sin_exif_devuelve_none_honesto_no_mtime_disfrazado(tmp_path):
    ruta = tmp_path / "sin_exif.png"
    _guardar_png_con_marca(ruta)  # sin exif

    meta = leer_metadatos(ruta)

    assert meta.legible is True
    assert meta.orientacion_exif == 1  # valor por defecto documentado, no un dato inventado
    assert meta.fecha_captura_exif is None  # honesto: no hay EXIF, no se rellena con nada
    assert meta.mtime_fichero is not None  # esto sí existe siempre (es del sistema de ficheros)


def test_leer_metadatos_fichero_corrupto_se_marca_ilegible_y_no_lanza(tmp_path, caplog):
    ruta = tmp_path / "corrupto.jpg"
    _escribir_corrupto(ruta)

    meta = leer_metadatos(ruta)  # NO debe lanzar

    assert meta.legible is False
    assert meta.error is not None
    assert meta.fecha_captura_exif is None
    assert meta.ancho is None and meta.alto is None
    # el fichero sí existe en disco, así que su mtime se puede leer
    # incluso siendo ilegible como imagen (son dos preguntas distintas).
    assert meta.mtime_fichero is not None


def test_leer_metadatos_fichero_inexistente_no_lanza(tmp_path):
    ruta = tmp_path / "no_existe.jpg"

    meta = leer_metadatos(ruta)

    assert meta.legible is False
    assert meta.error is not None
    assert meta.mtime_fichero is None
    assert meta.fecha_captura_exif is None


def test_leer_metadatos_heic(tmp_path):
    ruta = tmp_path / "iphone.heic"
    img = _imagen_producto()
    exif = _exif_con(fecha_original="2023:01:02 08:09:10")
    img.save(ruta, format="HEIF", exif=exif)

    meta = leer_metadatos(ruta)

    assert meta.legible is True, meta.error
    assert meta.fecha_captura_exif is not None
    assert meta.fecha_captura_exif.isoformat() == "2023-01-02T08:09:10"
    assert meta.ancho == 64 and meta.alto == 64


# --------------------------------------------------------------------------
# Normalización de orientación
# --------------------------------------------------------------------------


def test_normalizar_orientacion_rota_los_pixeles_de_verdad(tmp_path):
    ruta = tmp_path / "girada.png"
    # orientacion=6 sobre una imagen 60x30 con marca roja en (0,0):
    # comprobado empíricamente que Pillow, al normalizar, deja la imagen en
    # 30x60 con la marca desplazada a la esquina superior-DERECHA.
    _guardar_png_con_marca(ruta, tamano=(60, 30), orientacion=6)

    with Image.open(ruta) as img:
        assert img.size == (60, 30)
        assert img.getpixel((0, 0)) == (255, 0, 0)  # antes de normalizar, sin tocar

        derecha = normalizar_orientacion(img)

    assert derecha.size == (30, 60)  # la rotación cambia ancho/alto de verdad
    w, h = derecha.size
    assert derecha.getpixel((w - 1, 0)) == (255, 0, 0)
    assert derecha.getpixel((0, 0)) == (255, 255, 255)
    # el tag de orientación se limpia: los píxeles ya están del derecho
    assert derecha.getexif().get(274) in (None, 1)


def test_normalizar_orientacion_sin_tag_no_cambia_nada(tmp_path):
    ruta = tmp_path / "sin_rotar.png"
    _guardar_png_con_marca(ruta, tamano=(60, 30))

    with Image.open(ruta) as img:
        derecha = normalizar_orientacion(img)

    assert derecha.size == (60, 30)
    assert derecha.getpixel((0, 0)) == (255, 0, 0)


def test_normalizar_orientacion_nunca_toca_el_fichero_original(tmp_path):
    ruta = tmp_path / "original.png"
    _guardar_png_con_marca(ruta, orientacion=6)
    bytes_antes = ruta.read_bytes()

    with Image.open(ruta) as img:
        normalizar_orientacion(img)

    assert ruta.read_bytes() == bytes_antes


# --------------------------------------------------------------------------
# Hashing exacto + perceptual, deduplicación
# --------------------------------------------------------------------------


def test_sha256_identico_para_bytes_identicos_y_distinto_si_cambia_un_byte(tmp_path):
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"  # copia byte a byte de a
    c = tmp_path / "c.jpg"  # un byte distinto

    a.write_bytes(b"contenido-de-foto-1234567890")
    b.write_bytes(b"contenido-de-foto-1234567890")
    c.write_bytes(b"contenido-de-foto-1234567891")

    assert sha256_de_fichero(a) == sha256_de_fichero(b)
    assert sha256_de_fichero(a) != sha256_de_fichero(c)


def test_phash_distingue_casi_duplicado_de_foto_distinta(tmp_path):
    base = _imagen_producto()
    casi_duplicada = _recomprimir_jpeg(base, calidad=60)  # misma foto, recomprimida
    distinta = _imagen_producto_muy_distinta()  # otro producto

    h_base = calcular_phash(_ruta_jpeg(tmp_path, "base.jpg", base))
    h_casi = calcular_phash(_ruta_jpeg(tmp_path, "casi.jpg", casi_duplicada))
    h_distinta = calcular_phash(_ruta_jpeg(tmp_path, "distinta.jpg", distinta))

    from core.images import UMBRAL_DUPLICADO_SIMILAR_DEFECTO

    assert (h_base - h_casi) <= UMBRAL_DUPLICADO_SIMILAR_DEFECTO
    assert (h_base - h_distinta) > UMBRAL_DUPLICADO_SIMILAR_DEFECTO


def _ruta_jpeg(directorio: Path, nombre: str, img: Image.Image) -> Path:
    ruta = directorio / nombre
    _guardar_jpeg(ruta, img)
    return ruta


def test_hashear_lote_no_aborta_por_un_fichero_corrupto(tmp_path):
    buena_a = _ruta_jpeg(tmp_path, "buena_a.jpg", _imagen_producto())
    buena_b = _ruta_jpeg(tmp_path, "buena_b.jpg", _imagen_producto(color_forma=(30, 30, 200)))
    corrupta = tmp_path / "rota.jpg"
    _escribir_corrupto(corrupta)

    registros = hashear_lote([buena_a, corrupta, buena_b])

    assert len(registros) == 3  # el lote entero se procesa, nada se pierde por el camino
    por_ruta = {r.ruta: r for r in registros}
    assert por_ruta[buena_a].sha256 is not None
    assert por_ruta[buena_a].phash is not None
    assert por_ruta[buena_a].error is None
    assert por_ruta[buena_b].sha256 is not None
    # el fichero corrupto SÍ tiene sha256 (es sólo hashear bytes, no exige
    # decodificar una imagen válida) pero NO tiene phash (eso sí exige
    # decodificar) -- son fallos independientes, ver docstring de RegistroHash.
    assert por_ruta[corrupta].sha256 is not None
    assert por_ruta[corrupta].phash is None
    assert por_ruta[corrupta].error is not None
    assert "phash" in por_ruta[corrupta].error


def test_duplicados_exactos_agrupa_por_sha256_aunque_no_sean_imagenes_decodificables(tmp_path):
    # sha256 sólo lee bytes -- no necesita que el contenido sea una imagen
    # válida. Dos ficheros byte-a-byte idénticos deben agruparse igual
    # aunque ninguno se pueda decodificar como imagen (fichero dañado
    # arrastrado dos veces al lote sigue siendo, de forma verificable, el
    # mismo fichero dos veces).
    contenido = b"misma-foto-arrastrada-dos-veces-al-lote"
    a = tmp_path / "a.jpg"
    b_copia = tmp_path / "b_copia_de_a.jpg"
    c_distinta = tmp_path / "c.jpg"
    a.write_bytes(contenido)
    b_copia.write_bytes(contenido)
    c_distinta.write_bytes(b"otra-foto-completamente-distinta")

    registros = hashear_lote([a, b_copia, c_distinta])
    grupos = duplicados_exactos(registros)

    assert len(grupos) == 1
    (unico_grupo,) = grupos.values()
    assert set(unico_grupo) == {a, b_copia}


def test_duplicados_exactos_ignora_registros_sin_sha256(tmp_path):
    # Construcción directa de RegistroHash (sin pasar por hashear_lote):
    # un registro con sha256=None (p. ej. el fichero desapareció entre
    # listar el lote y hashearlo) nunca debe colarse en un grupo de
    # duplicados por accidente.
    from core.images import RegistroHash

    registros = [
        RegistroHash(ruta=Path("a.jpg"), sha256="abc", phash="1" * 16),
        RegistroHash(ruta=Path("b.jpg"), sha256="abc", phash="2" * 16),
        RegistroHash(ruta=Path("c.jpg"), sha256=None, phash=None, error="fichero no encontrado"),
    ]

    grupos = duplicados_exactos(registros)

    assert grupos == {"abc": [Path("a.jpg"), Path("b.jpg")]}


def test_duplicados_similares_respeta_el_umbral(tmp_path):
    base = _imagen_producto()
    casi_duplicada = _recomprimir_jpeg(base, calidad=60)
    distinta = _imagen_producto_muy_distinta()

    ruta_base = _ruta_jpeg(tmp_path, "base.jpg", base)
    ruta_casi = _ruta_jpeg(tmp_path, "casi.jpg", casi_duplicada)
    ruta_distinta = _ruta_jpeg(tmp_path, "distinta.jpg", distinta)

    registros = hashear_lote([ruta_base, ruta_casi, ruta_distinta])
    pares = duplicados_similares(registros)

    rutas_en_pares = {p for par in pares for p in par[:2]}
    assert ruta_base in rutas_en_pares and ruta_casi in rutas_en_pares
    assert ruta_distinta not in rutas_en_pares


# --------------------------------------------------------------------------
# Miniaturas cacheadas
# --------------------------------------------------------------------------


def test_miniatura_se_cachea_y_no_se_regenera_en_la_segunda_llamada(tmp_path):
    origen = _ruta_jpeg(tmp_path, "grande.jpg", _imagen_producto())
    cache = tmp_path / "cache_miniaturas"

    ruta_1 = obtener_o_crear_miniatura(origen, cache, tamano=(32, 32))
    mtime_1 = ruta_1.stat().st_mtime_ns

    ruta_2 = obtener_o_crear_miniatura(origen, cache, tamano=(32, 32))
    mtime_2 = ruta_2.stat().st_mtime_ns

    assert ruta_1 == ruta_2
    assert mtime_1 == mtime_2  # segunda llamada: cache hit, no se reescribe

    with Image.open(ruta_1) as miniatura:
        assert miniatura.size[0] <= 32 and miniatura.size[1] <= 32
        assert miniatura.format == "WEBP"


def test_miniatura_nunca_modifica_el_original(tmp_path):
    origen = _ruta_jpeg(tmp_path, "grande.jpg", _imagen_producto())
    bytes_antes = origen.read_bytes()
    cache = tmp_path / "cache_miniaturas"

    obtener_o_crear_miniatura(origen, cache, tamano=(32, 32))

    assert origen.read_bytes() == bytes_antes


def test_miniatura_ilegible_lanza_en_vez_de_devolver_algo_plausible(tmp_path):
    corrupta = tmp_path / "rota.jpg"
    _escribir_corrupto(corrupta)
    cache = tmp_path / "cache_miniaturas"

    with pytest.raises(OSError):
        obtener_o_crear_miniatura(corrupta, cache, tamano=(32, 32))


# --------------------------------------------------------------------------
# Nitidez y orden sugerido
# --------------------------------------------------------------------------


def test_puntuacion_nitidez_distingue_nitida_de_borrosa(tmp_path):
    nitida_img = _imagen_producto()
    borrosa_img = nitida_img.filter(ImageFilter.GaussianBlur(radius=6))

    ruta_nitida = _ruta_jpeg(tmp_path, "nitida.jpg", nitida_img)
    ruta_borrosa = _ruta_jpeg(tmp_path, "borrosa.jpg", borrosa_img)

    assert puntuacion_nitidez(ruta_nitida) > puntuacion_nitidez(ruta_borrosa)


def test_sugerir_orden_por_nitidez_pone_la_mas_nitida_primero(tmp_path):
    nitida_img = _imagen_producto()
    borrosa_img = nitida_img.filter(ImageFilter.GaussianBlur(radius=8))
    muy_borrosa_img = nitida_img.filter(ImageFilter.GaussianBlur(radius=16))

    ruta_nitida = _ruta_jpeg(tmp_path, "1_nitida.jpg", nitida_img)
    ruta_borrosa = _ruta_jpeg(tmp_path, "2_borrosa.jpg", borrosa_img)
    ruta_muy_borrosa = _ruta_jpeg(tmp_path, "3_muy_borrosa.jpg", muy_borrosa_img)

    # se pasan en un orden que NO es el esperado, para probar que sí reordena
    orden = sugerir_orden([ruta_muy_borrosa, ruta_borrosa, ruta_nitida])

    assert [o.ruta for o in orden] == [ruta_nitida, ruta_borrosa, ruta_muy_borrosa]
    assert [o.posicion for o in orden] == [1, 2, 3]
    assert all(o.criterio == "nitidez" for o in orden)
    assert all(o.puntuacion is not None for o in orden)
    assert orden[0].puntuacion > orden[1].puntuacion > orden[2].puntuacion


def test_sugerir_orden_cae_a_cronologico_si_una_foto_no_se_puede_leer(tmp_path, caplog):
    temprana = tmp_path / "temprana.png"
    tardia = tmp_path / "tardia.png"
    _guardar_png_con_marca(temprana, fecha_original="2024:01:01 09:00:00")
    _guardar_png_con_marca(tardia, fecha_original="2024:01:01 09:05:00")
    corrupta = tmp_path / "rota.jpg"
    _escribir_corrupto(corrupta)

    orden = sugerir_orden([tardia, corrupta, temprana])

    # se cae TODO el lote a cronológico -- ninguna foto queda con "nitidez"
    assert all(o.criterio == "cronologico" for o in orden)
    assert all(o.puntuacion is None for o in orden)
    # las que tienen fecha EXIF (tier 0) van antes que la ilegible (tier 1,
    # por mtime), en orden de fecha ascendente
    rutas = [o.ruta for o in orden]
    assert rutas.index(temprana) < rutas.index(tardia) < rutas.index(corrupta)


def test_sugerir_orden_lote_vacio():
    assert sugerir_orden([]) == []


# --------------------------------------------------------------------------
# Export por producto: límites reales de plataforma
# --------------------------------------------------------------------------


def _crear_n_fotos(directorio: Path, n: int) -> list[Path]:
    rutas = []
    for i in range(n):
        ruta = directorio / f"IMG_{i:03d}.jpg"
        _guardar_jpeg(ruta, _imagen_producto())
        rutas.append(ruta)
    return rutas


def test_exportar_producto_wallapop_limita_a_10_y_devuelve_excluidas(tmp_path):
    origen = tmp_path / "origen"
    origen.mkdir()
    fotos = _crear_n_fotos(origen, 14)
    destino = tmp_path / "export"

    resultado = exportar_producto(fotos, destino, "wallapop", "zapatillas-nike-42")

    assert resultado.limite == 10
    assert len(resultado.exportadas) == 10
    assert len(resultado.excluidas) == 4
    assert resultado.excluidas == fotos[10:]
    for ruta in resultado.exportadas:
        assert ruta.exists()


def test_exportar_producto_vinted_limita_a_20_y_devuelve_excluidas(tmp_path):
    origen = tmp_path / "origen"
    origen.mkdir()
    fotos = _crear_n_fotos(origen, 25)
    destino = tmp_path / "export"

    resultado = exportar_producto(fotos, destino, "vinted", "zapatillas-nike-42")

    assert resultado.limite == 20
    assert len(resultado.exportadas) == 20
    assert len(resultado.excluidas) == 5
    assert resultado.excluidas == fotos[20:]


def test_exportar_producto_no_excluye_nada_si_cabe_todo(tmp_path):
    origen = tmp_path / "origen"
    origen.mkdir()
    fotos = _crear_n_fotos(origen, 3)
    destino = tmp_path / "export"

    resultado = exportar_producto(fotos, destino, "wallapop", "camiseta")

    assert len(resultado.exportadas) == 3
    assert resultado.excluidas == []


def test_exportar_producto_copia_no_mueve_el_original(tmp_path):
    origen = tmp_path / "origen"
    origen.mkdir()
    fotos = _crear_n_fotos(origen, 2)
    destino = tmp_path / "export"

    exportar_producto(fotos, destino, "wallapop", "camiseta")

    for foto in fotos:
        assert foto.exists()  # el original sigue donde estaba


def test_exportar_producto_renombra_secuencial_con_slug_y_extension(tmp_path):
    origen = tmp_path / "origen"
    origen.mkdir()
    fotos = _crear_n_fotos(origen, 3)
    destino = tmp_path / "export"

    resultado = exportar_producto(fotos, destino, "wallapop", "camiseta-azul")

    nombres = sorted(p.name for p in resultado.exportadas)
    assert nombres == ["camiseta-azul_01.jpg", "camiseta-azul_02.jpg", "camiseta-azul_03.jpg"]
    assert resultado.directorio_producto == destino / "camiseta-azul"


def test_exportar_producto_plataforma_desconocida_lanza(tmp_path):
    origen = tmp_path / "origen"
    origen.mkdir()
    fotos = _crear_n_fotos(origen, 1)

    with pytest.raises(PlataformaDesconocidaError):
        exportar_producto(fotos, tmp_path / "export", "ebay", "producto")


def test_exportar_producto_lista_vacia_lanza(tmp_path):
    with pytest.raises(ValueError):
        exportar_producto([], tmp_path / "export", "wallapop", "producto")


def test_exportar_producto_slug_con_separador_de_ruta_lanza(tmp_path):
    origen = tmp_path / "origen"
    origen.mkdir()
    fotos = _crear_n_fotos(origen, 1)

    with pytest.raises(SlugInvalidoError):
        exportar_producto(fotos, tmp_path / "export", "wallapop", "../fuera")


def test_exportar_producto_slug_vacio_lanza(tmp_path):
    origen = tmp_path / "origen"
    origen.mkdir()
    fotos = _crear_n_fotos(origen, 1)

    with pytest.raises(SlugInvalidoError):
        exportar_producto(fotos, tmp_path / "export", "wallapop", "   ")


def test_exportar_producto_nombres_con_tildes_espacios_y_emoji(tmp_path):
    """Fotos de móvil: tildes, espacios y emojis en el nombre. pathlib debe
    manejarlo sin líos de encoding en Windows."""
    origen = tmp_path / "origen"
    origen.mkdir()
    ruta_rara = origen / "Foto de camión 📸 (2).jpg"
    _guardar_jpeg(ruta_rara, _imagen_producto())
    destino = tmp_path / "export"

    resultado = exportar_producto([ruta_rara], destino, "wallapop", "camión-rojo")

    assert len(resultado.exportadas) == 1
    assert resultado.exportadas[0].exists()
    assert resultado.exportadas[0].name == "camión-rojo_01.jpg"
