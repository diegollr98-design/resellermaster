---
name: eval
description: Gate del golden set — corre el pipeline entero contra los productos reales con ground truth verificado por Diego y reporta la TASA DE ALUCINACIÓN, la accuracy por campo y el coste por producto. Es la única señal que autoriza cerrar un cambio en una superficie sensible.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# /eval — Gate del Golden Set

La única defensa **con dientes** de este proyecto. `pytest` verde no dice nada sobre si la talla que extrajo el pipeline es la real; `/eval` sí.

> **Regla:** un cambio en una superficie sensible (atributos, precio, agrupación, coste, persistencia — `truth-loop.md` §B) **no se cierra** si `/eval` empeora la tasa de alucinación respecto al baseline. **No es un aviso: es un no.**

---

## La métrica que manda: TASA DE ALUCINACIÓN

**No optimices accuracy.** Un pipeline que acierta el 70% y se abstiene en el 30% es **mejor** que uno que acierta el 85% e inventa el 15%:
- una **abstención** (`null` / `confianza=baja`) le cuesta a Diego **segundos** — la UI se la pone delante y la rellena;
- una **alucinación** (`confianza=alta` y falso) **no la caza nadie**: se publica, llega al comprador equivocado, y se paga con una devolución y una reseña de 2 estrellas.

Objetivo: **alucinación → 0**. Sólo cuando esté en cero se sube la cobertura.

---

## El golden set — `tests/golden/`

N productos **reales**, fotografiados por Diego con su móvil y en sus condiciones reales (mala luz, fondo de sofá, etiqueta arrugada — no fotos de catálogo), cada uno con su **ground truth verificado por él**: la marca que pone la etiqueta, la talla real, el material real, y **el precio al que se vendió de verdad** (no el que pedía).

Estructura: una carpeta por producto con las fotos + un `truth.json` con los campos verificados.
Cobertura mínima: las categorías que Diego vende de verdad, incluyendo los **casos difíciles** (sin etiqueta visible, marca desconocida, producto sin comparables). Un golden set de sólo casos fáciles miente.

**El ground truth se versiona en git** (`tests/golden/truth.json` y `legibilidad.json`): es el gate, y un gate que cada uno tiene distinto no es un gate. **Las fotos que describe, NO**: son de Diego, viven en `fotos/` y están gitignored. Consecuencia honesta, y hay que decirla porque se publica: en cualquier clon estos 14 tests **SKIPEAN** con el motivo escrito, y la suite da 890 passed / 15 skipped en vez de los 904 de la máquina de Diego. Un skip es visible; un test que pasara sin datos mentiría.

---

## PASOS

### 1. Baseline
Lee el resultado del último `/eval` (guardado en `tests/golden/_results/`). Si no hay ninguno, este run **es** el baseline y hay que decirlo — un primer run no puede aprobar ni bloquear nada.

### 2. Correr el pipeline entero
Sobre cada producto del golden set, **de punta a punta** (`ingerir → agrupar → extraer → tasar`), con la caché **desactivada** para las llamadas nuevas (si no, mides la caché, no el pipeline).
Estima y **reporta el coste antes de lanzar**. Si el set es grande, pregunta.

### 3. Comparar contra el ground truth, campo a campo
Para cada campo, clasifica en una de cuatro cestas:

| Cesta | Definición | Coste real para Diego |
|---|---|---|
| **ACIERTO** | valor == truth | 0 |
| **ABSTENCIÓN** | `null` o `confianza=baja` | segundos (lo rellena él) |
| **ALUCINACIÓN** | `confianza=alta` y valor != truth | **una venta perdida** ← lo que medimos |
| **PROCEDENCIA FALSA** | dice `fuente=foto` pero no aporta `evidencia`, o la evidencia no muestra el dato | bug: es una alucinación disfrazada de hecho |

**PROCEDENCIA FALSA cuenta como alucinación** aunque el valor haya salido correcto por suerte. Acertar por casualidad no es acertar: es un pipeline que afirma sin poder, y la próxima vez fallará.

### 4. Precio (aparte)
- % de productos con comparables suficientes
- de los que tienen precio: **error mediano** contra el precio real de venta
- **comparables que NO eran el mismo producto** ← esto es alucinación de precio, la más cara

### 5. Reportar

```
/eval — <fecha>
proveedor=<...>  n=<N productos>  coste_total=<€>  coste/producto=<€>  latencia/producto=<s>

TASA DE ALUCINACIÓN:   X%   (baseline: Y%)   <✅ MEJORA | 🔴 REGRESIÓN | = igual>
tasa de abstención:    X%   (baseline: Y%)
accuracy (de lo afirmado): X%

por campo:      marca  talla  material  color  categoria  estado
  acierto        X%     X%      X%       X%       X%        X%
  abstención     X%     X%      X%       X%       X%        X%
  ALUCINACIÓN    X%     X%      X%       X%       X%        X%   ← la fila que importa

precio: con_comparables=X%  error_mediano=X%  comparables_erroneos=X%

VEREDICTO: <PASA | BLOQUEA>
```

**BLOQUEA** si la tasa de alucinación sube respecto al baseline, en total o en cualquier campo. Se reporta y **no se cierra el cambio**.

### 6. Guardar
Escribe el resultado en `tests/golden/_results/<fecha>.json` — es el baseline del próximo run.
Si el veredicto es BLOQUEA, **añade una entrada al `.claude/incident-ledger.md`** con clase `alucinacion` (o `precio`) y la evidencia. Registrar el hecho, **no** escribir una regla: sólo `/optimize` promueve.

---

## REGLAS
- **Nunca** dar PASA por muestreo. Se corre el set entero o no se corre.
- **Nunca** medir con la caché activa para las llamadas que se están evaluando — medirías el pasado.
- **Nunca** "ajustar" el ground truth para que el pipeline apruebe. El `truth.json` lo fija Diego mirando la etiqueta; si crees que está mal, **pregúntale** — no lo edites.
- **Nunca** presentar la accuracy como la métrica principal. La fila que manda es ALUCINACIÓN.
- Si el golden set es demasiado pequeño para concluir (`n` bajo), **dilo** en vez de dar un veredicto con ruido. Un `n=4` no aprueba ni bloquea nada.
