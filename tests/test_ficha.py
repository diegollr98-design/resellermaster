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


def test_sin_recorte_no_se_prerellena_ningun_valor(tmp_path):
    """EL PÍXEL CON DIENTES (hallazgo del listing-audit, §16): si el recorte
    NO existe en disco, NO se pre-rellena el valor — el default seguro es
    null, no un valor plausible confirmable a ciegas."""
    lote_id, pid = _preparar(tmp_path, lambda c: _marca_leida_no_publicada(c, con_crops=False))
    at = AppTest.from_function(_script, args=(str(tmp_path), lote_id)).run()
    assert not at.exception
    assert _texto_input(at, f"ficha_{pid}_marca_valor").value == ""
    assert _texto_input(at, f"ficha_{pid}_talla_valor").value == ""


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
