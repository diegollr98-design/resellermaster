"""ui/ingesta.py — Pantalla 1: Ingesta de fotos (RESELLERMASTER).

SOLO renderiza. Su trabajo es: dejar que Diego señale un origen de fotos
(carpeta en disco o arrastrar ficheros), copiarlas a
`data/lotes/<lote_id>/` (**nunca** tocar los originales de Diego), leer
EXIF/hash con `core.images`, y registrar todo en `core.store.LoteStore`.

Nada de esto decide cómo agrupar, qué vale un atributo o qué precio tiene
un producto — eso vive detrás de las costuras en `core/`.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

import streamlit as st

from core.images import (
    EXTENSIONES_SOPORTADAS,
    TAMANO_MINIATURA_DEFECTO,
    ResumenExif,
    es_soportada,
    leer_metadatos,
    obtener_o_crear_miniatura,
    resumen_exif,
    sha256_de_fichero,
)
from core.store import DEFAULT_DATA_DIR, Foto, FotoDuplicadaError, LoteStore

logger = logging.getLogger(__name__)

_TIPOS_UPLOADER = sorted(ext.lstrip(".") for ext in EXTENSIONES_SOPORTADAS)

# Mismo directorio de caché que lee `ui/curar.py::_DIR_CACHE_MINIATURAS` —
# indexado por sha256+tamaño, así que da igual qué módulo lo escriba
# primero. HALLAZGO 3 (`listing-audit`, 2026-07-14): pre-generar aquí, en
# la ingesta (que ya recorre cada fichero para EXIF/hash — es el momento
# natural), mueve el coste de decodificar/redimensionar N fotos a donde
# Diego YA está esperando con una barra de progreso visible, en vez de
# pagarlo en silencio (`show_spinner=False`) al llegar a "Curar
# agrupación" — medido: 8,4 s con 33 fotos, 17,9 s con 26, pantalla en
# blanco. Con esto, el curado llega con la caché ya caliente.
_DIR_CACHE_MINIATURAS = DEFAULT_DATA_DIR / "cache" / "miniaturas"

# Umbral (proporción sin EXIF sobre el total del lote) a partir del cual el
# aviso escala de `st.warning` a `st.error` — "la mayoría" pedido por Diego.
# No bloquea nada, sólo cambia cuánto grita.
_UMBRAL_MAYORIA_SIN_EXIF = 0.5


def _listar_fotos_en_carpeta(carpeta: Path) -> list[Path]:
    """Fotos soportadas bajo `carpeta`, recursivo (las fotos de móvil suelen
    venir en subcarpetas por fecha/álbum). Nunca escribe nada en `carpeta`."""
    if not carpeta.is_dir():
        return []
    return sorted(p for p in carpeta.rglob("*") if p.is_file() and es_soportada(p))


def _copia_destino_libre(carpeta_lote: Path, nombre_original: str) -> Path:
    """Elige un nombre de destino libre dentro de `carpeta_lote`. Si el
    nombre ya existe (dos ficheros con el mismo nombre desde orígenes
    distintos), antepone un contador — nunca sobrescribe una copia ya
    hecha en este lote."""
    origen = Path(nombre_original)
    destino = carpeta_lote / origen.name
    contador = 1
    while destino.exists():
        destino = carpeta_lote / f"{origen.stem}_{contador}{origen.suffix}"
        contador += 1
    return destino


def _avisar_exif_ausente(resumen: ResumenExif) -> None:
    """Aviso imposible de ignorar, pero NUNCA bloqueante: Diego puede seguir
    igual. El cálculo (cuántas fotos, qué proporción) vive en
    `core.images.resumen_exif` — esta función sólo decide CÓMO pintarlo."""
    if resumen.sin_fecha_exif == 0:
        return

    mensaje = (
        f"**{resumen.sin_fecha_exif} de {resumen.total} foto(s) no traen fecha de captura "
        "(EXIF).**\n\n"
        "**Qué se pierde:** sin esa fecha, la app no puede separar los productos por el "
        "momento en que los fotografiaste, y tendrás que corregir más grupos a mano.\n\n"
        "**Causa más probable:** WhatsApp y otras aplicaciones de mensajería (y algunas "
        "herramientas de descarga) borran la fecha de las fotos. Pásalas por cable desde el "
        "móvil (o con una app que conserve los metadatos) y la app podrá agrupar mucho "
        "mejor.\n\n"
        "Puedes seguir igual — a veces no hay otra opción — pero revisa los grupos con más "
        "cuidado."
    )
    if resumen.porcentaje_sin_exif / 100.0 >= _UMBRAL_MAYORIA_SIN_EXIF:
        st.error(mensaje)
    else:
        st.warning(mensaje)


def _ingerir(
    store: LoteStore,
    nombre_lote: str,
    carpeta_origen: str,
    rutas_origen: list[Path],
) -> str:
    """Copia todas `rutas_origen` a un lote nuevo y las registra en el
    store. Devuelve el `lote_id` creado. Un fichero individual ilegible o
    no copiable se registra con `logger.exception` y se salta — nunca
    aborta el lote entero por una foto rota (mismo criterio que
    `core/images.py`).

    También PRE-GENERA la miniatura cacheada de cada foto legible
    (HALLAZGO 3, `listing-audit`): así "Curar agrupación" llega con la
    caché caliente en vez de decodificar N fotos de golpe en su primer
    render (medido: 8-18 s de pantalla en blanco con `show_spinner=False`,
    ver constante `_DIR_CACHE_MINIATURAS`). Un fallo generando UNA
    miniatura no aborta la ingesta ni cuenta como error del lote: la foto
    ya quedó registrada correctamente, y `ui/curar.py::_miniatura_de` la
    regenera bajo demanda si hace falta — sólo se pierde el ahorro para
    esa foto, nunca se pierde la foto ni se silencia el fallo."""
    lote_id = store.crear_lote(nombre_lote, carpeta_origen)
    carpeta_lote = store.lotes_dir / lote_id

    total = len(rutas_origen)
    progreso = st.progress(0.0, text=f"Copiando 0/{total} fotos…")

    fotos_registro: list[Foto] = []
    n_con_error = 0
    for i, origen in enumerate(rutas_origen, start=1):
        destino = _copia_destino_libre(carpeta_lote, origen.name)
        try:
            shutil.copy2(origen, destino)
        except OSError:
            logger.exception("No se pudo copiar %s -> %s", origen, destino)
            n_con_error += 1
            progreso.progress(i / total, text=f"Copiando {i}/{total} fotos…")
            continue

        meta = leer_metadatos(destino)
        if not meta.legible:
            n_con_error += 1

        try:
            hash_sha256 = sha256_de_fichero(destino)
        except OSError:
            logger.exception("No se pudo calcular el hash de %s", destino)
            n_con_error += 1
            progreso.progress(i / total, text=f"Copiando {i}/{total} fotos…")
            continue

        if meta.legible:
            try:
                obtener_o_crear_miniatura(destino, _DIR_CACHE_MINIATURAS, TAMANO_MINIATURA_DEFECTO)
            except Exception:  # noqa: BLE001 — pre-calentado best-effort: NINGÚN fallo aquí puede abortar el lote (mismo criterio amplio que core/images.leer_metadatos).
                # No aborta la ingesta ni marca la foto como error del lote:
                # la foto ya está bien copiada y registrada; sólo se pierde
                # el pre-calentado de ESTA miniatura (se genera bajo demanda
                # en `ui/curar.py` cuando Diego llegue a curarla).
                logger.exception(
                    "No se pudo pre-generar la miniatura de %s en la ingesta; se "
                    "generará bajo demanda al curar (más lento, pero no bloquea)",
                    destino,
                )

        timestamp_exif = (
            meta.fecha_captura_exif.isoformat() if meta.fecha_captura_exif else None
        )
        # `legible`/`error_lectura` se persisten tal cual los mide
        # `core.images.leer_metadatos` — es lo que le permite a
        # `core.store.guardar_agrupacion` rechazar con dientes que un
        # fichero ilegible acabe mezclado dentro de un producto
        # (`[listing-audit] CRÍTICO 3`, `ui/confirmacion.py`).
        fotos_registro.append(
            Foto(
                ruta=str(destino),
                hash=hash_sha256,
                timestamp_exif=timestamp_exif,
                legible=meta.legible,
                error_lectura=meta.error,
            )
        )
        progreso.progress(i / total, text=f"Copiando {i}/{total} fotos…")

    if fotos_registro:
        try:
            store.añadir_fotos(lote_id, fotos_registro)
        except FotoDuplicadaError:
            logger.exception("Duplicado registrando fotos del lote %s", lote_id)
            raise

    progreso.empty()
    mensaje = f"Lote «{nombre_lote}» creado: {len(fotos_registro)} foto(s) registrada(s)."
    if n_con_error:
        mensaje += f" {n_con_error} fichero(s) con error (revisa el log) — se saltaron."
    st.success(mensaje)
    return lote_id


def render(store: LoteStore) -> str | None:
    """Pinta la pantalla de ingesta.

    Devuelve el `lote_id` recién creado cuando Diego acaba de lanzar una
    ingesta completa; `None` en cualquier otro caso. `app.py` usa ese
    valor para decidir si navega a la pantalla de Confirmación — la
    decisión de A DÓNDE navegar vive en `app.py`, aquí sólo se informa de
    que ya pasó.
    """
    st.header("1. Ingesta de fotos")
    st.caption(
        "Copia las fotos del lote a `data/lotes/<id>/` — **nunca** toca tus "
        "ficheros originales. Con cientos de fotos de varios MB puede "
        "tardar; verás el progreso."
    )

    nombre_lote = st.text_input(
        "Nombre del lote",
        value=datetime.now().strftime("Lote %Y-%m-%d %H:%M"),
    )

    metodo = st.radio(
        "Origen de las fotos",
        ["Carpeta en disco", "Arrastrar ficheros"],
        horizontal=True,
    )

    rutas: list[Path] = []
    carpeta_origen_str = ""

    if metodo == "Carpeta en disco":
        carpeta_texto = st.text_input(
            "Ruta de la carpeta con las fotos mezcladas",
            placeholder=r"C:\Fotos\lote_reventa",
        )
        if carpeta_texto:
            carpeta = Path(carpeta_texto)
            carpeta_origen_str = str(carpeta)
            if not carpeta.is_dir():
                st.warning("Esa carpeta no existe todavía.")
            else:
                rutas = _listar_fotos_en_carpeta(carpeta)
                if not rutas:
                    st.warning(
                        "La carpeta existe pero no contiene fotos en un formato "
                        f"soportado ({', '.join(_TIPOS_UPLOADER)})."
                    )
                else:
                    st.info(f"{len(rutas)} foto(s) encontradas.")
    else:
        subidos = st.file_uploader(
            "Arrastra las fotos aquí",
            type=_TIPOS_UPLOADER,
            accept_multiple_files=True,
        )
        if subidos:
            carpeta_origen_str = "(subida manual desde el navegador, sin carpeta de origen)"
            # El navegador no da una ruta de disco: los bytes subidos se
            # escriben a una carpeta temporal propia (nunca es "el
            # original" de Diego, es la única copia que existe hasta este
            # punto) y desde ahí `_ingerir` los copia al lote como con
            # cualquier otro origen.
            carpeta_temporal = store.data_dir / "_tmp_subidas"
            carpeta_temporal.mkdir(parents=True, exist_ok=True)
            for subido in subidos:
                destino_tmp = _copia_destino_libre(carpeta_temporal, subido.name)
                destino_tmp.write_bytes(subido.getbuffer())
                rutas.append(destino_tmp)
            st.info(f"{len(rutas)} foto(s) recibidas.")

    if not rutas:
        return None

    _avisar_exif_ausente(resumen_exif(rutas))

    st.info(
        "Coste estimado de procesar este lote: **0,00 €**. Fase 1 usa sólo "
        "extracción local (EXIF); no se llama a ningún proveedor de pago."
    )

    if not st.button("Ingerir lote", type="primary", disabled=not nombre_lote.strip()):
        return None

    lote_id = _ingerir(store, nombre_lote.strip(), carpeta_origen_str, rutas)

    if metodo == "Arrastrar ficheros":
        # Limpieza de la carpeta temporal de subida: los bytes ya están
        # copiados dentro del lote (`data/lotes/<lote_id>/`); esto era sólo
        # el paso intermedio para materializar en disco lo que llegó del
        # navegador.
        for ruta_tmp in rutas:
            try:
                ruta_tmp.unlink(missing_ok=True)
            except OSError:
                logger.exception("No se pudo limpiar el fichero temporal %s", ruta_tmp)

    return lote_id
