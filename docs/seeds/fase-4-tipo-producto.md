# Fase 4 (cont.) — El `tipo` de producto en la extracción · SEED DE PLANIFICACIÓN

> **Este seed es para PLANEAR, no para implementar.** Da el problema, lo que ya
> existe, las restricciones que aplican y las DECISIONES ABIERTAS — **a
> propósito NO da la respuesta**. Diego quiere planearlo sin el sesgo de la
> sesión anterior. Si el orquestador tiene una intuición, la mide o la propone
> como opción, no como conclusión.
>
> **Reconcilia el estado contra el repo antes de nada** (`change-loop.md` §D):
> `git log`, el código, `pytest`. Los seeds mienten; el código no.

## Prompt de arranque
```
Eres el orquestador ("papá oso") de RESELLERMASTER. Lee CLAUDE.md, .claude/rules/
(decision-making, truth-loop, change-loop, architecture, product, file-organization),
.claude/incident-ledger.md y docs/seeds/fase-4-tipo-producto.md.
Es una sesión de PLANIFICACIÓN (Loop de Cambios §A): decide CON DIEGO el approach
antes de escribir código. Superficie SENSIBLE (core/extract.py). NO propongas una
solución como conclusión sin medirla; presenta las opciones con sus trade-offs.
```

---

## EL HUECO (detectado por Diego, 2026-07-21, usando la app en vivo)

La síntesis (`core/extract.py::_sintetizar_ficha`) produce
`marca/talla/modelo/ean/color/estado/categoria/titulo/descripcion/desperfectos`,
pero **NO produce qué ES el producto**: el "masajeador de rodilla", la
"sudadera", el "jersey", la "camiseta". Diego, textual: *"en la ropa no pone si
son jerséis, sudaderas etc, y creo que este es fundamental no sólo para el
título, sino para el precio (al buscar por palabra clave)."*

**Toca TRES consumidores a la vez** (verificar cada uno en el código):
1. **Título** (`extract.py`, la síntesis lo redacta). Hoy sale genérico
   ("Lufthous LLLT-200 blanco y gris" en vez de "Masajeador de rodilla…").
2. **Precio** (`core/pricing.py`). La búsqueda de comparables por texto es
   marca+tipo+talla; hoy `tipo` se **deriva del título** con la lista
   `_TIPOS_PRENDA` (sólo prendas conocidas → NO cubre "masajeador de rodilla").
   `[INC-027]`: la cohorte se valida por la FUERZA del identificador — marca+tipo
   es más fuerte que marca sola.
3. **Candidatas de categoría** (`core/categorias.py`). Hoy rankean con el
   título+descripción; un `tipo` (+ género) explícito daría candidatas más
   certeras. El factor de género de `categorias.py` ya lee del título.

## LO QUE YA EXISTE (verificar en el código antes de decidir; no reinventar)
- `core/extract.py`: la síntesis YA añade campos de enum cerrado con un patrón
  repetible (`categoria`, `estado`): viven en `ESQUEMA_SINTESIS_FICHA`/
  `_CAMPOS_SINTESIS`, `fuente="inferido"` SIEMPRE, y si el modelo viola el enum
  la clave NO se añade (nunca un comodín silencioso). Leer esos párrafos del
  docstring del módulo — muestran cómo se añade un campo a la síntesis SIN
  llamada extra.
- `CAMPOS_PRODUCIDOS` (tupla de campos producidos), `pricing._TIPOS_PRENDA`,
  `categorias.py` (IDF + factor género/infantil) — los sitios que consumirían
  el nuevo campo.

## DECISIONES ABIERTAS (resolver CON DIEGO — presentadas sin veredicto)

**D1 — ¿Qué FORMA tiene `tipo`?** Opciones y sus tensiones (medir, no elegir a ojo):
- **Enum cerrado** (como `categoria`/`estado`): máxima defensa anti-alucinación,
  pero enumerar todos los tipos de un catálogo variado (ropa + cajas + variado)
  es difícil, y un enum incompleto TIRA datos (el tipo que no está → se pierde).
- **Texto corto libre** `fuente="inferido"`, que Diego confirma con el recorte
  delante: cubre cualquier producto, pero es un juicio del modelo → ¿cuánto
  alucina sobre las fotos reales? El ojo de Diego + que NO se publica como
  afirmación dura (va a título/búsqueda) es la defensa.
- **Híbrido** (enum para ropa, libre para el resto; o lista + "otro"): ¿vale la
  complejidad?
  → **Cómo decidir sin sesgo:** medir con `/eval` sobre el golden real cuánto
  cubre/alucina cada forma, y/o un panel ciego (`change-loop.md` §A). El dato
  manda, no la intuición.

**D2 — ¿Se añade el GÉNERO (hombre/mujer/niño) a la vez?** El seed anterior lo
emparejaba con `tipo` (mejora las candidatas de categoría y de precio). ¿Ahora,
después, o nunca? ¿Mismo debate de forma (enum vs libre) que `tipo`?

**D3 — Coste.** Debería ir DENTRO de la síntesis (0 llamadas extra, como
`categoria`/`estado`) → coste plano. Confirmarlo con `estimar_coste_lote` y
decirlo (`change-loop.md` §C5). Si algún approach añade una llamada, es un
cambio de arquitectura disfrazado.

## GATE (superficie sensible → obligatorio antes de cerrar)
- `/eval` contra el golden set: ¿el `tipo` es legible/correcto en las fotos
  reales? ¿sube o baja la tasa de alucinación (métrica primaria)?
- `listing-audit`: intentar que `tipo` cuele algo falso en el título o en la
  búsqueda de precio (una marca ajena como tipo, un tipo que no está en la foto).
- Medir el impacto REAL en las candidatas de categoría y en la cohorte de precio
  con la sudadera y el masajeador de Diego (antes/después).

## NO REABRIR (medido y cerrado)
- El EXPORT (orden por plataforma, envío, color, categoría candidata, precio
  editable) está hecho. No tocar salvo que `tipo` mejore una entrada concreta.
- Precio por imagen / Google Lens: DESCARTADO (`architecture.md`).
- `medidas` desde texto: NO (`[INC-025]`), sólo del metro.

## Pendientes menores (no bloquean el plan)
- **El GATE real, aún sin hacer:** Diego exporta un producto de verdad y
  CRONOMETRA (baseline ~285 s). Sin ese número no sabemos qué ahorra nada.
- Precio en Vinted (búsqueda tras Datadome; hoy sólo Wallapop).
- `test_curar::test_render_sin_excepcion` flaky por timeout de `AppTest` (3 s)
  bajo carga — subir el timeout de los `AppTest.run()`.
