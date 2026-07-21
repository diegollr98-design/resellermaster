"""core/store.py — Persistencia del lote (RESELLERMASTER).

Superficie sensible (`truth-loop.md` §B): perder el curado de un lote es
perder horas de Diego. Regla que gobierna este fichero:

    `st.session_state` es una CACHÉ, nunca la verdad. Todo el estado de
    curado de un lote se escribe aquí, a disco (SQLite + ficheros), dentro
    de transacciones. Un rerun de Streamlit, un crash o un cierre de
    pestaña no pueden costarle a Diego el trabajo ya hecho.

Contrato de `campos` en la tabla `productos`
---------------------------------------------
`core/schema.py` (costura 3) **todavía no existe** en el repo (Fase 1: sólo
costuras + ingesta + agrupación, cero atributos). Este módulo no puede
importar un contrato que no existe, así que `productos.campos` se guarda
como **JSON en texto plano**, de forma deliberadamente flexible. Cuando
`schema.py` aterrice, debe producir un dict serializable a ese JSON con,
por cada campo, la estructura de procedencia de `truth-loop.md` §A:

    {"marca": {"valor": "Nike", "fuente": "foto",
               "evidencia": "IMG_0421.jpg#etiqueta", "confianza": "alta"},
     "talla": {"valor": null, "fuente": null,
               "evidencia": null, "confianza": "baja"},
     ...}

`store.py` no valida ese contenido (no es su responsabilidad: eso es de
`schema.py`/`extract.py`). Su única responsabilidad es no perderlo.

Modelo de agrupación
---------------------
La pertenencia foto→producto vive en `fotos.producto_id` (relacional), NO
duplicada en un JSON `grupo_fotos` dentro de `productos`. Un JSON redundante
con la misma información que la FK es una segunda fuente de verdad que
puede desincronizarse — exactamente el tipo de bug silencioso que este
fichero existe para no cometer. `cargar_lote()` reconstruye el grupo de
fotos de cada producto a partir de esa FK.

Confirmación de Diego
----------------------
`truth-loop.md` §E: "el clustering propone, Diego confirma [...] Nunca
re-agrupar automáticamente después de que Diego haya confirmado. Su
confirmación es un hecho, no una sugerencia." Por eso:

- `guardar_agrupacion()` puede llamarse muchas veces mientras el producto
  no esté confirmado (Diego ajustando la propuesta del clustering) y en
  cada llamada **reemplaza** la propuesta de agrupación no confirmada del
  lote. Antes de la confirmación no hay "trabajo de Diego" que perder: hay
  una propuesta del algoritmo.
- En cuanto un producto está confirmado, sus fotos quedan bloqueadas:
  `guardar_agrupacion()` lanza `AgrupacionBloqueadaError` si una nueva
  propuesta intenta tocar una foto que pertenece a un producto ya
  confirmado. Eso sí es un hecho, y no se toca.
- La confirmación se registra también como fila append-only en
  `confirmaciones` (nunca se actualiza ni se borra): es el log auditable
  de "cuándo confirmó Diego qué".
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Ubicación por defecto de los datos: <raíz del repo>/data/
# --------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = _REPO_ROOT / "data"
DB_FILENAME = "resellermaster.db"

SCHEMA_VERSION = 3

# Cada entrada es la lista de sentencias DDL que llevan la base de datos de
# la versión (N-1) a la versión N. NUNCA se reescribe una migración ya
# publicada: si el esquema cambia, se añade una nueva entrada con el
# siguiente número. Así un lote a medias con un esquema viejo se actualiza
# sin perder filas — nunca hay un DROP de una tabla con datos de Diego.
_MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        """
        CREATE TABLE lotes (
            id              TEXT PRIMARY KEY,
            nombre          TEXT NOT NULL,
            carpeta_origen  TEXT NOT NULL,
            fecha_creacion  TEXT NOT NULL,
            estado          TEXT NOT NULL DEFAULT 'abierto'
        )
        """,
        """
        CREATE TABLE productos (
            id              TEXT PRIMARY KEY,
            lote_id         TEXT NOT NULL REFERENCES lotes(id),
            campos          TEXT NOT NULL DEFAULT '{}',
            confirmado      INTEGER NOT NULL DEFAULT 0,
            confirmado_en   TEXT,
            creado_en       TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE fotos (
            id              TEXT PRIMARY KEY,
            lote_id         TEXT NOT NULL REFERENCES lotes(id),
            ruta            TEXT NOT NULL,
            hash            TEXT NOT NULL,
            timestamp_exif  TEXT,
            producto_id     TEXT REFERENCES productos(id),
            confirmada      INTEGER NOT NULL DEFAULT 0,
            creada_en       TEXT NOT NULL,
            UNIQUE(lote_id, ruta)
        )
        """,
        """
        CREATE TABLE confirmaciones (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id     TEXT NOT NULL REFERENCES productos(id),
            lote_id         TEXT NOT NULL REFERENCES lotes(id),
            tipo            TEXT NOT NULL,
            timestamp       TEXT NOT NULL,
            detalle         TEXT NOT NULL DEFAULT '{}'
        )
        """,
        "CREATE INDEX idx_fotos_lote ON fotos(lote_id)",
        "CREATE INDEX idx_fotos_producto ON fotos(producto_id)",
        "CREATE INDEX idx_productos_lote ON productos(lote_id)",
        "CREATE INDEX idx_confirmaciones_producto ON confirmaciones(producto_id)",
    ),
    # v2 — CRÍTICO 3 (`ui/confirmacion.py`, listing-audit): una foto ILEGIBLE
    # (fichero corrupto, formato no soportado) se registraba en `fotos` sin
    # ninguna marca, así que nada impedía que acabara agrupada DENTRO de un
    # producto junto a fotos legítimas. `legible`/`error_lectura` persisten
    # lo que `core.images.leer_metadatos` ya calcula en la ingesta
    # (`ui/ingesta.py`), para que `guardar_agrupacion` (única vía de
    # escritura) pueda RECHAZAR con dientes un grupo que mezcle una foto
    # ilegible con otras — ver `FotoIlegibleError`, más abajo.
    2: (
        "ALTER TABLE fotos ADD COLUMN legible INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE fotos ADD COLUMN error_lectura TEXT",
    ),
    # v3 — FASE 5 FINANZAS (superficie sensible: ventas = dinero). Todo
    # additivo, ningún DROP: un lote v2 a medias se abre sin romper. Modelo
    # decidido por un panel multi-agente (seed `fase-5-finanzas.md`):
    #
    #  - `productos.referencia` + `referencia_seq`: el número humano ("Ref. N")
    #    que Diego imprime en la descripción como LLAVE para localizar la venta.
    #    `referencia_seq` es AUTOINCREMENT (marca de agua): nunca reutiliza un
    #    número tras borrar un producto — dos productos jamás comparten Ref.
    #  - `coste_cents`: coste de adquisición (opcional, céntimos ENTEROS, nunca
    #    float). Columna propia porque `campos` lo sobreescriben `guardar_extraccion`
    #    Y `confirmar_ficha` con `UPDATE productos SET campos=?` — el dinero NO
    #    puede vivir ahí o se pierde en la siguiente extracción.
    #  - `publicaciones`: el "Subido" por plataforma + snapshot congelado de la
    #    tasación (mediana+comparables) en el momento de pulsar Subido.
    #  - `ventas`: SIN FK a `productos` a propósito — una venta es dinero y debe
    #    SOBREVIVIR a un futuro borrado del producto; por eso congela snapshots
    #    (referencia/titulo/coste) en el momento de vender.
    #  - `movimientos`: log APPEND-ONLY, el rastro reversible de undo/devolución.
    3: (
        "ALTER TABLE productos ADD COLUMN referencia INTEGER",
        "ALTER TABLE productos ADD COLUMN coste_cents INTEGER NOT NULL DEFAULT 0",
        # SQLite permite múltiples NULL bajo un índice UNIQUE: los productos
        # sin referencia asignada conviven, pero dos NO pueden compartir número.
        "CREATE UNIQUE INDEX idx_productos_referencia ON productos(referencia)",
        """
        CREATE TABLE referencia_seq (
            n            INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id  TEXT NOT NULL REFERENCES productos(id),
            asignada_en  TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE publicaciones (
            id                    TEXT PRIMARY KEY,
            producto_id           TEXT NOT NULL REFERENCES productos(id),
            plataforma            TEXT NOT NULL,
            subido_en             TEXT NOT NULL,
            precio_elegido_cents  INTEGER,
            tasacion_json         TEXT,
            UNIQUE(producto_id, plataforma)
        )
        """,
        """
        CREATE TABLE ventas (
            producto_id        TEXT PRIMARY KEY,
            referencia_snap    INTEGER,
            titulo_snap        TEXT,
            coste_snap_cents   INTEGER NOT NULL DEFAULT 0,
            precio_final_cents INTEGER,
            plataforma_venta   TEXT,
            estado             TEXT NOT NULL DEFAULT 'vendida',
            lote_venta_id      TEXT,
            fecha_venta        TEXT,
            creada_en          TEXT NOT NULL,
            actualizada_en     TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE movimientos (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id  TEXT NOT NULL,
            tipo         TEXT NOT NULL,
            timestamp    TEXT NOT NULL,
            detalle      TEXT NOT NULL DEFAULT '{}'
        )
        """,
        "CREATE INDEX idx_publicaciones_producto ON publicaciones(producto_id)",
        "CREATE INDEX idx_movimientos_producto ON movimientos(producto_id)",
    ),
}

_PLATAFORMAS = ("wallapop", "vinted")


# --------------------------------------------------------------------------
# Errores — ruidosos a propósito. Nada de except Exception: pass.
# --------------------------------------------------------------------------
class StoreError(Exception):
    """Base de los errores de persistencia. Un fallo de escritura se propaga."""


class LoteNoEncontradoError(StoreError):
    pass


class ProductoNoEncontradoError(StoreError):
    pass


class FotoNoEncontradaError(StoreError):
    pass


class RutaInvalidaError(StoreError):
    """La ruta de una foto no está bajo el directorio de datos del lote."""


class AgrupacionBloqueadaError(StoreError):
    """Se intentó re-agrupar una foto cuyo producto ya está confirmado."""


class FotoDuplicadaError(StoreError):
    """Ya existe una foto con esa ruta en el lote (mismo fichero, dos veces)."""


class FotoIlegibleError(StoreError):
    """Un grupo intenta mezclar una foto ILEGIBLE (`legible=0`) con otras.

    Con dientes (`decision-making.md` §12): `guardar_agrupacion` la lanza y
    NO aplica ningún cambio, en vez de sólo avisar. Un fichero que no se
    puede abrir no puede acabar dentro de un producto junto a fotos
    legítimas — es el CRÍTICO 3 de `listing-audit` sobre
    `ui/confirmacion.py`. Un grupo de UNA sola foto ilegible sí es legal
    (así es como `core.grouping._grupos_ilegibles` la propone: sola, para
    que Diego la revise o la descarte con `descartar_foto`)."""


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nuevo_id() -> str:
    return uuid.uuid4().hex


def _titulo_de_campos(campos_json: str | None) -> str | None:
    """Extrae el título publicable del JSON de `productos.campos`, defensivo.

    El contrato de `campos` (ver docstring del módulo) anida la procedencia,
    así que el título vive en `campos['campos']['titulo']['valor']` (forma que
    produce `core.extract.serializar_extraccion`) o, en fichas más planas, en
    `campos['titulo']['valor']`. Nunca revienta: cualquier forma inesperada,
    JSON corrupto o ausencia devuelve `None` — es un SNAPSHOT informativo para
    el ledger de ventas, no una fuente de verdad que deba fallar ruidosamente.
    """
    if not campos_json:
        return None
    try:
        data = json.loads(campos_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for contenedor in (data.get("campos"), data):
        if isinstance(contenedor, dict):
            titulo = contenedor.get("titulo")
            if isinstance(titulo, dict):
                valor = titulo.get("valor")
                if isinstance(valor, str):
                    return valor
    return None


@dataclass(frozen=True)
class Foto:
    ruta: str
    hash: str
    timestamp_exif: str | None = None
    # Persistido desde `core.images.leer_metadatos(...).legible/.error` en
    # la ingesta (`ui/ingesta.py`). Default `True`/`None` sólo para no
    # romper los llamadores/tests existentes que ya asumían fotos legibles
    # — nunca se debe fijar `True` a mano para una foto que no se leyó.
    legible: bool = True
    error_lectura: str | None = None


class LoteStore:
    """Persistencia SQLite + disco de los lotes de RESELLERMASTER.

    Cada método abre su propia conexión y hace su trabajo dentro de una
    única transacción (`_transaccion()`): o se aplica entero, o no se
    aplica nada. Si el proceso muere a mitad de una escritura, SQLite
    (journal_mode=WAL) deja la base en el último estado consistente
    confirmado — nunca a medias.
    """

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
        self.lotes_dir = self.data_dir / "lotes"
        self.db_path = self.data_dir / DB_FILENAME
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lotes_dir.mkdir(parents=True, exist_ok=True)
        self._migrar()

    # -- infraestructura -----------------------------------------------

    def _conectar(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        return conn

    @contextmanager
    def _transaccion(self) -> Iterator[sqlite3.Connection]:
        """Una escritura atómica: BEGIN IMMEDIATE ... COMMIT, o ROLLBACK.

        `isolation_level=None` (autocommit) + BEGIN/COMMIT manual da control
        explícito: si algo dentro del bloque lanza, se hace ROLLBACK y el
        error se re-propaga sin tocar la base. `BEGIN IMMEDIATE` toma el
        lock de escritura de inmediato (evita condiciones de "database is
        locked" a mitad de una transacción concurrente).
        """
        # `_conectar()` va DENTRO del try: un fallo al abrir la conexión
        # ('database is locked' al tomar el lock, disco lleno) es justo el
        # caso que hay que envolver — si quedara fuera, escaparía como
        # sqlite3.Error crudo y la UI no lo capturaría.
        conn: sqlite3.Connection | None = None
        try:
            conn = self._conectar()
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException as exc:
            if conn is not None:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    # El ROLLBACK falló (conexión ya muerta, etc.). Se registra
                    # pero NO se deja que enmascare el error original que abortó
                    # la transacción — ese es el que le importa a quien llama.
                    logger.exception("ROLLBACK falló tras un error en la transacción")
            # Un fallo CRUDO de SQLite ('database is locked', disco lleno...)
            # debe salir como StoreError para que la capa UI lo capture con su
            # `except StoreError` y nunca le pinte un traceback a Diego
            # ([INC-006], `decision-making.md` §13). Un StoreError INTENCIONADO
            # que se lanzó dentro del bloque (FotoDuplicadaError, etc.) sale
            # tal cual — no se re-envuelve. Cualquier otra cosa (bug de
            # programación) sube sin tocar, para que se vea.
            if isinstance(exc, sqlite3.Error):
                raise StoreError(f"error de base de datos: {exc}") from exc
            raise
        finally:
            if conn is not None:
                conn.close()

    def _migrar(self) -> None:
        conn = self._conectar()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version     INTEGER PRIMARY KEY,
                    aplicada_en TEXT NOT NULL
                )
                """
            )
            fila = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            version_actual = fila["v"] if fila and fila["v"] is not None else 0
            for version in range(version_actual + 1, SCHEMA_VERSION + 1):
                for sentencia in _MIGRATIONS[version]:
                    conn.execute(sentencia)
                conn.execute(
                    "INSERT INTO schema_version (version, aplicada_en) VALUES (?, ?)",
                    (version, _ahora()),
                )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    # -- lote -------------------------------------------------------------

    def crear_lote(self, nombre: str, carpeta_origen: str) -> str:
        """Crea un lote nuevo y su carpeta de trabajo en disco. Devuelve su id.

        Orden deliberado: primero la carpeta en disco, luego la fila en la
        base de datos. `mkdir(exist_ok=True)` es idempotente y barato de
        rehacer; si el proceso muere justo después de crear la carpeta pero
        antes del COMMIT, sólo queda una carpeta vacía huérfana (sin datos
        que perder). El orden inverso es el peligroso: dejaría un lote que
        la base de datos dice que existe pero cuya carpeta no está — y
        cualquier `añadir_fotos()` posterior fallaría contra un directorio
        que nadie creó.
        """
        if not nombre.strip():
            raise ValueError("nombre de lote vacío")
        lote_id = _nuevo_id()
        (self.lotes_dir / lote_id).mkdir(parents=True, exist_ok=True)
        with self._transaccion() as conn:
            conn.execute(
                """INSERT INTO lotes (id, nombre, carpeta_origen, fecha_creacion, estado)
                   VALUES (?, ?, ?, ?, 'abierto')""",
                (lote_id, nombre, carpeta_origen, _ahora()),
            )
        return lote_id

    def listar_lotes(self) -> list[dict[str, Any]]:
        """Resumen de todos los lotes, más recientes primero."""
        conn = self._conectar()
        try:
            filas = conn.execute(
                """
                SELECT l.id, l.nombre, l.carpeta_origen, l.fecha_creacion, l.estado,
                       COUNT(DISTINCT f.id) AS n_fotos,
                       COUNT(DISTINCT p.id) AS n_productos,
                       COUNT(DISTINCT CASE WHEN p.confirmado = 1 THEN p.id END) AS n_confirmados
                FROM lotes l
                LEFT JOIN fotos f ON f.lote_id = l.id
                LEFT JOIN productos p ON p.lote_id = l.id
                GROUP BY l.id
                ORDER BY l.fecha_creacion DESC
                """
            ).fetchall()
            return [dict(fila) for fila in filas]
        finally:
            conn.close()

    def _lote_existe(self, conn: sqlite3.Connection, lote_id: str) -> None:
        fila = conn.execute("SELECT id FROM lotes WHERE id = ?", (lote_id,)).fetchone()
        if fila is None:
            raise LoteNoEncontradoError(f"lote no encontrado: {lote_id}")

    # -- fotos --------------------------------------------------------------

    def añadir_fotos(self, lote_id: str, fotos: list[Foto | dict[str, Any]]) -> list[str]:
        """Registra fotos ya copiadas a `data/lotes/<lote_id>/` (nunca el
        original de Diego: se valida que la ruta esté bajo esa carpeta).

        Devuelve los ids asignados, en el mismo orden que `fotos`.
        """
        registros = [f if isinstance(f, Foto) else Foto(**f) for f in fotos]
        carpeta_lote = (self.lotes_dir / lote_id).resolve()
        ids: list[str] = []
        with self._transaccion() as conn:
            self._lote_existe(conn, lote_id)
            for foto in registros:
                ruta_abs = Path(foto.ruta).resolve()
                try:
                    ruta_abs.relative_to(carpeta_lote)
                except ValueError as exc:
                    raise RutaInvalidaError(
                        f"la foto debe vivir bajo {carpeta_lote} (copia de trabajo, "
                        f"nunca el original de Diego); recibido: {foto.ruta}"
                    ) from exc
                foto_id = _nuevo_id()
                try:
                    conn.execute(
                        """INSERT INTO fotos
                           (id, lote_id, ruta, hash, timestamp_exif, producto_id,
                            confirmada, legible, error_lectura, creada_en)
                           VALUES (?, ?, ?, ?, ?, NULL, 0, ?, ?, ?)""",
                        (
                            foto_id,
                            lote_id,
                            str(foto.ruta),
                            foto.hash,
                            foto.timestamp_exif,
                            int(foto.legible),
                            foto.error_lectura,
                            _ahora(),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    # UNIQUE(lote_id, ruta): la misma copia de trabajo ya
                    # estaba registrada. Fallo ruidoso y tipado, no un
                    # sqlite3.IntegrityError crudo escapando de la API.
                    raise FotoDuplicadaError(
                        f"la foto {foto.ruta!r} ya está registrada en el lote {lote_id}"
                    ) from exc
                ids.append(foto_id)
        return ids

    def resumen_exif_lote(self, lote_id: str) -> dict[str, int]:
        """Cuántas fotos del lote traen fecha de captura EXIF y cuántas no.

        Se calcula sobre `fotos.timestamp_exif`, columna que YA persiste
        `core.images.leer_metadatos(...).fecha_captura_exif` desde la
        ingesta (`ui/ingesta.py`) — nunca el `mtime` del fichero. **No** se
        guarda como columna aparte en `lotes`: sería una segunda fuente de
        verdad derivada de `fotos` que se podría desincronizar (mismo
        criterio documentado arriba para `grupo_fotos`), y aquí no hace
        falta — se recalcula con una `COUNT` barata cada vez.

        Sirve para dos cosas (pedidas explícitamente por Diego): que la
        pantalla de confirmación explique por qué los grupos de este lote
        se degradan sin señal temporal, y para poder medir después cuántos
        lotes llegan sin EXIF (p. ej. por venir de WhatsApp).
        """
        conn = self._conectar()
        try:
            self._lote_existe(conn, lote_id)
            fila = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN timestamp_exif IS NOT NULL THEN 1 ELSE 0 END) AS con_exif
                FROM fotos
                WHERE lote_id = ?
                """,
                (lote_id,),
            ).fetchone()
            total = fila["total"] or 0
            con_exif = fila["con_exif"] or 0
            return {"total": total, "con_exif": con_exif, "sin_exif": total - con_exif}
        finally:
            conn.close()

    # -- agrupación (superficie sensible) -----------------------------------

    def guardar_agrupacion(self, lote_id: str, grupos: list[list[str]]) -> list[str]:
        """Guarda/reemplaza la PROPUESTA de agrupación no confirmada del lote.

        `grupos` es una lista de grupos; cada grupo es una lista de `foto_id`
        que el clustering (o Diego, ajustando a mano) propone como un mismo
        producto. Se crea un `producto_id` nuevo por grupo.

        No toca nada de un producto ya confirmado: si alguna foto de
        `grupos` pertenece a un producto confirmado, no se aplica NINGÚN
        cambio (falla entero, no a medias) y se lanza
        `AgrupacionBloqueadaError`.
        """
        with self._transaccion() as conn:
            self._lote_existe(conn, lote_id)

            fotos_por_id = {
                fila["id"]: fila
                for fila in conn.execute(
                    "SELECT id, producto_id, legible FROM fotos WHERE lote_id = ?", (lote_id,)
                ).fetchall()
            }
            confirmados = {
                fila["id"]
                for fila in conn.execute(
                    "SELECT id FROM productos WHERE lote_id = ? AND confirmado = 1",
                    (lote_id,),
                ).fetchall()
            }

            vistas: set[str] = set()
            for grupo in grupos:
                if not grupo:
                    raise ValueError("grupo vacío: un producto necesita al menos una foto")
                filas_grupo = []
                for foto_id in grupo:
                    if foto_id in vistas:
                        raise ValueError(f"foto {foto_id} aparece en más de un grupo")
                    vistas.add(foto_id)
                    fila = fotos_por_id.get(foto_id)
                    if fila is None:
                        raise FotoNoEncontradaError(f"foto no encontrada en el lote: {foto_id}")
                    if fila["producto_id"] in confirmados:
                        raise AgrupacionBloqueadaError(
                            f"foto {foto_id} pertenece a un producto ya confirmado "
                            f"({fila['producto_id']}); Diego ya cerró ese grupo, no se re-agrupa"
                        )
                    filas_grupo.append(fila)

                # CRÍTICO 3 (`ui/confirmacion.py`, listing-audit): un fichero
                # ilegible no se puede agrupar CON OTRAS fotos — sólo puede
                # quedarse solo (grupo de 1), que es como lo propone
                # `core.grouping._grupos_ilegibles`. Falla ENTERO (nada se
                # aplica), no a medias: es la misma disciplina que
                # `AgrupacionBloqueadaError`, más arriba.
                if len(grupo) > 1:
                    ilegibles = [fila["id"] for fila in filas_grupo if not fila["legible"]]
                    if ilegibles:
                        raise FotoIlegibleError(
                            f"grupo con {len(grupo)} foto(s) incluye {len(ilegibles)} "
                            f"ilegible(s) ({ilegibles}): un fichero que no se puede abrir "
                            "no puede mezclarse con otras fotos ni acabar dentro de un "
                            "producto — sácalo del grupo o descártalo con descartar_foto()."
                        )

            # Sólo se reemplazan los productos NO confirmados de este lote:
            # son propuesta del algoritmo, no trabajo confirmado de Diego.
            productos_no_confirmados = [
                fila["id"]
                for fila in conn.execute(
                    "SELECT id FROM productos WHERE lote_id = ? AND confirmado = 0",
                    (lote_id,),
                ).fetchall()
            ]
            if productos_no_confirmados:
                marcadores = ",".join("?" for _ in productos_no_confirmados)
                conn.execute(
                    f"UPDATE fotos SET producto_id = NULL "
                    f"WHERE producto_id IN ({marcadores})",
                    productos_no_confirmados,
                )
                conn.execute(
                    f"DELETE FROM productos WHERE id IN ({marcadores})",
                    productos_no_confirmados,
                )

            nuevos_ids: list[str] = []
            for grupo in grupos:
                producto_id = _nuevo_id()
                conn.execute(
                    """INSERT INTO productos (id, lote_id, campos, confirmado, creado_en)
                       VALUES (?, ?, '{}', 0, ?)""",
                    (producto_id, lote_id, _ahora()),
                )
                for foto_id in grupo:
                    conn.execute(
                        "UPDATE fotos SET producto_id = ? WHERE id = ?",
                        (producto_id, foto_id),
                    )
                nuevos_ids.append(producto_id)
            return nuevos_ids

    def descartar_foto(self, lote_id: str, foto_id: str) -> None:
        """Elimina definitivamente una foto ILEGIBLE del lote.

        Único camino de escritura para esto (CRÍTICO 3, `ui/confirmacion.py`
        vía `listing-audit`): la UI ya no ofrece "añadir a un grupo" para una
        foto `legible=0`, sólo este botón de descarte — pero la guardia real
        vive aquí, no confía en que la UI no se equivoque. Rechaza con
        dientes descartar una foto LEGIBLE: eso perdería trabajo de curado
        de Diego en silencio, que es justo lo que este módulo existe para
        no hacer nunca.

        Si la foto era la única de su producto (el caso normal: un
        ilegible siempre llega como grupo de 1, ver
        `core.grouping._grupos_ilegibles`) y ese producto no está
        confirmado, el producto huérfano también se limpia — nunca se deja
        un `productos` con cero fotos colgando.
        """
        with self._transaccion() as conn:
            fila = conn.execute(
                "SELECT id, producto_id, legible FROM fotos WHERE id = ? AND lote_id = ?",
                (foto_id, lote_id),
            ).fetchone()
            if fila is None:
                raise FotoNoEncontradaError(f"foto no encontrada en el lote {lote_id}: {foto_id}")
            if fila["legible"]:
                raise ValueError(
                    f"descartar_foto sólo es válido para fotos ILEGIBLES; {foto_id} es "
                    "legible — descartarla perdería trabajo de curado sin avisar. Usa "
                    "guardar_agrupacion/confirmar_producto para una foto legible."
                )
            producto_id = fila["producto_id"]
            conn.execute("DELETE FROM fotos WHERE id = ?", (foto_id,))
            if producto_id is not None:
                restante = conn.execute(
                    "SELECT COUNT(*) AS n FROM fotos WHERE producto_id = ?", (producto_id,)
                ).fetchone()["n"]
                if restante == 0:
                    producto_fila = conn.execute(
                        "SELECT confirmado FROM productos WHERE id = ?", (producto_id,)
                    ).fetchone()
                    if producto_fila is not None and not producto_fila["confirmado"]:
                        conn.execute("DELETE FROM productos WHERE id = ?", (producto_id,))

    def archivar_foto(self, lote_id: str, foto_id: str) -> Path:
        """Quita una foto MALA o CASI-duplicada del lote a petición de Diego —
        RECUPERABLE, nunca un borrado del disco (a diferencia de
        `descartar_foto`, que sí borra el fichero de un ILEGIBLE y sólo vale
        para eso). Mueve el fichero a `<carpeta_del_lote>/descartadas/` y
        SÓLO ENTONCES borra su fila de `fotos` — nunca al revés: si algo
        falla a mitad, el fallo correcto es "queda un fichero de más en
        `descartadas/`", nunca "desapareció una foto de la que Diego seguía
        teniendo trabajo hecho".

        Vale tanto para fotos LEGIBLES (es su razón de ser: una foto
        borrosa, repetida o mal encuadrada que Diego no quiere en la ficha)
        como para ilegibles — para éstas es una alternativa a
        `descartar_foto` que además conserva el fichero por si hace falta
        inspeccionarlo luego.

        Orden fichero-antes-que-DB, deliberado: `shutil.move` se ejecuta
        FUERA de cualquier transacción de la base de datos. Si falla (disco
        lleno, antivirus de Windows bloqueando el fichero, permiso
        denegado), la excepción se propaga ANTES de tocar una sola fila —
        el lote de Diego no pierde nada (`decision-making.md` §13: nunca
        un fallback silencioso ni un estado a medias). Si el fichero se
        movió bien pero la escritura en la DB falla después (el caso raro:
        SQLite bloqueada, disco lleno un instante más tarde), se intenta
        devolver el fichero a su sitio original; si ESO también falla, se
        deja constancia ruidosa con `logger.exception` (nunca en silencio)
        y se propaga la excepción igualmente — el fichero puede quedar
        huérfano en `descartadas/` sin fila que lo referencie, y eso tiene
        que quedar en el log para que Diego (o el propio store, en un
        arreglo posterior) lo pueda reconciliar a mano.

        Rechaza con dientes (`AgrupacionBloqueadaError`) archivar una foto
        de un producto YA CONFIRMADO: Diego ya cerró esa ficha; tocarle una
        foto ahora es una operación distinta (editar un producto
        confirmado), no este flujo de curado — la guardia vive aquí, no
        confía en que la UI no ofrezca el botón.

        Devuelve la ruta nueva del fichero en `descartadas/`.
        """
        # Lectura de validación por conexión directa (no necesita transacción).
        # Un fallo CRUDO de SQLite aquí ('database is locked' al abrir) se
        # envuelve en StoreError como el resto del módulo, para que la UI lo
        # capture y nunca sea un traceback ([INC-006]). Los StoreError
        # intencionados de dentro (FotoNoEncontradaError, AgrupacionBloqueadaError)
        # NO se re-envuelven: `except sqlite3.Error` no los toca.
        try:
            conn = self._conectar()
            try:
                self._lote_existe(conn, lote_id)
                fila = conn.execute(
                    "SELECT id, ruta, producto_id FROM fotos WHERE id = ? AND lote_id = ?",
                    (foto_id, lote_id),
                ).fetchone()
                if fila is None:
                    raise FotoNoEncontradaError(
                        f"foto no encontrada en el lote {lote_id}: {foto_id}"
                    )
                producto_id = fila["producto_id"]
                if producto_id is not None:
                    producto_fila = conn.execute(
                        "SELECT confirmado FROM productos WHERE id = ?", (producto_id,)
                    ).fetchone()
                    if producto_fila is not None and producto_fila["confirmado"]:
                        raise AgrupacionBloqueadaError(
                            f"foto {foto_id} pertenece a un producto ya confirmado "
                            f"({producto_id}); Diego ya cerró esa ficha, archivar_foto "
                            "no es el camino para tocarla"
                        )
                ruta_original = Path(fila["ruta"])
            finally:
                conn.close()
        except sqlite3.Error as exc:
            raise StoreError(
                f"error de base de datos al validar la foto {foto_id}: {exc}"
            ) from exc

        directorio_descartadas = self.lotes_dir / lote_id / "descartadas"
        directorio_descartadas.mkdir(parents=True, exist_ok=True)
        ruta_destino = directorio_descartadas / ruta_original.name
        if ruta_destino.exists():
            # No machacar un fichero ya descartado con el mismo nombre: el
            # foto_id (uuid hex) es único por fila, así que el sufijo nunca
            # puede colisionar entre dos fotos distintas del mismo lote.
            ruta_destino = (
                directorio_descartadas / f"{ruta_original.stem}_{foto_id}{ruta_original.suffix}"
            )

        try:
            shutil.move(str(ruta_original), str(ruta_destino))
        except OSError as exc:
            logger.exception(
                "No se pudo mover %s a %s al archivar la foto %s; la DB no se toca",
                ruta_original,
                ruta_destino,
                foto_id,
            )
            # Se envuelve en StoreError (no se re-lanza el OSError crudo) para
            # que la UI lo capture con su `except StoreError` — en la máquina
            # de Diego este fallo es real (antivirus de Windows reteniendo el
            # .jpg, disco lleno, permiso denegado) y nunca puede pintarle un
            # traceback ([INC-006]). La DB no se ha tocado: sin pérdida.
            raise StoreError(
                f"no se pudo mover el fichero de la foto {foto_id} a 'descartadas': {exc}"
            ) from exc

        try:
            with self._transaccion() as conn2:
                conn2.execute("DELETE FROM fotos WHERE id = ?", (foto_id,))
                if producto_id is not None:
                    restante = conn2.execute(
                        "SELECT COUNT(*) AS n FROM fotos WHERE producto_id = ?", (producto_id,)
                    ).fetchone()["n"]
                    if restante == 0:
                        producto_fila = conn2.execute(
                            "SELECT confirmado FROM productos WHERE id = ?", (producto_id,)
                        ).fetchone()
                        if producto_fila is not None and not producto_fila["confirmado"]:
                            conn2.execute("DELETE FROM productos WHERE id = ?", (producto_id,))
        except BaseException:
            logger.exception(
                "El fichero de la foto %s ya se movió a %s pero la DB falló al "
                "borrar su fila; intentando devolverlo a %s",
                foto_id,
                ruta_destino,
                ruta_original,
            )
            try:
                shutil.move(str(ruta_destino), str(ruta_original))
            except OSError:
                logger.exception(
                    "No se pudo devolver %s a %s tras el fallo de DB: el fichero "
                    "queda en 'descartadas/' SIN fila en la base de datos — "
                    "requiere reconciliación manual",
                    ruta_destino,
                    ruta_original,
                )
            raise

        return ruta_destino

    def confirmar_producto(self, producto_id: str, detalle: dict[str, Any] | None = None) -> None:
        """Registra que Diego confirmó la agrupación de un producto.

        Es un HECHO append-only: marca `productos.confirmado` y añade una
        fila en `confirmaciones` que nunca se edita ni se borra. A partir de
        aquí, `guardar_agrupacion()` rechaza cualquier intento de mover las
        fotos de este producto.
        """
        with self._transaccion() as conn:
            fila = conn.execute(
                "SELECT id, lote_id, confirmado FROM productos WHERE id = ?",
                (producto_id,),
            ).fetchone()
            if fila is None:
                raise ProductoNoEncontradoError(f"producto no encontrado: {producto_id}")
            ahora = _ahora()
            if not fila["confirmado"]:
                conn.execute(
                    "UPDATE productos SET confirmado = 1, confirmado_en = ? WHERE id = ?",
                    (ahora, producto_id),
                )
            conn.execute(
                "UPDATE fotos SET confirmada = 1 WHERE producto_id = ?",
                (producto_id,),
            )
            conn.execute(
                """INSERT INTO confirmaciones (producto_id, lote_id, tipo, timestamp, detalle)
                   VALUES (?, ?, 'agrupacion', ?, ?)""",
                (producto_id, fila["lote_id"], ahora, json.dumps(detalle or {}, ensure_ascii=False)),
            )

    # -- extracción de atributos (superficie sensible) -------------------

    def guardar_extraccion(self, producto_id: str, extraccion: dict[str, Any]) -> None:
        """Persiste el resultado de `core.extract.serializar_extraccion(...)`
        en `productos.campos` (JSON en texto plano — ver "Contrato de
        campos" en el docstring del módulo, columna que existe desde la
        Fase 1 justo para esto).

        Re-ejecutable a propósito: volver a extraer un producto (relanzar
        el VLM) sobreescribe su ficha con la versión nueva. `campos` NO es
        append-only (a diferencia de `confirmaciones`) — es el borrador
        vigente que la UI de revisión lee y Diego confirma después con
        `confirmar_ficha`. El coste ya gastado en la extracción anterior
        no se pierde por esto: vive en la caché de `core/llm.py` por hash
        de imagen, nunca aquí.

        Si el producto no existe → `ProductoNoEncontradoError` (mismo
        patrón que `confirmar_producto`)."""
        with self._transaccion() as conn:
            fila = conn.execute(
                "SELECT id FROM productos WHERE id = ?", (producto_id,)
            ).fetchone()
            if fila is None:
                raise ProductoNoEncontradoError(f"producto no encontrado: {producto_id}")
            conn.execute(
                "UPDATE productos SET campos = ? WHERE id = ?",
                (json.dumps(extraccion, ensure_ascii=False), producto_id),
            )

    def confirmar_ficha(
        self,
        producto_id: str,
        campos_confirmados: dict[str, Any],
        detalle: dict[str, Any] | None = None,
    ) -> None:
        """Registra el HECHO de que Diego confirmó los ATRIBUTOS de la
        ficha — distinto de `confirmar_producto`, que confirma la
        AGRUPACIÓN de fotos (Fase 1). `truth-loop.md` §A: el campo
        `estado` y cualquier otro que Diego edite a mano deben quedar con
        `fuente="diego"` en `campos_confirmados` — esa decisión es de
        quien llama (la UI); este método sólo persiste lo que le pasan,
        tal cual, no reinterpreta procedencia.

        Dentro de una única transacción: (a) sobreescribe
        `productos.campos` con `campos_confirmados`, (b) añade una fila
        append-only en `confirmaciones` con `tipo='ficha'` — nunca se
        edita ni se borra, es el log auditable de "cuándo confirmó Diego
        qué" (mismo patrón que `confirmar_producto`, que usa
        `tipo='agrupacion'`).

        Si el producto no existe → `ProductoNoEncontradoError`."""
        with self._transaccion() as conn:
            fila = conn.execute(
                "SELECT id, lote_id FROM productos WHERE id = ?", (producto_id,)
            ).fetchone()
            if fila is None:
                raise ProductoNoEncontradoError(f"producto no encontrado: {producto_id}")
            ahora = _ahora()
            conn.execute(
                "UPDATE productos SET campos = ? WHERE id = ?",
                (json.dumps(campos_confirmados, ensure_ascii=False), producto_id),
            )
            conn.execute(
                """INSERT INTO confirmaciones (producto_id, lote_id, tipo, timestamp, detalle)
                   VALUES (?, ?, 'ficha', ?, ?)""",
                (producto_id, fila["lote_id"], ahora, json.dumps(detalle or {}, ensure_ascii=False)),
            )

    # -- finanzas: referencia + coste (superficie sensible: dinero) -------

    def asignar_referencia(self, producto_id: str) -> int:
        """Asigna (o devuelve) el número de referencia humano de un producto.

        Es la LLAVE que Diego imprime en la descripción ("Ref. N") para
        localizar después la venta. `assign-once` / idempotente: si el
        producto ya tiene `referencia`, la devuelve sin tocar nada. Si es
        NULL, inserta una fila en `referencia_seq` (AUTOINCREMENT = marca de
        agua: no se reutiliza tras un DELETE, así dos productos jamás
        comparten número) y fija `productos.referencia` con ese `n`.

        `ProductoNoEncontradoError` si el producto no existe.
        """
        with self._transaccion() as conn:
            fila = conn.execute(
                "SELECT referencia FROM productos WHERE id = ?", (producto_id,)
            ).fetchone()
            if fila is None:
                raise ProductoNoEncontradoError(f"producto no encontrado: {producto_id}")
            if fila["referencia"] is not None:
                return int(fila["referencia"])
            cursor = conn.execute(
                "INSERT INTO referencia_seq (producto_id, asignada_en) VALUES (?, ?)",
                (producto_id, _ahora()),
            )
            n = int(cursor.lastrowid)
            conn.execute(
                "UPDATE productos SET referencia = ? WHERE id = ?", (n, producto_id)
            )
            return n

    def guardar_coste(self, producto_id: str, coste_cents: int) -> None:
        """Guarda el coste de adquisición del producto (opcional), en céntimos
        ENTEROS — nunca float. Columna propia `coste_cents`, NO dentro de
        `campos` (que lo sobreescriben `guardar_extraccion`/`confirmar_ficha`).

        `ValueError` si `coste_cents < 0`; `ProductoNoEncontradoError` si el
        producto no existe.
        """
        if coste_cents < 0:
            raise ValueError(f"coste_cents no puede ser negativo: {coste_cents}")
        with self._transaccion() as conn:
            fila = conn.execute(
                "SELECT id FROM productos WHERE id = ?", (producto_id,)
            ).fetchone()
            if fila is None:
                raise ProductoNoEncontradoError(f"producto no encontrado: {producto_id}")
            conn.execute(
                "UPDATE productos SET coste_cents = ? WHERE id = ?",
                (coste_cents, producto_id),
            )

    # -- finanzas: publicación ("Subido") --------------------------------

    def registrar_subido(
        self,
        producto_id: str,
        plataforma: str,
        precio_elegido_cents: int | None = None,
        tasacion: dict | None = None,
    ) -> None:
        """Registra que Diego subió el producto a una plataforma.

        Idempotente por `UNIQUE(producto_id, plataforma)`: el PRIMER "Subido"
        fija `subido_en=ahora` y deja una fila `movimientos(tipo='subido')`.
        Una segunda pulsada NO duplica la publicación ni cambia `subido_en`,
        pero SÍ refresca `precio_elegido_cents`/`tasacion_json` (el snapshot
        más reciente de la tasación al pulsar). `tasacion` se congela como
        JSON. `plataforma` debe ser 'wallapop' o 'vinted'.

        `ValueError` si la plataforma es desconocida;
        `ProductoNoEncontradoError` si el producto no existe.
        """
        if plataforma not in _PLATAFORMAS:
            raise ValueError(
                f"plataforma desconocida: {plataforma!r} (esperado {_PLATAFORMAS})"
            )
        tasacion_json = (
            json.dumps(tasacion, ensure_ascii=False) if tasacion is not None else None
        )
        with self._transaccion() as conn:
            fila = conn.execute(
                "SELECT id FROM productos WHERE id = ?", (producto_id,)
            ).fetchone()
            if fila is None:
                raise ProductoNoEncontradoError(f"producto no encontrado: {producto_id}")
            existente = conn.execute(
                "SELECT id FROM publicaciones WHERE producto_id = ? AND plataforma = ?",
                (producto_id, plataforma),
            ).fetchone()
            ahora = _ahora()
            if existente is None:
                conn.execute(
                    """INSERT INTO publicaciones
                       (id, producto_id, plataforma, subido_en,
                        precio_elegido_cents, tasacion_json)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        _nuevo_id(),
                        producto_id,
                        plataforma,
                        ahora,
                        precio_elegido_cents,
                        tasacion_json,
                    ),
                )
                conn.execute(
                    """INSERT INTO movimientos (producto_id, tipo, timestamp, detalle)
                       VALUES (?, 'subido', ?, ?)""",
                    (
                        producto_id,
                        ahora,
                        json.dumps(
                            {
                                "plataforma": plataforma,
                                "precio_elegido_cents": precio_elegido_cents,
                            },
                            ensure_ascii=False,
                        ),
                    ),
                )
            else:
                # Idempotente: no duplica ni re-fecha, pero refresca el snapshot
                # de precio/tasación (Diego re-pulsó Subido con una tasación más
                # reciente). No añade un segundo movimiento 'subido'.
                conn.execute(
                    """UPDATE publicaciones
                       SET precio_elegido_cents = ?, tasacion_json = ?
                       WHERE producto_id = ? AND plataforma = ?""",
                    (precio_elegido_cents, tasacion_json, producto_id, plataforma),
                )

    # -- finanzas: venta / undo / devolución -----------------------------

    def marcar_vendido(
        self,
        producto_id: str,
        precio_final_cents: int,
        plataforma_venta: str,
        lote_venta_id: str | None = None,
        fecha_venta: str | None = None,
    ) -> None:
        """Marca un producto como VENDIDO, congelando los snapshots del dinero.

        En el momento de vender congela, desde `productos`, lo que puede
        cambiar o desaparecer después: `referencia_snap`, `titulo_snap` (del
        JSON de `campos`, defensivo) y `coste_snap_cents`. Así el beneficio se
        calcula siempre sobre el coste que había AL VENDER, aunque Diego edite
        el coste luego o se borre el producto (por eso `ventas` no tiene FK).

        Idempotente (UPSERT por PK `producto_id`): re-marcar actualiza
        `precio_final_cents`/`plataforma_venta`/`estado='vendida'`/
        `actualizada_en`, pero NO re-congela coste/titulo/referencia — el
        PRIMER snapshot manda. Deja siempre una fila `movimientos(tipo='vendido')`.

        `ValueError` si `precio_final_cents < 0`; `ProductoNoEncontradoError`
        si el producto no existe (se vende un producto que existe; `ventas`
        sobrevive a un borrado POSTERIOR, no permite crear la venta sin él).
        """
        if precio_final_cents < 0:
            raise ValueError(
                f"precio_final_cents no puede ser negativo: {precio_final_cents}"
            )
        with self._transaccion() as conn:
            prod = conn.execute(
                "SELECT referencia, campos, coste_cents FROM productos WHERE id = ?",
                (producto_id,),
            ).fetchone()
            if prod is None:
                raise ProductoNoEncontradoError(f"producto no encontrado: {producto_id}")
            ahora = _ahora()
            existente = conn.execute(
                "SELECT producto_id FROM ventas WHERE producto_id = ?", (producto_id,)
            ).fetchone()
            if existente is None:
                conn.execute(
                    """INSERT INTO ventas
                       (producto_id, referencia_snap, titulo_snap, coste_snap_cents,
                        precio_final_cents, plataforma_venta, estado, lote_venta_id,
                        fecha_venta, creada_en, actualizada_en)
                       VALUES (?, ?, ?, ?, ?, ?, 'vendida', ?, ?, ?, ?)""",
                    (
                        producto_id,
                        prod["referencia"],
                        _titulo_de_campos(prod["campos"]),
                        prod["coste_cents"],
                        precio_final_cents,
                        plataforma_venta,
                        lote_venta_id,
                        fecha_venta or ahora,
                        ahora,
                        ahora,
                    ),
                )
            else:
                # Re-marca: el primer snapshot (coste/titulo/referencia/fecha)
                # manda; sólo se refresca lo que Diego puede corregir.
                conn.execute(
                    """UPDATE ventas
                       SET precio_final_cents = ?, plataforma_venta = ?,
                           estado = 'vendida', lote_venta_id = COALESCE(?, lote_venta_id),
                           actualizada_en = ?
                       WHERE producto_id = ?""",
                    (
                        precio_final_cents,
                        plataforma_venta,
                        lote_venta_id,
                        ahora,
                        producto_id,
                    ),
                )
            conn.execute(
                """INSERT INTO movimientos (producto_id, tipo, timestamp, detalle)
                   VALUES (?, 'vendido', ?, ?)""",
                (
                    producto_id,
                    ahora,
                    json.dumps(
                        {
                            "precio": precio_final_cents,
                            "plataforma": plataforma_venta,
                            "lote_venta_id": lote_venta_id,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )

    def deshacer_venta(self, producto_id: str) -> None:
        """Deshace una venta, DEJANDO RASTRO. Borra la fila de `ventas` pero
        antes añade `movimientos(tipo='undo_venta')` con el snapshot completo
        de lo que había — el historial vive en `movimientos` (append-only) y
        nunca se pierde, así que un undo es reversible a mano si hiciera falta.

        Decisión en el punto ambiguo del contrato: si el producto NO tenía
        venta → `ProductoNoEncontradoError` (ruidoso, no un no-op silencioso).
        Deshacer algo que no existe es un error de la UI que conviene ver, no
        tragar (`decision-making.md` §13).
        """
        with self._transaccion() as conn:
            venta = conn.execute(
                "SELECT * FROM ventas WHERE producto_id = ?", (producto_id,)
            ).fetchone()
            if venta is None:
                raise ProductoNoEncontradoError(
                    f"no hay venta que deshacer para el producto: {producto_id}"
                )
            conn.execute(
                """INSERT INTO movimientos (producto_id, tipo, timestamp, detalle)
                   VALUES (?, 'undo_venta', ?, ?)""",
                (producto_id, _ahora(), json.dumps(dict(venta), ensure_ascii=False)),
            )
            conn.execute("DELETE FROM ventas WHERE producto_id = ?", (producto_id,))

    def marcar_devuelta(self, producto_id: str) -> None:
        """Marca una venta como DEVUELTA. La fila de `ventas` se CONSERVA (una
        devolución con envío de vuelta puede ser pérdida neta; el dashboard la
        muestra como tal). Sólo cambia `estado='devuelta'` + `actualizada_en`
        y deja `movimientos(tipo='devuelta')`.

        `ProductoNoEncontradoError` si no hay venta que devolver.
        """
        with self._transaccion() as conn:
            venta = conn.execute(
                "SELECT producto_id FROM ventas WHERE producto_id = ?", (producto_id,)
            ).fetchone()
            if venta is None:
                raise ProductoNoEncontradoError(
                    f"no hay venta que devolver para el producto: {producto_id}"
                )
            ahora = _ahora()
            conn.execute(
                "UPDATE ventas SET estado = 'devuelta', actualizada_en = ? WHERE producto_id = ?",
                (ahora, producto_id),
            )
            conn.execute(
                """INSERT INTO movimientos (producto_id, tipo, timestamp, detalle)
                   VALUES (?, 'devuelta', ?, '{}')""",
                (producto_id, ahora),
            )

    def cargar_ventas(self) -> list[dict[str, Any]]:
        """El ledger de FINANZAS, CROSS-LOTE (abarca varios lotes; `cargar_lote`
        es por-lote y NO sirve aquí).

        Devuelve una fila por producto que tenga AL MENOS una `publicacion` o
        una `venta`, ordenada de más reciente a más antigua. Cada dict:
        `producto_id` (la clave durable, NUNCA la referencia), `lote_id`,
        `referencia`, `titulo` (del JSON de `campos`, defensivo), `coste_cents`,
        `publicaciones` (lista), `venta` (dict o None) y `beneficio_bruto_cents`
        (= `precio_final − coste_snap` si la venta está 'vendida', None en
        cualquier otro caso).

        Si el producto fue borrado pero su venta sobrevive (sin FK, por diseño),
        cae a los snapshots congelados en `ventas` (referencia/titulo/coste) y
        `lote_id=None` — el ledger no pierde la venta.
        """
        conn = self._conectar()
        try:
            ids: set[str] = set()
            for fila in conn.execute("SELECT DISTINCT producto_id FROM publicaciones"):
                ids.add(fila["producto_id"])
            for fila in conn.execute("SELECT producto_id FROM ventas"):
                ids.add(fila["producto_id"])

            resultado: list[dict[str, Any]] = []
            for pid in ids:
                prod = conn.execute(
                    "SELECT lote_id, referencia, campos, coste_cents FROM productos WHERE id = ?",
                    (pid,),
                ).fetchone()
                venta_row = conn.execute(
                    "SELECT * FROM ventas WHERE producto_id = ?", (pid,)
                ).fetchone()
                pubs = conn.execute(
                    """SELECT plataforma, subido_en, precio_elegido_cents, tasacion_json
                       FROM publicaciones WHERE producto_id = ? ORDER BY subido_en""",
                    (pid,),
                ).fetchall()

                if prod is not None:
                    lote_id = prod["lote_id"]
                    referencia = prod["referencia"]
                    titulo = _titulo_de_campos(prod["campos"])
                    coste_cents = prod["coste_cents"]
                else:
                    # Producto borrado: el ledger no pierde la venta, cae a los
                    # snapshots congelados al vender.
                    lote_id = None
                    referencia = venta_row["referencia_snap"] if venta_row else None
                    titulo = venta_row["titulo_snap"] if venta_row else None
                    coste_cents = venta_row["coste_snap_cents"] if venta_row else 0

                publicaciones = [
                    {
                        "plataforma": p["plataforma"],
                        "subido_en": p["subido_en"],
                        "precio_elegido_cents": p["precio_elegido_cents"],
                        "tasacion": (
                            json.loads(p["tasacion_json"]) if p["tasacion_json"] else None
                        ),
                    }
                    for p in pubs
                ]

                venta: dict[str, Any] | None = None
                beneficio: int | None = None
                if venta_row is not None:
                    venta = {
                        "precio_final_cents": venta_row["precio_final_cents"],
                        "plataforma_venta": venta_row["plataforma_venta"],
                        "estado": venta_row["estado"],
                        "lote_venta_id": venta_row["lote_venta_id"],
                        "fecha_venta": venta_row["fecha_venta"],
                    }
                    if (
                        venta_row["estado"] == "vendida"
                        and venta_row["precio_final_cents"] is not None
                    ):
                        beneficio = (
                            venta_row["precio_final_cents"] - venta_row["coste_snap_cents"]
                        )

                # Orden por lo más reciente: máximo timestamp ISO (UTC isoformat
                # ordena lexicográfica == cronológicamente) de cualquier evento.
                marcas = [p["subido_en"] for p in pubs]
                if venta_row is not None:
                    for clave in ("actualizada_en", "creada_en", "fecha_venta"):
                        if venta_row[clave]:
                            marcas.append(venta_row[clave])

                resultado.append(
                    {
                        "producto_id": pid,
                        "lote_id": lote_id,
                        "referencia": referencia,
                        "titulo": titulo,
                        "coste_cents": coste_cents,
                        "publicaciones": publicaciones,
                        "venta": venta,
                        "beneficio_bruto_cents": beneficio,
                        "_orden": max(marcas) if marcas else "",
                    }
                )

            resultado.sort(key=lambda r: r.pop("_orden"), reverse=True)
            return resultado
        finally:
            conn.close()

    # -- carga / reanudación --------------------------------------------

    def cargar_lote(self, lote_id: str) -> dict[str, Any]:
        """Reconstruye el estado completo del lote para reanudar el curado.

        Cerrar la app y volver a llamar a esto con el mismo `lote_id` debe
        dejar a Diego exactamente donde lo dejó: mismas fotos, mismos
        grupos, mismas confirmaciones.
        """
        conn = self._conectar()
        try:
            lote_fila = conn.execute("SELECT * FROM lotes WHERE id = ?", (lote_id,)).fetchone()
            if lote_fila is None:
                raise LoteNoEncontradoError(f"lote no encontrado: {lote_id}")

            fotos = [
                dict(fila)
                for fila in conn.execute(
                    "SELECT * FROM fotos WHERE lote_id = ? ORDER BY timestamp_exif, creada_en",
                    (lote_id,),
                ).fetchall()
            ]

            productos_filas = conn.execute(
                "SELECT * FROM productos WHERE lote_id = ? ORDER BY creado_en",
                (lote_id,),
            ).fetchall()
            productos = []
            for fila in productos_filas:
                fotos_del_producto = [f["id"] for f in fotos if f["producto_id"] == fila["id"]]
                productos.append(
                    {
                        "id": fila["id"],
                        "lote_id": fila["lote_id"],
                        "campos": json.loads(fila["campos"]),
                        "confirmado": bool(fila["confirmado"]),
                        "confirmado_en": fila["confirmado_en"],
                        "creado_en": fila["creado_en"],
                        "fotos": fotos_del_producto,
                    }
                )

            confirmaciones = [
                dict(fila)
                for fila in conn.execute(
                    "SELECT * FROM confirmaciones WHERE lote_id = ? ORDER BY timestamp",
                    (lote_id,),
                ).fetchall()
            ]

            return {
                "lote": dict(lote_fila),
                "fotos": fotos,
                "productos": productos,
                "confirmaciones": confirmaciones,
            }
        finally:
            conn.close()
