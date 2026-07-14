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
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# --------------------------------------------------------------------------
# Ubicación por defecto de los datos: <raíz del repo>/data/
# --------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = _REPO_ROOT / "data"
DB_FILENAME = "resellermaster.db"

SCHEMA_VERSION = 1

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
}


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


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nuevo_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class Foto:
    ruta: str
    hash: str
    timestamp_exif: str | None = None


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
        conn = self._conectar()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
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
                            confirmada, creada_en)
                           VALUES (?, ?, ?, ?, ?, NULL, 0, ?)""",
                        (foto_id, lote_id, str(foto.ruta), foto.hash, foto.timestamp_exif, _ahora()),
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
                    "SELECT id, producto_id FROM fotos WHERE lote_id = ?", (lote_id,)
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
