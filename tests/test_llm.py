"""Tests de core/llm.py — COSTURA 1 (unico punto de llamada a un proveedor LLM).

TODOS corren SIN `ANTHROPIC_API_KEY`: los que necesitarian llamar de verdad
a la API mockean `anthropic.Anthropic` (nunca se toca la red). Cubre:

  - hit de cache: no llama a la API, coste 0.
  - estimacion de coste ANTES de gastar, contando lo que ya esta en cache.
  - error de la API propagado como `LLMLlamadaFallidaError`, nunca tragado.
  - ausencia de clave -> `ApiKeyFaltanteError` con mensaje accionable.
"""

from __future__ import annotations

import json

import httpx
import pytest

import anthropic
import anthropic.types as at

from core.llm import (
    ApiKeyFaltanteError,
    Imagen,
    LLMEngine,
    LLMLlamadaFallidaError,
    PRECIOS_USD_POR_MTOK,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _imagen(nombre: str = "IMG_0001.jpg", contenido: bytes | None = None) -> Imagen:
    # Por defecto, bytes DISTINTOS por nombre de fichero (la clave de cache
    # depende de los bytes, no del nombre — dos "fotos" con el mismo
    # contenido literal deben, correctamente, compartir cache).
    return Imagen(bytes_=contenido or f"fake-jpeg-bytes-{nombre}".encode(), fichero=nombre)


def _mensaje_fake(datos: dict, tokens_entrada: int = 1500, tokens_salida: int = 40) -> at.Message:
    return at.Message(
        id="msg_test",
        content=[{"type": "text", "text": json.dumps(datos)}],
        model="claude-haiku-4-5",
        role="assistant",
        stop_reason="end_turn",
        stop_sequence=None,
        type="message",
        usage=at.Usage(input_tokens=tokens_entrada, output_tokens=tokens_salida),
    )


class _ClienteFake:
    """Sustituye a `anthropic.Anthropic`: `messages.create` hace lo que le
    digamos (devolver un mensaje fake o lanzar un error tipado), sin tocar
    la red en ningun caso."""

    def __init__(self, respuesta=None, error: Exception | None = None):
        self._respuesta = respuesta
        self._error = error
        self.llamadas: list[dict] = []
        self.messages = self  # para que `cliente.messages.create(...)` funcione

    def create(self, **kwargs):
        self.llamadas.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._respuesta


def _peticion_httpx() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


# ---------------------------------------------------------------------------
# Hit de cache: nunca llama a la API, coste 0.
# ---------------------------------------------------------------------------
def test_hit_de_cache_no_llama_a_la_api_y_coste_cero(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    imagenes = [_imagen()]
    schema = {"type": "object", "properties": {"marca": {"type": "string"}}}

    # Si el motor intentase llamar a la API de verdad (porque el cache-hit
    # fallase), esto reventaria: no hay clave configurada y el cliente no
    # esta parcheado. Precalentamos la cache llamando una vez con un
    # cliente fake, y comprobamos que la SEGUNDA llamada (idéntica) no
    # vuelve a tocar ese cliente en absoluto.
    cliente_fake = _ClienteFake(respuesta=_mensaje_fake({"marca": "Nike"}))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: cliente_fake)
    motor_con_key = LLMEngine(cache_dir=tmp_path / "cache", api_key="sk-ant-fake")
    primero = motor_con_key.consultar(imagenes, "extrae la marca", schema)
    assert primero.fuente == "api"
    assert cliente_fake.llamadas  # sí llamó, es el warm-up

    # Ahora un motor SIN clave y SIN cliente parcheable: si intentase llamar
    # a la API reventaría con ApiKeyFaltanteError. Como el hash de imagen +
    # modelo + version_prompt es idéntico, debe ser un HIT.
    motor_sin_key = LLMEngine(cache_dir=tmp_path / "cache")
    segundo = motor_sin_key.consultar(imagenes, "extrae la marca", schema)

    assert segundo.fuente == "cache"
    assert segundo.coste_usd == 0.0
    assert segundo.datos == {"marca": "Nike"}
    assert motor_sin_key.coste_lote.coste_usd == 0.0
    assert motor_sin_key.coste_lote.llamadas == 0


def test_cache_es_sensible_a_la_imagen_al_modelo_a_la_version_y_al_TEXTO_del_prompt(tmp_path, monkeypatch):
    """C6: antes la clave de cache solo hasheaba `version_prompt`, no el
    texto del prompt -- esto REPRODUCE ese bug (misma imagen, mismo
    version_prompt, texto DISTINTO -> antes era HIT) y demuestra que ahora
    es MISS: los prompts son donde vive media defensa anti-alucinacion, y
    endurecer uno sin subir `VERSION_PROMPT_*` no puede responder con el
    prompt VIEJO en silencio."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cache_dir = tmp_path / "cache"
    cliente_fake = _ClienteFake(respuesta=_mensaje_fake({"marca": "Nike"}))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: cliente_fake)
    motor = LLMEngine(cache_dir=cache_dir, api_key="sk-ant-fake")
    schema = {"type": "object", "properties": {"marca": {"type": "string"}}}

    motor.consultar([_imagen()], "prompt", schema, version_prompt="v1")
    assert len(cliente_fake.llamadas) == 1

    # Misma imagen, MISMO texto de prompt, misma version -> HIT, no llama de nuevo.
    motor.consultar([_imagen()], "prompt", schema, version_prompt="v1")
    assert len(cliente_fake.llamadas) == 1

    # C6: mismo version_prompt, pero el TEXTO del prompt es distinto -> MISS.
    motor.consultar([_imagen()], "prompt distinto en texto", schema, version_prompt="v1")
    assert len(cliente_fake.llamadas) == 2

    # Version de prompt distinta -> MISS, sí llama.
    motor.consultar([_imagen()], "prompt", schema, version_prompt="v2")
    assert len(cliente_fake.llamadas) == 3

    # Imagen distinta -> MISS, sí llama.
    motor.consultar([_imagen(contenido=b"otros-bytes")], "prompt", schema, version_prompt="v1")
    assert len(cliente_fake.llamadas) == 4


# ---------------------------------------------------------------------------
# Estimación de coste ANTES de gastar.
# ---------------------------------------------------------------------------
def test_estimacion_de_coste_cuenta_cache_como_cero_y_calcula_bien_el_resto(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cache_dir = tmp_path / "cache"
    schema = {"type": "object", "properties": {}}

    # Precalentamos la cache de UNA de las tres solicitudes.
    cliente_fake = _ClienteFake(respuesta=_mensaje_fake({"marca": "Nike"}))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: cliente_fake)
    motor_warmup = LLMEngine(cache_dir=cache_dir, api_key="sk-ant-fake")
    imagen_cacheada = _imagen("IMG_cacheada.jpg")
    motor_warmup.consultar([imagen_cacheada], "prompt", schema, version_prompt="v1")

    # La estimación NO necesita clave: solo mira el disco de cache.
    motor = LLMEngine(cache_dir=cache_dir)
    solicitudes = [
        ([imagen_cacheada], "prompt", "v1"),  # ya en cache -> coste 0
        ([_imagen("IMG_nueva_1.jpg")], "prompt", "v1"),  # a pagar
        ([_imagen("IMG_nueva_2a.jpg"), _imagen("IMG_nueva_2b.jpg")], "prompt", "v1"),  # 2 imágenes a pagar
    ]
    estimacion = motor.estimar_coste_lote(solicitudes)

    assert estimacion.n_llamadas_total == 3
    assert estimacion.n_en_cache == 1
    assert estimacion.n_a_pagar == 2

    precio_entrada, precio_salida = PRECIOS_USD_POR_MTOK["claude-haiku-4-5"]
    tokens_entrada_1 = 1 * 1600
    tokens_entrada_2 = 2 * 1600
    tokens_salida = 300  # TOKENS_SALIDA_ESTIMADOS_DEFECTO
    coste_esperado = (
        (tokens_entrada_1 / 1_000_000) * precio_entrada
        + (tokens_salida / 1_000_000) * precio_salida
        + (tokens_entrada_2 / 1_000_000) * precio_entrada
        + (tokens_salida / 1_000_000) * precio_salida
    )
    assert estimacion.coste_usd_estimado == pytest.approx(coste_esperado)

    llamada_cacheada = next(
        llamada for llamada in estimacion.llamadas if llamada.en_cache
    )
    assert llamada_cacheada.coste_usd_estimado == 0.0
    assert llamada_cacheada.tokens_entrada_estimados == 0


def test_estimacion_rechaza_solicitud_sin_imagenes(tmp_path):
    motor = LLMEngine(cache_dir=tmp_path / "cache")
    with pytest.raises(ValueError):
        motor.estimar_coste_lote([([], "prompt", "v1")])


def test_estimar_coste_texto_lote_no_subestima_lo_medido_con_la_api_real(tmp_path):
    """`[listing-audit] MEDIA, 2026-07-17` (FIX 4): la llamada REAL de
    redacción (`core/extract.py::PROMPT_REDACCION_FICHA` + su
    `json_schema`) mide **753 tokens de entrada / 98 de salida** contra la
    API real -- el comentario viejo ("~450 típicos") era FALSO (ignoraba
    los tokens del `json_schema`, facturados como tool). El estimador
    nunca puede quedar por debajo de lo medido, o la costura del coste
    (`CLAUDE.md`) subestima el gasto real por diseño, no por accidente."""
    motor = LLMEngine(cache_dir=tmp_path / "cache")
    estimacion = motor.estimar_coste_texto_lote([("prompt de prueba, texto puro", "v1")])
    llamada = estimacion.llamadas[0]
    assert llamada.tokens_entrada_estimados >= 753
    assert llamada.tokens_salida_estimados >= 98


# ---------------------------------------------------------------------------
# Error de la API: se propaga, nunca se tragua ni se rellena con un valor
# plausible.
# ---------------------------------------------------------------------------
def test_error_de_rate_limit_se_propaga_como_llmllamadafallidaerror(tmp_path, monkeypatch):
    respuesta_http = httpx.Response(429, request=_peticion_httpx(), json={"error": {}})
    error = anthropic.RateLimitError("rate limited", response=respuesta_http, body=None)
    cliente_fake = _ClienteFake(error=error)
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: cliente_fake)

    motor = LLMEngine(cache_dir=tmp_path / "cache", api_key="sk-ant-fake")
    schema = {"type": "object", "properties": {}}

    with pytest.raises(LLMLlamadaFallidaError):
        motor.consultar([_imagen()], "prompt", schema)

    # Nada se cacheo ni se contabilizo como gasto: el fallo no dejó un
    # rastro que pareciera un resultado válido.
    assert motor.coste_lote.llamadas == 0
    assert not list((tmp_path / "cache").glob("*.json"))


def test_error_de_conexion_se_propaga_como_llmllamadafallidaerror(tmp_path, monkeypatch):
    error = anthropic.APIConnectionError(message="boom", request=_peticion_httpx())
    cliente_fake = _ClienteFake(error=error)
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: cliente_fake)

    motor = LLMEngine(cache_dir=tmp_path / "cache", api_key="sk-ant-fake")
    schema = {"type": "object", "properties": {}}

    with pytest.raises(LLMLlamadaFallidaError):
        motor.consultar([_imagen()], "prompt", schema)


def test_respuesta_no_json_se_propaga_como_llmllamadafallidaerror(tmp_path, monkeypatch):
    mensaje_no_json = at.Message(
        id="msg_test",
        content=[{"type": "text", "text": "esto no es json"}],
        model="claude-haiku-4-5",
        role="assistant",
        stop_reason="end_turn",
        stop_sequence=None,
        type="message",
        usage=at.Usage(input_tokens=10, output_tokens=5),
    )
    cliente_fake = _ClienteFake(respuesta=mensaje_no_json)
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: cliente_fake)

    motor = LLMEngine(cache_dir=tmp_path / "cache", api_key="sk-ant-fake")
    schema = {"type": "object", "properties": {}}

    with pytest.raises(LLMLlamadaFallidaError):
        motor.consultar([_imagen()], "prompt", schema)


# ---------------------------------------------------------------------------
# Sin clave configurada: error claro y accionable, no un crash críptico.
# ---------------------------------------------------------------------------
def test_sin_api_key_da_error_claro_y_no_generico(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    motor = LLMEngine(cache_dir=tmp_path / "cache")
    schema = {"type": "object", "properties": {}}

    with pytest.raises(ApiKeyFaltanteError, match="ANTHROPIC_API_KEY"):
        motor.consultar([_imagen()], "prompt", schema)


def test_sin_api_key_pero_con_hit_de_cache_no_falla(tmp_path, monkeypatch):
    """Un lote enteramente cacheado no debe exigir clave — el hit de cache
    ni siquiera intenta construir el cliente."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cache_dir = tmp_path / "cache"
    schema = {"type": "object", "properties": {}}

    cliente_fake = _ClienteFake(respuesta=_mensaje_fake({"talla": "M"}))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: cliente_fake)
    LLMEngine(cache_dir=cache_dir, api_key="sk-ant-fake").consultar(
        [_imagen()], "prompt", schema
    )

    motor_sin_key = LLMEngine(cache_dir=cache_dir)
    resultado = motor_sin_key.consultar([_imagen()], "prompt", schema)
    assert resultado.fuente == "cache"


# ---------------------------------------------------------------------------
# Contabilidad de coste por producto y por lote.
# ---------------------------------------------------------------------------
def test_coste_se_contabiliza_por_producto_y_por_lote(tmp_path, monkeypatch):
    cliente_fake = _ClienteFake(respuesta=_mensaje_fake({"marca": "Nike"}, 1500, 40))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: cliente_fake)
    motor = LLMEngine(cache_dir=tmp_path / "cache", api_key="sk-ant-fake")
    schema = {"type": "object", "properties": {}}

    motor.consultar([_imagen("a.jpg")], "prompt", schema, producto_id="prod-1")
    motor.consultar([_imagen("b.jpg")], "prompt", schema, producto_id="prod-1")
    motor.consultar([_imagen("c.jpg")], "prompt", schema, producto_id="prod-2")

    costes = motor.costes_por_producto()
    assert costes["prod-1"].llamadas == 2
    assert costes["prod-2"].llamadas == 1
    assert motor.coste_lote.llamadas == 3
    assert motor.coste_lote.coste_usd == pytest.approx(
        costes["prod-1"].coste_usd + costes["prod-2"].coste_usd
    )


def test_cache_corrupta_se_trata_como_miss_y_no_se_borra(tmp_path, monkeypatch, caplog):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    schema = {"type": "object", "properties": {}}
    imagen = _imagen()

    motor_para_clave = LLMEngine(cache_dir=cache_dir)
    clave = motor_para_clave._clave_cache([imagen], "prompt", "v1")
    ruta_corrupta = cache_dir / f"{clave}.json"
    ruta_corrupta.write_text("{ esto no es json valido", encoding="utf-8")

    cliente_fake = _ClienteFake(respuesta=_mensaje_fake({"marca": "Adidas"}))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: cliente_fake)
    motor = LLMEngine(cache_dir=cache_dir, api_key="sk-ant-fake")

    with caplog.at_level("ERROR"):
        resultado = motor.consultar([imagen], "prompt", schema, version_prompt="v1")

    assert resultado.fuente == "api"
    assert resultado.datos == {"marca": "Adidas"}
    assert cliente_fake.llamadas  # sí tuvo que llamar, la cache no servía


# ---------------------------------------------------------------------------
# `consultar_texto` (2026-07-17, fix del bug de Diego: la descripción se
# regenera al confirmar, con SOLO los campos de texto confirmados -- nunca
# una foto). La garantía anti-marca-ajena es estructural: este método no
# tiene parámetro `imagenes`, así que no hay dónde colar una imagen.
# ---------------------------------------------------------------------------
def test_consultar_texto_no_tiene_parametro_imagenes():
    import inspect

    firma = inspect.signature(LLMEngine.consultar_texto)
    assert "imagenes" not in firma.parameters


def test_consultar_texto_manda_solo_bloque_de_texto_sin_imagen(tmp_path, monkeypatch):
    cliente_fake = _ClienteFake(respuesta=_mensaje_fake({"titulo": "T", "descripcion": "D"}))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: cliente_fake)
    motor = LLMEngine(cache_dir=tmp_path / "cache", api_key="sk-ant-fake")
    schema = {"type": "object", "properties": {}}

    resultado = motor.consultar_texto("redacta esto", schema)
    assert resultado.fuente == "api"
    assert resultado.datos == {"titulo": "T", "descripcion": "D"}

    contenido = cliente_fake.llamadas[0]["messages"][0]["content"]
    assert len(contenido) == 1  # SOLO el bloque de texto, cero imágenes
    assert contenido[0]["type"] == "text"
    assert contenido[0]["text"] == "redacta esto"


def test_consultar_texto_cachea_y_no_repite_llamada(tmp_path, monkeypatch):
    cliente_fake = _ClienteFake(respuesta=_mensaje_fake({"titulo": "T", "descripcion": "D"}))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: cliente_fake)
    motor = LLMEngine(cache_dir=tmp_path / "cache", api_key="sk-ant-fake")
    schema = {"type": "object", "properties": {}}

    motor.consultar_texto("mismo prompt", schema, version_prompt="v1")
    segundo = motor.consultar_texto("mismo prompt", schema, version_prompt="v1")

    assert len(cliente_fake.llamadas) == 1  # la segunda fue HIT de cache
    assert segundo.fuente == "cache"
    assert segundo.coste_usd == 0.0


def test_consultar_texto_contabiliza_coste_por_producto(tmp_path, monkeypatch):
    cliente_fake = _ClienteFake(respuesta=_mensaje_fake({"titulo": "T", "descripcion": "D"}, 750, 100))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: cliente_fake)
    motor = LLMEngine(cache_dir=tmp_path / "cache", api_key="sk-ant-fake")
    schema = {"type": "object", "properties": {}}

    motor.consultar_texto("redacta", schema, producto_id="prod-redaccion")

    coste = motor.costes_por_producto()["prod-redaccion"]
    assert coste.llamadas == 1
    assert coste.tokens_entrada == 750
    assert coste.tokens_salida == 100
    precio_entrada, precio_salida = PRECIOS_USD_POR_MTOK["claude-haiku-4-5"]
    esperado = (750 / 1_000_000) * precio_entrada + (100 / 1_000_000) * precio_salida
    assert coste.coste_usd == pytest.approx(esperado)


def test_consultar_texto_error_de_api_se_propaga(tmp_path, monkeypatch):
    cliente_fake = _ClienteFake(
        error=anthropic.APIConnectionError(request=_peticion_httpx())
    )
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: cliente_fake)
    motor = LLMEngine(cache_dir=tmp_path / "cache", api_key="sk-ant-fake")
    schema = {"type": "object", "properties": {}}

    with pytest.raises(LLMLlamadaFallidaError):
        motor.consultar_texto("redacta", schema)


def test_consultar_texto_sin_clave_lanza_apikeyfaltanteerror(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    motor = LLMEngine(cache_dir=tmp_path / "cache")
    schema = {"type": "object", "properties": {}}

    with pytest.raises(ApiKeyFaltanteError):
        motor.consultar_texto("redacta esto, nunca visto antes", schema)


def test_consultar_con_lista_vacia_sigue_exigiendo_imagen(tmp_path):
    """El invariante VIEJO de `consultar()` no se relaja por la existencia
    de `consultar_texto` -- son dos contratos DISTINTOS a propósito."""
    motor = LLMEngine(cache_dir=tmp_path / "cache")
    with pytest.raises(ValueError):
        motor.consultar([], "prompt", {"type": "object", "properties": {}})
