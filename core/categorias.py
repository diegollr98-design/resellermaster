"""Candidatas de HOJA de categoria para Wallapop/Vinted -- reduce el trabajo
de Diego de "navegar 859/2482 hojas a mano" a "elegir entre 2-3 con 1 clic",
sin auto-decidir NUNCA la hoja (esa es la linea de `decision-making.md` §18 y
`truth-loop.md`: una hoja mal = anuncio OCULTO = venta perdida silenciosa, el
modo de fallo caro del proyecto).

QUE HACE Y QUE NO
------------------------------------------------------------------------
- SI: dado `(categoria_amplia, texto)` -- donde `texto` es el titulo +
  descripcion + marca que Diego YA confirmo -- rankea las hojas del subarbol
  relevante de cada plataforma por solapamiento de palabras y devuelve las
  `k` mejores CON SU RUTA COMPLETA, para que Diego elija.
- NO: elegir una sola hoja y meterla en `catalog_id`/`categoria`. La maquina
  PROPONE y ENSENA; Diego cierra. Si ninguna candidata encaja, el fallback
  es navegar el arbol a mano en la plataforma -- exactamente lo de hoy, nunca
  peor.

De donde salen los arboles (AMBOS PUBLICOS Y GRATIS, `product.md` corregido
2026-07-17):
- Wallapop: `GET api.wallapop.com/api/v3/categories` (200 sin auth,
  `Accept-Language: es_ES`).
- Vinted: SSR de `www.vinted.es` (`catalogTree` embebido; SIN token -- solo
  la API Pro de ontologias exige token, no el arbol).

Los arboles VARIAN (Vinted esta versionado por pais+divisa+fecha). Se
versionan como snapshot en `core/taxonomia/*.json` y se refrescan con
`python -m core.categorias --refrescar` (job MANUAL, no en caliente: el SSR
de Vinted esta tras Datadome; a 1 fetch ocasional es despreciable). El
snapshot lleva su fecha de descarga.

Coste: 0 EUR. Sin red en el camino caliente (solo `--refrescar` toca la red);
sin LLM (es solapamiento de palabras, no un modelo). No toca `core/extract.py`
-- deriva del titulo/descripcion YA compuestos, asi que no anade riesgo a la
superficie mas sensible del repo.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from core.schema import CategoriaTipo

_DIR_TAXONOMIA = Path(__file__).parent / "taxonomia"

Plataforma = str  # "wallapop" | "vinted" -- ver core.schema.Plataforma


# ============================================================================
# Mapa: categoria interna AMPLIA -> raices del subarbol de cada plataforma.
# Restringir el subarbol es lo que hace utiles las candidatas: buscar
# "sudadera" en TODO Wallapop mezcla ropa con juguetes; buscar solo bajo
# "Moda y accesorios" no. Las raices se identifican por id (estable), nunca
# por nombre (traducible). `None` = sin restriccion (todas las hojas).
# ============================================================================

_RAICES: dict[Plataforma, dict[CategoriaTipo, tuple[int, ...] | None]] = {
    "wallapop": {
        "moda": (12465,),  # Moda y accesorios
        "electronica": (24200,),  # Tecnologia y electronica
        "hogar": (12467, 13100),  # Hogar y jardin + Electrodomesticos
        "libros": (12463,),  # Cine, libros y musica
        "otros": None,
    },
    "vinted": {
        # Mujer, Hombre, Moda de diseno. "Ninos" (1193) NO va aqui de base:
        # metia "Camisas de niña" como candidata de una camiseta de HOMBRE (el
        # caso comun de Diego). Diego SI vende infantil, asi que se anade
        # CONDICIONALMENTE (`_RAICES_INFANTIL`) solo cuando el texto lo senala
        # (niño/bebe/meses/años) -- ver `candidatas`. Asi el query adulto sale
        # limpio y el infantil saca infantil.
        "moda": (1904, 5, 2993),
        "electronica": (2994,),  # Electronica
        "hogar": (1918,),  # Hogar
        "libros": (2309,),  # Entretenimiento
        "otros": None,
    },
}

# Raices de ropa INFANTIL, anadidas a las de "moda" SOLO cuando el texto
# senala que el producto es de niño/bebe (Diego SI vende infantil, pero
# meterlas siempre ensuciaba los queries de adulto -- `[listing-audit]`).
_RAICES_INFANTIL: dict[Plataforma, tuple[int, ...]] = {
    "wallapop": (12461,),  # Niños y bebés
    "vinted": (1193,),  # Niños
}

# Señales de que el producto es infantil (por palabra; "12 meses"/"6 años" se
# captura por "meses"/"años"). `_STEMS_INFANTIL` se DERIVA de estas pasandolas
# por el MISMO `_tokens`+`_stem` que la consulta -- NUNCA se escribe a ojo
# (`[INC-026]`: `_stem("meses")=="mes"`, `_sin_acentos("años")=="anos"`).
_INFANTIL_SINONIMOS: frozenset[str] = frozenset({
    "niño", "niña", "niños", "niñas", "bebé", "bebés",
    "infantil", "junior", "kids", "meses", "años",
})


# ============================================================================
# Normalizacion de texto -- comun a la consulta y a los nombres de hoja.
# ============================================================================

# Palabras que NO discriminan una hoja de otra (ruido para el ranking). NO se
# meten marcas aqui: la marca puede coincidir con el nombre de una hoja por
# accidente, pero eso lo penaliza poco y es raro; meter una lista de marcas
# seria otra `_MARCAS_COMUNES_HEURISTICA` que mantener.
_STOPWORDS: frozenset[str] = frozenset({
    "de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas", "y",
    "o", "con", "sin", "para", "por", "en", "al", "a", "su", "sus", "mas",
    "otro", "otros", "otra", "otras", "talla", "color", "nuevo", "nueva",
    "usado", "usada", "estado", "buen", "bueno", "muy", "como", "the",
    # Señales de SEGMENTO/talla infantil: son puerta (`_es_infantil`) + factor
    # (`_factor_infantil`), NO tokens de ranking. Como tokens ensuciaban:
    # "anos"(años) casaba con paños/manos, y "bebe"/"nino" con material de bebé
    # (portabebés, monitores) en vez de con ropa (`[listing-audit]`). El
    # segmento lo steerea el factor; el ranking va por la PRENDA (camiseta/body).
    "meses", "mes", "anos", "ano",
    "nino", "nina", "ninos", "ninas", "bebe", "bebes", "infantil", "junior", "kids",
})

_RE_TOKEN = re.compile(r"[a-z0-9]+")

# Genero: si el titulo/descripcion lo dice ("de hombre", "camiseta mujer"),
# una hoja del genero PEDIDO sube y una del CONTRARIO baja -- resuelve el
# empate Mujer/Hombre que si no deja al azar cual sale primero. Sin genero en
# el texto -> no se toca nada (salen ambos, Diego elige).
#
# `[listing-audit] SERIO, 2026-07-17`: los stems se DERIVAN aplicando `_stem`
# a los sinonimos completos, NUNCA se escriben a ojo. La version anterior
# ponia 'hombr' a mano, pero `_stem('hombre')=='hombre'` (solo recorta plural
# s/es), asi que 'hombre' SINGULAR -- la forma natural mas comun -- no casaba
# y el sesgo de genero quedaba MUERTO para hombre (mujer si funcionaba: fallo
# asimetrico, ganaba Mujer siempre). Derivarlos garantiza que casen con lo que
# produce `_tokens`/`_stem` sobre la consulta.
_GENERO_SINONIMOS: dict[str, frozenset[str]] = {
    "hombre": frozenset({"hombre", "hombres", "masculino", "chico", "chicos"}),
    "mujer": frozenset({"mujer", "mujeres", "femenino", "chica", "chicas"}),
}
# `_GENERO_STEMS` se deriva mas abajo, DESPUES de definir `_stem` (aplicar
# `_stem` a los sinonimos exige que la funcion ya exista).
_BONUS_GENERO_PEDIDO = 1.7
_PENAL_GENERO_CONTRARIO = 0.25
# Mismo mecanismo para el segmento infantil: si el texto lo señala, las hojas
# de la sección de niños suben y las de adulto bajan (Diego elige igual).
_BONUS_INFANTIL = 1.7
_PENAL_ADULTO_SI_INFANTIL = 0.4


def _sin_acentos(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _stem(token: str) -> str:
    """Stemming crudo de plurales espanoles: 'sudaderas'->'sudader',
    'jerseis'->'jersei'. No es linguistica fina -- solo iguala singular/plural
    para el match ('camiseta'~'camisetas'). Deja tokens de <=3 intactos."""
    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _tokens(texto: str) -> list[str]:
    base = _sin_acentos(texto.lower())
    return [t for t in _RE_TOKEN.findall(base) if len(t) >= 3 and t not in _STOPWORDS]


def _casan(a: str, b: str) -> bool:
    """Dos tokens casan si comparten stem, o si uno contiene al otro con
    longitud suficiente (para 'sudadera' dentro de 'sudaderas' aunque el stem
    falle en algun borde)."""
    if a == b:
        return True
    sa, sb = _stem(a), _stem(b)
    if sa == sb:
        return True
    if len(a) >= 4 and len(b) >= 4 and (a in b or b in a):
        return True
    return False


# Stems de genero e infantil derivados AHORA que `_stem`/`_tokens` existen.
# `[listing-audit] SERIO / [INC-026]`: derivarlos pasandolos por el MISMO
# pipeline que la consulta (`_tokens`+`_stem`), nunca escribirlos a ojo -- asi
# casan con lo que la consulta produce ('hombre', 'meses'->'mes', 'años'->'ano').
def _stems_de(palabras: frozenset[str]) -> frozenset[str]:
    # Normaliza+stemma SIN el filtro de stopwords de `_tokens` -- si no, las
    # señales de talla infantil ('meses'/'años'), que SÍ son stopwords del
    # ranking, se perderían de la puerta. Mira el texto crudo, igual que
    # `_genero_en`/`_es_infantil`.
    return frozenset(
        _stem(t) for p in palabras for t in _RE_TOKEN.findall(_sin_acentos(p.lower()))
    )


_GENERO_STEMS: dict[str, frozenset[str]] = {
    g: _stems_de(syn) for g, syn in _GENERO_SINONIMOS.items()
}
_STEMS_INFANTIL: frozenset[str] = _stems_de(_INFANTIL_SINONIMOS)


# ============================================================================
# Carga del snapshot versionado (cacheada por proceso).
# ============================================================================


@dataclass(frozen=True)
class Hoja:
    """Una hoja del arbol de una plataforma, con su ruta completa."""

    id: int
    nombre: str
    ruta: tuple[str, ...]  # ancestros, de raiz a padre (sin la propia hoja)
    raiz_id: int
    leaf_mandatory: bool

    @property
    def ruta_completa(self) -> str:
        return " > ".join((*self.ruta, self.nombre))


@dataclass(frozen=True)
class Candidata:
    """Una hoja propuesta a Diego, con la puntuacion que la ordeno."""

    hoja: Hoja
    puntuacion: float


@lru_cache(maxsize=None)
def _cargar_hojas(plataforma: Plataforma) -> tuple[Hoja, ...]:
    ruta = _DIR_TAXONOMIA / f"{plataforma}.json"
    if not ruta.exists():
        raise FileNotFoundError(
            f"No existe el snapshot de categorias de {plataforma!r} en {ruta}. "
            "Genera/actualiza con: python -m core.categorias --refrescar"
        )
    doc = json.loads(ruta.read_text(encoding="utf-8"))
    return tuple(
        Hoja(
            id=h["id"],
            nombre=h["nombre"],
            ruta=tuple(h["ruta"]),
            raiz_id=h["raiz_id"],
            leaf_mandatory=bool(h.get("leaf_mandatory")),
        )
        for h in doc["hojas"]
    )


def fecha_snapshot(plataforma: Plataforma) -> str | None:
    ruta = _DIR_TAXONOMIA / f"{plataforma}.json"
    if not ruta.exists():
        return None
    return json.loads(ruta.read_text(encoding="utf-8")).get("descargado")


# ============================================================================
# Ranking -- el corazon. Solapamiento de palabras PONDERADO POR IDF y por
# profundidad. Sin el IDF, un modificador comun ("manga", "corta") puntuaba
# igual que el TIPO de prenda ("camiseta") y una hoja de otra prenda ("Camisas
# de manga corta") le ganaba a la correcta. Con IDF, un token que aparece en
# muchas hojas del arbol pesa poco y el tipo de prenda -- que es raro -- manda.
# El IDF se calcula del propio arbol (gratis, sin datos externos).
# ============================================================================

# Peso de un match segun donde caiga en la ruta: la propia hoja manda; el
# padre inmediato ayuda; ancestros lejanos (incluida la raiz) casi no. La raiz
# se ignora del todo -- ya la fijamos con `_RAICES`, no debe puntuar.
_PESO_HOJA = 3.0
_PESO_PADRE = 1.5
_PESO_ANCESTRO = 0.5


@lru_cache(maxsize=None)
def _idf(plataforma: Plataforma) -> dict[str, float]:
    """IDF por STEM sobre el corpus de hojas de `plataforma`: idf(t) =
    log(1 + N/df(t)), df = nº de hojas cuya ruta completa contiene el token.
    Un token raro (p.ej. 'sudader') sale con IDF alto; uno omnipresente
    ('ropa', 'manga') con IDF bajo."""
    hojas = _cargar_hojas(plataforma)
    n = len(hojas)
    df: dict[str, int] = {}
    for h in hojas:
        vistos = {_stem(t) for t in _tokens(" ".join((*h.ruta, h.nombre)))}
        for s in vistos:
            df[s] = df.get(s, 0) + 1
    return {s: math.log(1 + n / d) for s, d in df.items()}


def _puntuar(hoja: Hoja, tokens_consulta: list[str], idf: dict[str, float]) -> float:
    # Tokens de la hoja por nivel (excluye la raiz: ruta[0]).
    tok_hoja = _tokens(hoja.nombre)
    tok_padre = _tokens(hoja.ruta[-1]) if len(hoja.ruta) >= 1 else []
    tok_ancestros: list[str] = []
    for nombre in hoja.ruta[1:-1]:  # entre la raiz y el padre
        tok_ancestros += _tokens(nombre)

    # IDF medio como respaldo para un token que no este en el corpus (raro).
    idf_medio = (sum(idf.values()) / len(idf)) if idf else 1.0

    def _peso(qt: str, tokens: list[str], peso_nivel: float) -> float:
        mejor = 0.0
        for ht in tokens:
            if _casan(qt, ht):
                mejor = max(mejor, peso_nivel * idf.get(_stem(ht), idf_medio))
        return mejor

    total = 0.0
    for qt in tokens_consulta:
        mejor = max(
            _peso(qt, tok_hoja, _PESO_HOJA),
            _peso(qt, tok_padre, _PESO_PADRE),
            _peso(qt, tok_ancestros, _PESO_ANCESTRO),
        )
        total += mejor
    return total


def _es_infantil(texto: str) -> bool:
    """`True` si el texto señala ropa de niño/bebé -- la puerta que activa las
    raices infantiles (`_RAICES_INFANTIL`). Lee el texto CRUDO (no los tokens
    de ranking) porque las señales de talla ('meses'/'años') son stopwords del
    ranking: aquí sí cuentan, allí no."""
    stems = {_stem(t) for t in _RE_TOKEN.findall(_sin_acentos(texto.lower()))}
    return bool(stems & _STEMS_INFANTIL)


def _genero_en(tokens: list[str]) -> str | None:
    """Que genero declara el texto ('hombre'/'mujer'), o `None` si no lo dice
    o dice los dos (ambiguo -> no se sesga)."""
    stems = {_stem(t) for t in tokens}
    encontrados = {g for g, syn in _GENERO_STEMS.items() if stems & syn}
    return next(iter(encontrados)) if len(encontrados) == 1 else None


def _factor_genero(hoja: Hoja, genero_pedido: str | None) -> float:
    """1.0 si no se pidio genero o la hoja no declara ninguno; sube si la hoja
    es del genero pedido, baja si es del contrario. El genero de la hoja se lee
    de TODA su ruta (para Vinted esta en la raiz 'Hombre'/'Mujer'; para
    Wallapop en el nivel 1) -- por eso no basta el scoring normal, que ignora
    la raiz."""
    if genero_pedido is None:
        return 1.0
    stems_hoja = {_stem(t) for t in _tokens(" ".join((*hoja.ruta, hoja.nombre)))}
    generos_hoja = {g for g, syn in _GENERO_STEMS.items() if stems_hoja & syn}
    if not generos_hoja:
        return 1.0
    if genero_pedido in generos_hoja and len(generos_hoja) == 1:
        return _BONUS_GENERO_PEDIDO
    if genero_pedido not in generos_hoja:
        return _PENAL_GENERO_CONTRARIO
    return 1.0


def _factor_infantil(raiz_id: int, es_infantil: bool, inf_roots: frozenset[int]) -> float:
    """1.0 si el texto no señala infantil; si lo señala, sube las hojas de la
    sección de niños y baja las de adulto (que siguen presentes por si el
    modelo se equivocó de segmento -- Diego elige igual)."""
    if not es_infantil:
        return 1.0
    return _BONUS_INFANTIL if raiz_id in inf_roots else _PENAL_ADULTO_SI_INFANTIL


def candidatas(
    categoria_amplia: str | None,
    texto: str,
    plataforma: Plataforma,
    k: int = 3,
) -> list[Candidata]:
    """Las `k` hojas mejor puntuadas de `plataforma` para este producto.

    `categoria_amplia`: la categoria interna (`core.schema.CATEGORIAS`) que la
        extraccion propuso -- restringe el subarbol. `None` o desconocida =>
        se busca en TODO el arbol (sin restriccion), nunca se lanza.
    `texto`: titulo + descripcion + marca que Diego confirmo. De aqui salen
        los tokens de consulta.
    Devuelve SOLO candidatas con puntuacion > 0, ordenadas de mejor a peor,
    empatando hacia la ruta MAS CORTA (mas general -> menos arriesgada de
    elegir mal). Lista vacia = ninguna palabra caso: Diego navega a mano.
    NUNCA devuelve "la hoja"; devuelve un ranking para que el elija.
    """
    hojas = _cargar_hojas(plataforma)
    tokens_consulta = _tokens(texto)
    if not tokens_consulta:
        return []

    es_infantil = categoria_amplia == "moda" and _es_infantil(texto)
    raices_infantiles = frozenset(_RAICES_INFANTIL.get(plataforma, ()))

    raices = None
    if categoria_amplia is not None:
        raices = _RAICES.get(plataforma, {}).get(categoria_amplia, None)
    if raices is not None:
        raices_set = set(raices)
        # Ropa infantil: solo se anade su raiz si el texto lo senala (Diego
        # vende infantil, pero un query de adulto no debe sacar hojas de niño).
        if es_infantil:
            raices_set |= raices_infantiles
        hojas = tuple(h for h in hojas if h.raiz_id in raices_set)

    idf = _idf(plataforma)
    genero_pedido = _genero_en(tokens_consulta)
    inf_roots = raices_infantiles if es_infantil else frozenset()
    puntuadas = [
        Candidata(
            h,
            _puntuar(h, tokens_consulta, idf)
            * _factor_genero(h, genero_pedido)
            * _factor_infantil(h.raiz_id, es_infantil, inf_roots),
        )
        for h in hojas
    ]
    puntuadas = [c for c in puntuadas if c.puntuacion > 0]
    puntuadas.sort(key=lambda c: (-c.puntuacion, len(c.hoja.ruta), c.hoja.nombre))
    return puntuadas[:k]


# ============================================================================
# Refresco MANUAL de los snapshots (unico camino que toca la red).
# ============================================================================


def _descargar_wallapop() -> list[dict]:
    import urllib.request

    req = urllib.request.Request(
        "https://api.wallapop.com/api/v3/categories",
        headers={"Accept-Language": "es_ES", "X-DeviceOS": "0"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 -- URL fija https
        d = json.loads(r.read().decode("utf-8"))

    hojas: list[dict] = []

    def walk(c, ruta, raiz_id, raiz_nombre):
        subs = c.get("subcategories") or []
        if not subs:
            hojas.append({
                "id": c["id"], "nombre": c["name"], "ruta": ruta,
                "raiz_id": raiz_id, "raiz_nombre": raiz_nombre,
                "leaf_mandatory": bool(c.get("category_leaf_selection_mandatory")),
            })
            return
        for s in subs:
            walk(s, ruta + [c["name"]], raiz_id, raiz_nombre)

    for c in d["categories"]:
        walk(c, [], c["id"], c["name"])
    return hojas


def _descargar_vinted() -> list[dict]:
    import urllib.request

    req = urllib.request.Request(
        "https://www.vinted.es/",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Accept-Language": "es-ES,es;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 -- URL fija https
        html = r.read().decode("utf-8")

    i = html.find('catalogTree\\":')
    if i < 0:
        raise RuntimeError(
            "No se encontro 'catalogTree' en el SSR de Vinted -- puede que "
            "Datadome haya bloqueado el fetch o que cambiaran el markup. "
            "Reintenta desde un navegador y copia el arbol a mano si persiste."
        )
    start = html.find("[", i)
    raw = html[start:start + 3_000_000]
    bs, nul = chr(92), chr(0)
    raw = raw.replace(bs + bs, nul).replace(bs + '"', '"').replace(bs + "/", "/").replace(nul, bs)
    arr, _ = json.JSONDecoder().raw_decode(raw)

    hojas: list[dict] = []

    def walk(c, ruta, raiz_id, raiz_nombre):
        subs = c.get("catalogs") or []
        if not subs:
            hojas.append({
                "id": c["id"], "nombre": c["title"], "ruta": ruta,
                "raiz_id": raiz_id, "raiz_nombre": raiz_nombre,
                "leaf_mandatory": True,
            })
            return
        for s in subs:
            walk(s, ruta + [c["title"]], raiz_id, raiz_nombre)

    for c in arr:
        walk(c, [], c["id"], c["title"])
    return hojas


def refrescar(fecha_iso: str) -> None:
    """Re-descarga AMBOS arboles y reescribe los snapshots versionados.
    `fecha_iso` la pasa quien llama (no se usa `datetime.now()` para no
    esconder de donde sale la fecha del snapshot)."""
    _DIR_TAXONOMIA.mkdir(parents=True, exist_ok=True)
    for plat, fn, url in [
        ("wallapop", _descargar_wallapop,
         "https://api.wallapop.com/api/v3/categories (Accept-Language: es_ES)"),
        ("vinted", _descargar_vinted,
         "https://www.vinted.es/ SSR catalogTree (sin token; cookie anonima)"),
    ]:
        hojas = fn()
        doc = {"plataforma": plat, "descargado": fecha_iso, "fuente": url,
               "n_hojas": len(hojas), "hojas": hojas}
        (_DIR_TAXONOMIA / f"{plat}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=0), encoding="utf-8"
        )
        print(f"{plat}: {len(hojas)} hojas -> core/taxonomia/{plat}.json")
    _cargar_hojas.cache_clear()


if __name__ == "__main__":
    import argparse
    import datetime

    parser = argparse.ArgumentParser(description="Refresca los arboles de categorias.")
    parser.add_argument("--refrescar", action="store_true", help="re-descarga ambos arboles")
    args = parser.parse_args()
    if args.refrescar:
        hoy = datetime.date.today().isoformat()
        refrescar(hoy)
    else:
        parser.print_help()
