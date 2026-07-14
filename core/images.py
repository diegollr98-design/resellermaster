"""core/images.py — Capa de imagen (RESELLERMASTER).

Responsabilidad única: medir píxeles y metadatos. Este módulo **no afirma
nada sobre el producto** (marca, talla, estado...) — eso vive en
`core/extract.py`, detrás de la costura del `truth-loop`. Aquí sólo hay
geometría, bytes y ficheros. Cero red, cero coste, cero LLM.

Qué hace:
  - Lee EXIF: la fecha de disparo real (`DateTimeOriginal`) y la
    orientación. Un fichero sin EXIF devuelve `None` — nunca se disfraza
    el `mtime` del fichero de fecha EXIF (`leer_metadatos` guarda ambos
    campos por separado, explícitamente, para que no se puedan confundir).
  - Normaliza la orientación EXIF sobre los píxeles (las fotos de móvil
    vienen giradas; Streamlit las pintaría tumbadas si no se corrige).
  - Soporta `.heic`/`.heif` (iPhone) además de jpg/jpeg/png/webp, vía
    `pillow-heif` (se registra como opener de Pillow al importar este
    módulo, así que `PIL.Image.open()` funciona con `.heic` en todo el
    proyecto sin que cada llamador tenga que acordarse).
  - Hash exacto (sha256, duplicados literales) y perceptual (pHash,
    duplicados/casi-duplicados) para apoyar la deduplicación.
  - Miniaturas cacheadas en disco, indexadas por sha256+tamaño, para que
    Streamlit no tenga que releer y redecodificar fotos de varios MB en
    cada rerun.
  - Export por producto respetando los límites reales de cada plataforma
    (Vinted máx. 20 fotos, Wallapop máx. 10): renombra y COPIA (nunca
    mueve, nunca toca el original de Diego) a una carpeta por producto, y
    devuelve explícitamente qué fotos se quedaron fuera si sobran.
  - Orden sugerido dentro de un producto ya agrupado: nitidez (varianza
    del filtro Laplaciano) como proxy barato y explicable de "foto
    principal". Si no se puede calcular para alguna foto del lote, el
    lote entero cae a orden cronológico — nunca se inventa un ranking a
    medias (ver `sugerir_orden`).

Reglas duras de este fichero:
  - Nunca se escribe ni se modifica la foto original de Diego. Todo lo
    que se genera (miniaturas, copias de export) va a rutas explícitas
    bajo `data/`, decididas por el llamador — este módulo no asume dónde
    vive `data/`.
  - Nada de `except Exception: pass`. La única captura amplia de
    excepciones que existe aquí es la de "un fichero de un lote está
    corrupto": se registra con `logger.exception` (traceback completo),
    se marca el resultado como ilegible/fallido, y se sigue con el resto
    del lote. Eso es justo lo que pide `truth-loop.md`/`CLAUDE.md`: un
    fallo nunca degrada en silencio, y un fichero malo no puede tirar el
    lote entero de Diego.
  - Todo path se maneja con `pathlib.Path`, nunca con concatenación de
    strings — los nombres de fichero de móvil llevan tildes, espacios y
    emojis, y eso rompe con facilidad en Windows si se tratan como texto
    plano.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

import imagehash
import pillow_heif
from PIL import ExifTags, Image, ImageFilter, ImageOps, ImageStat

logger = logging.getLogger(__name__)

# pillow-heif se registra UNA vez, al importar este módulo, para que
# `PIL.Image.open()` entienda `.heic`/`.heif` en todo el proyecto (fotos de
# iPhone). Efecto secundario deliberado y documentado, no un accidente.
pillow_heif.register_heif_opener()

# --------------------------------------------------------------------------
# Constantes
# --------------------------------------------------------------------------
EXTENSIONES_SOPORTADAS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"})

# Tag EXIF 274 = Orientation (vive en IFD0). Tag 36867 = DateTimeOriginal
# (vive en la sub-IFD "Exif", no en IFD0 — es un error común leerlo del
# sitio equivocado y obtener `None` siempre).
_TAG_ORIENTACION = 274
_TAG_FECHA_ORIGINAL = 36867
_FORMATO_FECHA_EXIF = "%Y:%m:%d %H:%M:%S"

TAMANO_MINIATURA_DEFECTO: tuple[int, int] = (320, 320)

# Distancia de Hamming máxima entre dos pHash (64 bits, hash_size=8 por
# defecto en ImageHash) para considerar dos fotos "casi duplicadas". 5/64
# es el umbral habitual citado por la propia librería para "muy parecidas
# pero no bit a bit iguales" (recompresión, pequeño recorte, etc.).
UMBRAL_DUPLICADO_SIMILAR_DEFECTO = 5

# Límite real de cada plataforma (`rules/product.md`, verificado contra
# fuente primaria). Vive aquí porque es `core/images.py` quien decide qué
# fotos caben en un export; si cambia, cambia en un solo sitio.
LIMITE_FOTOS_PLATAFORMA: dict[str, int] = {
    "vinted": 20,
    "wallapop": 10,
}

# Kernel Laplaciano 3x3 clásico para detección de bordes. Suma 0 → hace
# falta `scale` explícito. `offset=128` centra la respuesta en gris medio
# para no perder por recorte (clipping a 0/255 en una imagen de 8 bits)
# la mitad negativa de la respuesta del filtro; la varianza no cambia por
# un desplazamiento constante mientras no haya recorte adicional.
_KERNEL_LAPLACIANO = ImageFilter.Kernel(
    size=(3, 3),
    kernel=[0, 1, 0, 1, -4, 1, 0, 1, 0],
    scale=1,
    offset=128,
)


class ImagenError(Exception):
    """Base de los errores propios de `core/images.py`."""


class PlataformaDesconocidaError(ImagenError, ValueError):
    """Se pidió exportar para una plataforma que no está en `LIMITE_FOTOS_PLATAFORMA`."""


class SlugInvalidoError(ImagenError, ValueError):
    """El slug de producto para el export está vacío o contiene separadores de ruta."""


def es_soportada(ruta: Path) -> bool:
    """`True` si la extensión del fichero es una de las que este módulo sabe leer."""
    return ruta.suffix.lower() in EXTENSIONES_SOPORTADAS


# --------------------------------------------------------------------------
# EXIF y metadatos
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class MetadatosImagen:
    """Lo que se puede medir de una foto sin decir nada del producto.

    `fecha_captura_exif` y `mtime_fichero` se guardan SIEMPRE por
    separado y con nombres que no dejan lugar a confusión: el primero es
    el instante real del disparo (según la cámara/móvil, `None` si no hay
    EXIF); el segundo es cuándo se tocó el fichero por última vez en este
    disco (miente al copiar, mover o sincronizar). Ningún código de este
    módulo escribe uno en el campo del otro.
    """

    ruta: Path
    legible: bool
    formato: str | None
    ancho: int | None
    alto: int | None
    orientacion_exif: int  # tag EXIF 274 crudo; 1 = normal/sin rotar
    fecha_captura_exif: datetime | None
    mtime_fichero: datetime | None
    error: str | None = None


def _parsear_fecha_exif(crudo: object, ruta: Path) -> datetime | None:
    """EXIF guarda fechas como texto `'YYYY:MM:DD HH:MM:SS'`. Si no hay
    valor o no se puede parsear, es `None` — nunca una excusa para rellenar
    con el mtime del fichero."""
    if not crudo or not isinstance(crudo, str):
        return None
    try:
        return datetime.strptime(crudo.strip(), _FORMATO_FECHA_EXIF)
    except ValueError:
        logger.warning("DateTimeOriginal EXIF ilegible (%r) en %s", crudo, ruta)
        return None


@dataclass(frozen=True)
class ResumenExif:
    """Cuántas fotos de un lote traen fecha de captura EXIF y cuántas no.

    Pura aritmética sobre `MetadatosImagen.fecha_captura_exif` — una foto
    ilegible cuenta como "sin fecha" igual que una legible sin el tag; en
    ningún caso se sustituye por `mtime_fichero` (sería mentir sobre cuándo
    se disparó la foto, ver docstring del módulo)."""

    total: int
    con_fecha_exif: int
    sin_fecha_exif: int

    @property
    def porcentaje_sin_exif(self) -> float:
        return (self.sin_fecha_exif / self.total * 100.0) if self.total else 0.0


def resumen_exif(rutas: Sequence[Path]) -> ResumenExif:
    """Lee los metadatos de cada foto de `rutas` (vía `leer_metadatos`) y
    cuenta cuántas traen fecha de captura EXIF (`DateTimeOriginal`).

    Por qué importa: `core/grouping.py` usa el timestamp EXIF como señal
    primaria para separar productos. Sin EXIF esa señal no existe y la
    agrupación se degrada a solo similitud visual. WhatsApp y otras
    aplicaciones de mensajería (y algunas herramientas de descarga) borran
    el EXIF al recomprimir/reenviar una foto — medido con fotos reales de
    Diego: 13/13 llegadas por WhatsApp, cero fecha. Este cálculo es lo que
    permite avisarle ANTES de que curre el lote entero a mano.

    Sólo lee (nunca escribe), y es barato: `leer_metadatos` no decodifica
    los píxeles, sólo la cabecera del fichero."""
    total = len(rutas)
    sin_fecha = sum(1 for ruta in rutas if leer_metadatos(ruta).fecha_captura_exif is None)
    return ResumenExif(total=total, con_fecha_exif=total - sin_fecha, sin_fecha_exif=sin_fecha)


def _mtime_fichero(ruta: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(ruta.stat().st_mtime)
    except OSError as exc:
        logger.warning("No se pudo leer mtime de %s: %s", ruta, exc)
        return None


def leer_metadatos(ruta: Path) -> MetadatosImagen:
    """Lee dimensiones, orientación y fecha EXIF de una foto.

    Nunca lanza por un fichero corrupto o ilegible: lo registra con
    `logger.exception` (traceback completo, no un aviso mudo), y devuelve
    un `MetadatosImagen` con `legible=False` y `error` relleno para que el
    llamador pueda seguir procesando el resto del lote sin perder la
    foto rota por el camino (`truth-loop.md`: nunca degradar en silencio).
    """
    mtime = _mtime_fichero(ruta)
    try:
        with Image.open(ruta) as img:
            ancho, alto = img.size
            formato = img.format
            exif = img.getexif()
            orientacion = int(exif.get(_TAG_ORIENTACION, 1))
            # DateTimeOriginal vive en la sub-IFD "Exif" en una foto real;
            # se comprueba también IFD0 como fallback defensivo por si
            # algún escritor de EXIF no estándar lo puso ahí — nunca se
            # inventa la fecha si no aparece en ninguno de los dos sitios.
            exif_sub = exif.get_ifd(ExifTags.IFD.Exif)
            crudo_fecha = exif_sub.get(_TAG_FECHA_ORIGINAL) or exif.get(_TAG_FECHA_ORIGINAL)
            fecha = _parsear_fecha_exif(crudo_fecha, ruta)
        return MetadatosImagen(
            ruta=ruta,
            legible=True,
            formato=formato,
            ancho=ancho,
            alto=alto,
            orientacion_exif=orientacion,
            fecha_captura_exif=fecha,
            mtime_fichero=mtime,
            error=None,
        )
    except Exception as exc:  # noqa: BLE001 — frontera "un fichero del lote", documentada arriba.
        logger.exception("Foto ilegible, se marca y se sigue con el lote: %s", ruta)
        return MetadatosImagen(
            ruta=ruta,
            legible=False,
            formato=None,
            ancho=None,
            alto=None,
            orientacion_exif=1,
            fecha_captura_exif=None,
            mtime_fichero=mtime,
            error=str(exc) or repr(exc),
        )


# --------------------------------------------------------------------------
# Orientación
# --------------------------------------------------------------------------
def normalizar_orientacion(imagen: Image.Image) -> Image.Image:
    """Aplica a los PÍXELES la rotación/flip que indica el tag EXIF
    Orientation, y elimina el tag (ya no hace falta: la imagen resultante
    ya está del derecho). Devuelve una imagen NUEVA — nunca muta
    `imagen` in situ ni toca ningún fichero."""
    return ImageOps.exif_transpose(imagen)


def abrir_derecha(ruta: Path) -> Image.Image:
    """Abre una foto y devuelve los píxeles ya orientados como se verían
    en pantalla (aplica `normalizar_orientacion`). Es la función que debe
    usar cualquier código que vaya a mostrar o medir la imagen (miniaturas,
    nitidez, hashing) — así una foto girada y su gemela ya-derecha se
    tratan de forma consistente."""
    with Image.open(ruta) as img:
        img.load()
        return normalizar_orientacion(img)


# --------------------------------------------------------------------------
# Hashing (exacto + perceptual) y deduplicación
# --------------------------------------------------------------------------
def sha256_de_fichero(ruta: Path) -> str:
    """Hash exacto del CONTENIDO del fichero (no de los píxeles decodificados):
    detecta copias byte-a-byte idénticas, p. ej. la misma foto arrastrada
    dos veces al lote."""
    digest = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def calcular_phash(ruta: Path) -> imagehash.ImageHash:
    """Hash perceptual sobre los píxeles ya orientados (`abrir_derecha`):
    dos fotos visualmente casi iguales (recompresión, recorte mínimo,
    girada vs. ya-girada) caen cerca en distancia de Hamming aunque su
    sha256 sea distinto."""
    img = abrir_derecha(ruta)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return imagehash.phash(img)


@dataclass(frozen=True)
class RegistroHash:
    """Resultado de hashear una foto dentro de un lote.

    `sha256` y `phash` fallan por razones DISTINTAS y se calculan de forma
    independiente, a propósito: `sha256` sólo necesita leer bytes del
    fichero (casi nunca falla, ni siquiera si el contenido no es una
    imagen válida); `phash` necesita que Pillow pueda DECODIFICAR la
    imagen (falla con un fichero corrupto o con una extensión mentirosa).
    Acoplar ambos en un único intento haría que un fallo de decodificación
    escondiera un sha256 perfectamente válido — y ese sha256 es justo lo
    que hace falta para pillar un duplicado exacto aunque el fichero esté
    dañado. `error` (si no es `None`) explica qué falló y de cuál de los
    dos cálculos."""

    ruta: Path
    sha256: str | None
    phash: str | None
    error: str | None = None


def hashear_lote(rutas: Sequence[Path]) -> list[RegistroHash]:
    """Calcula sha256 + pHash de cada foto de `rutas`. Un fallo en
    cualquiera de los dos cálculos para una foto concreta no aborta el
    lote entero: se registra (traceback completo vía `logger.exception`),
    se marca con `error`, y se continúa con el resto — regla dura del
    proyecto, nunca fallback silencioso."""
    registros: list[RegistroHash] = []
    for ruta in rutas:
        errores: list[str] = []

        digest: str | None
        try:
            digest = sha256_de_fichero(ruta)
        except Exception as exc:  # noqa: BLE001 — frontera "un fichero del lote", documentada arriba.
            logger.exception("No se pudo calcular sha256 de %s", ruta)
            digest = None
            errores.append(f"sha256: {exc or repr(exc)}")

        phash: str | None
        try:
            phash = str(calcular_phash(ruta))
        except Exception as exc:  # noqa: BLE001 — misma frontera.
            logger.exception("No se pudo calcular phash de %s", ruta)
            phash = None
            errores.append(f"phash: {exc or repr(exc)}")

        registros.append(
            RegistroHash(ruta=ruta, sha256=digest, phash=phash, error="; ".join(errores) or None)
        )
    return registros


def duplicados_exactos(registros: Sequence[RegistroHash]) -> dict[str, list[Path]]:
    """Agrupa por sha256 las fotos con contenido byte-a-byte idéntico.
    Sólo devuelve grupos con 2+ fotos (un sha256 con una sola foto no es
    un duplicado de nada)."""
    grupos: dict[str, list[Path]] = {}
    for r in registros:
        if r.sha256 is None:
            continue
        grupos.setdefault(r.sha256, []).append(r.ruta)
    return {sha: rutas for sha, rutas in grupos.items() if len(rutas) > 1}


def _distancia_hamming(hex_a: str, hex_b: str) -> int:
    return imagehash.hex_to_hash(hex_a) - imagehash.hex_to_hash(hex_b)


def duplicados_similares(
    registros: Sequence[RegistroHash],
    umbral: int = UMBRAL_DUPLICADO_SIMILAR_DEFECTO,
) -> list[tuple[Path, Path, int]]:
    """Pares de fotos visualmente casi iguales (distancia de Hamming de
    su pHash ≤ `umbral`) que NO son duplicados exactos. Comparación
    O(n²): asumible para el tamaño de lote de un solo usuario (decenas a
    pocos cientos de fotos); si el lote crece mucho, esto es lo primero
    a revisar."""
    validos = [r for r in registros if r.phash is not None]
    pares: list[tuple[Path, Path, int]] = []
    for i in range(len(validos)):
        for j in range(i + 1, len(validos)):
            d = _distancia_hamming(validos[i].phash, validos[j].phash)  # type: ignore[arg-type]
            if d <= umbral:
                pares.append((validos[i].ruta, validos[j].ruta, d))
    return pares


# --------------------------------------------------------------------------
# Miniaturas cacheadas en disco
# --------------------------------------------------------------------------
def nombre_miniatura(sha256: str, tamano: tuple[int, int]) -> str:
    return f"{sha256}_{tamano[0]}x{tamano[1]}.webp"


def obtener_o_crear_miniatura(
    origen: Path,
    directorio_cache: Path,
    tamano: tuple[int, int] = TAMANO_MINIATURA_DEFECTO,
) -> Path:
    """Miniatura cacheada en disco, indexada por sha256 del contenido +
    tamaño. Nunca toca `origen`: escribe únicamente bajo `directorio_cache`.
    Si la miniatura ya existe se reutiliza (evita releer/redecodificar
    fotos de varios MB en cada rerun de Streamlit). Escritura atómica
    (fichero temporal + `replace`) para que un rerun que lea a mitad de
    una generación no encuentre un webp a medio escribir."""
    digest = sha256_de_fichero(origen)
    directorio_cache.mkdir(parents=True, exist_ok=True)
    ruta_miniatura = directorio_cache / nombre_miniatura(digest, tamano)
    if ruta_miniatura.exists():
        return ruta_miniatura

    try:
        img = abrir_derecha(origen)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail(tamano)
        ruta_tmp = ruta_miniatura.with_name(ruta_miniatura.name + ".tmp")
        img.save(ruta_tmp, format="WEBP", quality=80)
        ruta_tmp.replace(ruta_miniatura)
    except OSError:
        logger.exception("No se pudo generar la miniatura de %s", origen)
        raise
    return ruta_miniatura


# --------------------------------------------------------------------------
# Orden sugerido dentro de un producto
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class FotoOrdenada:
    ruta: Path
    posicion: int  # 1-indexado
    criterio: str  # "nitidez" | "cronologico"
    puntuacion: float | None  # sólo tiene sentido si criterio == "nitidez"


def puntuacion_nitidez(ruta: Path) -> float:
    """Heurística de nitidez: varianza del filtro Laplaciano sobre la
    imagen en gris (ya orientada). Es la medida clásica y barata de
    "blur detection": a más borde detectado, más nítida la foto. Es TODO
    lo que mide — no intenta estimar "fondo limpio": no hay una
    heurística barata y honesta para eso, así que no se inventa una."""
    gris = abrir_derecha(ruta).convert("L")
    bordes = gris.filter(_KERNEL_LAPLACIANO)
    return ImageStat.Stat(bordes).var[0]


def _clave_cronologica(ruta: Path) -> tuple[int, object]:
    meta = leer_metadatos(ruta)
    if meta.fecha_captura_exif is not None:
        return (0, meta.fecha_captura_exif)
    if meta.mtime_fichero is not None:
        return (1, meta.mtime_fichero)
    return (2, ruta.name)


def sugerir_orden(rutas: Sequence[Path]) -> list[FotoOrdenada]:
    """Orden sugerido de las fotos DENTRO de un producto ya agrupado (esto
    no decide agrupación, sólo el orden de un grupo que Diego ya confirmó
    o está revisando).

    Criterio: nitidez (varianza del Laplaciano) descendente — la foto más
    nítida se sugiere primero, como candidata a foto principal.

    Si CUALQUIER foto del lote no se puede leer para calcular su nitidez,
    no se mezclan criterios (nitidez para unas, otra cosa para las que
    fallan): el LOTE ENTERO cae a orden cronológico (fecha EXIF si existe,
    si no mtime del fichero, si no nombre de fichero) para que el criterio
    devuelto sea uniforme y honesto, no un ranking a medio construir que
    aparente más certeza de la que hay."""
    if not rutas:
        return []

    puntuaciones: dict[Path, float] = {}
    fallo = False
    for ruta in rutas:
        try:
            puntuaciones[ruta] = puntuacion_nitidez(ruta)
        except Exception:  # noqa: BLE001 — mismo patrón: se degrada a un fallback EXPLÍCITO, no en silencio.
            logger.exception(
                "No se pudo calcular nitidez de %s; el lote entero cae a orden cronológico", ruta
            )
            fallo = True
            break

    if not fallo:
        ordenadas = sorted(rutas, key=lambda r: puntuaciones[r], reverse=True)
        return [
            FotoOrdenada(ruta=r, posicion=i + 1, criterio="nitidez", puntuacion=puntuaciones[r])
            for i, r in enumerate(ordenadas)
        ]

    ordenadas = sorted(rutas, key=_clave_cronologica)
    return [
        FotoOrdenada(ruta=r, posicion=i + 1, criterio="cronologico", puntuacion=None)
        for i, r in enumerate(ordenadas)
    ]


# --------------------------------------------------------------------------
# Export por producto
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ResultadoExport:
    directorio_producto: Path
    exportadas: list[Path]  # copias creadas, en el orden final
    excluidas: list[Path]  # fotos que no cupieron en el límite de la plataforma
    plataforma: str
    limite: int


def _sanear_slug(slug: str) -> str:
    """El slug lo decide quien llama (grouping/UI); esta función sólo
    evita que se escape del directorio destino, no inventa ni normaliza
    el nombre del producto (eso no es responsabilidad de `core/images.py`)."""
    limpio = slug.strip()
    if not limpio:
        raise SlugInvalidoError("product_slug vacío")
    for prohibido in ("/", "\\", "\x00"):
        if prohibido in limpio:
            raise SlugInvalidoError(
                f"product_slug contiene un carácter no permitido ({prohibido!r}): {slug!r}"
            )
    return limpio


def exportar_producto(
    fotos: Sequence[Path],
    directorio_destino: Path,
    plataforma: str,
    slug_producto: str,
) -> ResultadoExport:
    """Copia (nunca mueve, nunca modifica) las fotos YA ORDENADAS de un
    producto a `directorio_destino/slug_producto/`, renombradas
    secuencialmente, respetando el límite real de la plataforma.

    Si `fotos` trae más fotos que el límite, las que sobran NO se tiran
    en silencio: se devuelven en `ResultadoExport.excluidas` para que la
    UI se lo diga a Diego explícitamente.
    """
    if plataforma not in LIMITE_FOTOS_PLATAFORMA:
        raise PlataformaDesconocidaError(
            f"plataforma desconocida: {plataforma!r}; válidas: {sorted(LIMITE_FOTOS_PLATAFORMA)}"
        )
    if not fotos:
        raise ValueError("exportar_producto recibió una lista de fotos vacía")

    slug = _sanear_slug(slug_producto)
    limite = LIMITE_FOTOS_PLATAFORMA[plataforma]
    directorio_producto = directorio_destino / slug
    directorio_producto.mkdir(parents=True, exist_ok=True)

    a_exportar = list(fotos[:limite])
    excluidas = list(fotos[limite:])
    if excluidas:
        logger.warning(
            "%s: %d foto(s) por encima del límite de %s (máx %d), no se exportan: %s",
            slug,
            len(excluidas),
            plataforma,
            limite,
            [p.name for p in excluidas],
        )

    digitos = max(len(str(len(a_exportar))), 2)
    exportadas: list[Path] = []
    for i, origen in enumerate(a_exportar, start=1):
        sufijo = origen.suffix.lower()
        nombre_destino = f"{slug}_{i:0{digitos}d}{sufijo}"
        destino = directorio_producto / nombre_destino
        try:
            shutil.copy2(origen, destino)
        except OSError:
            logger.exception("Fallo copiando %s -> %s durante el export", origen, destino)
            raise
        exportadas.append(destino)

    return ResultadoExport(
        directorio_producto=directorio_producto,
        exportadas=exportadas,
        excluidas=excluidas,
        plataforma=plataforma,
        limite=limite,
    )
