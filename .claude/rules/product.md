# Producto — RESELLERMASTER

> ⚠️ **ESTE FICHERO ESTÁ VACÍO A PROPÓSITO.** Se rellena en la **sesión de planificación**, no antes. Escribir aquí ahora sería inventar la taxonomía de campos de Wallapop y Vinted de memoria — exactamente el fallo que el `truth-loop` existe para evitar (`decision-making.md` §10: no especular sin datos).

## Qué debe contener cuando se rellene

1. **Campos por plataforma.** Los campos que Wallapop y Vinted piden **de verdad** en su formulario de publicación, a día de hoy — verificados contra sus webs/docs en esa sesión, no de memoria. Cuáles son obligatorios, cuáles opcionales, qué formato aceptan (¿la talla es texto libre o una lista cerrada? ¿la categoría es un árbol?).
2. **Taxonomía de categorías.** Diego vende **de todo** ("todo mezclado") → la app detecta la categoría desde la imagen y adapta los campos. Hace falta el árbol de categorías real de cada plataforma, o al menos el subconjunto que Diego usa.
3. **Campos por categoría.** Ropa pide talla/material/color; electrónica pide modelo/capacidad/accesorios; hogar pide medidas. Esto alimenta `core/schema.py` (costura 3).
4. **La escala de estado** de cada plataforma (nuevo / como nuevo / en buen estado / …) — sus etiquetas literales, no una aproximación.
5. **Límites duros:** nº máximo de fotos por anuncio, longitud máxima de título y descripción, formatos y resolución de imagen aceptados. Determinan qué hace `core/images.py`.
6. **Precio:** de dónde salen los comparables y con qué umbral `n` se considera suficiente (ver `truth-loop.md` §D).

## Cómo se rellena
Verificando las fuentes en esa sesión (webs de ayuda de Wallapop y Vinted, formularios reales, y las propias fichas que Diego ya tiene publicadas si las hay). **Cada campo con su fuente.** Un campo obligatorio que nos inventamos = un anuncio que no se puede publicar, descubierto al pegar.
