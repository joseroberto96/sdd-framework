#!/usr/bin/env python3
"""lessons_reflect.py — Digest determinista de lecciones con decaimiento y corroboración.

Know-how portado de graphify (reflect.py, MIT) y adaptado al framework:
- Cada lección de `_project/22_lessons.md` puede llevar señales de outcome
  (`useful | dead_end | corrected`) que se añaden durante el trabajo real.
- Cada señal aporta un peso con signo y decaimiento temporal (half-life,
  30 días por defecto): un dead_end fresco pesa más que un useful de hace meses.
- Umbral de corroboración: una lección solo es "Preferida" con >= 2 señales
  útiles distintas (la creación cuenta como la primera) — un solo registro no
  acuña una lección de confianza.
- Determinista y byte-estable dado `--now`: sin LLM, se puede regenerar en cada
  /gycd sin coste. La salida es una PROYECCIÓN de solo lectura (con banner);
  la fuente de verdad sigue siendo 22_lessons.md.

Formato de señal (línea dentro de la sección `**Señales:**` de una lección):
    - 2026-07-09 useful — aplicada en el módulo M2
    - 2026-08-01 dead_end — la regla no aplicaba a este caso
    - 2026-08-10 corrected — la regla correcta es W

Degradación segura: si 22_lessons.md no existe o no tiene lecciones (modo
template, proyecto recién inicializado), no se escribe nada y sale 0.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HALF_LIFE_DAYS_DEFAULT = 30
MIN_CORROBORATION_DEFAULT = 2
# Peso total (suma de |peso| de todas las señales) por debajo del cual la
# lección se considera caducada y sale del cuerpo del digest.
EXPIRY_WEIGHT_THRESHOLD = 0.05

BANNER = "<!-- GENERADO AUTOMÁTICAMENTE - NO EDITAR A MANO -->"

OUTCOMES = ("useful", "dead_end", "corrected")

_LESSON_RE = re.compile(r"(?m)^## Lección (\d+) — (.+)$")
_FECHA_RE = re.compile(r"(?m)^\*\*Fecha:\*\*\s*(\S+)")
_SENAL_RE = re.compile(
    r"(?m)^-\s*(\d{4}-\d{2}-\d{2}(?:T[\d:.+Z-]+)?)\s+(useful|dead_end|corrected)\b(?:\s*[—-]\s*(.*))?$"
)


# Clases planas en vez de dataclasses: los tests cargan este script vía
# importlib sin registrarlo en sys.modules, y dataclasses (3.14) resuelve
# anotaciones contra sys.modules[cls.__module__] — con dataclass el import
# dinámico revienta.
class Senal:
    def __init__(self, fecha: datetime, outcome: str, nota: str = ""):
        self.fecha = fecha
        self.outcome = outcome
        self.nota = nota


class Leccion:
    def __init__(
        self,
        numero: int,
        titulo: str,
        fecha: datetime | None,
        regla: str,
        senales: list[Senal] | None = None,
    ):
        self.numero = numero
        self.titulo = titulo
        self.fecha = fecha
        self.regla = regla
        self.senales = senales if senales is not None else []


def parse_now(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_fecha(value: str) -> datetime | None:
    try:
        return parse_now(value)
    except ValueError:
        return None


def _extraer_regla(cuerpo: str) -> str:
    match = re.search(r"(?ms)^\*\*Regla:\*\*\s*\n(.+?)(?=^\*\*|\Z)", cuerpo)
    if not match:
        return ""
    for line in match.group(1).splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def parsear_lecciones(texto: str) -> list[Leccion]:
    lecciones: list[Leccion] = []
    matches = list(_LESSON_RE.finditer(texto))
    for i, m in enumerate(matches):
        fin = matches[i + 1].start() if i + 1 < len(matches) else len(texto)
        cuerpo = texto[m.end():fin]
        fecha_m = _FECHA_RE.search(cuerpo)
        fecha = _parse_fecha(fecha_m.group(1)) if fecha_m else None
        senales = []
        # Las señales solo cuentan dentro de la sección **Señales:** para no
        # confundirlas con bullets de Aplicabilidad u otro campo.
        senales_m = re.search(r"(?ms)^\*\*Señales:\*\*\s*\n(.*?)(?=^\*\*|\Z)", cuerpo)
        if senales_m:
            for s in _SENAL_RE.finditer(senales_m.group(1)):
                fecha_s = _parse_fecha(s.group(1))
                if fecha_s is not None:
                    senales.append(Senal(fecha_s, s.group(2), (s.group(3) or "").strip()))
        senales.sort(key=lambda s: s.fecha)
        lecciones.append(
            Leccion(int(m.group(1)), m.group(2).strip(), fecha, _extraer_regla(cuerpo), senales)
        )
    return lecciones


def peso_senal(fecha: datetime, now: datetime, half_life_days: float) -> float:
    age_days = max((now - fecha).total_seconds() / 86400.0, 0.0)
    return 0.5 ** (age_days / half_life_days)


def _senales_efectivas(leccion: Leccion) -> list[Senal]:
    """Señales de la lección, con la creación contando como primer 'useful'."""
    senales = list(leccion.senales)
    if leccion.fecha is not None:
        senales.insert(0, Senal(leccion.fecha, "useful", "creación"))
    return senales


def score_leccion(leccion: Leccion, now: datetime, half_life_days: float) -> float:
    score = 0.0
    for s in _senales_efectivas(leccion):
        peso = peso_senal(s.fecha, now, half_life_days)
        score += peso if s.outcome == "useful" else -peso
    return score


def clasificar(
    leccion: Leccion,
    now: datetime,
    half_life_days: float,
    min_corroboration: int,
) -> str:
    senales = _senales_efectivas(leccion)
    if senales and max(senales, key=lambda s: s.fecha).outcome == "corrected":
        return "corregidas"
    peso_total = sum(peso_senal(s.fecha, now, half_life_days) for s in senales)
    if peso_total < EXPIRY_WEIGHT_THRESHOLD:
        return "caducadas"
    outcomes = {s.outcome for s in senales}
    if "dead_end" in outcomes or "corrected" in outcomes:
        return "cuestionadas"
    utiles = sum(1 for s in senales if s.outcome == "useful")
    score = score_leccion(leccion, now, half_life_days)
    if utiles >= min_corroboration and score > 0:
        return "preferidas"
    return "tentativas"


def _linea_digest(leccion: Leccion, score: float) -> str:
    regla = leccion.regla or "(sin regla extraíble)"
    return f"- **Lección {leccion.numero} — {leccion.titulo}** (score {score:.2f}): {regla}"


def _linea_corregida(leccion: Leccion) -> str:
    correcciones = [s for s in leccion.senales if s.outcome == "corrected"]
    nota = correcciones[-1].nota if correcciones else ""
    detalle = f" corrección — {nota}" if nota else " (corregida sin nota)"
    return f"- **Lección {leccion.numero} — {leccion.titulo}**:{detalle}"


def render_digest(
    lecciones: list[Leccion],
    now: datetime,
    half_life_days: float,
    min_corroboration: int,
) -> str:
    grupos: dict[str, list[tuple[float, Leccion]]] = {
        "preferidas": [], "cuestionadas": [], "corregidas": [], "tentativas": [], "caducadas": [],
    }
    for leccion in lecciones:
        categoria = clasificar(leccion, now, half_life_days, min_corroboration)
        grupos[categoria].append((score_leccion(leccion, now, half_life_days), leccion))
    for entradas in grupos.values():
        # Orden estable: score desc, número de lección asc como desempate.
        entradas.sort(key=lambda par: (-par[0], par[1].numero))

    lineas = [
        BANNER,
        "# Digest de Lecciones",
        "",
        "> Proyección de solo lectura de `_project/22_lessons.md` — nunca editar a mano.",
        f"> Regenerado por `.agent/scripts/lessons_reflect.py` (half-life={half_life_days:g}d, "
        f"corroboración mínima={min_corroboration}, now={now.isoformat()}).",
        "> Al inicio de sesión cargar ESTE digest, no el archivo completo de lecciones.",
        f"> Fuente: {len(lecciones)} lecciones.",
        "",
    ]

    secciones = (
        ("preferidas", "## Preferidas (corroboradas — aplicar por defecto)"),
        ("cuestionadas", "## Cuestionadas (señales mixtas — verificar antes de aplicar)"),
        ("corregidas", "## Corregidas (la regla original era errónea)"),
        ("tentativas", "## Tentativas (sin corroborar todavía)"),
    )
    for clave, encabezado in secciones:
        entradas = grupos[clave]
        if not entradas:
            continue
        lineas.append(encabezado)
        for score, leccion in entradas:
            if clave == "corregidas":
                lineas.append(_linea_corregida(leccion))
            else:
                lineas.append(_linea_digest(leccion, score))
        lineas.append("")

    lineas.append("---")
    lineas.append(
        f"Caducadas (peso decaído < {EXPIRY_WEIGHT_THRESHOLD:g}, omitidas del digest): "
        f"{len(grupos['caducadas'])}"
    )
    lineas.append("")
    return "\n".join(lineas)


_FUENTE_RE = re.compile(r"(?m)^> Fuente: (\d+) lecciones\.$")


def _lecciones_en_digest_existente(out_path: Path) -> int | None:
    """Número de lecciones fuente declarado en el digest ya escrito, o None."""
    if not out_path.exists():
        return None
    match = _FUENTE_RE.search(out_path.read_text(encoding="utf-8", errors="ignore"))
    return int(match.group(1)) if match else None


def generar_digest(
    lessons_path: Path,
    out_path: Path,
    now_iso: str,
    half_life_days: float = HALF_LIFE_DAYS_DEFAULT,
    min_corroboration: int = MIN_CORROBORATION_DEFAULT,
    force: bool = False,
) -> int:
    """Genera el digest. Nunca lanza por ausencia de insumos: sin archivo o sin
    lecciones (modo template / proyecto recién inicializado) no escribe nada.

    Guarda de encogimiento (port de graphify #479): si el digest existente
    declara más lecciones fuente que las parseadas ahora, la causa probable es
    corrupción parcial de 22_lessons.md — negarse a sobrescribir salvo --force."""
    if not lessons_path.exists():
        print(f"lessons_reflect: {lessons_path} no existe — nada que digerir (ok).")
        return 0
    lecciones = parsear_lecciones(lessons_path.read_text(encoding="utf-8"))
    if not lecciones:
        print(f"lessons_reflect: {lessons_path} sin lecciones — no se escribe digest (ok).")
        return 0
    previas = _lecciones_en_digest_existente(out_path)
    if not force and previas is not None and len(lecciones) < previas:
        print(
            f"lessons_reflect: ERROR — el digest existente declara {previas} lecciones y ahora "
            f"solo se parsearon {len(lecciones)}; posible corrupción de {lessons_path}. "
            "No se sobrescribe. Si el encogimiento es intencional (borraste lecciones), "
            "re-ejecutar con --force.",
            file=sys.stderr,
        )
        return 1
    digest = render_digest(lecciones, parse_now(now_iso), half_life_days, min_corroboration)
    out_path.write_text(digest, encoding="utf-8")
    print(f"lessons_reflect: digest de {len(lecciones)} lecciones escrito en {out_path}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Digest determinista de lecciones (decaimiento + corroboración)"
    )
    parser.add_argument("--lessons", default="_project/22_lessons.md")
    parser.add_argument("--out", default="_project/22_lessons_digest.md")
    parser.add_argument("--now", default=None, help="ISO8601; por defecto, ahora (UTC)")
    parser.add_argument("--half-life-days", type=float, default=HALF_LIFE_DAYS_DEFAULT)
    parser.add_argument("--min-corroboration", type=int, default=MIN_CORROBORATION_DEFAULT)
    parser.add_argument(
        "--force", action="store_true",
        help="Permite sobrescribir un digest con menos lecciones fuente (encogimiento intencional)",
    )
    args = parser.parse_args()

    now_iso = args.now or datetime.now(timezone.utc).isoformat()
    return generar_digest(
        lessons_path=Path(args.lessons),
        out_path=Path(args.out),
        now_iso=now_iso,
        half_life_days=args.half_life_days,
        min_corroboration=args.min_corroboration,
        force=args.force,
    )


if __name__ == "__main__":
    sys.exit(main())
