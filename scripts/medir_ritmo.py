"""Mide el RITMO REAL de fotografiado de Diego leyendo el EXIF de una carpeta.

Por qué existe (ver `.claude/incident-ledger.md` → [INC-003]):
`core/grouping.py` corta los productos por los huecos de tiempo entre disparos.
Tres rondas de arreglo se calibraron contra imágenes sintéticas y ninguna
convergió, porque el parámetro que decide si el módulo es viable NO es ninguno
de los que se estaban calibrando: es el **jitter del ritmo real de Diego**.

Este script lo mide. No necesita anotar nada, ni el golden set, ni CLIP.
Sólo lee timestamps.

Uso:
    python scripts/medir_ritmo.py "C:/ruta/a/una/carpeta/de/fotos"
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

from core.images import leer_metadatos

EXTENSIONES = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}


def main(carpeta: Path) -> int:
    fotos = sorted(p for p in carpeta.rglob("*") if p.suffix.lower() in EXTENSIONES)
    if not fotos:
        print(f"No hay fotos en {carpeta}")
        return 1

    con_fecha, sin_fecha = [], []
    for f in fotos:
        meta = leer_metadatos(f)
        fecha = getattr(meta, "fecha_captura_exif", None)
        (con_fecha if fecha else sin_fecha).append((f, fecha))

    print(f"Fotos encontradas : {len(fotos)}")
    print(f"  con EXIF        : {len(con_fecha)}")
    print(f"  SIN EXIF        : {len(sin_fecha)}  <- estas no se pueden agrupar por tiempo")

    if len(con_fecha) < 3:
        print("\nDemasiado pocas fotos con EXIF para medir el ritmo.")
        return 1

    con_fecha.sort(key=lambda t: t[1])
    huecos = [
        (con_fecha[i + 1][1] - con_fecha[i][1]).total_seconds()
        for i in range(len(con_fecha) - 1)
    ]

    print("\n--- DISTRIBUCION DE HUECOS ENTRE DISPAROS (segundos) ---")
    for q, etiqueta in [(0.10, "p10"), (0.25, "p25"), (0.50, "MEDIANA"), (0.75, "p75"), (0.90, "p90")]:
        idx = int(q * (len(huecos) - 1))
        print(f"  {etiqueta:8}: {sorted(huecos)[idx]:8.1f}s")
    print(f"  {'min':8}: {min(huecos):8.1f}s")
    print(f"  {'max':8}: {max(huecos):8.1f}s")

    # Lo que de verdad decide si cortar por tiempo es viable:
    # ¿se separan los huecos "dentro de un producto" de los "entre productos"?
    cortos = [h for h in huecos if h <= statistics.median(huecos)]
    if len(cortos) >= 2:
        jitter = statistics.stdev(cortos)
        print("\n--- EL DATO QUE IMPORTA ---")
        print(f"  Jitter (desviacion tipica de los huecos cortos): +/- {jitter:.1f}s")
        print()
        if jitter <= 1.5:
            print("  => Ritmo REGULAR. Cortar por tiempo es viable con margen.")
        elif jitter <= 2.5:
            print("  => Ritmo IRREGULAR. Cortar por tiempo es fragil: hara falta")
            print("     apoyarse mas en la senal visual (CLIP) que en la temporal.")
        else:
            print("  => Ritmo MUY IRREGULAR. Cortar por tiempo NO es viable como")
            print("     senal primaria. Hay que replantear la agrupacion.")

    print("\nPega esta salida entera en el chat. Con eso se decide el diseno,")
    print("en vez de seguir adivinando contra imagenes sinteticas.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    sys.exit(main(Path(sys.argv[1])))
