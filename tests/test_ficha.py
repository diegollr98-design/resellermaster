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
from core.schema import Campo, Evidencia
from core.store import Foto, LoteStore
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


def _script(data_dir: str, lote_id: str) -> None:
    from pathlib import Path as _Path

    from core.store import LoteStore as _LoteStore
    from ui import ficha as _ficha

    # crear_motor=lambda: None -> no se construye un LLMEngine en un render que
    # sólo revisa: el motor sólo se usa dentro del diálogo de extracción.
    _ficha.render(_LoteStore(data_dir=_Path(data_dir)), lote_id, crear_motor=lambda: None)


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
# Confirmación: persiste con fuente='diego', append-only.
# ============================================================================
def test_confirmar_persiste_con_fuente_diego(tmp_path):
    lote_id, pid = _preparar(tmp_path, _marca_leida_no_publicada)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception

    campos = _producto(tmp_path, lote_id, pid)["campos"]["campos"]
    assert campos["marca"]["valor"] == "Reebok" and campos["marca"]["fuente"] == "diego"
    assert campos["talla"]["valor"] == "M" and campos["talla"]["fuente"] == "diego"
    estado = LoteStore(data_dir=tmp_path).cargar_lote(lote_id)
    fichas = [c for c in estado["confirmaciones"] if c["tipo"] == "ficha" and c["producto_id"] == pid]
    assert len(fichas) == 1


def test_campo_vacio_se_confirma_como_null(tmp_path):
    lote_id, pid = _preparar(tmp_path, _marca_leida_no_publicada)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    _texto_input(at, f"ficha_{pid}_marca_valor").set_value("").run()
    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception

    marca = _producto(tmp_path, lote_id, pid)["campos"]["campos"]["marca"]
    assert marca["valor"] is None and marca["fuente"] == "diego"


def test_confirmar_ficha_es_append_only(tmp_path):
    lote_id, pid = _preparar(tmp_path, _marca_leida_no_publicada)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
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


def test_categoria_sin_elegir_se_confirma_como_null(tmp_path):
    lote_id, pid = _preparar(tmp_path, _ficha_completa)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    next(
        s for s in at.selectbox if s.key == f"ficha_{pid}_categoria_categoria"
    ).set_value("(sin elegir)").run()
    at.button(key=f"confirmar_{pid}").click().run()
    assert not at.exception

    categoria = _producto(tmp_path, lote_id, pid)["campos"]["campos"]["categoria"]
    assert categoria["valor"] is None
    assert categoria["fuente"] == "diego"


# ============================================================================
# Hallazgos del listing-audit ya corregidos.
# ============================================================================
def test_badge_no_miente_tras_reextraer(tmp_path):
    """Re-extraer (guardar_extraccion) sobre una ficha confirmada la deja SIN
    la marca `confirmada` → `_ficha_confirmada` pasa a False. El badge deriva
    del contenido de `campos`, no de `confirmaciones` (append-only, mentiría).
    `[INC-008]`: mostrar ≠ defender."""
    lote_id, pid = _preparar(tmp_path, _marca_leida_no_publicada)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    at.button(key=f"confirmar_{pid}").click().run()
    assert _ficha_confirmada(_producto(tmp_path, lote_id, pid)) is True

    # Simula el re-extract: sobreescribe campos con una extracción cruda nueva.
    store = LoteStore(data_dir=tmp_path)
    store.guardar_extraccion(pid, serializar_extraccion(_marca_leida_no_publicada(tmp_path / "crops2")))
    assert _ficha_confirmada(_producto(tmp_path, lote_id, pid)) is False


def test_aviso_coherencia_topa_confianza_a_media(tmp_path):
    """Con `aviso_coherencia` (Frankenstein), un valor confirmado NUNCA sube a
    `alta` — el aviso baja el techo con dientes (`[INC-011]`/§12)."""
    lote_id, pid = _preparar(tmp_path, _con_aviso_coherencia)
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
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
