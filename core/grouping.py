"""core/grouping.py — Costura de AGRUPACION (RESELLERMASTER).

Superficie SENSIBLE (`.claude/rules/truth-loop.md` SS B y SS E). Una foto
en la ficha del producto equivocado es el fallo mas caro y mas silencioso
del proyecto: se publica, y nadie lo caza. Este modulo PROPONE grupos;
Diego CONFIRMA (`core/store.py` bloquea cualquier re-agrupacion despues de
esa confirmacion, ver su docstring). Ninguna funcion de aqui cierra nada.

## La tesis: el timestamp EXIF, no los pixeles
La senal PRIMARIA de agrupacion es CUANDO se disparo cada foto
(`fecha_captura_exif`, de `core/images.py::leer_metadatos`): las fotos de
un mismo producto se disparan seguidas (segundos/minutos); entre un
producto y el siguiente hay un hueco. Es determinista, gratis, y NO puede
alucinar. El hash perceptual (pHash) es CONFIRMACION dentro de un grupo ya
propuesto por tiempo -- nunca el criterio de corte.

## Como se deriva el corte temporal (nada de "5 minutos" a ojo)
1. Se ordenan las fotos CON fecha EXIF cronologicamente y se calculan los
   huecos entre disparos consecutivos.
2. La linea base del ritmo de disparo de ESTE LOTE es la MEDIANA de esos
   huecos (si sale 0 -- rafaga con timestamps al segundo identico -- se
   cae a la media de los huecos > 0; si TODOS los huecos son 0, no hay
   base estadistica para cortar por tiempo y se documenta asi). Con menos
   de 2 huecos (0 o 1) tampoco hay distribucion de la que derivar "grande":
   se deja sin cortar, ver `_umbral_corte`.
3. Se corta donde un hueco supera `FACTOR_CORTE_MEDIANA` veces esa linea
   base -- un valor RELATIVO al propio lote, no una duracion absoluta: un
   lote a rafaga de 2 s corta en huecos >6 s; un lote con calma a 20 s
   corta en huecos >60 s. Eso es lo que hace que el criterio no se rompa
   segun el ritmo real de Diego ese dia (ver constante mas abajo para el
   razonamiento completo, y `tests/test_grouping.py` para el caso limite
   de dos productos seguidos con poco hueco entre ellos).

## La confirmacion por pHash (dentro de un grupo ya propuesto por tiempo)
Si, dentro de un grupo agrupado por tiempo, una foto no se parece a
NINGUNA otra del grupo (distancia de Hamming de su pHash por encima de
`UMBRAL_PHASH_MUY_DISTINTA` respecto a TODAS las demas), el grupo entero
baja a `confianza="baja"` y el motivo nombra la foto sospechosa. La foto
NO se expulsa del grupo -- eso seria decidir, no proponer. Es justo el
caso que existe para cazar: una foto de otro producto colada por error en
medio de una sesion de fotos.

## Fotos SIN EXIF
Es el caso real (fotos reenviadas por WhatsApp, capturas de pantalla,
descargas de otro sitio). No hay timestamp que agrupar, y forzarlas en un
grupo por tiempo para que "cuadre" seria mentir sobre la procedencia. Se
intenta el UNICO criterio que queda sin inventar nada nuevo: si dos fotos
sin fecha son CASI-DUPLICADAS por pHash (el MISMO umbral que
`core.images.duplicados_similares` ya usa para deduplicacion -- no un
umbral mas laxo inventado aqui para "mismo producto, otro angulo" sin
datos que lo respalden), se agrupan. Lo que no conecta con nada es su
propio grupo, `confianza="baja"`, con un motivo honesto. Preferimos que
Diego vea diez grupos de una foto a que una foto reenviada se cuele en el
producto equivocado.

## Cero red, cero LLM, cero coste
Este modulo solo usa lo que `core/images.py` ya calcula (EXIF, sha256,
pHash). No llama a ningun proveedor, no importa nada de `core/llm.py`.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import imagehash

from core.images import MetadatosImagen, RegistroHash, duplicados_similares, hashear_lote, leer_metadatos

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Constantes -- derivadas y documentadas, ninguna puesta "porque suena bien"
# --------------------------------------------------------------------------

# El hueco temporal se considera "grande" cuando supera este multiplo de la
# MEDIANA de huecos DE ESTE LOTE (no una duracion absoluta como "5
# minutos", que se rompe segun el ritmo de disparo real de Diego ese dia).
# 3x se eligio porque el jitter normal DENTRO de una misma sesion de fotos
# de un producto (reencuadrar, esperar foco, mover la prenda) rara vez
# triplica el ritmo tipico de disparo del lote, mientras que cambiar de
# producto (moverlo, colocar el siguiente, a veces salir de cuadro) si lo
# hace con holgura. Ver `tests/test_grouping.py` para el caso limite real:
# dos productos fotografiados seguidos con un hueco entre ellos pequeno en
# terminos absolutos pero varias veces mayor que el ritmo intra-producto
# -- exactamente el caso que un umbral absoluto fijo NO detectaria bien.
FACTOR_CORTE_MEDIANA = 3

# Distancia de Hamming de pHash (64 bits, `imagehash.phash` por defecto) a
# partir de la cual dos fotos ya no comparten NADA de estructura visible.
# Calibrado EMPIRICAMENTE, no a ojo (medido sobre imagenes sinteticas
# comparables a las de `tests/test_images.py`):
#   - variaciones legitimas del MISMO objeto (recorte leve, cambio de
#     brillo) midieron <=18 de distancia;
#   - contenido sin ninguna relacion (color solido, patron de tablero,
#     ruido aleatorio) midio >=31.
# 24 deja margen holgado a ambos lados. Es DELIBERADAMENTE alto (mas cerca
# del "sin relacion" que del "casi identico"): pHash mide composicion de
# pixeles, no identidad del producto, y dos fotos legitimas del MISMO
# articulo desde angulos muy distintos pueden diferir bastante sin ser una
# foto cruzada. Un umbral mas bajo generaria demasiados falsos "dudoso" y
# ahogaria la senal; se prefiere que solo dispare ante un contraste
# brutal, que es la situacion real que este chequeo existe para cazar.
UMBRAL_PHASH_MUY_DISTINTA = 24


# --------------------------------------------------------------------------
# El contrato
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Grupo:
    """Una PROPUESTA de agrupacion de fotos por producto.

    `core/grouping.py` nunca cierra un grupo -- Diego confirma
    (`core/store.py` bloquea cualquier re-agrupacion despues de esa
    confirmacion). Ver `.claude/rules/truth-loop.md` SS E.

    fotos: en orden cronologico si el grupo tiene fecha EXIF. Si NINGUNA
        foto del grupo tiene EXIF, el mejor proxy disponible es el mtime
        del fichero -- documentado como tal en el motivo, nunca disfrazado
        de fecha real de disparo (ver `core/images.py::MetadatosImagen`).
    confianza: "alta" SOLO cuando la senal primaria (EXIF, huecos
        cortos y consistentes) Y la de confirmacion (pHash, fotos
        parecidas entre si) coinciden limpio. Cualquier duda -> "media" o
        "baja". Un grupo de una sola foto es SIEMPRE "baja"
        (`truth-loop.md` SS E: "un producto con una sola foto [...] marcar
        como grupo dudoso, no colarlo en silencio").
    motivo: en espanol, para que Diego entienda en un vistazo por que se
        agruparon o por que hay que revisarlas.
    """

    fotos: list[Path]
    confianza: Literal["alta", "media", "baja"]
    motivo: str


def agrupar(fotos: list[Path]) -> list[Grupo]:
    """Propone una agrupacion por producto a partir de un lote de fotos
    mezcladas. Nunca decide -- Diego confirma. Cero red, cero LLM, cero
    coste: solo reusa lo que `core/images.py` ya calcula.

    Estrategia (detalle completo en el docstring del modulo):
      1. Fotos CON fecha EXIF: se ordenan cronologicamente y se cortan
         donde el hueco entre disparos consecutivos es grande RELATIVO al
         ritmo de disparo de este lote.
      2. Cada segmento se confirma por pHash: si una foto no se parece a
         ninguna otra del segmento, el grupo baja a "baja" -- no se
         expulsa la foto, se marca.
      3. Fotos SIN fecha EXIF nunca se mezclan con las anteriores. Se
         agrupan solo si son casi-duplicados por pHash; lo que no conecta
         con nada es su propio grupo, "baja".
    """
    if not fotos:
        return []

    metadatos: dict[Path, MetadatosImagen] = {foto: leer_metadatos(foto) for foto in fotos}
    registros_hash = hashear_lote(fotos)
    phash_por_ruta: dict[Path, str | None] = {r.ruta: r.phash for r in registros_hash}

    con_fecha = sorted(
        (foto for foto in fotos if metadatos[foto].fecha_captura_exif is not None),
        key=lambda foto: metadatos[foto].fecha_captura_exif,
    )
    sin_fecha = [foto for foto in fotos if metadatos[foto].fecha_captura_exif is None]

    grupos: list[Grupo] = []
    grupos.extend(_agrupar_por_tiempo(con_fecha, metadatos, phash_por_ruta))
    grupos.extend(_agrupar_por_phash_sin_fecha(sin_fecha, metadatos, phash_por_ruta))
    return grupos


# --------------------------------------------------------------------------
# Fotos CON fecha EXIF: corte por hueco temporal + confirmacion por pHash
# --------------------------------------------------------------------------
def _agrupar_por_tiempo(
    con_fecha: list[Path],
    metadatos: dict[Path, MetadatosImagen],
    phash_por_ruta: dict[Path, str | None],
) -> list[Grupo]:
    if not con_fecha:
        return []

    if len(con_fecha) == 1:
        foto = con_fecha[0]
        return [
            Grupo(
                fotos=[foto],
                confianza="baja",
                motivo=(
                    "Una sola foto con fecha EXIF en todo el lote: no hay "
                    "ninguna otra con la que comparar el timestamp ni la "
                    "imagen. Revisa si faltan mas fotos de este producto."
                ),
            )
        ]

    fechas = [metadatos[foto].fecha_captura_exif for foto in con_fecha]
    huecos = [(fechas[i + 1] - fechas[i]).total_seconds() for i in range(len(fechas) - 1)]
    umbral = _umbral_corte(huecos)

    segmentos: list[list[Path]] = [[con_fecha[0]]]
    for i, hueco in enumerate(huecos):
        if umbral is not None and hueco > umbral:
            segmentos.append([])
        segmentos[-1].append(con_fecha[i + 1])

    return [_construir_grupo_temporal(seg, metadatos, phash_por_ruta) for seg in segmentos]


def _umbral_corte(huecos: list[float]) -> float | None:
    """Deriva el umbral de "hueco grande" de la distribucion de huecos DE
    ESTE LOTE (ver constante `FACTOR_CORTE_MEDIANA`) -- nunca un numero de
    segundos fijo.

    `None` significa "sin base estadistica para cortar por tiempo":
      - con menos de 2 huecos (0 o 1 foto extra tras la primera con
        fecha) no hay distribucion de la que derivar "grande" -- inventar
        un umbral con un solo dato seria arbitrario, asi que se deja sin
        cortar y la unica senal que queda es la confirmacion por pHash
        (`_construir_grupo_temporal`).
      - si TODOS los huecos son 0 (rafaga con timestamps al segundo
        identico en todo el lote), tampoco hay base: no se corta nada por
        tiempo.
    """
    if len(huecos) < 2:
        return None
    positivos = [hueco for hueco in huecos if hueco > 0]
    if not positivos:
        return None
    mediana = statistics.median(huecos)
    base = mediana if mediana > 0 else statistics.mean(positivos)
    return base * FACTOR_CORTE_MEDIANA


def _construir_grupo_temporal(
    seg: list[Path],
    metadatos: dict[Path, MetadatosImagen],
    phash_por_ruta: dict[Path, str | None],
) -> Grupo:
    if len(seg) == 1:
        return Grupo(
            fotos=seg,
            confianza="baja",
            motivo=(
                "Una sola foto en este tramo de tiempo (hueco grande a los "
                "lados, o extremo del lote): no hay ninguna otra con la que "
                "confirmarla visualmente. Revisa si falta alguna foto de "
                "este producto."
            ),
        )

    n = len(seg)
    duracion = (
        metadatos[seg[-1]].fecha_captura_exif - metadatos[seg[0]].fecha_captura_exif
    ).total_seconds()

    sin_phash = [foto.name for foto in seg if phash_por_ruta.get(foto) is None]
    if sin_phash:
        return Grupo(
            fotos=seg,
            confianza="media",
            motivo=(
                f"{n} fotos en {duracion:.0f} s por timestamp EXIF seguido, pero "
                f"no se pudo calcular el hash visual de {', '.join(sin_phash)} "
                "para confirmar que se parecen al resto. Revisalo a mano."
            ),
        )

    distintas = _fotos_muy_distintas(seg, phash_por_ruta)
    if distintas:
        nombres = ", ".join(foto.name for foto in distintas)
        plural = "n" if len(distintas) > 1 else ""
        logger.info("Grupo temporal con foto(s) visualmente distinta(s): %s", nombres)
        return Grupo(
            fotos=seg,
            confianza="baja",
            motivo=(
                f"{n} fotos en {duracion:.0f} s por timestamp EXIF seguido, pero "
                f"{nombres} no se parece{plural} visualmente a ninguna otra del "
                "grupo -- posible foto cruzada de otro producto. Revisala antes "
                "de confirmar."
            ),
        )

    return Grupo(
        fotos=seg,
        confianza="alta",
        motivo=(
            f"{n} fotos en {duracion:.0f} s, timestamps EXIF seguidos y "
            "visualmente parecidas entre si."
        ),
    )


def _fotos_muy_distintas(
    seg: list[Path], phash_por_ruta: dict[Path, str | None]
) -> list[Path]:
    """Fotos de `seg` cuya distancia de Hamming de pHash MINIMA a
    cualquier otra foto del grupo supera `UMBRAL_PHASH_MUY_DISTINTA`: no se
    parecen a NINGUNA otra foto del grupo. Precondicion: ningun pHash de
    `seg` es `None` (eso se trata antes, como confirmacion inconclusa, no
    como "distinta" -- ver `_construir_grupo_temporal`)."""
    hashes = {foto: imagehash.hex_to_hash(phash_por_ruta[foto]) for foto in seg}
    distintas: list[Path] = []
    for foto in seg:
        distancias = [hashes[foto] - hashes[otra] for otra in seg if otra != foto]
        if min(distancias) > UMBRAL_PHASH_MUY_DISTINTA:
            distintas.append(foto)
    return distintas


# --------------------------------------------------------------------------
# Fotos SIN fecha EXIF: solo pHash, o cada una es su propio grupo dudoso
# --------------------------------------------------------------------------
def _agrupar_por_phash_sin_fecha(
    sin_fecha: list[Path],
    metadatos: dict[Path, MetadatosImagen],
    phash_por_ruta: dict[Path, str | None],
) -> list[Grupo]:
    if not sin_fecha:
        return []

    registros = [
        RegistroHash(ruta=foto, sha256=None, phash=phash_por_ruta.get(foto))
        for foto in sin_fecha
    ]
    # Mismo umbral que `core.images.duplicados_similares` usa para
    # deduplicacion (casi-duplicado, no "mismo producto otro angulo" --
    # no inventamos un umbral mas laxo aqui sin datos que lo respalden).
    pares = duplicados_similares(registros)
    componentes = _componentes_conexas(sin_fecha, pares)

    grupos: list[Grupo] = []
    for componente in componentes:
        ordenada = sorted(componente, key=lambda foto: _clave_orden_sin_fecha(foto, metadatos))
        if len(ordenada) == 1:
            foto = ordenada[0]
            if phash_por_ruta.get(foto) is None:
                motivo = (
                    "Esta foto no tiene fecha EXIF y no se pudo analizar "
                    "visualmente (fichero ilegible o corrupto). Revisala a mano."
                )
            else:
                motivo = (
                    "Esta foto no tiene fecha y no se parece a ninguna otra del "
                    "lote. Queda sola: revisala a mano."
                )
            grupos.append(Grupo(fotos=[foto], confianza="baja", motivo=motivo))
        else:
            grupos.append(
                Grupo(
                    fotos=ordenada,
                    confianza="media",
                    motivo=(
                        f"{len(ordenada)} fotos sin fecha EXIF pero casi identicas "
                        "por comparacion visual (pHash): probablemente el mismo "
                        "producto. Sin timestamp no se puede confirmar el orden "
                        "real de disparo (se ordenan por fecha del fichero, no de "
                        "camara)."
                    ),
                )
            )
    return grupos


def _componentes_conexas(
    nodos: list[Path], pares: list[tuple[Path, Path, int]]
) -> list[list[Path]]:
    """Union-find minimo: agrupa `nodos` conectados (directa o
    transitivamente) por algun par en `pares`. Un nodo sin ningun par es
    su propia componente de tamano 1."""
    padre: dict[Path, Path] = {nodo: nodo for nodo in nodos}

    def encontrar(x: Path) -> Path:
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    def unir(a: Path, b: Path) -> None:
        ra, rb = encontrar(a), encontrar(b)
        if ra != rb:
            padre[ra] = rb

    for a, b, _distancia in pares:
        unir(a, b)

    componentes: dict[Path, list[Path]] = {}
    for nodo in nodos:
        raiz = encontrar(nodo)
        componentes.setdefault(raiz, []).append(nodo)
    return list(componentes.values())


def _clave_orden_sin_fecha(
    foto: Path, metadatos: dict[Path, MetadatosImagen]
) -> tuple[int, object]:
    """Sin EXIF no hay cronologia real que ordenar. El mejor proxy
    disponible, honesto y documentado, es el mtime del fichero; si tampoco
    existe, el nombre. Nunca se disfraza de fecha de disparo real (ver
    `core/images.py::MetadatosImagen`)."""
    meta = metadatos[foto]
    if meta.mtime_fichero is not None:
        return (0, meta.mtime_fichero)
    return (1, foto.name)
