"""core/llm.py — COSTURA 1: unico punto de llamada a un proveedor LLM/vision.

`.claude/rules/architecture.md` (Costura 1) + `.claude/rules/truth-loop.md` SS A.
Nadie mas en el repo importa `anthropic` (ni ningun otro SDK de proveedor):
toda llamada pasa por `LLMEngine`, aqui. Eso hace del proveedor una decision
reversible (config, no codigo esparcido) y permite contar coste y cachear en
UN sitio.

Que produce este modulo
------------------------
`LLMEngine.consultar(...)` manda una o varias imagenes + un prompt + un
json_schema (structured outputs) y devuelve un `ResultadoLLM` con el dict ya
parseado. **Este modulo NO decide que campos existen** (eso es
`core/schema.py`, `Campo`/`Evidencia`) ni **valida procedencia** (eso lo hace
quien construya el `Campo` a partir de `ResultadoLLM.datos`, tipicamente
`core/extract.py`). La responsabilidad de este fichero es estrictamente:
llamar al proveedor, cachear, contar coste, fallar ruidoso.

DATO CRITICO DE DISENO — recortes, no fotos enteras
-----------------------------------------------------
Haiku 4.5 (y el resto de la familia Claude) reescala toda imagen enviada a un
lado largo de `LADO_LARGO_RESCALADO_PX` px antes de "verla". Las fotos de
Diego salen de movil a 3072x4080: mandar la foto ENTERA tira el dinero,
porque el modelo nunca ve mas detalle que el que cabe en esos ~1568px de
lado largo, y una etiqueta o un texto pequeno se vuelve ilegible a esa
resolucion aunque en la foto original se lea perfectamente. Este motor
esta pensado para recibir RECORTES pequenos (la etiqueta, la talla, el
metro) A RESOLUCION NATIVA — quien construya `Imagen` (probablemente
`core/extract.py`, usando el recorte que ya localizo el OCR local) es quien
decide el recorte; este modulo no recorta nada por su cuenta.

Coste de una imagen a resolucion maxima: ~1600 tokens de entrada
(`TOKENS_ENTRADA_ESTIMADOS_POR_IMAGEN`) ~= 0.16 centimos con Haiku 4.5. Con
cache por hash, ese coste se paga UNA sola vez por imagen+prompt+modelo.

Nota de divisa: los precios oficiales de Anthropic estan en USD por millon
de tokens (`PRECIOS_USD_POR_MTOK`). Este modulo NO aplica ningun tipo de
cambio USD->EUR (inventar uno seria exactamente el tipo de dato no
verificado que este proyecto existe para evitar — `product.md` HUECOS). El
coste que se reporta es un numero en USD; `CLAUDE.md`/`architecture.md` ya
citan "0.2 cts/foto" usando ese mismo numero sin conversion, asi que se
mantiene la misma convencion. Si algun dia hace falta EUR exacto, se aplica
el tipo de cambio del dia en la capa de facturacion real, no aqui.

CACHE POR HASH — obligatoria, y NUNCA se borra sin permiso
-------------------------------------------------------------
Clave = sha256(bytes de cada imagen, en orden, + modelo + version_prompt +
EL TEXTO DEL PROMPT). El texto del prompt entra en el hash (no solo su
`version_prompt`) porque los prompts son donde vive media defensa
anti-alucinacion: endurecer un prompt y olvidarse de subir
`VERSION_PROMPT_*` no puede responder con el prompt VIEJO en silencio — la
clave se deriva de la entrada real, no de que alguien se acuerde de subir
un numero.
Se guarda en `cache_dir` (por defecto `data/cache/`) como un JSON por
llamada. Un hit de cache NO llama a la API y cuesta 0. Cada entrada de esta
cache es dinero YA GASTADO (`decision-making.md` SS 15): **nunca** se borra
sin permiso explicito de Diego, ni este modulo ni ningun otro codigo del
repo debe hacerlo. Un fichero de cache corrupto (JSON invalido, disco a
medias) se trata como un miss — se recalcula, y el fichero corrupto se deja
intacto para que Diego pueda inspeccionarlo si le extrana el gasto extra
(nunca se sobreescribe en silencio sin loguear).

ERRORES — ruidosos, jamas fallback silencioso
------------------------------------------------
Si la API falla (limite de tasa, error de conexion, error de servidor,
clave invalida) se loguea el traceback completo y se propaga como
`LLMLlamadaFallidaError`. Este modulo JAMAS devuelve un dict de atributos
inventado cuando la llamada real fallo: quien llama debe capturar el error
y marcar el campo como fallido (`fuente=None`/`confianza=baja` en
`core/extract.py`), nunca rellenar un valor plausible en su lugar.

Sin `ANTHROPIC_API_KEY` configurada (ni pasada explicitamente), la PRIMERA
vez que hace falta llamar de verdad a la API se lanza
`ApiKeyFaltanteError` con un mensaje accionable. Un hit de cache NUNCA
necesita la clave — por eso la construccion del cliente es perezosa (lazy):
un lote entero ya cacheado corre sin que Diego tenga que configurar nada.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import anthropic

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Precios VERIFICADOS (USD por millon de tokens). No inventar otros modelos
# ni otros precios: son los que Diego confirmo contra la referencia oficial.
# ---------------------------------------------------------------------------
PRECIOS_USD_POR_MTOK: dict[str, tuple[float, float]] = {
    # modelo: (precio_entrada, precio_salida)
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-4-8": (5.00, 25.00),
}

MODELO_DEFECTO = "claude-haiku-4-5"

# Anthropic reescala toda imagen enviada a este lado largo antes de "verla"
# (ver docstring del modulo). Puramente informativo aqui; ningun codigo de
# este fichero redimensiona nada.
LADO_LARGO_RESCALADO_PX = 1568

# Estimaciones usadas SOLO por `estimar_coste_lote` (antes de gastar un
# euro). Medido en Fase 1 (`CLAUDE.md`): una imagen a resolucion maxima con
# Haiku ronda 1600 tokens de entrada. La salida de un JSON de atributos de
# ficha es tipicamente corta; 300 tokens es un techo conservador (mas caro
# que el caso tipico, nunca subestima el gasto real).
TOKENS_ENTRADA_ESTIMADOS_POR_IMAGEN = 1600
TOKENS_SALIDA_ESTIMADOS_DEFECTO = 300

VERSION_PROMPT_DEFECTO = "v1"

# Aviso (no bloqueante) si una imagen individual pesa mas que esto: es la
# senal barata de "esto probablemente es la foto entera, no un recorte de
# la etiqueta" (ver DATO CRITICO DE DISENO arriba). Solo loguea; el llamador
# decide si hace caso.
UMBRAL_BYTES_AVISO_FOTO_COMPLETA = 400_000


# ---------------------------------------------------------------------------
# Errores propios — ruidosos, tipados, nunca `except Exception: pass`.
# ---------------------------------------------------------------------------
class LLMEngineError(Exception):
    """Base de los errores propios de `core/llm.py`."""


class ApiKeyFaltanteError(LLMEngineError):
    """No hay `ANTHROPIC_API_KEY` disponible para llamar de verdad al proveedor.

    Mensaje accionable a proposito: Diego debe poder leerlo y saber
    exactamente que hacer, no un traceback criptico del SDK.
    """


class LLMLlamadaFallidaError(LLMEngineError):
    """La llamada al proveedor fallo (tras agotar los reintentos del SDK) o
    la respuesta no se pudo interpretar como el JSON esperado.

    Se propaga siempre: quien llama (`core/extract.py`) debe capturarla y
    marcar el campo como fallido, nunca tragarsela y rellenar un valor
    plausible en su lugar (`decision-making.md` SS 13).
    """


# ---------------------------------------------------------------------------
# Estructuras de datos publicas
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Imagen:
    """Una imagen lista para enviar al modelo.

    `fichero` es solo para logging/evidencia (que foto es, para poder
    trazar despues de donde salio un dato) — no se usa para nada mas aqui.
    Quien construye esto decide si son bytes de una foto entera o (lo
    recomendado, ver docstring del modulo) un recorte de una region
    concreta.
    """

    bytes_: bytes
    fichero: str
    media_type: str = "image/jpeg"

    def __post_init__(self) -> None:
        if not self.bytes_:
            raise ValueError(f"Imagen sin bytes: {self.fichero!r}")
        if not self.fichero or not self.fichero.strip():
            raise ValueError("Imagen.fichero no puede estar vacio")


@dataclass(frozen=True)
class ResultadoLLM:
    """Salida de una llamada (o de un hit de cache) a `LLMEngine.consultar`.

    `datos` es siempre un dict valido contra el `json_schema` pedido — si
    la llamada fallo, no existe un `ResultadoLLM`: se lanzo
    `LLMLlamadaFallidaError` en su lugar (ver docstring del modulo).
    """

    datos: dict[str, Any]
    fuente: str  # "api" | "cache"
    coste_usd: float
    tokens_entrada: int
    tokens_salida: int


@dataclass
class ContadorCoste:
    """Acumulador de coste y tokens. Una instancia para el lote entero, y
    una por `producto_id` si se etiquetan las llamadas (ver
    `LLMEngine.costes_por_producto`)."""

    llamadas: int = 0
    tokens_entrada: int = 0
    tokens_salida: int = 0
    tokens_cache_leidos: int = 0
    coste_usd: float = 0.0

    def registrar(
        self,
        tokens_entrada: int,
        tokens_salida: int,
        tokens_cache_leidos: int,
        coste_usd: float,
    ) -> None:
        self.llamadas += 1
        self.tokens_entrada += tokens_entrada
        self.tokens_salida += tokens_salida
        self.tokens_cache_leidos += tokens_cache_leidos
        self.coste_usd += coste_usd


@dataclass(frozen=True)
class EstimacionLlamada:
    """Estimacion de UNA llamada dentro de `estimar_coste_lote`."""

    ficheros: tuple[str, ...]
    en_cache: bool
    tokens_entrada_estimados: int
    tokens_salida_estimados: int
    coste_usd_estimado: float


@dataclass(frozen=True)
class EstimacionLote:
    """Estimacion de coste de un lote ANTES de lanzar nada
    (`decision-making.md` SS 15: procesar sin estimar antes esta prohibido).

    `coste_usd_estimado` cuenta solo las llamadas que NO estan ya en cache
    (esas cuestan 0 — dinero ya gastado, ver docstring del modulo)."""

    llamadas: tuple[EstimacionLlamada, ...]
    n_llamadas_total: int
    n_en_cache: int
    n_a_pagar: int
    coste_usd_estimado: float


def _extraer_texto(respuesta: anthropic.types.Message) -> str:
    """Concatena los bloques de tipo 'text' de la respuesta. Con
    `output_config.format = json_schema` el modelo devuelve el JSON como
    texto en ese bloque (structured outputs no cambia el tipo de bloque,
    solo restringe su contenido)."""
    partes = [
        bloque.text for bloque in respuesta.content if getattr(bloque, "type", None) == "text"
    ]
    if not partes:
        raise LLMLlamadaFallidaError(
            f"la respuesta del modelo no contiene ningun bloque de texto: {respuesta.content!r}"
        )
    return "".join(partes)


class LLMEngine:
    """COSTURA 1: unico punto de llamada a un proveedor LLM/vision.

    Uso tipico (desde `core/extract.py`, que aun no existe):

        motor = LLMEngine()
        estimacion = motor.estimar_coste_lote(solicitudes)
        # ... mostrar estimacion.coste_usd_estimado a Diego, esperar su OK ...
        resultado = motor.consultar(imagenes, prompt, json_schema, producto_id=pid)
        # resultado.datos ya es un dict validado contra json_schema

    El cliente del SDK se crea de forma PEREZOSA (solo al primer intento
    real de llamar a la API) para que un lote enteramente cacheado corra
    sin que haga falta ninguna clave configurada.
    """

    def __init__(
        self,
        modelo: str = MODELO_DEFECTO,
        cache_dir: Path | str = Path("data/cache"),
        api_key: str | None = None,
        max_tokens: int = 1024,
    ) -> None:
        if modelo not in PRECIOS_USD_POR_MTOK:
            raise ValueError(
                f"modelo desconocido: {modelo!r}; validos: {sorted(PRECIOS_USD_POR_MTOK)}"
            )
        self.modelo = modelo
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_tokens = max_tokens
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._cliente: anthropic.Anthropic | None = None

        self.coste_lote = ContadorCoste()
        self._coste_por_producto: dict[str, ContadorCoste] = {}

    # -- cliente perezoso ---------------------------------------------------

    def _obtener_cliente(self) -> anthropic.Anthropic:
        if self._cliente is not None:
            return self._cliente
        if not self._api_key:
            raise ApiKeyFaltanteError(
                "Falta ANTHROPIC_API_KEY: no se puede llamar al proveedor LLM. "
                "Copia .env.example a .env y rellena la clave de la Console de "
                "Anthropic (console.anthropic.com) — la suscripcion Pro/Max de "
                "Claude NO vale para esto, Anthropic prohibe enrutar apps de "
                "terceros por credenciales de plan de consumidor."
            )
        self._cliente = anthropic.Anthropic(api_key=self._api_key)
        return self._cliente

    # -- cache por hash -------------------------------------------------------

    def _clave_cache(self, imagenes: Sequence[Imagen], prompt: str, version_prompt: str) -> str:
        """C6: la clave deriva de la ENTRADA REAL -- bytes de cada imagen +
        modelo + version_prompt + EL TEXTO DEL PROMPT. Antes NO incluia el
        texto: dos llamadas con bytes+version_prompt identicos pero prompt
        DISTINTO colisionaban en la misma clave, asi que endurecer un
        prompt (donde vive media defensa anti-alucinacion) y olvidarse de
        subir `VERSION_PROMPT_*` respondia con el prompt VIEJO en silencio
        -- y encima parecia que el nuevo prompt ya estaba validado. La
        clave no puede depender de que alguien se acuerde de subir un
        numero."""
        hasher = hashlib.sha256()
        for imagen in imagenes:
            hasher.update(imagen.bytes_)
        hasher.update(self.modelo.encode("utf-8"))
        hasher.update(version_prompt.encode("utf-8"))
        hasher.update(prompt.encode("utf-8"))
        return hasher.hexdigest()

    def _ruta_cache(self, clave: str) -> Path:
        return self.cache_dir / f"{clave}.json"

    def _leer_cache(self, clave: str) -> dict[str, Any] | None:
        ruta = self._ruta_cache(clave)
        if not ruta.exists():
            return None
        try:
            with ruta.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            # Cache corrupta (disco a medias, fichero truncado). Se trata
            # como un miss -> se recalcula. El fichero NO se borra ni se
            # sobreescribe aqui: es dinero ya gastado, y borrarlo en
            # silencio violaria la regla de la cache (docstring del modulo).
            logger.exception(
                "Fichero de cache corrupto en %s; se recalcula, el fichero se deja intacto", ruta
            )
            return None

    def _guardar_cache(
        self,
        clave: str,
        datos: dict[str, Any],
        tokens_entrada: int,
        tokens_salida: int,
        imagenes: Sequence[Imagen],
        prompt: str,
        version_prompt: str,
    ) -> None:
        ruta = self._ruta_cache(clave)
        contenido = {
            "datos": datos,
            "modelo": self.modelo,
            "version_prompt": version_prompt,
            # C6: hash del texto del prompt (no el texto completo, para no
            # inflar el fichero de cache) -- trazabilidad: si Diego ve un
            # gasto que no esperaba, puede confirmar CON QUE prompt exacto
            # se pago esta entrada.
            "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
            "tokens_entrada": tokens_entrada,
            "tokens_salida": tokens_salida,
            "ficheros": [imagen.fichero for imagen in imagenes],
            "creado_en": datetime.now(timezone.utc).isoformat(),
        }
        # Escritura atomica (fichero temporal + replace), mismo patron que
        # `core/images.py::obtener_o_crear_miniatura`: un rerun de
        # Streamlit que lea a mitad de una escritura no debe encontrar un
        # JSON a medio escribir.
        ruta_tmp = ruta.with_name(ruta.name + ".tmp")
        with ruta_tmp.open("w", encoding="utf-8") as f:
            json.dump(contenido, f, ensure_ascii=False, indent=2)
        ruta_tmp.replace(ruta)

    # -- coste ----------------------------------------------------------------

    def _calcular_coste(self, tokens_entrada: int, tokens_salida: int) -> float:
        precio_entrada, precio_salida = PRECIOS_USD_POR_MTOK[self.modelo]
        return (tokens_entrada / 1_000_000) * precio_entrada + (
            tokens_salida / 1_000_000
        ) * precio_salida

    def costes_por_producto(self) -> dict[str, ContadorCoste]:
        """Copia de solo lectura del gasto acumulado por `producto_id`
        (los que se han etiquetado en `consultar(..., producto_id=...)`)."""
        return dict(self._coste_por_producto)

    # -- estimacion ANTES de gastar (decision-making.md SS 15) -----------------

    def estimar_coste_lote(
        self,
        solicitudes: Sequence[tuple[Sequence[Imagen], str, str]],
        tokens_salida_estimados: int = TOKENS_SALIDA_ESTIMADOS_DEFECTO,
    ) -> EstimacionLote:
        """Estima el coste de un lote de llamadas ANTES de lanzar ninguna.

        `solicitudes` es una lista de `(imagenes, prompt, version_prompt)`,
        una entrada por cada llamada que se planea hacer con `consultar`
        (mismo orden que sus argumentos posicionales). Las que ya estan en
        cache (mismo hash de imagenes+modelo+version_prompt+prompt, C6)
        cuentan coste 0 — son dinero ya gastado. No llama a la API ni
        necesita `ANTHROPIC_API_KEY`: solo mira el disco de cache.
        """
        llamadas: list[EstimacionLlamada] = []
        for imagenes, prompt, version_prompt in solicitudes:
            if not imagenes:
                raise ValueError("una solicitud de estimacion no puede tener cero imagenes")
            clave = self._clave_cache(imagenes, prompt, version_prompt)
            en_cache = self._ruta_cache(clave).exists()
            tokens_entrada_est = (
                0 if en_cache else len(imagenes) * TOKENS_ENTRADA_ESTIMADOS_POR_IMAGEN
            )
            tokens_salida_est = 0 if en_cache else tokens_salida_estimados
            coste_est = 0.0 if en_cache else self._calcular_coste(tokens_entrada_est, tokens_salida_est)
            llamadas.append(
                EstimacionLlamada(
                    ficheros=tuple(imagen.fichero for imagen in imagenes),
                    en_cache=en_cache,
                    tokens_entrada_estimados=tokens_entrada_est,
                    tokens_salida_estimados=tokens_salida_est,
                    coste_usd_estimado=coste_est,
                )
            )
        n_en_cache = sum(1 for llamada in llamadas if llamada.en_cache)
        return EstimacionLote(
            llamadas=tuple(llamadas),
            n_llamadas_total=len(llamadas),
            n_en_cache=n_en_cache,
            n_a_pagar=len(llamadas) - n_en_cache,
            coste_usd_estimado=sum(llamada.coste_usd_estimado for llamada in llamadas),
        )

    # -- la llamada real --------------------------------------------------------

    def consultar(
        self,
        imagenes: Sequence[Imagen],
        prompt: str,
        json_schema: dict[str, Any],
        version_prompt: str = VERSION_PROMPT_DEFECTO,
        producto_id: str | None = None,
    ) -> ResultadoLLM:
        """Manda `imagenes` + `prompt` al modelo, forzando la respuesta a
        cumplir `json_schema` (structured outputs), y devuelve el dict ya
        parseado.

        Si la misma combinacion (bytes de las imagenes + modelo +
        `version_prompt`) ya esta en cache, se devuelve sin llamar a la API
        (`ResultadoLLM.fuente == "cache"`, coste 0).

        Nunca devuelve un resultado con datos inventados: si la llamada
        falla o la respuesta no es JSON valido, se lanza
        `LLMLlamadaFallidaError` (ver docstring del modulo). Si falta la
        clave de API y hace falta llamar de verdad, se lanza
        `ApiKeyFaltanteError`.

        `imagenes` vacio esta PROHIBIDO aqui a proposito (invariante viejo,
        cubierto por `test_estimacion_rechaza_solicitud_sin_imagenes` y
        equivalentes): quien quiera una llamada de puro TEXTO, SIN ninguna
        imagen, debe usar `consultar_texto` — un metodo DISTINTO, sin
        parametro `imagenes` en su firma, para que sea estructuralmente
        imposible colar una foto en una llamada que se disenó para no
        verla (ver `core/extract.py::redactar_desde_campos_confirmados`,
        la garantia anti-marca-ajena de la redaccion de titulo/descripcion:
        `truth-loop.md`/`product.md` SS7, `MENTIONS_OTHER_BRAND` oculta el
        anuncio en Vinted).
        """
        if not imagenes:
            raise ValueError(
                "consultar() requiere al menos una imagen -- usa consultar_texto() "
                "para una llamada de puro texto, sin imagenes"
            )
        return self._consultar_interno(imagenes, prompt, json_schema, version_prompt, producto_id)

    def consultar_texto(
        self,
        prompt: str,
        json_schema: dict[str, Any],
        version_prompt: str = VERSION_PROMPT_DEFECTO,
        producto_id: str | None = None,
    ) -> ResultadoLLM:
        """Variante de `consultar` SIN imagenes -- para llamadas de puro
        TEXTO (hoy, una sola: `core/extract.py::redactar_desde_campos_confirmados`,
        que redacta titulo/descripcion a partir de los campos YA
        CONFIRMADOS por Diego, nunca de una foto).

        La garantia no es una convencion de llamada ("pasa `imagenes=[]`"):
        es que este metodo NO TIENE parametro `imagenes` en absoluto. Quien
        quiera colar una foto aqui no puede — no hay donde ponerla. Mismo
        cache, mismo conteo de coste, mismos errores ruidosos que
        `consultar`.
        """
        return self._consultar_interno((), prompt, json_schema, version_prompt, producto_id)

    def _consultar_interno(
        self,
        imagenes: Sequence[Imagen],
        prompt: str,
        json_schema: dict[str, Any],
        version_prompt: str,
        producto_id: str | None,
    ) -> ResultadoLLM:
        """Cuerpo real compartido por `consultar` (>=1 imagen, exigido por
        su guarda) y `consultar_texto` (0 imagenes, exigido por su firma).
        Nadie mas debe llamar a esto directamente."""
        for imagen in imagenes:
            if len(imagen.bytes_) > UMBRAL_BYTES_AVISO_FOTO_COMPLETA:
                logger.warning(
                    "Imagen %s pesa %d bytes (> %d): probablemente es la foto "
                    "ENTERA, no un recorte — Haiku la reescala a %dpx de lado "
                    "largo igualmente, mandar la foto completa tira el dinero "
                    "(ver docstring de core/llm.py)",
                    imagen.fichero,
                    len(imagen.bytes_),
                    UMBRAL_BYTES_AVISO_FOTO_COMPLETA,
                    LADO_LARGO_RESCALADO_PX,
                )

        clave = self._clave_cache(imagenes, prompt, version_prompt)
        cacheado = self._leer_cache(clave)
        if cacheado is not None:
            logger.info(
                "cache HIT (%s): %s", clave[:12], [imagen.fichero for imagen in imagenes]
            )
            return ResultadoLLM(
                datos=cacheado["datos"],
                fuente="cache",
                coste_usd=0.0,
                tokens_entrada=cacheado.get("tokens_entrada", 0),
                tokens_salida=cacheado.get("tokens_salida", 0),
            )

        cliente = self._obtener_cliente()

        contenido: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": imagen.media_type,
                    "data": base64.b64encode(imagen.bytes_).decode("ascii"),
                },
            }
            for imagen in imagenes
        ]
        contenido.append({"type": "text", "text": prompt})

        try:
            respuesta = cliente.messages.create(
                model=self.modelo,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": contenido}],
                output_config={"format": {"type": "json_schema", "schema": json_schema}},
            )
        except anthropic.RateLimitError as exc:
            logger.exception("RateLimitError llamando a %s", self.modelo)
            raise LLMLlamadaFallidaError(f"limite de tasa excedido: {exc}") from exc
        except anthropic.AuthenticationError as exc:
            logger.exception("AuthenticationError llamando a %s", self.modelo)
            raise LLMLlamadaFallidaError(f"clave de API invalida o sin permisos: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            logger.exception("APIConnectionError llamando a %s", self.modelo)
            raise LLMLlamadaFallidaError(f"fallo de conexion con la API: {exc}") from exc
        except anthropic.APIStatusError as exc:
            logger.exception("APIStatusError (%s) llamando a %s", exc.status_code, self.modelo)
            raise LLMLlamadaFallidaError(
                f"error de la API (status {exc.status_code}): {exc}"
            ) from exc

        texto = _extraer_texto(respuesta)
        try:
            datos = json.loads(texto)
        except json.JSONDecodeError as exc:
            logger.exception(
                "La respuesta no es JSON valido pese a output_config.format=json_schema: %r",
                texto,
            )
            raise LLMLlamadaFallidaError(
                f"la respuesta del modelo no es JSON valido: {exc}"
            ) from exc

        tokens_entrada = respuesta.usage.input_tokens
        tokens_salida = respuesta.usage.output_tokens
        tokens_cache_leidos = respuesta.usage.cache_read_input_tokens or 0
        coste = self._calcular_coste(tokens_entrada, tokens_salida)

        self._guardar_cache(clave, datos, tokens_entrada, tokens_salida, imagenes, prompt, version_prompt)

        self.coste_lote.registrar(tokens_entrada, tokens_salida, tokens_cache_leidos, coste)
        if producto_id is not None:
            contador_producto = self._coste_por_producto.setdefault(
                producto_id, ContadorCoste()
            )
            contador_producto.registrar(tokens_entrada, tokens_salida, tokens_cache_leidos, coste)

        return ResultadoLLM(
            datos=datos,
            fuente="api",
            coste_usd=coste,
            tokens_entrada=tokens_entrada,
            tokens_salida=tokens_salida,
        )
