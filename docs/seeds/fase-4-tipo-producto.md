# Fase 4 (cont.) — El `tipo` de producto en la extracción

> Arranca aquí. **Reconcilia el estado contra el repo antes de tocar nada** (`change-loop.md` §D): `git log`, el código, `pytest`. Los seeds mienten; el código no.

## Prompt de arranque
```
Eres el orquestador ("papá oso") de RESELLERMASTER. Lee CLAUDE.md, .claude/rules/
(decision-making, truth-loop, change-loop, architecture, product, file-organization),
.claude/incident-ledger.md y docs/seeds/fase-4-tipo-producto.md.
El export ya está pulido y en el orden de cada plataforma. Falta UN campo en la
extracción: el TIPO de producto. Corre el Loop de Cambios; es superficie SENSIBLE
(core/extract.py) → listing-audit + /eval antes de cerrar.
```

---

## EL HUECO (detectado por Diego, 2026-07-21, usando la app en vivo)

La síntesis (`core/extract.py::_sintetizar_ficha`) produce
`marca/talla/modelo/ean/color/estado/categoria/titulo/descripcion/desperfectos`,
pero **NO produce qué ES el producto**: el "masajeador de rodilla", la
"sudadera", el "jersey", la "camiseta". Diego lo dijo textual: *"en la ropa no
pone si son jerséis, sudaderas etc, y creo que este es fundamental no sólo para
el título, sino para el precio (al buscar por palabra clave)."*

**Por qué es fundamental — toca TRES cosas a la vez:**
1. **Título:** hoy sale genérico ("Lufthous LLLT-200 blanco y gris" en vez de
   "Masajeador de rodilla Lufthous LLLT-200"). El tipo es la palabra que un
   comprador busca.
2. **Precio** (`core/pricing.py`): la búsqueda de comparables por texto es
   marca+tipo+talla. Hoy el `tipo` se **deriva del título** con una lista de
   prendas (`_TIPOS_PRENDA` en `pricing.py`) — funciona para ropa conocida,
   pero NO para "masajeador de rodilla" (no está en la lista) ni cuando el
   título no nombra el tipo. Un `tipo` estructurado y fiable haría la cohorte
   de precio mucho mejor (`[INC-027]`: la cohorte se valida por la fuerza del
   identificador — marca+tipo es más fuerte que marca sola).
3. **Candidatas de categoría** (`core/categorias.py`): hoy rankean con el
   título+descripción. Un `tipo` explícito (+ género, que también falta) daría
   candidatas mucho más certeras — de hecho el seed anterior
   (`fase-4-precio-y-pulido-export.md`) ya proponía añadir `tipo_prenda`+`género`
   a la extracción por esto mismo.

## LO QUE YA EXISTE (no reinventar)
- `core/extract.py`: la síntesis ya añade campos de ENUM CERRADO siguiendo un
  patrón repetible (`categoria`, `estado`) — el `tipo` debe seguir EL MISMO:
  vive en `ESQUEMA_SINTESIS_FICHA`/`_CAMPOS_SINTESIS`, `fuente="inferido"`
  SIEMPRE (nunca "foto" — es un juicio), si el modelo viola el enum la clave NO
  se añade (nunca un comodín silencioso, `decision-making.md` §13). Ver los
  párrafos "categoria (2026-07-17...)" y "estado (2026-07-17...)" del docstring
  del módulo: son la plantilla exacta.
- `pricing._TIPOS_PRENDA` (lista de prendas para derivar el tipo del título) y
  `categorias.py` (IDF + factor de género/infantil) ya consumen "tipo"/género
  como término — enchufar el nuevo campo estructurado ahí sube su calidad.
- `CAMPOS_PRODUCIDOS` en `extract.py` es la tupla de campos; añadir "tipo" (y
  quizá "genero") ahí.

## DECISIONES QUE FALTAN (resolver con Diego ANTES de construir)
1. **¿`tipo` es un ENUM CERRADO o un texto corto libre?**
   - ENUM cerrado (como `categoria`/`estado`): anti-alucinación fuerte, pero
     enumerar TODOS los tipos (ropa + cajas + variado) es inviable y un enum
     incompleto tira datos. **Probablemente NO** para el catálogo variado de Diego.
   - Texto corto libre `fuente="inferido"` + `confianza="baja"`, que Diego
     confirma/edita con el recorte delante (patrón de la ficha): más flexible,
     cubre "masajeador de rodilla". El riesgo de alucinación lo cubre el ojo de
     Diego + que NO se publica como afirmación dura (va al título/búsqueda).
     **Recomendación de partida**, a validar con Diego y con /eval.
2. **¿Y el GÉNERO** (hombre/mujer/niño)? El seed anterior lo emparejaba con
   `tipo`. `categorias.py` ya tiene factor de género leyendo del título — un
   campo estructurado lo haría fiable. ¿Se añade a la vez o después?
3. **¿Coste?** Va DENTRO de la llamada de síntesis existente (0 llamadas extra,
   como `categoria`/`estado`) → coste plano. Confírmalo con `estimar_coste_lote`
   y dilo (`change-loop.md` §C5).

## GATE (superficie sensible → obligatorio antes de cerrar)
- `/eval` contra el golden set: ¿el `tipo` extraído es legible/correcto en las
  fotos reales? ¿Sube o baja la tasa de alucinación? (métrica primaria).
- `listing-audit`: intentar que el `tipo` cuele algo falso en el título o en la
  búsqueda de precio (¿mete una marca ajena como tipo? ¿un tipo inventado que
  no está en la foto?).
- Medir el impacto en las **candidatas de categoría** y en la **cohorte de
  precio** con los productos reales de Diego (la sudadera y el masajeador).

## NO REABRIR (medido y cerrado)
- El EXPORT (orden, envío, color, categoría candidata, precio editable) está
  hecho y en el orden de cada plataforma. No tocar salvo que el `tipo` mejore
  una entrada concreta.
- Precio por imagen / Google Lens: DESCARTADO (`architecture.md`).
- `medidas` desde texto: NO (`[INC-025]`), sólo del metro.

## Pendientes menores (no bloquean)
- El GATE real que sigue sin hacerse: Diego exporta un producto de verdad y
  CRONOMETRA (baseline ~285 s). Sin ese número no sabemos qué ahorra.
- Precio en Vinted (su búsqueda está tras Datadome; hoy sólo Wallapop).
- `test_curar::test_render_sin_excepcion` es flaky por timeout de `AppTest`
  (3 s) bajo carga — subir el timeout de los `AppTest.run()`.
