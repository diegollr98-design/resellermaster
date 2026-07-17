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

from PIL import Image
from streamlit.testing.v1 import AppTest

from core.extract import (
    Candidato,
    Lectura,
    Propuesta,
    ResultadoExtraccion,
    serializar_extraccion,
)
from core.llm import LLMLlamadaFallidaError, ResultadoLLM
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


def test_titulo_y_descripcion_son_cajas_editables(tmp_path):
    lote_id, pid = _preparar(tmp_path, _ficha_completa)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    areas = {a.key: a.value for a in at.text_area}
    assert areas.get(f"ficha_{pid}_titulo_valor", "").startswith("Masajeador")
    assert "rodilla" in areas.get(f"ficha_{pid}_descripcion_valor", "")


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
    lote_id, pid = _preparar(tmp_path, _ficha_con_desperfecto)
    at = AppTest.from_function(
        _script,
        args=(str(tmp_path), lote_id),
        kwargs={
            "titulo_generado": "no deberia usarse este titulo",
            "descripcion_generada": "no deberia usarse esta descripcion",
        },
    ).run()
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


def test_categoria_ausente_no_pinta_selectbox_ni_revienta(tmp_path):
    """`_marca_leida_no_publicada` no incluye 'categoria' en `campos` (el
    modelo violó el enum, o el test simplemente no la configuró) — la
    pantalla no debe reventar, y no debe inventarse un selectbox para un
    campo que no existe."""
    lote_id, pid = _preparar(tmp_path, _marca_leida_no_publicada)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    assert not any(s.key == f"ficha_{pid}_categoria_categoria" for s in at.selectbox)


def test_categoria_confirmar_deja_fuente_diego(tmp_path):
    lote_id, pid = _preparar(tmp_path, _ficha_completa)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception

    categoria = _producto(tmp_path, lote_id, pid)["campos"]["campos"]["categoria"]
    assert categoria["valor"] == "electronica"
    assert categoria["fuente"] == "diego"


# ============================================================================
# CAMPOS OBLIGATORIOS (Fase 3, 2026-07-17): "no deje confirmar ficha hasta
# que no se rellene". `categoria`/`estado`/`titulo`/`descripcion` bloquean
# duro -- el botón se deshabilita (UX) Y `_accion_confirmar_ficha` bloquea
# de verdad aunque se pulse igual (`decision-making.md` §12: un botón
# deshabilitado del lado del cliente NO es una defensa por sí solo --
# comprobado ejecutando: `AppTest` SÍ puede pulsar un botón `disabled=True`).
# ============================================================================
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
    confirmar — en Vinted `MENTIONS_OTHER_BRAND` oculta el anuncio."""
    lote_id, pid = _preparar(tmp_path, _ficha_completa)  # marca = lufthous
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    key_desc = f"ficha_{pid}_descripcion_valor"
    next(a for a in at.text_area if a.key == key_desc).set_value(
        "Masajeador Nike de rodilla, como nuevo."
    ).run()
    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception

    estado = LoteStore(data_dir=tmp_path).cargar_lote(lote_id)
    fichas = [c for c in estado["confirmaciones"] if c["tipo"] == "ficha" and c["producto_id"] == pid]
    assert not fichas, "no debió confirmarse con una marca ajena en el texto"
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
