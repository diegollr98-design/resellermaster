"""Tests de `ui/ficha.py` — LA FICHA (superficie `atributos`, `truth-loop.md`
§A y §B; superficie que Diego TOCA CON LAS MANOS → `AppTest` obligatorio,
`[INC-006]`, `change-loop.md` §C4).

Qué se prueba, y por qué así:
- El bucle que Diego usa de verdad —revisar una propuesta, confirmar, y que
  la confirmación PERSISTA con `fuente="diego"`— se ejercita end-to-end con
  `streamlit.testing.v1.AppTest` (el botón "Confirmar ficha" vive en el
  cuerpo del script, no en un `@st.dialog`, así que `AppTest` sí puede
  pulsarlo — mismo límite medido que en `test_curar.py`).
- EL HALLAZGO DEL PASO 1: una marca LEÍDA pero no publicada (`valor=None` +
  `lecturas`) llega a Diego como valor por defecto confirmable — PERO sólo
  si su recorte existe (el píxel con dientes, hallazgo del `listing-audit`).
- Un CONFLICTO nunca se pre-elige; el badge "confirmada" no miente tras
  re-extraer; un `aviso_coherencia` (Frankenstein, `[INC-011]`) topa la
  confianza confirmada a `media`.

La ficha fake se construye con los dataclasses REALES + `serializar_extraccion`
(la misma función que usa la app), no a mano — así el test no puede
desincronizarse del formato real, y no necesita OCR ni la API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import streamlit as st
from PIL import Image
from streamlit.testing.v1 import AppTest

from core.extract import (
    Candidato,
    Lectura,
    Propuesta,
    ResultadoExtraccion,
    serializar_extraccion,
)
from core.llm import LLMEngine, LLMLlamadaFallidaError, ResultadoLLM
from core.schema import Campo, Evidencia
from core.store import Foto, LoteStore
from ui import ficha
from ui.ficha import _ficha_confirmada


def _crear_img(ruta: Path, color: tuple[int, int, int] = (0, 0, 0)) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (48, 48), color).save(ruta, format="JPEG")


def _evidencia() -> Evidencia:
    return Evidencia(fichero="IMG_1.jpg", bbox=(10, 20, 30, 40))


def _marca_leida_no_publicada(crops: Path, *, con_crops: bool = True) -> ResultadoExtraccion:
    """El caso del Paso 1: marca 'Reebok' LEÍDA por el VLM pero NO publicada
    (`valor=None`), talla 'M' publicada, estado siempre pendiente de Diego.
    `con_crops=False` simula el píxel AUSENTE (crops borrados / reanudación)."""
    ev = _evidencia()
    rc_marca, rc_talla = crops / "marca.jpg", crops / "talla.jpg"
    if con_crops:
        _crear_img(rc_marca)
        _crear_img(rc_talla)
    campos = {
        "marca": Campo(valor=None, fuente="inferido", confianza="baja"),
        "talla": Campo(valor="M", fuente="foto", confianza="media", evidencia=ev),
        "estado": Campo(valor=None, fuente="inferido", confianza="baja"),
    }
    propuestas = {
        "marca": Propuesta(
            campo="marca", valor=None, recorte=rc_marca, evidencia=ev,
            lecturas=(Lectura(origen="vlm", texto="Reebok"),), motivo="leída, ubicación no publicable",
        ),
        "talla": Propuesta(
            campo="talla", valor="M", recorte=rc_talla, evidencia=ev,
            lecturas=(Lectura(origen="vlm", texto="M"),), motivo="etiqueta de cuello",
        ),
        "estado": Propuesta(campo="estado", valor=None, recorte=None, evidencia=None, motivo="lo pones tú"),
    }
    return ResultadoExtraccion(campos=campos, propuestas=propuestas, fallos=(), coste_usd=0.02)


def _conflicto_dos_marcas(crops: Path) -> ResultadoExtraccion:
    """UMBRO/RAMI JALAB: dos marcas legibles → el pipeline no elige, ambas
    candidatas con su recorte llegan a Diego (`valor=None`)."""
    ev = _evidencia()
    rc_umbro, rc_rami = crops / "umbro.jpg", crops / "rami.jpg"
    _crear_img(rc_umbro)
    _crear_img(rc_rami)
    alternativas = (
        Candidato(valor="UMBRO", recorte=rc_umbro, evidencia=ev),
        Candidato(valor="RAMI JALAB", recorte=rc_rami, evidencia=ev),
    )
    campos = {"marca": Campo(valor=None, fuente="inferido", confianza="baja")}
    propuestas = {
        "marca": Propuesta(
            campo="marca", valor=None, recorte=None, evidencia=None,
            alternativas=alternativas, motivo="dos marcas legibles: elige tú",
        )
    }
    return ResultadoExtraccion(campos=campos, propuestas=propuestas, fallos=(), coste_usd=0.02)


def _con_aviso_coherencia(crops: Path) -> ResultadoExtraccion:
    """Marca legible pero con aviso de coherencia (`[INC-011]`, campos de
    fotos disjuntas) — la confianza confirmada NUNCA debe subir a `alta`."""
    base = _marca_leida_no_publicada(crops)
    return ResultadoExtraccion(
        campos=base.campos, propuestas=base.propuestas, fallos=base.fallos,
        coste_usd=base.coste_usd, aviso_coherencia="marca y talla vienen de fotos disjuntas",
    )


# ============================================================================
# CAMPOS OBLIGATORIOS (Fase 3, 2026-07-17, pedido de Diego: "no deje
# confirmar ficha hasta que no se rellene"). `_marca_leida_no_publicada` NO
# trae "categoria"/"titulo"/"descripcion" y deja "estado" sin elegir A
# PROPÓSITO (es el fixture que prueba que un campo AUSENTE no revienta la
# pantalla, `test_categoria_ausente_no_pinta_selectbox_ni_revienta`) — así
# que los tests que necesitan una confirmación EXITOSA usan esta variante,
# que añade categoría/título/descripción ya propuestos y deja "estado" a
# elegir (SIEMPRE lo elige Diego con el selectbox, nunca la extracción,
# `truth-loop.md` §A.4 — los tests que confirman con esto deben elegirlo
# ellos mismos, ver `_elegir_estado_y_categoria`).
# ============================================================================
def _marca_leida_no_publicada_lista_para_confirmar(crops: Path) -> ResultadoExtraccion:
    base = _marca_leida_no_publicada(crops)
    campos = dict(base.campos)
    propuestas = dict(base.propuestas)
    campos["categoria"] = Campo(valor="moda", fuente="inferido", confianza="baja")
    campos["titulo"] = Campo(valor="Sudadera Reebok talla M", fuente="inferido", confianza="baja")
    campos["descripcion"] = Campo(valor="Sudadera en buen estado, apenas usada.", fuente="inferido", confianza="baja")
    propuestas["categoria"] = Propuesta(
        campo="categoria", valor="moda", recorte=None, evidencia=None, motivo="clasificación"
    )
    propuestas["titulo"] = Propuesta(
        campo="titulo", valor="Sudadera Reebok talla M", recorte=None, evidencia=None, motivo="borrador"
    )
    propuestas["descripcion"] = Propuesta(
        campo="descripcion", valor="Sudadera en buen estado, apenas usada.",
        recorte=None, evidencia=None, motivo="borrador",
    )
    return ResultadoExtraccion(
        campos=campos, propuestas=propuestas, fallos=base.fallos, coste_usd=base.coste_usd
    )


def _con_aviso_coherencia_lista_para_confirmar(crops: Path) -> ResultadoExtraccion:
    base = _marca_leida_no_publicada_lista_para_confirmar(crops)
    return ResultadoExtraccion(
        campos=base.campos, propuestas=base.propuestas, fallos=base.fallos,
        coste_usd=base.coste_usd, aviso_coherencia="marca y talla vienen de fotos disjuntas",
    )


def _elegir_estado_y_categoria(at: AppTest, pid: str, *, estado: str = "Bueno", categoria: str = "moda") -> AppTest:
    """Simula a Diego eligiendo los dos selectbox obligatorios que la
    extracción NUNCA pre-rellena (`estado`/`categoria` son SIEMPRE un
    juicio suyo, `truth-loop.md` §A.4) -- lo que hace falta para que
    `_accion_confirmar_ficha` deje pasar la puerta de obligatorios."""
    at = next(s for s in at.selectbox if s.key == f"ficha_{pid}_estado_estado").set_value(estado).run()
    at = next(s for s in at.selectbox if s.key == f"ficha_{pid}_categoria_categoria").set_value(categoria).run()
    return at


def _preparar(
    tmp_path: Path,
    crear_resultado: Callable[[Path], ResultadoExtraccion],
    *,
    confirmar_agrupacion: bool = True,
) -> tuple[str, str]:
    """Crea lote + 1 foto + 1 producto, confirma su AGRUPACIÓN (Fase 1) y le
    guarda una extracción (serializada, sin llamar al VLM). Los recortes
    fake viven en `tmp_path/crops_fake`. Devuelve `(lote_id, producto_id)`."""
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote ficha", "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id
    ruta = carpeta / "IMG_1.jpg"
    _crear_img(ruta, (120, 60, 60))
    (foto_id,) = store.añadir_fotos(lote_id, [Foto(ruta=str(ruta), hash="hash_1")])
    (producto_id,) = store.guardar_agrupacion(lote_id, [[foto_id]])
    if confirmar_agrupacion:
        store.confirmar_producto(producto_id)
    resultado = crear_resultado(tmp_path / "crops_fake")
    store.guardar_extraccion(producto_id, serializar_extraccion(resultado))
    return lote_id, producto_id


# `_MotorTextoFake` (2026-07-17, fix del bug de Diego): desde que confirmar
# regenera título/descripción (`redactar_desde_campos_confirmados`), el
# motor YA NO puede ser `None` — se usa también fuera del diálogo de
# extracción. Definido DENTRO de `_script` (no a nivel de módulo) porque
# `AppTest.from_function` extrae el CÓDIGO FUENTE de la función y lo
# ejecuta en un contexto aislado: no puede cerrar sobre nombres de fuera
# (mismo motivo por el que los imports de abajo están dentro, no arriba).
def _script(
    data_dir: str,
    lote_id: str,
    *,
    titulo_generado: str = "Producto de segunda mano en buen estado",
    descripcion_generada: str = "Descripción generada automáticamente, revisa antes de publicar.",
    fallar_redaccion: bool = False,
) -> None:
    from pathlib import Path as _Path

    from core.llm import LLMLlamadaFallidaError as _LLMLlamadaFallidaError
    from core.llm import ResultadoLLM as _ResultadoLLM
    from core.store import LoteStore as _LoteStore
    from ui import ficha as _ficha

    class _MotorTextoFake:
        """Sólo implementa `consultar_texto` -- es lo único que necesita
        `_accion_confirmar_ficha` (los diálogos `@st.dialog` de extracción
        no son alcanzables por `AppTest`, ver docstring de la sección de
        extracción-de-lote más abajo)."""

        def consultar_texto(self, prompt, json_schema, version_prompt=None, producto_id=None):
            if fallar_redaccion:
                raise _LLMLlamadaFallidaError("fallo simulado de red (AVG/rate-limit/etc.)")
            return _ResultadoLLM(
                datos={"titulo": titulo_generado, "descripcion": descripcion_generada},
                fuente="api",
                coste_usd=0.00005,
                tokens_entrada=120,
                tokens_salida=60,
            )

    _ficha.render(
        _LoteStore(data_dir=_Path(data_dir)), lote_id, crear_motor=lambda: _MotorTextoFake()
    )


def _texto_input(at: AppTest, key: str):
    return next(t for t in at.text_input if t.key == key)


def _producto(tmp_path: Path, lote_id: str, pid: str) -> dict:
    estado = LoteStore(data_dir=tmp_path).cargar_lote(lote_id)
    return next(p for p in estado["productos"] if p["id"] == pid)


# ============================================================================
# Render y promoción de lecturas (el hallazgo del Paso 1) — CON el píxel.
# ============================================================================
def test_render_producto_extraido_sin_excepcion(tmp_path):
    lote_id, _pid = _preparar(tmp_path, _marca_leida_no_publicada)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception


def test_lectura_no_publicada_se_promueve_si_hay_recorte(tmp_path):
    """Marca 'Reebok' leída pero NO publicada: con su recorte EN DISCO, la
    pantalla la ofrece como valor por defecto confirmable en un click."""
    lote_id, pid = _preparar(tmp_path, _marca_leida_no_publicada)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    assert _texto_input(at, f"ficha_{pid}_marca_valor").value == "Reebok"
    assert _texto_input(at, f"ficha_{pid}_talla_valor").value == "M"


def test_se_prerellena_aunque_falte_el_recorte(tmp_path):
    """DECISIÓN DE DIEGO (revierte el 'píxel con dientes' anterior): se
    pre-rellena SIEMPRE con el mejor intento, aunque el recorte no esté en
    disco. En su flujo un campo vacío cuesta teclearlo; un valor mal, 2 s
    corregirlo. La verificación es su ojo con el recorte al lado, no un hueco."""
    lote_id, pid = _preparar(tmp_path, lambda c: _marca_leida_no_publicada(c, con_crops=False))
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    assert _texto_input(at, f"ficha_{pid}_marca_valor").value == "Reebok"
    assert _texto_input(at, f"ficha_{pid}_talla_valor").value == "M"


# ============================================================================
# La ficha "todo relleno" que produce la síntesis: marca inferida, título y
# descripción redactados, estado canónico, comparables por EAN.
# ============================================================================
def _ficha_completa(crops: Path) -> ResultadoExtraccion:
    ev = _evidencia()
    rc = crops / "modelo.jpg"
    _crear_img(rc)
    campos = {
        "marca": Campo(valor="lufthous", fuente="inferido", confianza="baja"),
        "modelo": Campo(valor="LLLT-200", fuente="foto", confianza="media", evidencia=ev),
        "ean": Campo(valor="8445061029720", fuente="foto", confianza="alta", evidencia=ev),
        "categoria": Campo(valor="electronica", fuente="inferido", confianza="baja"),
        "estado": Campo(valor="Como nuevo", fuente="inferido", confianza="baja"),
        "titulo": Campo(valor="Masajeador lufthous LLLT-200", fuente="inferido", confianza="baja"),
        "descripcion": Campo(valor="Masajeador de rodilla lufthous, como nuevo.", fuente="inferido", confianza="baja"),
    }
    propuestas = {
        "marca": Propuesta(campo="marca", valor="lufthous", recorte=None, evidencia=None, motivo="mejor intento"),
        "modelo": Propuesta(campo="modelo", valor="LLLT-200", recorte=rc, evidencia=ev, motivo="leído"),
        "ean": Propuesta(campo="ean", valor="8445061029720", recorte=rc, evidencia=ev, motivo="checksum"),
        "categoria": Propuesta(
            campo="categoria", valor="electronica", recorte=None, evidencia=None,
            motivo="clasificacion del modelo, confirmala",
        ),
        "estado": Propuesta(campo="estado", valor="Como nuevo", recorte=None, evidencia=None, motivo="estímalo"),
        "titulo": Propuesta(campo="titulo", valor="Masajeador lufthous LLLT-200", recorte=None, evidencia=None, motivo="borrador"),
        "descripcion": Propuesta(campo="descripcion", valor="Masajeador de rodilla lufthous, como nuevo.", recorte=None, evidencia=None, motivo="borrador"),
    }
    return ResultadoExtraccion(campos=campos, propuestas=propuestas, fallos=(), coste_usd=0.02)


# ============================================================================
# EL BUG DE DIEGO (2026-07-17): "la descripción no menciona la CREMALLERA
# ROTA" -- `desperfectos` viene de un papel manuscrito (`fuente="foto"`),
# el titulo/descripcion son el BORRADOR VIEJO de la extracción (redactado
# ANTES de que Diego confirmara nada) y NO lo mencionan -- exactamente el
# estado real que Diego vio en su captura.
# ============================================================================
def _ficha_con_desperfecto(crops: Path) -> ResultadoExtraccion:
    ev = _evidencia()
    campos = {
        "marca": Campo(valor="Umbro", fuente="diego", confianza="alta"),
        # "categoria" -- obligatoria desde el gate de "campos obligatorios"
        # (2026-07-17, pedido de Diego, sección más abajo): sin esto el
        # confirm quedaría BLOQUEADO y estos tests no llegarían a probar
        # la regeneración de título/descripción, que es lo que importa aquí.
        "categoria": Campo(valor="moda", fuente="inferido", confianza="baja"),
        "desperfectos": Campo(valor="CREMALLERA ROTA", fuente="foto", confianza="media", evidencia=ev),
        "estado": Campo(valor="Bueno", fuente="inferido", confianza="baja"),
        "titulo": Campo(valor="Sudadera Umbro talla M", fuente="inferido", confianza="baja"),
        "descripcion": Campo(
            valor="Sudadera Umbro en buen estado, talla M.", fuente="inferido", confianza="baja"
        ),
    }
    propuestas = {
        "marca": Propuesta(campo="marca", valor="Umbro", recorte=None, evidencia=None, motivo="confirmada"),
        "categoria": Propuesta(
            campo="categoria", valor="moda", recorte=None, evidencia=None, motivo="clasificación",
        ),
        "desperfectos": Propuesta(
            campo="desperfectos", valor="CREMALLERA ROTA", recorte=None, evidencia=ev,
            motivo="nota manuscrita",
        ),
        "estado": Propuesta(campo="estado", valor="Bueno", recorte=None, evidencia=None, motivo="estímalo"),
        "titulo": Propuesta(
            campo="titulo", valor="Sudadera Umbro talla M", recorte=None, evidencia=None, motivo="borrador",
        ),
        "descripcion": Propuesta(
            campo="descripcion", valor="Sudadera Umbro en buen estado, talla M.",
            recorte=None, evidencia=None, motivo="borrador",
        ),
    }
    return ResultadoExtraccion(campos=campos, propuestas=propuestas, fallos=(), coste_usd=0.02)


def test_marca_inferida_se_prerellena(tmp_path):
    lote_id, pid = _preparar(tmp_path, _ficha_completa)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    assert _texto_input(at, f"ficha_{pid}_marca_valor").value == "lufthous"


# ============================================================================
# LA TRAMPA DE titulo/descripcion (Diego, 2026-07-17): editarlos ANTES de
# confirmar desactivaba `_diego_edito_texto` -> la regeneración con los
# campos corregidos nunca se disparaba. Fix: ANTES de confirmar son SÓLO
# LECTURA (sin `text_area` que tocar por accidente); DESPUÉS de la primera
# confirmación (`_ficha_confirmada`=True) se vuelven editables, para que
# Diego pueda retocar el texto YA regenerado.
# ============================================================================
def test_titulo_descripcion_son_solo_lectura_antes_de_confirmar(tmp_path):
    lote_id, pid = _preparar(tmp_path, _ficha_completa)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    # NO hay text_area para titulo/descripcion antes de confirmar.
    assert not any(a.key == f"ficha_{pid}_titulo_valor" for a in at.text_area)
    assert not any(a.key == f"ficha_{pid}_descripcion_valor" for a in at.text_area)
    # PERO el texto (el mejor intento de la extracción) SÍ se ve.
    textos = " ".join(m.value for m in at.info) + " " + " ".join(c.value for c in at.caption)
    assert "Masajeador lufthous" in textos
    assert "rodilla" in textos


def test_titulo_descripcion_son_editables_despues_de_confirmar(tmp_path):
    lote_id, pid = _preparar(tmp_path, _ficha_completa)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception
    assert _ficha_confirmada(_producto(tmp_path, lote_id, pid)) is True

    assert any(a.key == f"ficha_{pid}_titulo_valor" for a in at.text_area)
    assert any(a.key == f"ficha_{pid}_descripcion_valor" for a in at.text_area)


def test_estado_se_preselecciona_del_mejor_intento(tmp_path):
    lote_id, pid = _preparar(tmp_path, _ficha_completa)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    selebox = next(s for s in at.selectbox if s.key == f"ficha_{pid}_estado_estado")
    assert selebox.value == "Como nuevo"


def test_comparables_por_ean_muestra_enlaces(tmp_path):
    """El botón de comparables (Costura 2): con EAN, match 'exacto' (mismo
    producto). Nunca tasa; abre la búsqueda para que Diego vea precios."""
    lote_id, _pid = _preparar(tmp_path, _ficha_completa)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    textos = " ".join(c.value for c in at.caption)
    assert "código de barras" in textos


def test_conflicto_no_se_pre_elige_y_muestra_ambas_candidatas(tmp_path):
    lote_id, pid = _preparar(tmp_path, _conflicto_dos_marcas)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    assert _texto_input(at, f"ficha_{pid}_marca_valor").value == ""
    labels = " ".join(b.label for b in at.button)
    assert "UMBRO" in labels and "RAMI JALAB" in labels


# ============================================================================
# REGENERACIÓN DE TÍTULO/DESCRIPCIÓN AL CONFIRMAR (fix del bug de Diego).
# Unidad pura sobre `_diego_edito_texto`/`_regenerar_titulo_descripcion`
# (ninguna de las dos llama a `st.*`, se prueban directas, sin `AppTest`).
# ============================================================================
class _MotorTextoFake:
    """Motor de mentira que sólo implementa `consultar_texto` -- exactamente
    el contrato que necesita `redactar_desde_campos_confirmados`. Guarda
    cada prompt real que recibió (`self.prompts`) para poder comprobar QUÉ
    se le mandó -- la garantía anti-marca-ajena vive en que este método ni
    siquiera tiene un parámetro `imagenes`, así que no hay forma de
    colarle una foto."""

    def __init__(self, titulo: str = "Titulo IA", descripcion: str = "Descripcion IA") -> None:
        self.titulo = titulo
        self.descripcion = descripcion
        self.prompts: list[str] = []
        self.excepcion: Exception | None = None

    def consultar_texto(self, prompt, json_schema, version_prompt="v1", producto_id=None):
        self.prompts.append(prompt)
        if self.excepcion is not None:
            raise self.excepcion
        return ResultadoLLM(
            datos={"titulo": self.titulo, "descripcion": self.descripcion},
            fuente="api", coste_usd=0.00005, tokens_entrada=120, tokens_salida=60,
        )


def test_diego_edito_texto_false_si_es_el_borrador_intacto():
    """EL CASO DEL BUG: el borrador del modelo, tal cual, sin tocar ->
    debe regenerarse."""
    serial = {"campos": {"titulo": {"valor": "Borrador", "fuente": "inferido"}}}
    confirmado = {"campos": {"titulo": {"valor": "Borrador"}}}
    assert ficha._diego_edito_texto("titulo", serial, confirmado) is False


def test_diego_edito_texto_true_si_lo_escribio_ahora():
    serial = {"campos": {"titulo": {"valor": "Borrador", "fuente": "inferido"}}}
    confirmado = {"campos": {"titulo": {"valor": "Mi propio titulo"}}}
    assert ficha._diego_edito_texto("titulo", serial, confirmado) is True


def test_diego_edito_texto_true_si_ya_estaba_confirmado_por_el():
    """Una vuelta anterior YA lo fijó a mano (`fuente="diego"`); aunque el
    widget no lo haya tocado EN ESTE confirm, sigue siendo suyo -- no se
    revierte al borrador del modelo."""
    serial = {"campos": {"titulo": {"valor": "Texto de Diego", "fuente": "diego"}}}
    confirmado = {"campos": {"titulo": {"valor": "Texto de Diego"}}}
    assert ficha._diego_edito_texto("titulo", serial, confirmado) is True


def _serial_confirmado_para_regenerar(marca: str = "Reebok", desperfectos: str | None = None) -> tuple[dict, dict]:
    campos_serial = {
        "marca": {"valor": marca, "fuente": "diego", "confianza": "alta"},
        "titulo": {"valor": "Borrador", "fuente": "inferido", "confianza": "baja"},
        "descripcion": {"valor": "Borrador desc", "fuente": "inferido", "confianza": "baja"},
    }
    if desperfectos is not None:
        campos_serial["desperfectos"] = {"valor": desperfectos, "fuente": "foto", "confianza": "media"}
    confirmado = {"campos": {k: dict(v) for k, v in campos_serial.items()}}
    serial = {"campos": {k: dict(v) for k, v in campos_serial.items()}}
    return serial, confirmado


def test_regenerar_llama_al_motor_si_nadie_toco_el_texto():
    serial, confirmado = _serial_confirmado_para_regenerar(desperfectos="CREMALLERA ROTA")
    motor = _MotorTextoFake(titulo="Nuevo titulo", descripcion="Nueva desc: CREMALLERA ROTA incluida")
    resultado, error = ficha._regenerar_titulo_descripcion(confirmado, serial, motor, "pid1")
    assert error is None
    assert resultado["campos"]["titulo"]["valor"] == "Nuevo titulo"
    assert resultado["campos"]["titulo"]["fuente"] == "inferido"
    assert resultado["campos"]["descripcion"]["valor"] == "Nueva desc: CREMALLERA ROTA incluida"
    assert resultado["campos"]["descripcion"]["fuente"] == "inferido"
    assert len(motor.prompts) == 1  # una sola llamada redacta AMBOS a la vez


def test_regenerar_prompt_lleva_marca_corregida_y_desperfectos():
    """Diego corrigió marca 'Umbro' -> 'Reebok' y confirmó el desperfecto:
    el prompt real que ve el modelo lleva 'Reebok' (nunca 'Umbro', que ni
    siquiera está en el dict que se le pasa) y menciona la cremallera."""
    serial, confirmado = _serial_confirmado_para_regenerar(marca="Reebok", desperfectos="CREMALLERA ROTA")
    motor = _MotorTextoFake()
    ficha._regenerar_titulo_descripcion(confirmado, serial, motor, "pid1")
    prompt = motor.prompts[0]
    assert "Reebok" in prompt
    assert "Umbro" not in prompt
    assert "CREMALLERA ROTA" in prompt


def test_regenerar_no_pisa_texto_editado_a_mano():
    serial, confirmado = _serial_confirmado_para_regenerar()
    confirmado["campos"]["titulo"]["valor"] = "Mi propio titulo"  # Diego lo tecleó ahora
    motor = _MotorTextoFake(titulo="NO DEBERIA USARSE", descripcion="Nueva desc")
    resultado, error = ficha._regenerar_titulo_descripcion(confirmado, serial, motor, "pid1")
    assert error is None
    assert resultado["campos"]["titulo"]["valor"] == "Mi propio titulo"  # intacto
    assert resultado["campos"]["descripcion"]["valor"] == "Nueva desc"  # éste sí, no lo tocó


def test_regenerar_no_llama_al_motor_si_diego_edito_los_dos():
    serial, confirmado = _serial_confirmado_para_regenerar()
    confirmado["campos"]["titulo"]["valor"] = "Mi titulo"
    confirmado["campos"]["descripcion"]["valor"] = "Mi descripcion"
    motor = _MotorTextoFake()
    resultado, error = ficha._regenerar_titulo_descripcion(confirmado, serial, motor, "pid1")
    assert error is None
    assert motor.prompts == []  # cero llamadas -- cero coste
    assert resultado["campos"]["titulo"]["valor"] == "Mi titulo"
    assert resultado["campos"]["descripcion"]["valor"] == "Mi descripcion"


def test_regenerar_propaga_fallo_y_no_muta_nada():
    serial, confirmado = _serial_confirmado_para_regenerar()
    motor = _MotorTextoFake()
    motor.excepcion = LLMLlamadaFallidaError("fallo simulado de red")
    resultado, error = ficha._regenerar_titulo_descripcion(confirmado, serial, motor, "pid1")
    assert error is not None
    assert "fallo simulado de red" in error
    # el texto viejo (borrador) NO se sobreescribe con nada a medias
    assert resultado["campos"]["titulo"]["valor"] == "Borrador"
    assert resultado["campos"]["descripcion"]["valor"] == "Borrador desc"


def test_regenerar_sin_campos_titulo_descripcion_no_llama_al_motor():
    """Extracción muy vieja / sin estos campos: no hay nada que regenerar,
    no se gasta un céntimo."""
    confirmado = {"campos": {"marca": {"valor": "Reebok"}}}
    serial = {"campos": {"marca": {"valor": "Reebok", "fuente": "diego"}}}
    motor = _MotorTextoFake()
    resultado, error = ficha._regenerar_titulo_descripcion(confirmado, serial, motor, "pid1")
    assert error is None
    assert motor.prompts == []


# ============================================================================
# Lo mismo, end-to-end vía `AppTest` (`[INC-006]`: superficie que Diego toca
# con las manos) -- el botón "Confirmar ficha" persiste el texto que
# realmente sale de `_MotorTextoFake` (definido dentro de `_script`).
# ============================================================================
def test_confirmar_sin_tocar_texto_regenera_con_campos_confirmados(tmp_path):
    """EL BUG DE DIEGO reproducido y arreglado: la descripción vieja no
    menciona la CREMALLERA ROTA porque se redactó ANTES de confirmar nada.
    Diego NO toca la caja de texto, sólo confirma -> se regenera con los
    campos ya confirmados (aquí, el motor fake ya "sabe" mencionarla —
    lo que se prueba es que el pipeline USA el resultado regenerado, no el
    borrador viejo)."""
    lote_id, pid = _preparar(tmp_path, _ficha_con_desperfecto)
    # texto en minúsculas (como escribiría el modelo de verdad, no en
    # MAYÚSCULAS -- eso lo tumbaría el sanitizador `EXCESSIVE_UPPERCASE`,
    # que es una defensa DISTINTA a la que este test verifica).
    nueva_desc = "Sudadera Umbro talla M, buen estado, con la cremallera rota (se vende tal cual)."
    at = AppTest.from_function(
        _script,
        args=(str(tmp_path), lote_id),
        kwargs={
            "titulo_generado": "Sudadera Umbro M, cremallera rota",
            "descripcion_generada": nueva_desc,
        },
    ).run()
    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception

    campos = _producto(tmp_path, lote_id, pid)["campos"]["campos"]
    assert campos["descripcion"]["valor"] == nueva_desc
    assert campos["descripcion"]["fuente"] == "inferido"
    assert "cremallera" in campos["descripcion"]["valor"].lower()
    # el borrador VIEJO (el que no mencionaba el desperfecto) ya no está
    assert campos["descripcion"]["valor"] != "Sudadera Umbro en buen estado, talla M."


def test_confirmar_respeta_descripcion_editada_a_mano(tmp_path):
    """Editar a mano SÓLO es posible DESPUÉS de la primera confirmación
    (la trampa ya no existe: no hay `text_area` antes). Flujo real: (1)
    Diego confirma sin tocar nada -> se regenera con el motor; (2) la ficha
    queda confirmada y el texto se vuelve editable; (3) lo retoca y vuelve
    a confirmar -> su texto manda, `fuente="diego"`."""
    lote_id, pid = _preparar(tmp_path, _ficha_con_desperfecto)
    at = AppTest.from_function(
        _script,
        args=(str(tmp_path), lote_id),
        kwargs={
            "titulo_generado": "titulo generado automaticamente",
            "descripcion_generada": "descripcion generada automaticamente",
        },
    ).run()
    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception
    assert _ficha_confirmada(_producto(tmp_path, lote_id, pid)) is True

    texto_propio = "Mi propia descripción: tiene la cremallera rota, la vendo tal cual."
    key_desc = f"ficha_{pid}_descripcion_valor"
    next(a for a in at.text_area if a.key == key_desc).set_value(texto_propio).run()
    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception

    campos = _producto(tmp_path, lote_id, pid)["campos"]["campos"]
    assert campos["descripcion"]["valor"] == texto_propio
    assert campos["descripcion"]["fuente"] == "diego"


def test_confirmar_falla_redaccion_no_persiste_en_silencio(tmp_path):
    """`decision-making.md` §13: nunca un fallback silencioso. Si la
    redacción falla (red/AVG/API), NO se confirma con el texto viejo
    (describiría el producto PRE-corrección) -- se avisa y no se persiste."""
    lote_id, pid = _preparar(tmp_path, _ficha_con_desperfecto)
    at = AppTest.from_function(
        _script, args=(str(tmp_path), lote_id), kwargs={"fallar_redaccion": True},
    ).run()
    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception  # el error se pinta con st.error, no revienta la pantalla
    assert len(at.error) >= 1

    estado = LoteStore(data_dir=tmp_path).cargar_lote(lote_id)
    fichas = [c for c in estado["confirmaciones"] if c["tipo"] == "ficha" and c["producto_id"] == pid]
    assert not fichas, "no debió confirmarse: la redacción falló"
    campos = _producto(tmp_path, lote_id, pid)["campos"]["campos"]
    assert campos["descripcion"]["valor"] == "Sudadera Umbro en buen estado, talla M."  # intacto, no a medias


# ============================================================================
# Confirmación: persiste con fuente='diego', append-only.
# ============================================================================
def test_confirmar_persiste_con_fuente_diego(tmp_path):
    lote_id, pid = _preparar(tmp_path, _marca_leida_no_publicada_lista_para_confirmar)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    at = _elegir_estado_y_categoria(at, pid)
    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception

    campos = _producto(tmp_path, lote_id, pid)["campos"]["campos"]
    assert campos["marca"]["valor"] == "Reebok" and campos["marca"]["fuente"] == "diego"
    assert campos["talla"]["valor"] == "M" and campos["talla"]["fuente"] == "diego"
    estado = LoteStore(data_dir=tmp_path).cargar_lote(lote_id)
    fichas = [c for c in estado["confirmaciones"] if c["tipo"] == "ficha" and c["producto_id"] == pid]
    assert len(fichas) == 1


def test_campo_vacio_se_confirma_como_null(tmp_path):
    """`marca` NO es obligatoria (`_CAMPOS_OBLIGATORIOS` la excluye a
    propósito: en Vinted "Sin marca" es válido, en Wallapop es opcional)
    -- vaciarla no debe bloquear la confirmación."""
    lote_id, pid = _preparar(tmp_path, _marca_leida_no_publicada_lista_para_confirmar)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    at = _elegir_estado_y_categoria(at, pid)
    _texto_input(at, f"ficha_{pid}_marca_valor").set_value("").run()
    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception

    marca = _producto(tmp_path, lote_id, pid)["campos"]["campos"]["marca"]
    assert marca["valor"] is None and marca["fuente"] == "diego"


def test_confirmar_ficha_es_append_only(tmp_path):
    lote_id, pid = _preparar(tmp_path, _marca_leida_no_publicada_lista_para_confirmar)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    at = _elegir_estado_y_categoria(at, pid)
    at.button(key=f"confirmar_{pid}").click().run()
    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception

    estado = LoteStore(data_dir=tmp_path).cargar_lote(lote_id)
    fichas = [c for c in estado["confirmaciones"] if c["tipo"] == "ficha" and c["producto_id"] == pid]
    assert len(fichas) == 2


def test_estado_es_selectbox_sin_preelegir(tmp_path):
    lote_id, pid = _preparar(tmp_path, _marca_leida_no_publicada)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    selebox = next(s for s in at.selectbox if s.key == f"ficha_{pid}_estado_estado")
    assert selebox.value == "(sin elegir)"


# ============================================================================
# `categoria` (Fase 3, 2026-07-17): selectbox como `estado` — SIEMPRE la
# confirma Diego (fuente="inferido" del extractor, "diego" tras confirmar).
# ============================================================================
def test_categoria_se_pinta_como_selectbox_y_preselecciona_el_mejor_intento(tmp_path):
    lote_id, pid = _preparar(tmp_path, _ficha_completa)  # categoria="electronica"
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    selebox = next(s for s in at.selectbox if s.key == f"ficha_{pid}_categoria_categoria")
    assert selebox.value == "electronica"


def test_categoria_ausente_SI_pinta_selectbox_porque_es_obligatoria(tmp_path):
    """REESCRITO (2026-07-17). Antes se llamaba `test_categoria_ausente_no_
    pinta_selectbox_ni_revienta` y asertaba lo CONTRARIO: "no debe inventarse
    un selectbox para un campo que no existe". Sonaba prudente, pasaba en
    verde, y **consagraba un callejón sin salida**: `categoria` es
    OBLIGATORIA, así que no pintarla dejaba la ficha bloqueada para siempre
    ("obligatorio, falta: categoría" y ningún sitio donde rellenarla). Lo
    cazó Diego con su producto real `eea6b292`, extraído antes de que el
    campo existiera.

    Es `[INC-018]` otra vez: un test escrito por quien escribió el código
    puede estar de acuerdo con él en el error. La distinción que faltaba:
    para un campo OPCIONAL, no pintar lo ausente es correcto; para uno
    OBLIGATORIO, es encerrar al usuario.

    `_marca_leida_no_publicada` no incluye 'categoria' en `campos` (el modelo
    violó el enum, o la ficha es de una versión anterior de la app)."""
    lote_id, pid = _preparar(tmp_path, _marca_leida_no_publicada)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    assert any(s.key == f"ficha_{pid}_categoria_categoria" for s in at.selectbox), (
        "un obligatorio ausente TIENE que pintarse: si no, el gate que lo "
        "exige no se puede satisfacer nunca"
    )


def test_categoria_confirmar_deja_fuente_diego(tmp_path):
    lote_id, pid = _preparar(tmp_path, _ficha_completa)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception

    categoria = _producto(tmp_path, lote_id, pid)["campos"]["campos"]["categoria"]
    assert categoria["valor"] == "electronica"
    assert categoria["fuente"] == "diego"


# ============================================================================
# `tipo` (Fase 4, `docs/seeds/fase-4-tipo-producto.md`): qué ES el producto
# ("masajeador de rodilla", "sudadera") -- SIEMPRE `fuente="inferido"` (un
# juicio de la síntesis, nunca una lectura de píxel) y SIN recorte propio
# (`recorte=None`). No es un campo especial en `_render_campo` (no es
# `estado`/`categoria`/`titulo`/`descripcion`): cae en la rama GENÉRICA de
# texto libre editable, igual que `marca`/`talla` -- por eso NO hace falta
# código nuevo para que se pinte o se confirme; sólo su sitio en
# `_ORDEN_CAMPOS` (`ui/ficha.py`).
# ============================================================================
def _ficha_con_tipo(crops: Path) -> ResultadoExtraccion:
    """Basada en la ficha "lista para confirmar" (ya trae categoria/titulo/
    descripcion propuestos; `estado` lo elige Diego) + el campo `tipo`
    nuevo, sin recorte -- verifica que `_render_campo` no exige un píxel
    para un campo que por diseño no lo tiene (es un juicio, `truth-loop.md`
    §A.5)."""
    base = _marca_leida_no_publicada_lista_para_confirmar(crops)
    campos = dict(base.campos)
    propuestas = dict(base.propuestas)
    campos["tipo"] = Campo(valor="masajeador de rodilla", fuente="inferido", confianza="baja")
    propuestas["tipo"] = Propuesta(
        campo="tipo", valor="masajeador de rodilla", recorte=None, evidencia=None,
        motivo="qué es el producto (inferido por la síntesis, confírmalo)",
    )
    return ResultadoExtraccion(
        campos=campos, propuestas=propuestas, fallos=base.fallos, coste_usd=base.coste_usd
    )


def test_tipo_se_pinta_sin_recorte_con_badge_inferido(tmp_path):
    lote_id, pid = _preparar(tmp_path, _ficha_con_tipo)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    assert _texto_input(at, f"ficha_{pid}_tipo_valor").value == "masajeador de rodilla"

    captions = " ".join(c.value for c in at.caption)
    markdowns = " ".join(m.value for m in at.markdown)
    assert "sin recorte" in captions.lower(), "tipo no tiene recorte propio -- debe decirlo, no simularlo"
    assert "inferido" in markdowns.lower(), "tipo SIEMPRE es un juicio (fuente=inferido), nunca 'foto'"
    assert "qué es el producto" in captions.lower()


def test_tipo_confirmar_deja_fuente_diego(tmp_path):
    lote_id, pid = _preparar(tmp_path, _ficha_con_tipo)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    at = _elegir_estado_y_categoria(at, pid)
    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception

    tipo = _producto(tmp_path, lote_id, pid)["campos"]["campos"]["tipo"]
    assert tipo["valor"] == "masajeador de rodilla"
    assert tipo["fuente"] == "diego"


def test_tipo_editado_por_diego_se_persiste(tmp_path):
    """Diego corrige el juicio del modelo ("masajeador de rodilla" ->
    "masajeador de piernas") -- texto libre editable, mismo camino que
    corregir una marca mal leída."""
    lote_id, pid = _preparar(tmp_path, _ficha_con_tipo)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    at = _elegir_estado_y_categoria(at, pid)
    _texto_input(at, f"ficha_{pid}_tipo_valor").set_value("masajeador de piernas").run()
    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception

    tipo = _producto(tmp_path, lote_id, pid)["campos"]["campos"]["tipo"]
    assert tipo["valor"] == "masajeador de piernas"
    assert tipo["fuente"] == "diego"


# ============================================================================
# CAMPOS OBLIGATORIOS (Fase 3, 2026-07-17): "no deje confirmar ficha hasta
# que no se rellene". `categoria`/`estado`/`titulo`/`descripcion` bloquean
# duro -- el botón se deshabilita (UX) Y `_accion_confirmar_ficha` bloquea
# de verdad aunque se pulse igual (`decision-making.md` §12: un botón
# deshabilitado del lado del cliente NO es una defensa por sí solo --
# comprobado ejecutando: `AppTest` SÍ puede pulsar un botón `disabled=True`).
# ============================================================================
def _ficha_de_version_vieja_sin_categoria(crops: Path) -> ResultadoExtraccion:
    """La ficha REAL de Diego (`eea6b292`): extraída ANTES de que `categoria`
    existiera, así que su dict de campos NO tiene esa clave."""
    resultado = _ficha_completa(crops)
    campos = {c: v for c, v in resultado.campos.items() if c != "categoria"}
    propuestas = {c: v for c, v in resultado.propuestas.items() if c != "categoria"}
    return ResultadoExtraccion(campos=campos, propuestas=propuestas)


def test_obligatorio_que_la_extraccion_no_produjo_se_pinta_igual(tmp_path):
    """EL CALLEJÓN SIN SALIDA que cazó Diego (2026-07-17) con su producto
    `eea6b292`: se extrajo con una versión anterior de la app, sin
    `categoria`. La pantalla sólo pintaba los campos PRESENTES en la
    extracción → la categoría no se pintaba, pero seguía siendo obligatoria
    → "obligatorio, falta: categoría" y NINGÚN sitio donde rellenarla. La
    ficha quedaba imposible de confirmar para siempre.

    Un gate que bloquea por un campo que no se puede rellenar no es una
    defensa con dientes: es una puerta cerrada con la llave dentro."""
    lote_id, pid = _preparar(tmp_path, _ficha_de_version_vieja_sin_categoria)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    # El selectbox EXISTE aunque la extracción no produjo el campo.
    selector = next(
        (s for s in at.selectbox if s.key == f"ficha_{pid}_categoria_categoria"), None
    )
    assert selector is not None, (
        "la categoría es obligatoria y la extracción no la produjo: si no se "
        "pinta, la ficha es IMPOSIBLE de confirmar (callejón sin salida)"
    )
    assert selector.value == "(sin elegir)"
    assert at.button(key=f"confirmar_{pid}").proto.disabled is True


def test_obligatorio_ausente_se_puede_rellenar_y_se_persiste(tmp_path):
    """La otra mitad: no basta con PINTARLO -- lo que Diego teclee tiene que
    llegar al store. Si `_construir_confirmado` iterase sólo los campos de la
    extracción, el valor se pintaría y se perdería en silencio."""
    lote_id, pid = _preparar(tmp_path, _ficha_de_version_vieja_sin_categoria)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()

    next(
        s for s in at.selectbox if s.key == f"ficha_{pid}_categoria_categoria"
    ).set_value("electronica").run()
    assert at.button(key=f"confirmar_{pid}").proto.disabled is False

    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception

    store = LoteStore(data_dir=tmp_path)
    producto = next(
        p for p in store.cargar_lote(lote_id)["productos"] if p["id"] == pid
    )
    assert producto["campos"]["confirmada"] is True
    assert producto["campos"]["campos"]["categoria"]["valor"] == "electronica"
    assert producto["campos"]["campos"]["categoria"]["fuente"] == "diego"


def test_categoria_sin_elegir_bloquea_confirmar(tmp_path):
    """DECISIÓN NUEVA DE DIEGO (revierte `test_categoria_sin_elegir_se_
    confirma_como_null`, que existía antes de que `categoria` fuera
    obligatoria): dejarla en "(sin elegir)" ya NO produce un null
    confirmado -- BLOQUEA, y no se persiste nada."""
    lote_id, pid = _preparar(tmp_path, _ficha_completa)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert at.button(key=f"confirmar_{pid}").proto.disabled is False  # categoría="electronica" ya propuesta

    next(
        s for s in at.selectbox if s.key == f"ficha_{pid}_categoria_categoria"
    ).set_value("(sin elegir)").run()
    assert at.button(key=f"confirmar_{pid}").proto.disabled is True

    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception
    assert len(at.error) >= 1
    assert any("categoría" in e.value.lower() for e in at.error)

    estado = LoteStore(data_dir=tmp_path).cargar_lote(lote_id)
    fichas = [c for c in estado["confirmaciones"] if c["tipo"] == "ficha" and c["producto_id"] == pid]
    assert not fichas, "no debió confirmarse sin categoría"
    # el valor viejo (confirmado o no) sigue intacto -- nada a medias
    categoria = _producto(tmp_path, lote_id, pid)["campos"]["campos"]["categoria"]
    assert categoria["valor"] == "electronica"


def test_estado_sin_elegir_bloquea_confirmar(tmp_path):
    """`estado` es el ejemplo explícito de Diego ("como el estado")."""
    lote_id, pid = _preparar(tmp_path, _ficha_completa)  # estado="Como nuevo" ya viene propuesto
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    next(
        s for s in at.selectbox if s.key == f"ficha_{pid}_estado_estado"
    ).set_value("(sin elegir)").run()
    assert at.button(key=f"confirmar_{pid}").proto.disabled is True

    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception
    assert any("estado" in e.value.lower() for e in at.error)

    estado_lote = LoteStore(data_dir=tmp_path).cargar_lote(lote_id)
    fichas = [c for c in estado_lote["confirmaciones"] if c["tipo"] == "ficha" and c["producto_id"] == pid]
    assert not fichas, "no debió confirmarse sin estado"


def test_todos_los_obligatorios_llenos_confirma_bien(tmp_path):
    """El caso positivo, explícito: `_ficha_completa` ya trae categoría,
    estado, título y descripción -- el botón sale HABILITADO y confirmar
    persiste sin ningún error."""
    lote_id, pid = _preparar(tmp_path, _ficha_completa)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert at.button(key=f"confirmar_{pid}").proto.disabled is False
    assert not any(c.value.startswith("⚠️ obligatorio") for c in at.caption)

    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception
    assert len(at.error) == 0

    estado = LoteStore(data_dir=tmp_path).cargar_lote(lote_id)
    fichas = [c for c in estado["confirmaciones"] if c["tipo"] == "ficha" and c["producto_id"] == pid]
    assert len(fichas) == 1


# ============================================================================
# Hallazgos del listing-audit ya corregidos.
# ============================================================================
def test_badge_no_miente_tras_reextraer(tmp_path):
    """Re-extraer (guardar_extraccion) sobre una ficha confirmada la deja SIN
    la marca `confirmada` → `_ficha_confirmada` pasa a False. El badge deriva
    del contenido de `campos`, no de `confirmaciones` (append-only, mentiría).
    `[INC-008]`: mostrar ≠ defender."""
    lote_id, pid = _preparar(tmp_path, _marca_leida_no_publicada_lista_para_confirmar)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    at = _elegir_estado_y_categoria(at, pid)
    at.button(key=f"confirmar_{pid}").click().run()
    assert _ficha_confirmada(_producto(tmp_path, lote_id, pid)) is True

    # Simula el re-extract: sobreescribe campos con una extracción cruda nueva.
    store = LoteStore(data_dir=tmp_path)
    store.guardar_extraccion(pid, serializar_extraccion(_marca_leida_no_publicada(tmp_path / "crops2")))
    assert _ficha_confirmada(_producto(tmp_path, lote_id, pid)) is False


def test_aviso_coherencia_topa_confianza_a_media(tmp_path):
    """Con `aviso_coherencia` (Frankenstein), un valor confirmado NUNCA sube a
    `alta` — el aviso baja el techo con dientes (`[INC-011]`/§12)."""
    lote_id, pid = _preparar(tmp_path, _con_aviso_coherencia_lista_para_confirmar)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    at = _elegir_estado_y_categoria(at, pid)
    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception

    marca = _producto(tmp_path, lote_id, pid)["campos"]["campos"]["marca"]
    assert marca["valor"] == "Reebok" and marca["confianza"] == "media"


# ============================================================================
# Un producto sin la agrupación confirmada NO llega a la ficha ([INC-011]).
# ============================================================================
def test_sin_agrupacion_confirmada_no_se_muestra_ficha(tmp_path):
    lote_id, pid = _preparar(tmp_path, _marca_leida_no_publicada, confirmar_agrupacion=False)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    assert not any(t.key == f"ficha_{pid}_marca_valor" for t in at.text_input)
    textos = " ".join(m.value for m in at.info)
    assert "agrupación" in textos.lower()


def test_confirmar_bloquea_marca_ajena_en_la_descripcion(tmp_path):
    """Sanitizador con dientes (product.md §7, §12): una descripción que
    menciona OTRA marca (Nike, sobre un producto lufthous) NO se puede
    confirmar — en Vinted `MENTIONS_OTHER_BRAND` oculta el anuncio. Ahora
    que titulo/descripcion sólo son editables DESPUÉS de confirmar, el
    ataque se prueba en la segunda vuelta ("Volver a confirmar")."""
    lote_id, pid = _preparar(tmp_path, _ficha_completa)  # marca = lufthous
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception
    assert _ficha_confirmada(_producto(tmp_path, lote_id, pid)) is True

    key_desc = f"ficha_{pid}_descripcion_valor"
    next(a for a in at.text_area if a.key == key_desc).set_value(
        "Masajeador Nike de rodilla, como nuevo."
    ).run()
    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception

    estado = LoteStore(data_dir=tmp_path).cargar_lote(lote_id)
    fichas = [c for c in estado["confirmaciones"] if c["tipo"] == "ficha" and c["producto_id"] == pid]
    assert len(fichas) == 1, "sólo la PRIMERA confirmación (texto limpio) debió persistirse"
    assert len(at.error) >= 1


def test_texto_limpio_si_confirma(tmp_path):
    """El caso positivo: título/descripción sin marca ajena ni email/enlace
    confirman sin problema (no falso positivo del sanitizador)."""
    lote_id, pid = _preparar(tmp_path, _ficha_completa)  # descripción limpia
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception
    estado = LoteStore(data_dir=tmp_path).cargar_lote(lote_id)
    fichas = [c for c in estado["confirmaciones"] if c["tipo"] == "ficha" and c["producto_id"] == pid]
    assert len(fichas) == 1


def test_badge_de_fuente_distingue_leido_de_inferido(tmp_path):
    """La UI muestra la FUENTE (hallazgo del audit): un inferido no puede
    verse igual de confirmable que un leído en foto."""
    lote_id, _pid = _preparar(tmp_path, _ficha_completa)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    textos = " ".join(m.value for m in at.markdown)
    assert "leído en foto" in textos  # modelo/ean son fuente=foto
    assert "inferido" in textos       # marca lufthous es inferido


# ============================================================================
# "composicion" ELIMINADA DE LA FICHA (Diego, 2026-07-17: "solo es en la
# ropa y no es realmente importante"). `core/extract.py` ya no la produce
# -- aquí se prueba que `ui/ficha.py` tampoco la pinta, ni siquiera si una
# extracción VIEJA (persistida antes del cambio) todavía trae la clave.
# ============================================================================
def test_composicion_no_se_pinta_en_una_extraccion_fresca(tmp_path):
    lote_id, pid = _preparar(tmp_path, _ficha_completa)  # ya no incluye "composicion"
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    assert not any(t.key == f"ficha_{pid}_composicion_valor" for t in at.text_input)
    textos = " ".join(m.value for m in at.markdown)
    assert "**composicion**" not in textos


def test_composicion_de_una_extraccion_vieja_degrada_sin_petar(tmp_path):
    """Una extracción PERSISTIDA antes del cambio (2026-07-14/15) podía
    traer "composicion" en `campos` -- la pantalla no debe reventar al
    abrirla; simplemente ya no la trata de forma especial (cae al final
    por el fallback de `orden`, `_render_producto`)."""
    def _con_composicion_vieja(crops: Path) -> ResultadoExtraccion:
        base = _ficha_completa(crops)
        campos = dict(base.campos)
        propuestas = dict(base.propuestas)
        campos["composicion"] = Campo(valor=None, fuente="inferido", confianza="baja")
        propuestas["composicion"] = Propuesta(
            campo="composicion", valor=None, recorte=None, evidencia=None, motivo="viejo, ya no se produce",
        )
        return ResultadoExtraccion(
            campos=campos, propuestas=propuestas, fallos=base.fallos, coste_usd=base.coste_usd
        )

    lote_id, pid = _preparar(tmp_path, _con_composicion_vieja)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception


# ============================================================================
# GALERÍA MINIMIZADA (Diego, 2026-07-17: "que todo ocupe mucho menos" —
# foto principal siempre visible, el resto escondido en un expander cerrado).
# ============================================================================
def test_galeria_foto_principal_visible_resto_en_expander_cerrado(tmp_path):
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote galería", "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id
    foto_ids = []
    for i in range(3):
        ruta = carpeta / f"IMG_{i}.jpg"
        _crear_img(ruta, (10 * i, 20, 30))
        (fid,) = store.añadir_fotos(lote_id, [Foto(ruta=str(ruta), hash=f"hash_g{i}")])
        foto_ids.append(fid)
    (pid,) = store.guardar_agrupacion(lote_id, [foto_ids])
    store.confirmar_producto(pid)
    store.guardar_extraccion(pid, serializar_extraccion(_ficha_completa(tmp_path / "crops_g")))

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    # 1 expander, cerrado por defecto, con las 3 fotos en el título.
    expanders = [e for e in at.expander if "foto" in e.label.lower()]
    assert len(expanders) == 1
    assert "3" in expanders[0].label
    assert expanders[0].proto.expanded is False

    # la foto principal se ve FUERA del expander (no está entre sus hijos).
    imagenes_dentro = len(expanders[0].get("imgs"))
    imagenes_totales = len(at.get("imgs"))
    assert imagenes_totales > imagenes_dentro  # al menos la principal, fuera


# ============================================================================
# RECORTES COMO MINIATURA + CANDIDATAS EN EXPANDER CERRADO (Diego,
# 2026-07-17: "mínimo scroll posible" -- el recorte junto a CADA campo, y
# sobre todo las hasta 9 candidatas de un conflicto, ocupaban media pantalla
# apiladas siempre desplegadas). LÍMITE DURO que se comprueba aquí: el
# recorte principal SIGUE PINTÁNDOSE (nunca desaparece, `truth-loop.md`
# §A), y ninguna candidata se pierde -- sólo dejan de estorbar.
# ============================================================================
def test_recorte_principal_de_un_campo_se_sigue_pintando(tmp_path):
    """`_marca_leida_no_publicada` trae recortes REALES para marca/talla
    (sin candidatas) -- deben seguir pintándose como miniatura, no
    desaparecer."""
    lote_id, _pid = _preparar(tmp_path, _marca_leida_no_publicada)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    # al menos: la foto principal de la galería + los 2 recortes (marca, talla)
    assert len(at.get("imgs")) >= 3


def test_candidatas_alternativas_en_expander_cerrado(tmp_path):
    lote_id, pid = _preparar(tmp_path, _conflicto_dos_marcas)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    expanders = [e for e in at.expander if "candidata" in e.label.lower()]
    assert len(expanders) == 1
    assert "2" in expanders[0].label
    assert expanders[0].proto.expanded is False

    # ninguna candidata se pierde: los dos botones siguen ahí (aunque el
    # expander esté cerrado, AppTest ve el árbol completo del script).
    labels = " ".join(b.label for b in at.button)
    assert "UMBRO" in labels and "RAMI JALAB" in labels
    # y sus recortes (2 candidatas) siguen pintándose dentro del expander.
    assert len(expanders[0].get("imgs")) >= 2

    # el campo sigue sin pre-elegir ninguna.
    assert _texto_input(at, f"ficha_{pid}_marca_valor").value == ""


# ============================================================================
# BADGE DE OBLIGATORIO (Diego, 2026-07-17: "que se distingan bien del
# resto, no solo con (obligatorio) delante"). Rojo cuando está vacío (el que
# BLOQUEA confirmar), naranja cuando ya tiene valor -- mismo predicado que
# deshabilita el botón (`_campo_esta_vacio_en_pantalla`, un solo sitio).
# ============================================================================
def test_badge_obligatorio_rojo_cuando_esta_vacio(tmp_path):
    lote_id, pid = _preparar(tmp_path, _marca_leida_no_publicada)  # sin categoria/titulo/descripcion
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    textos = " ".join(m.value for m in at.markdown)
    assert "OBLIGATORIO" in textos  # el rojo, para "estado" (vacío)


def test_badge_obligatorio_naranja_cuando_tiene_valor(tmp_path):
    lote_id, _pid = _preparar(tmp_path, _ficha_completa)  # categoria/estado/titulo/descripcion ya propuestos
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    textos = " ".join(m.value for m in at.markdown)
    assert ":orange-badge[obligatorio]" in textos
    assert "OBLIGATORIO — falta" not in textos  # nada obligatorio vacío en esta ficha


def test_badge_obligatorio_no_se_pinta_en_campo_no_obligatorio(tmp_path):
    """`marca` NO es obligatoria (`_CAMPOS_OBLIGATORIOS` la excluye a
    propósito) -- su línea no debe llevar el badge."""
    lote_id, _pid = _preparar(tmp_path, _ficha_completa)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    linea_marca = next(m.value for m in at.markdown if m.value.startswith("**marca**"))
    assert "obligatorio" not in linea_marca.lower()


def test_obligatorio_vacio_sigue_bloqueando_confirmar(tmp_path):
    """El bloqueo REAL (con dientes) no cambia con el rediseño visual: un
    obligatorio vacío sigue impidiendo confirmar, botón deshabilitado Y
    `_accion_confirmar_ficha` bloquea si se pulsa igual."""
    lote_id, pid = _preparar(tmp_path, _ficha_completa)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    next(
        s for s in at.selectbox if s.key == f"ficha_{pid}_categoria_categoria"
    ).set_value("(sin elegir)").run()
    assert at.button(key=f"confirmar_{pid}").proto.disabled is True

    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception
    assert len(at.error) >= 1
    estado = LoteStore(data_dir=tmp_path).cargar_lote(lote_id)
    fichas = [c for c in estado["confirmaciones"] if c["tipo"] == "ficha" and c["producto_id"] == pid]
    assert not fichas


def test_valor_pegado_de_extraccion_vieja_se_refresca(tmp_path):
    """EL BUG QUE VIO DIEGO: extrajo con código viejo (marca=null), la key
    quedó pegada en "" en session_state, y al re-extraer (marca=lufthous)
    Streamlit ignoraba el value= nuevo -> input vacío teniendo el dato.
    La siembra por firma lo refresca sin reiniciar la app."""
    lote_id, pid = _preparar(tmp_path, _ficha_completa)  # marca=lufthous
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id))
    at.session_state[f"ficha_{pid}_marca_valor"] = ""  # valor pegado, sin marcador de firma
    at.run()
    assert not at.exception
    assert _texto_input(at, f"ficha_{pid}_marca_valor").value == "lufthous"


# ============================================================================
# EL GC DE NAVEGACIÓN ([INC-014]/[INC-021], hallazgo de Diego 2026-07-21):
# navegar Ficha -> Export -> Ficha vía `st.sidebar.radio` (`app.py`) deja de
# ejecutar `ficha.render()` mientras Diego está en Export -- Streamlit hace
# GC de toda `key` de WIDGET que no se instancia en un run. El marcador
# `_ficha_firma_{pid}` es una key NORMAL (nunca `key=` de un widget) y
# SOBREVIVE al GC; con el `return` temprano de antes, la firma idéntica
# hacía saltar la re-siembra -> los widgets nacían vacíos al volver, aunque
# el disco (y Export) siguieran mostrando el dato intacto.
# ============================================================================
def test_sembrar_valores_iniciales_re_siembra_tras_gc_de_widget():
    """Unitario del invariante (`_sembrar_valores_iniciales` sola, sin
    `AppTest`): siembra, simula el GC borrando SÓLO las keys de widget
    (deja el marcador `_ficha_firma_*` vivo, que es justo lo que hace
    Streamlit) y re-invoca con la MISMA firma -- las keys de widget deben
    volver a poblarse con los valores del disco. Antes del fix (`return`
    temprano si la firma no cambió) esta segunda siembra era un no-op y
    las keys seguían ausentes."""
    pid = "prod1"
    # `_valor_por_defecto` sólo mira `.get("valor")`/`.get("propuesta")` --
    # dicts planos (no dataclasses `Campo`) son exactamente lo que trae
    # `deserializar_extraccion` + `_con_obligatorios` en el flujo real, y
    # basta para ejercitar la siembra.
    campos = {
        "categoria": {"valor": "moda", "fuente": "inferido", "confianza": "baja"},
        "tipo": {"valor": "masajeador de rodilla", "fuente": "inferido", "confianza": "baja"},
        "titulo": {"valor": "Sudadera Reebok talla M", "fuente": "inferido", "confianza": "baja"},
    }

    # Un test unitario "puro" fuera de un script run de Streamlit no tiene
    # `st.session_state` disponible como dict mutable -- se salta a favor
    # del `AppTest` de navegación real de abajo, que es el que manda
    # (`_sembrar_valores_iniciales` es la primera función de este módulo
    # que se prueba AISLADA usando `st.session_state` de verdad).
    import pytest
    try:
        st.session_state.clear()
    except Exception:
        pytest.skip("st.session_state no es accesible fuera de un ScriptRunContext")

    ficha._sembrar_valores_iniciales(pid, campos)
    assert st.session_state[f"ficha_{pid}_categoria_categoria"] == "moda"
    assert st.session_state[f"ficha_{pid}_tipo_valor"] == "masajeador de rodilla"
    assert st.session_state[f"ficha_{pid}_titulo_valor"] == "Sudadera Reebok talla M"

    # Simula el GC: se van las keys de WIDGET, el marcador de firma sobrevive.
    del st.session_state[f"ficha_{pid}_categoria_categoria"]
    del st.session_state[f"ficha_{pid}_tipo_valor"]
    del st.session_state[f"ficha_{pid}_titulo_valor"]
    assert f"_ficha_firma_{pid}" in st.session_state

    # Re-invoca con la MISMA firma (mismos `campos`) -- antes del fix, el
    # `return` temprano dejaba las keys ausentes.
    ficha._sembrar_valores_iniciales(pid, campos)
    assert st.session_state[f"ficha_{pid}_categoria_categoria"] == "moda"
    assert st.session_state[f"ficha_{pid}_tipo_valor"] == "masajeador de rodilla"
    assert st.session_state[f"ficha_{pid}_titulo_valor"] == "Sudadera Reebok talla M"


def test_sembrar_valores_iniciales_no_pisa_edicion_sin_navegar():
    """Caso (b) del docstring: Diego edita una key SIN que la firma cambie
    (no navegó, no re-extrajo) -- la re-siembra NO debe pisar su edición."""
    pid = "prod1"
    campos = {"tipo": {"valor": "masajeador de rodilla", "fuente": "inferido", "confianza": "baja"}}

    import pytest
    try:
        st.session_state.clear()
    except Exception:
        pytest.skip("st.session_state no es accesible fuera de un ScriptRunContext")

    ficha._sembrar_valores_iniciales(pid, campos)
    st.session_state[f"ficha_{pid}_tipo_valor"] = "sudadera (editado por Diego)"

    ficha._sembrar_valores_iniciales(pid, campos)  # misma firma, key presente
    assert st.session_state[f"ficha_{pid}_tipo_valor"] == "sudadera (editado por Diego)"


def _script_navegacion(data_dir: str, lote_id: str) -> None:
    """Reproduce el MECANISMO real de `app.py::main` (`st.sidebar.radio`
    con `key="sb_pantalla"` decidiendo qué módulo de `ui/` renderiza este
    script run) sin arrastrar las dependencias de `ui/export.py`
    (pricing/categorías/API) -- lo único que importa para el GC es que,
    en el run donde Diego está en "Export", `ficha.render()` NO se
    ejecuta EN ABSOLUTO, exactamente igual que en la app real."""
    from pathlib import Path as _Path

    import streamlit as st
    from core.llm import ResultadoLLM as _ResultadoLLM
    from core.store import LoteStore as _LoteStore
    from ui import ficha as _ficha

    class _MotorTextoFake:
        def consultar_texto(self, prompt, json_schema, version_prompt=None, producto_id=None):
            return _ResultadoLLM(
                datos={"titulo": "t", "descripcion": "d"}, fuente="api",
                coste_usd=0.0, tokens_entrada=1, tokens_salida=1,
            )

    pantalla = st.sidebar.radio("Pantalla", ["3. Ficha", "4. Export"], key="sb_pantalla")
    store = _LoteStore(data_dir=_Path(data_dir))
    if pantalla == "3. Ficha":
        _ficha.render(store, lote_id, crear_motor=lambda: _MotorTextoFake())
    else:
        st.write("(pantalla Export -- placeholder, no depende de ficha.render)")


def test_navegar_a_export_y_volver_no_vacia_la_ficha(tmp_path):
    """AppTest de navegación real: el `bug-hunter` confirmó que `AppTest`
    SÍ reproduce el GC. Ficha con tipo/categoria POBLADOS -> navega a
    Export -> vuelve a Ficha -> los dos campos deben seguir poblados
    (antes del fix: salían vacíos, aunque el disco -y Export- estuvieran
    intactos). Se comprueban `tipo` (text_input) y `categoria` (selectbox)
    -- ambos son keys de WIDGET de verdad (sujetas al GC de Streamlit);
    `titulo`/`descripcion` NO lo son mientras la ficha no está confirmada
    (`_render_campo` las pinta con `st.info`, sin `key=`, ver su
    docstring), así que no reproducen este bug y no hace falta afirmarlas
    aquí."""
    lote_id, pid = _preparar(tmp_path, _ficha_con_tipo)
    at = AppTest.from_function(_script_navegacion, args=(str(tmp_path), lote_id))
    at.run(timeout=10)
    assert not at.exception

    assert _texto_input(at, f"ficha_{pid}_tipo_valor").value == "masajeador de rodilla"
    assert (
        next(s for s in at.selectbox if s.key == f"ficha_{pid}_categoria_categoria").value
        == "moda"
    )

    # Navega a Export -- `ficha.render()` NO corre en este run.
    at.sidebar.radio(key="sb_pantalla").set_value("4. Export").run(timeout=10)
    assert not at.exception

    # Vuelve a Ficha -- MISMA firma (el disco no cambió).
    at.sidebar.radio(key="sb_pantalla").set_value("3. Ficha").run(timeout=10)
    assert not at.exception

    assert _texto_input(at, f"ficha_{pid}_tipo_valor").value == "masajeador de rodilla", (
        "el tipo se vació al volver de Export -- el GC de navegación no se refrescó"
    )
    assert (
        next(s for s in at.selectbox if s.key == f"ficha_{pid}_categoria_categoria").value
        == "moda"
    ), "la categoría se vació al volver de Export"


# ============================================================================
# EXTRACCIÓN DE TODO EL LOTE (Fase 3, 2026-07-17) — pedido urgente de Diego:
# "no quiero rellenar las fichas manualmente". Antes: 2 clics + 2 esperas POR
# PRODUCTO (14 clics para su lote de 7). Ahora: 1 clic + 1 confirmación de
# coste PARA TODO EL LOTE.
#
# `_accion_extraer_lote` es PURA (nunca llama a `st.*`) — mismo límite que
# `test_curar.py` documenta para `_accion_dividir_grupo`/`_cerrar_costura`:
# `AppTest` no puede clicar un botón DENTRO de un `@st.dialog` que ya está
# abierto (fuerza un rerun completo y el diálogo no reabre solo), así que la
# MUTACIÓN se prueba llamando la función directamente — es exactamente lo
# que el botón "💸 Extraer los N productos ahora" invoca. Lo que SÍ ve
# `AppTest` es que el botón de fuera aparece/desaparece con el conteo
# correcto (`decision-making.md` §16: el caso de FALLO se ejecuta, no se lee).
# ============================================================================
class _ExtractorFake:
    """Extractor de mentira para estos tests: no toca OCR ni la API real.
    `falla_en` son los `producto_id` que deben REVENTAR — el caso de fallo
    que `decision-making.md` §16 exige ejecutar, no sólo leer."""

    def __init__(self, motor, carpeta_crops, *, falla_en: frozenset[str] = frozenset()):
        self.carpeta_crops = carpeta_crops
        self.falla_en = falla_en

    def construir_solicitudes(self, fotos):
        return []

    def extraer_producto(self, fotos, categoria="moda", producto_id=None, carpeta_crops=None):
        if producto_id in self.falla_en:
            raise RuntimeError(f"fallo simulado en {producto_id}")
        valor = f"Marca-{(producto_id or '')[:6]}"
        campos = {"marca": Campo(valor=valor, fuente="inferido", confianza="baja")}
        propuestas = {
            "marca": Propuesta(
                campo="marca", valor=valor, recorte=None, evidencia=None, motivo="test masivo"
            ),
        }
        return ResultadoExtraccion(campos=campos, propuestas=propuestas, fallos=(), coste_usd=0.0)


def _preparar_n_sin_extraer(tmp_path: Path, n: int) -> tuple[str, list[str]]:
    """`n` productos, cada uno con 1 foto, agrupación CONFIRMADA (Fase 1),
    SIN extraer todavía (`campos == {}`, el estado real tras `guardar_agrupacion`)."""
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote masivo", "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id
    foto_ids = []
    for i in range(n):
        ruta = carpeta / f"IMG_{i}.jpg"
        _crear_img(ruta, (10 * i % 255, 20, 30))
        (foto_id,) = store.añadir_fotos(lote_id, [Foto(ruta=str(ruta), hash=f"hash_{i}")])
        foto_ids.append(foto_id)
    pids = store.guardar_agrupacion(lote_id, [[fid] for fid in foto_ids])
    for pid in pids:
        store.confirmar_producto(pid)
    return lote_id, pids


def _fotos_por_id(tmp_path: Path, lote_id: str) -> dict[str, dict]:
    estado = LoteStore(data_dir=tmp_path).cargar_lote(lote_id)
    return {f["id"]: f for f in estado["fotos"]}


def _productos_de(tmp_path: Path, lote_id: str) -> list[dict]:
    estado = LoteStore(data_dir=tmp_path).cargar_lote(lote_id)
    return estado["productos"]


def test_boton_extraer_lote_aparece_y_dice_n(tmp_path):
    lote_id, _pids = _preparar_n_sin_extraer(tmp_path, 6)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    labels = [b.label for b in at.button]
    assert any("Extraer TODO el lote" in lbl and "6" in lbl for lbl in labels)


def test_boton_extraer_lote_no_aparece_si_ya_todo_extraido(tmp_path):
    lote_id, _pid = _preparar(tmp_path, _marca_leida_no_publicada)  # ya extraído
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    labels = [b.label for b in at.button]
    assert not any("Extraer TODO el lote" in lbl for lbl in labels)


def test_accion_extraer_lote_persiste_los_n_productos(tmp_path):
    """`ui.ficha._accion_extraer_lote` es exactamente lo que llama el botón
    "💸 Extraer los N productos ahora" — se prueba directo (ver docstring
    de esta sección)."""
    lote_id, pids = _preparar_n_sin_extraer(tmp_path, 6)
    store = LoteStore(data_dir=tmp_path)
    fotos_por_id = _fotos_por_id(tmp_path, lote_id)
    productos = _productos_de(tmp_path, lote_id)

    ok, fallos = ficha._accion_extraer_lote(
        store, lote_id, productos, fotos_por_id, None,
        lambda motor, crops: _ExtractorFake(motor, crops),
    )
    assert fallos == []
    assert set(ok) == set(pids)

    productos_tras = _productos_de(tmp_path, lote_id)
    extraidos = [p for p in productos_tras if ficha._esta_extraido(p)]
    assert len(extraidos) == 6
    for p in extraidos:
        assert p["campos"]["campos"]["marca"]["valor"].startswith("Marca-")


def test_accion_extraer_lote_un_producto_revienta_los_demas_se_persisten(tmp_path):
    """EL TEST QUE IMPORTA (§16): un producto revienta A MITAD del lote ->
    los otros 5 SE PERSISTEN igual (dinero ya gastado, no se pierde), y el
    fallo sale NOMBRADO con su producto_id y el motivo real — nunca un
    `except: pass`, nunca un producto que se salta en silencio."""
    lote_id, pids = _preparar_n_sin_extraer(tmp_path, 6)
    pid_que_revienta = pids[3]
    store = LoteStore(data_dir=tmp_path)
    fotos_por_id = _fotos_por_id(tmp_path, lote_id)
    productos = _productos_de(tmp_path, lote_id)

    ok, fallos = ficha._accion_extraer_lote(
        store, lote_id, productos, fotos_por_id, None,
        lambda motor, crops: _ExtractorFake(motor, crops, falla_en=frozenset({pid_que_revienta})),
    )

    assert len(ok) == 5
    assert pid_que_revienta not in ok
    assert len(fallos) == 1
    fallo_pid, motivo = fallos[0]
    assert fallo_pid == pid_que_revienta
    assert "fallo simulado" in motivo

    productos_tras = _productos_de(tmp_path, lote_id)
    extraidos = {p["id"] for p in productos_tras if ficha._esta_extraido(p)}
    assert extraidos == set(ok)
    assert pid_que_revienta not in extraidos


def test_valores_nuevos_se_pintan_tras_extraccion_masiva(tmp_path):
    """`[INC-014]` (Streamlit ignora `value=` si la key ya está en
    `session_state`): tras la extracción MASIVA, el campo muestra el valor
    NUEVO — no tapado por una key vieja pegada de un render anterior.
    Verificado EJECUTANDO, no leído del docstring de `_sembrar_valores_iniciales`."""
    lote_id, pids = _preparar_n_sin_extraer(tmp_path, 2)
    store = LoteStore(data_dir=tmp_path)
    fotos_por_id = _fotos_por_id(tmp_path, lote_id)
    productos = _productos_de(tmp_path, lote_id)
    ok, fallos = ficha._accion_extraer_lote(
        store, lote_id, productos, fotos_por_id, None,
        lambda motor, crops: _ExtractorFake(motor, crops),
    )
    assert fallos == []
    pid = pids[0]

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id))
    at.session_state[f"ficha_{pid}_marca_valor"] = ""  # valor pegado, sin marcador de firma
    at.run()
    assert not at.exception
    assert _texto_input(at, f"ficha_{pid}_marca_valor").value.startswith("Marca-")


# ============================================================================
# CONFIRMAR TODAS LAS FICHAS LISTAS DE GOLPE (Fase 3, pedido de Diego:
# "menos clics"). CONTRATO EXACTO (decidido por él cuando se le preguntó):
# SIN MENTIR SOBRE LA PROCEDENCIA -- un campo que Diego NO tocó mantiene su
# `fuente`/`confianza` ORIGINALES (`foto`/`inferido`); sólo lo que sí tocó
# pasa a `fuente="diego"`. `_accion_confirmar_lote`/`_productos_listos_y_
# saltados`/`_solicitudes_redaccion_pendientes` son PURAS (nunca llaman a
# `st.*` salvo leer `st.session_state`, que funciona en modo bare -- ver
# docstring de `_construir_confirmado`) -- se prueban DIRECTAS, sin
# `AppTest`, mismo límite que `_accion_extraer_lote` (un botón dentro de un
# `@st.dialog` ya abierto no es alcanzable por `AppTest`).
# ============================================================================
def _preparar_multi(
    tmp_path: Path, fixtures: list[Callable[[Path], ResultadoExtraccion]]
) -> tuple[str, list[str]]:
    """`len(fixtures)` productos en el MISMO lote, cada uno con 1 foto,
    agrupación confirmada (Fase 1) y su propia extracción ya guardada."""
    store = LoteStore(data_dir=tmp_path)
    lote_id = store.crear_lote("Lote multi", "C:/fotos/origen")
    carpeta = store.lotes_dir / lote_id
    pids: list[str] = []
    for i, fixture in enumerate(fixtures):
        ruta = carpeta / f"IMG_multi_{i}.jpg"
        _crear_img(ruta, (10 * i % 255, 50, 80))
        (foto_id,) = store.añadir_fotos(lote_id, [Foto(ruta=str(ruta), hash=f"hash_multi_{i}")])
        (producto_id,) = store.guardar_agrupacion(lote_id, [[foto_id]])
        store.confirmar_producto(producto_id)
        resultado = fixture(tmp_path / f"crops_multi_{i}")
        store.guardar_extraccion(producto_id, serializar_extraccion(resultado))
        pids.append(producto_id)
    return lote_id, pids


class _MotorTextoFakeConFallos:
    """Como `_MotorTextoFake`, pero puede fallar SÓLO para los
    `producto_id` en `falla_en` -- el caso de fallo que
    `decision-making.md` §16 exige EJECUTAR, no sólo leer."""

    def __init__(self, falla_en: frozenset[str] = frozenset()) -> None:
        self.falla_en = falla_en
        self.prompts: list[str] = []

    def consultar_texto(self, prompt, json_schema, version_prompt="v1", producto_id=None):
        self.prompts.append(prompt)
        if producto_id in self.falla_en:
            raise LLMLlamadaFallidaError(f"fallo simulado de redacción en {producto_id}")
        return ResultadoLLM(
            datos={"titulo": "Título IA", "descripcion": "Descripción IA"},
            fuente="api", coste_usd=0.00005, tokens_entrada=120, tokens_salida=60,
        )


def test_bulk_campo_no_tocado_mantiene_fuente_original(tmp_path):
    """EL TEST QUE IMPORTA: `marca` es `fuente="inferido"`, `modelo`/`ean`
    son `fuente="foto"` -- Diego NUNCA abrió esta ficha (no hay nada en
    `session_state` para este `pid`). Tras confirmar en bloque, NINGUNO de
    los tres debe pasar a `fuente="diego"` -- se lee del STORE EN DISCO,
    no de `session_state`."""
    lote_id, pid = _preparar(tmp_path, _ficha_completa)
    store = LoteStore(data_dir=tmp_path)
    producto = _producto(tmp_path, lote_id, pid)

    ok, fallos = ficha._accion_confirmar_lote(store, [producto], _MotorTextoFake())
    assert fallos == []
    assert ok == [pid]

    campos = _producto(tmp_path, lote_id, pid)["campos"]["campos"]
    assert campos["marca"]["valor"] == "lufthous"
    assert campos["marca"]["fuente"] == "inferido"  # NUNCA "diego": no lo tocó
    assert campos["modelo"]["fuente"] == "foto"
    assert campos["ean"]["fuente"] == "foto"
    assert campos["estado"]["fuente"] == "inferido"
    assert campos["categoria"]["fuente"] == "inferido"
    # la ficha SÍ queda confirmada -- Diego aceptó los valores, aunque no
    # haya revisado cada píxel uno a uno.
    assert _producto(tmp_path, lote_id, pid)["campos"]["confirmada"] is True


def test_bulk_campo_tocado_por_diego_queda_fuente_diego(tmp_path):
    """Si Diego SÍ editó un campo (a mano, o con un botón "usar «X»") antes
    de pulsar "confirmar todas", ESE campo sí pasa a `fuente="diego"` --
    aunque el confirm sea en bloque."""
    lote_id, pid = _preparar(tmp_path, _ficha_completa)
    store = LoteStore(data_dir=tmp_path)
    st.session_state[f"ficha_{pid}_marca_valor"] = "Lufthous Pro"  # Diego lo tecleó
    producto = _producto(tmp_path, lote_id, pid)

    ok, fallos = ficha._accion_confirmar_lote(store, [producto], _MotorTextoFake())
    assert fallos == []
    assert ok == [pid]

    campos = _producto(tmp_path, lote_id, pid)["campos"]["campos"]
    assert campos["marca"]["valor"] == "Lufthous Pro"
    assert campos["marca"]["fuente"] == "diego"
    # lo que NO tocó sigue sin ser suyo:
    assert campos["modelo"]["fuente"] == "foto"


def test_productos_listos_y_saltados_separa_lo_incompleto(tmp_path):
    """`_marca_leida_no_publicada_lista_para_confirmar` deja "estado" sin
    elegir a propósito -- falta un obligatorio, así que se SALTA y se
    nombra qué le falta."""
    lote_id, pid = _preparar(tmp_path, _marca_leida_no_publicada_lista_para_confirmar)
    producto = _producto(tmp_path, lote_id, pid)

    listos, saltados = ficha._productos_listos_y_saltados([producto])
    assert listos == []
    assert len(saltados) == 1
    saltado_pid, faltan = saltados[0]
    assert saltado_pid == pid
    assert any("estado" in f for f in faltan)


def test_bulk_confirma_listos_salta_incompletos_y_los_nombra(tmp_path):
    """3 fichas listas + 1 incompleta -> se confirman 3, se salta 1
    NOMBRADA (nunca en silencio, `decision-making.md` §13)."""
    fixtures = [
        _ficha_completa, _ficha_completa, _ficha_completa,
        _marca_leida_no_publicada_lista_para_confirmar,
    ]
    lote_id, pids = _preparar_multi(tmp_path, fixtures)
    store = LoteStore(data_dir=tmp_path)
    productos = _productos_de(tmp_path, lote_id)

    listos, saltados = ficha._productos_listos_y_saltados(productos)
    assert {p["id"] for p in listos} == set(pids[:3])
    assert len(saltados) == 1
    assert saltados[0][0] == pids[3]

    ok, fallos = ficha._accion_confirmar_lote(store, listos, _MotorTextoFake())
    assert set(ok) == set(pids[:3])
    assert fallos == []

    confirmadas = {p["id"] for p in _productos_de(tmp_path, lote_id) if ficha._ficha_confirmada(p)}
    assert confirmadas == set(pids[:3])
    assert pids[3] not in confirmadas


def test_bulk_una_redaccion_falla_las_demas_se_confirman_igual(tmp_path):
    """EL TEST QUE IMPORTA (§16): la redacción de UNA ficha revienta a
    mitad del lote -> las otras SE CONFIRMAN igual (dinero ya gastado no se
    pierde), y el fallo sale NOMBRADO con su `producto_id` y el motivo
    real -- nunca un `except: pass`."""
    fixtures = [_ficha_completa, _ficha_completa, _ficha_completa]
    lote_id, pids = _preparar_multi(tmp_path, fixtures)
    store = LoteStore(data_dir=tmp_path)
    productos = _productos_de(tmp_path, lote_id)
    listos, saltados = ficha._productos_listos_y_saltados(productos)
    assert saltados == []

    pid_que_falla = pids[1]
    motor = _MotorTextoFakeConFallos(falla_en=frozenset({pid_que_falla}))
    ok, fallos = ficha._accion_confirmar_lote(store, listos, motor)

    assert set(ok) == set(pids) - {pid_que_falla}
    assert len(fallos) == 1
    fallo_pid, motivo = fallos[0]
    assert fallo_pid == pid_que_falla
    assert "fallo simulado" in motivo

    confirmadas = {p["id"] for p in _productos_de(tmp_path, lote_id) if ficha._ficha_confirmada(p)}
    assert pid_que_falla not in confirmadas
    assert len(confirmadas) == 2


def test_solicitudes_redaccion_pendientes_cuenta_lo_que_hace_falta(tmp_path):
    """El diálogo tiene que poder ENSEÑAR EL COSTE de las N redacciones
    ANTES de lanzarlas (§15) -- esto es lo que alimenta
    `motor.estimar_coste_texto_lote` dentro de `_dialog_confirmar_lote`."""
    fixtures = [_ficha_completa, _ficha_completa]
    lote_id, _pids = _preparar_multi(tmp_path, fixtures)
    productos = _productos_de(tmp_path, lote_id)
    listos, _saltados = ficha._productos_listos_y_saltados(productos)

    solicitudes = ficha._solicitudes_redaccion_pendientes(listos)
    assert len(solicitudes) == 2  # nadie tocó título/descripción -> las dos hacen falta

    motor = LLMEngine(cache_dir=tmp_path / "cache_test")
    estimacion = motor.estimar_coste_texto_lote(solicitudes)
    assert estimacion.n_llamadas_total == 2
    assert estimacion.n_a_pagar == 2  # nada en caché todavía
    assert estimacion.coste_usd_estimado > 0


def test_solicitudes_redaccion_pendientes_omite_texto_ya_editado(tmp_path):
    """Si Diego YA escribió su propio título Y descripción antes de pulsar
    "confirmar todas", esa ficha no necesita redacción -- cero coste."""
    lote_id, pid = _preparar(tmp_path, _ficha_completa)
    st.session_state[f"ficha_{pid}_titulo_valor"] = "Mi propio título"
    st.session_state[f"ficha_{pid}_descripcion_valor"] = "Mi propia descripción, tal cual."
    producto = _producto(tmp_path, lote_id, pid)

    solicitudes = ficha._solicitudes_redaccion_pendientes([producto])
    assert solicitudes == []


def test_boton_confirmar_lote_aparece_con_n_listos_y_avisa_de_saltados(tmp_path):
    fixtures = [_ficha_completa, _ficha_completa, _marca_leida_no_publicada_lista_para_confirmar]
    lote_id, _pids = _preparar_multi(tmp_path, fixtures)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    labels = [b.label for b in at.button]
    assert any("Confirmar todas las fichas listas" in lbl and "2" in lbl for lbl in labels)
    textos = " ".join(c.value for c in at.caption)
    assert "1 ficha" in textos


def test_contador_del_boton_no_lo_decide_una_key_rancia(tmp_path):
    """EL BUG QUE CAZÓ DIEGO (2026-07-17): el botón decía "(1)" con las 7
    fichas completas EN DISCO. El código estaba bien -- una sesión limpia
    cuenta bien. Fallaba el ORDEN: `_sembrar_valores_iniciales` vive dentro
    de `_render_producto`, o sea DESPUÉS del botón, así que el contador leía
    el `session_state` del render ANTERIOR (extracción vieja: estado en
    prosa -> "(sin elegir)") mientras la pantalla de abajo ya pintaba los
    valores nuevos. El botón iba un render por detrás.

    Éste es el test que ningún otro podía dar: todos arrancan `session_state`
    LIMPIO, y con la key ausente `_construir_confirmado` cae al default y
    acierta. El bug sólo existe con la key PRESENTE y RANCIA (`[INC-014]`:
    "un test que siempre arranca en estado limpio no puede ver un bug de
    estado sucio acumulado")."""
    lote_id, pids = _preparar_multi(tmp_path, [_ficha_completa, _ficha_completa])
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id))

    # Estado sucio de una extracción ANTERIOR, como el navegador de Diego:
    # el estado salía en prosa, así que el selectbox se quedó "(sin elegir)".
    for pid in pids:
        at.session_state[f"ficha_{pid}_estado_estado"] = "(sin elegir)"
    at.run()
    assert not at.exception

    labels = [b.label for b in at.button if "Confirmar todas" in (b.label or "")]
    assert labels, "el botón de confirmar en bloque no se pintó"
    assert "(2)" in labels[0], (
        f"el contador leyó una key rancia en vez del dato refrescado: {labels[0]!r}. "
        "Sembrar tiene que ocurrir ANTES de contar, o el botón va un render por detrás."
    )


def test_boton_confirmar_lote_deshabilitado_si_ninguno_listo(tmp_path):
    """Extraído pero SIN obligatorios (`_marca_leida_no_publicada`, sin
    categoría/título/descripción) -- el botón se ENSEÑA (nunca se
    esconde), pero deshabilitado con "0"."""
    lote_id, _pid = _preparar(tmp_path, _marca_leida_no_publicada)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    boton = next(b for b in at.button if "Confirmar todas las fichas listas" in b.label)
    assert boton.proto.disabled is True
    assert "(0)" in boton.label


def test_boton_confirmar_lote_no_aparece_sin_nada_extraido(tmp_path):
    lote_id, _pids = _preparar_n_sin_extraer(tmp_path, 3)  # nada extraído todavía
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    labels = [b.label for b in at.button]
    assert not any("Confirmar todas las fichas listas" in lbl for lbl in labels)


def _multiselect(at: AppTest, key: str):
    return next(m for m in at.multiselect if m.key == key)


def _script_reextraer(data_dir: str, lote_id: str, *, falla_en: tuple[str, ...] = ()) -> None:
    """Variante de `_script` para abrir `_dialog_reextraer_seleccionados` de
    verdad: usa un `LLMEngine` REAL (`estimar_coste_lote` no llama a la red
    ni necesita API key -- sólo mira la caché en disco, ver
    `test_solicitudes_redaccion_pendientes_cuenta_lo_que_hace_falta`) junto a
    un `ExtractorEngine` fake (`construir_solicitudes` -> `[]`, cero OCR
    real). `_MotorTextoFake` (usado por `_script`) NO implementa
    `estimar_coste_lote` -- por eso este script existe aparte: la puerta de
    coste de la extracción en bloque necesita ese método de verdad. Mismo
    motivo que `_script` para definir todo DENTRO de la función
    (`AppTest.from_function` ejecuta el CÓDIGO FUENTE en un módulo aislado,
    no puede cerrar sobre nombres externos)."""
    from pathlib import Path as _Path

    from core.extract import Propuesta as _Propuesta
    from core.extract import ResultadoExtraccion as _ResultadoExtraccion
    from core.llm import LLMEngine as _LLMEngine
    from core.schema import Campo as _Campo
    from core.store import LoteStore as _LoteStore
    from ui import ficha as _ficha

    class _ExtractorFakeLocal:
        def __init__(self, motor, carpeta_crops):
            self.carpeta_crops = carpeta_crops

        def construir_solicitudes(self, fotos):
            return []

        def extraer_producto(self, fotos, categoria="moda", producto_id=None, carpeta_crops=None):
            if producto_id in falla_en:
                raise RuntimeError(f"fallo simulado en {producto_id}")
            valor = f"Re-{(producto_id or '')[:6]}"
            campos = {"marca": _Campo(valor=valor, fuente="inferido", confianza="baja")}
            propuestas = {
                "marca": _Propuesta(
                    campo="marca", valor=valor, recorte=None, evidencia=None, motivo="reextraido"
                ),
            }
            return _ResultadoExtraccion(campos=campos, propuestas=propuestas, fallos=(), coste_usd=0.0)

    motor = _LLMEngine(cache_dir=_Path(data_dir) / "cache_reextraer")
    _ficha.render(
        _LoteStore(data_dir=_Path(data_dir)),
        lote_id,
        crear_motor=lambda: motor,
        crear_extractor=lambda m, crops: _ExtractorFakeLocal(m, crops),
    )


# ============================================================================
# RE-EXTRAER LOS SELECCIONADOS (Fase 3, 2026-07-17) -- pedido explícito de
# Diego: "un botón para poder reextraer las que quiera a la vez... por si un
# día hay alguno que quiere hacerse a mano o hay que reextraer X". Reusa
# `_accion_extraer_lote` (sin cambios: ya era genérica) y la puerta de coste
# compartida con "Extraer TODO el lote". LO ÚNICO NUEVO: EL AVISO CON
# DIENTES cuando la selección incluye una ficha YA CONFIRMADA (perder horas
# de curado de un clic, `CLAUDE.md` LO QUE NUNCA).
# ============================================================================
def test_multiselect_reextraer_aparece_con_etiquetas_identificables(tmp_path):
    """Mezcla: 1 sin extraer, 1 extraída, 1 confirmada -- las tres deben
    poder identificarse SIN adivinar (id + detalle + nº fotos + confirmada)."""
    fixtures = [_ficha_completa, _ficha_completa]
    lote_id, pids = _preparar_multi(tmp_path, fixtures)
    store = LoteStore(data_dir=tmp_path)
    # un tercer producto SIN extraer, mismo lote
    carpeta = store.lotes_dir / lote_id
    ruta = carpeta / "IMG_sin_extraer.jpg"
    _crear_img(ruta, (5, 5, 5))
    (foto_id,) = store.añadir_fotos(lote_id, [Foto(ruta=str(ruta), hash="hash_sin_extraer")])
    (pid_sin_extraer,) = store.guardar_agrupacion(lote_id, [[foto_id]])
    store.confirmar_producto(pid_sin_extraer)

    pid_confirmado = pids[0]
    producto_confirmado = _producto(tmp_path, lote_id, pid_confirmado)
    confirmado = dict(producto_confirmado["campos"])
    confirmado["confirmada"] = True
    store.confirmar_ficha(pid_confirmado, confirmado)

    at = AppTest.from_function(_script_reextraer, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    ms = _multiselect(at, "ficha_multiselect_reextraer")
    assert ms.value == []  # vacío por defecto -- nunca pre-seleccionado

    etiquetas = " ".join(ms.options)
    assert pid_sin_extraer[:8] in etiquetas and "sin extraer" in etiquetas
    assert pid_confirmado[:8] in etiquetas and "confirmada" in etiquetas
    assert pids[1][:8] in etiquetas and "lufthous" in etiquetas  # marca del fixture


def test_reextraer_seleccionados_solo_toca_los_elegidos(tmp_path):
    """Seleccionar 2 de 3 -> `_accion_extraer_lote` (la MISMA función que
    invoca el botón "💸 Re-extraer... ahora") sólo toca esos 2; el tercero
    conserva sus campos VIEJOS intactos en el store."""
    fixtures = [_ficha_completa, _ficha_completa, _ficha_completa]
    lote_id, pids = _preparar_multi(tmp_path, fixtures)
    store = LoteStore(data_dir=tmp_path)
    todos = _productos_de(tmp_path, lote_id)
    seleccionados = [p for p in todos if p["id"] in (pids[0], pids[2])]
    valor_viejo_no_tocado = _producto(tmp_path, lote_id, pids[1])["campos"]["campos"]["marca"]["valor"]

    ok, fallos = ficha._accion_extraer_lote(
        store, lote_id, seleccionados, _fotos_por_id(tmp_path, lote_id), None,
        lambda motor, crops: _ExtractorFake(motor, crops),
    )
    assert fallos == []
    assert set(ok) == {pids[0], pids[2]}

    assert _producto(tmp_path, lote_id, pids[0])["campos"]["campos"]["marca"]["valor"].startswith("Marca-")
    assert _producto(tmp_path, lote_id, pids[2])["campos"]["campos"]["marca"]["valor"].startswith("Marca-")
    # EL NO SELECCIONADO NO SE TOCA: mismo valor de antes, tal cual.
    assert _producto(tmp_path, lote_id, pids[1])["campos"]["campos"]["marca"]["valor"] == valor_viejo_no_tocado


def test_confirmadas_entre_detecta_las_confirmadas(tmp_path):
    fixtures = [_ficha_completa, _ficha_completa]
    lote_id, pids = _preparar_multi(tmp_path, fixtures)
    store = LoteStore(data_dir=tmp_path)
    producto_confirmado = _producto(tmp_path, lote_id, pids[0])
    confirmado = dict(producto_confirmado["campos"])
    confirmado["confirmada"] = True
    store.confirmar_ficha(pids[0], confirmado)

    productos = _productos_de(tmp_path, lote_id)
    assert ficha._confirmadas_entre(productos) == [pids[0]]

    # ninguna confirmada -> lista vacía, sin fricción
    lote_id2, pids2 = _preparar_multi(tmp_path, [_ficha_completa])
    assert ficha._confirmadas_entre(_productos_de(tmp_path, lote_id2)) == []


def test_boton_reextraer_seleccionados_deshabilitado_sin_seleccion(tmp_path):
    fixtures = [_ficha_completa, _ficha_completa]
    lote_id, _pids = _preparar_multi(tmp_path, fixtures)
    at = AppTest.from_function(_script_reextraer, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    boton = next(b for b in at.button if b.key == "btn_reextraer_seleccionados")
    assert boton.proto.disabled is True
    assert "(0)" in boton.label


def test_dialog_reextraer_con_confirmada_nombra_y_no_habilita_boton_sin_checkbox(tmp_path):
    """EL TEST QUE IMPORTA: la selección incluye una ficha YA CONFIRMADA ->
    el diálogo la NOMBRA en el aviso y el botón de gasto real NO se habilita
    hasta marcar la casilla -- sin marcarla, la puerta bloquea (§12: una
    defensa que sólo avisa no es defensa)."""
    fixtures = [_ficha_completa, _ficha_completa, _ficha_completa]
    lote_id, pids = _preparar_multi(tmp_path, fixtures)
    store = LoteStore(data_dir=tmp_path)
    pid_confirmado = pids[1]
    producto = _producto(tmp_path, lote_id, pid_confirmado)
    confirmado = dict(producto["campos"])
    confirmado["confirmada"] = True
    store.confirmar_ficha(pid_confirmado, confirmado)

    at = AppTest.from_function(_script_reextraer, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    ms = _multiselect(at, "ficha_multiselect_reextraer")
    at = ms.select(pid_confirmado).run()
    assert not at.exception

    boton_abrir = next(b for b in at.button if b.key == "btn_reextraer_seleccionados")
    at = boton_abrir.click().run()
    assert not at.exception, f"abrir el diálogo de re-extraer lanzó: {at.exception}"

    textos_aviso = " ".join(w.value for w in at.warning)
    assert pid_confirmado[:8] in textos_aviso
    assert "CONFIRMADAS" in textos_aviso

    checkbox = next(c for c in at.checkbox if "descarta mis valores confirmados" in c.label)
    assert checkbox.value is False  # sin marcar por defecto -- nunca pre-aceptado

    boton_gasto = next(b for b in at.button if b.key == "btn_reextraer_gasto")
    assert boton_gasto.proto.disabled is True  # NO habilitado sin la casilla


def test_dialog_reextraer_sin_confirmadas_sin_friccion(tmp_path):
    """Ninguna de las seleccionadas está confirmada -> sin aviso, sin
    casilla, el botón de gasto sale habilitado directo."""
    fixtures = [_ficha_completa, _ficha_completa]
    lote_id, pids = _preparar_multi(tmp_path, fixtures)

    at = AppTest.from_function(_script_reextraer, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    ms = _multiselect(at, "ficha_multiselect_reextraer")
    at = ms.select(pids[0]).run()
    boton_abrir = next(b for b in at.button if b.key == "btn_reextraer_seleccionados")
    at = boton_abrir.click().run()
    assert not at.exception

    assert len(at.warning) == 0
    assert not any("descarta mis valores confirmados" in c.label for c in at.checkbox)

    boton_gasto = next(b for b in at.button if b.key == "btn_reextraer_gasto")
    assert boton_gasto.proto.disabled is False


def test_accion_extraer_lote_seleccionados_un_producto_revienta_los_demas_se_persisten(tmp_path):
    """MISMO caso de fallo que la extracción masiva (§16), pero sobre una
    selección PARCIAL: el que revienta no se guarda, los demás SELECCIONADOS
    sí, y el que NO se seleccionó ni se toca ni se reporta."""
    fixtures = [_ficha_completa, _ficha_completa, _ficha_completa]
    lote_id, pids = _preparar_multi(tmp_path, fixtures)
    store = LoteStore(data_dir=tmp_path)
    todos = _productos_de(tmp_path, lote_id)
    seleccionados = [p for p in todos if p["id"] in (pids[0], pids[1])]
    pid_que_revienta = pids[0]

    ok, fallos = ficha._accion_extraer_lote(
        store, lote_id, seleccionados, _fotos_por_id(tmp_path, lote_id), None,
        lambda motor, crops: _ExtractorFake(motor, crops, falla_en=frozenset({pid_que_revienta})),
    )
    assert ok == [pids[1]]
    assert len(fallos) == 1
    assert fallos[0][0] == pid_que_revienta
    assert "fallo simulado" in fallos[0][1]

    # el que NUNCA se seleccionó conserva su marca original ("lufthous", de
    # `_ficha_completa`), sin tocar.
    assert _producto(tmp_path, lote_id, pids[2])["campos"]["campos"]["marca"]["valor"] == "lufthous"


def test_valores_nuevos_tras_reextraccion_seleccionada_se_pintan(tmp_path):
    """`[INC-014]` sobre el camino de "seleccionados": tras re-extraer sólo
    ESE producto, la pantalla pinta el valor NUEVO -- no uno viejo pegado en
    `session_state`. Verificado EJECUTANDO, no leído de un docstring."""
    fixtures = [_ficha_completa, _ficha_completa]
    lote_id, pids = _preparar_multi(tmp_path, fixtures)
    store = LoteStore(data_dir=tmp_path)
    producto = _producto(tmp_path, lote_id, pids[0])

    ok, fallos = ficha._accion_extraer_lote(
        store, lote_id, [producto], _fotos_por_id(tmp_path, lote_id), None,
        lambda motor, crops: _ExtractorFake(motor, crops),
    )
    assert fallos == []
    assert ok == [pids[0]]

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id))
    at.session_state[f"ficha_{pids[0]}_marca_valor"] = "valor viejo pegado"
    at.run()
    assert not at.exception
    assert _texto_input(at, f"ficha_{pids[0]}_marca_valor").value.startswith("Marca-")


def test_boton_confirmar_lote_no_aparece_si_ya_todo_confirmado(tmp_path):
    """Producto YA confirmado (persistido directo en el store, SIN pasar
    por un click dentro de `AppTest`): `AppTest` no limpia su cola de
    mensajes entre un `st.rerun()` interno y el run que lo disparó (a
    diferencia de la app real, cuyo `AppSession` sí lo hace) -- combinar
    "click que confirma" + "un run() más" en el MISMO test dispara ese
    límite del arnés de pruebas, no un bug de producto (mismo patrón que
    documenta la sección de extracción-en-lote más arriba: la mutación se
    verifica llamando la función directa; aquí, en cambio, se puede
    reproducir con `AppTest` con tal de que el estado YA venga confirmado
    ANTES del primer `.run()`, sin un click de por medio)."""
    lote_id, pid = _preparar(tmp_path, _ficha_completa)
    store = LoteStore(data_dir=tmp_path)
    producto = _producto(tmp_path, lote_id, pid)
    confirmado = dict(producto["campos"])
    confirmado["confirmada"] = True
    store.confirmar_ficha(pid, confirmado)

    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    labels = [b.label for b in at.button]
    assert not any("Confirmar todas las fichas listas" in lbl for lbl in labels)


# ============================================================================
# FALLO DE SÍNTESIS (2026-07-21, hallazgo de Diego: "se han borrado los
# datos"). Causa real: un 429 en `core/extract.py::_sintetizar_ficha` deja
# categoria/titulo/descripcion/tipo SIN producir y `estado.valor=None` --
# nada se borró, la síntesis nunca llegó a escribirlos. Dos bugs de UI:
# (A) el aviso se enterraba en un `st.caption` gris indistinguible de
# cualquier otro fallo técnico; (B) el motivo por-campo de cada obligatorio
# ausente mentía "esta ficha se extrajo con una versión anterior de la
# app" -- FALSO cuando la causa es un 429 reciente, no una ficha vieja.
#
# El discriminante es el prefijo EXACTO `"vlm_sintesis:"`
# (`fallos.append(f"vlm_sintesis: {exc}")`, `core/extract.py`) por
# `startswith` -- por diseño NO matchea `limite_llamadas_vlm_alcanzado:...`
# (el backstop de coste de los crops -- OTRO fallo que coexiste en cajas
# pero no es éste) ni `sintesis:sin_fotos_abribles` / `sintesis_categoria_
# fuera_de_enum` / `sintesis_estado_fuera_de_enum` (empiezan por "sintesis"
# a secas, no por "vlm_sintesis:" -- y es correcto que queden fuera:
# re-extraer no arregla "sin fotos abribles").
# ============================================================================
def _ficha_sintesis_fallida(crops: Path) -> ResultadoExtraccion:
    """Un 429 real en la síntesis: marca/talla SÍ se leyeron (los crops de
    OCR/VLM son una fase previa e independiente de la síntesis), pero
    categoria/titulo/descripcion NUNCA se produjeron. `fallos` lleva el
    prefijo EXACTO que anota `core/extract.py`."""
    base = _marca_leida_no_publicada(crops)
    return ResultadoExtraccion(
        campos=base.campos, propuestas=base.propuestas,
        fallos=("vlm_sintesis: limite de tasa excedido: 429 rate_limit_error",),
        coste_usd=base.coste_usd,
    )


def _ficha_con_fallo_no_sintesis(crops: Path) -> ResultadoExtraccion:
    """Fallos técnicos que NO son de síntesis: el backstop de coste de los
    crops (`limite_llamadas_vlm_alcanzado`) y un crop concreto que reventó
    (`vlm_crop:`). Ninguno de los dos debe disparar el aviso prominente de
    síntesis -- son fallos DISTINTOS, y re-extraer no repara lo mismo."""
    base = _marca_leida_no_publicada(crops)
    return ResultadoExtraccion(
        campos=base.campos, propuestas=base.propuestas,
        fallos=(
            "limite_llamadas_vlm_alcanzado:20:21",
            "vlm_crop:IMG_1.jpg:(0, 0, 10, 10): timeout",
        ),
        coste_usd=base.coste_usd,
    )


def test_fallo_sintesis_dispara_aviso_prominente_y_motivo_veraz(tmp_path):
    """EL CASO QUE IMPORTA: un `vlm_sintesis:` real -- aviso PROMINENTE
    (no un caption gris) y el motivo por-campo de los obligatorios ausentes
    dice la VERDAD (fallo técnico / re-extraer, coste ~0), nunca "versión
    anterior de la app" (sería falso: la ficha es de HOY)."""
    lote_id, pid = _preparar(tmp_path, _ficha_sintesis_fallida)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    textos_warning = " ".join(w.value for w in at.warning)
    assert "síntesis" in textos_warning.lower()
    assert "no se ha borrado nada" in textos_warning.lower()
    assert "re-extrae" in textos_warning.lower()

    captions = " ".join(c.value for c in at.caption)
    assert "saturación" in captions.lower(), (
        "el motivo por-campo del obligatorio ausente debe explicar el fallo "
        "técnico, no inventar una excusa distinta"
    )
    assert "versión anterior de la app" not in captions.lower(), (
        "con un fallo vlm_sintesis: reciente, el motivo NO puede decir que "
        "la ficha es de una versión anterior -- es FALSO, la causa es el 429"
    )


def test_fallo_no_sintesis_no_dispara_aviso_de_sintesis(tmp_path):
    """`limite_llamadas_vlm_alcanzado` y `vlm_crop:` NO son un fallo de
    síntesis -- el discriminante debe ser el prefijo EXACTO
    `"vlm_sintesis:"`, no una subcadena floja como "sintesis" o "vlm"."""
    lote_id, pid = _preparar(tmp_path, _ficha_con_fallo_no_sintesis)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    textos_warning = " ".join(w.value for w in at.warning)
    assert "síntesis" not in textos_warning.lower()

    # Sin un fallo vlm_sintesis:, el motivo del obligatorio ausente sigue
    # siendo el genérico de "versión anterior" -- no hay regresión.
    captions = " ".join(c.value for c in at.caption)
    assert "versión anterior de la app" in captions.lower()


def test_ficha_vieja_de_verdad_sin_fallo_sintesis_mantiene_mensaje_version_anterior(tmp_path):
    """La ficha REAL de Diego (`eea6b292`, sin fallos técnicos, extraída
    antes de que `categoria` existiera): SIN ningún fallo `vlm_sintesis:`,
    el mensaje sigue siendo EXACTAMENTE "versión anterior de la app" -- no
    regresión del comportamiento legítimo para fichas viejas de verdad."""
    lote_id, pid = _preparar(tmp_path, _ficha_de_version_vieja_sin_categoria)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception

    textos_warning = " ".join(w.value for w in at.warning)
    assert "síntesis" not in textos_warning.lower()

    captions = " ".join(c.value for c in at.caption)
    assert "versión anterior de la app" in captions.lower()
