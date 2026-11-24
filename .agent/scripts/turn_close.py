#!/usr/bin/env python3
"""Validador único del cierre de turno (puro, sin E/S, solo biblioteca estándar).

Contrato vigente: `.agent/skills/decision_translation_core.md`, sección «Bloque
de cierre de turno». Todo turno que devuelve el control al builder termina con
una línea `Siguiente:` y, justo debajo, una línea de modelo y esfuerzo.

Este módulo es la definición ejecutable de ese contrato. Lo reutilizan la
auditoría de transcripciones (`turn_close_audit.py`) y, más adelante, un hook,
el doctor y una proyección. No abre ficheros: recibe el texto y el manifest ya
cargado (`{"commands": [{"name": ..., "builder_facing": bool}, ...]}`).

Formas válidas de la línea de cierre (se describen con la sintaxis `Siguiente: …`
sin escribirla como línea completa, para que ningún escáner de plantillas tome
estos docstrings por cierres reales):

1. `/<cmd> <instrucción>` con `<cmd>` del builder e instrucción no vacía.
2. `/ciclo fase` y `/ciclo fase --paralelo`, válidas tal cual.
3. `ninguno — <motivo>`.
4. `decisión — <qué contestar y dónde>` (decisión B3 del builder): sin comando,
   no aplican `SIN_COMANDO` ni `COMANDO_DESCONOCIDO`.

En 3 y 4 el separador canónico es la raya «—»; se aceptan también «–» y « - ».

Adornos markdown: `**Siguiente:**`, la cita `> ` y el subrayado/negrita
envolvente se TOLERAN (no son defecto): no cambian lo que se lee ni lo que se
copia, y el contrato no los prohíbe. Se registran en `CloseParts.adornos` para
poder medirlos. Las comillas invertidas alrededor de la línea o del comando SÍ
son defecto (`COMILLAS_EN_COMANDO`): rompen el «string copiable» del contrato.

Línea de modelo: `Modelo: <strong|standard|limited> · Esfuerzo: <bajo|medio|alto|xhigh> (<tier>)`,
con emoji opcional delante del esfuerzo y paréntesis opcional con el modelo
concreto detrás del nivel de modelo. Coincide con el contrato; no hay
contradicción con el encargo.

Modo `template`: la línea procede de una plantilla de workflow. Se admiten los
marcadores angulares que empiezan por «instrucción» y `<módulo>`; cualquier
`[...]` sigue siendo error. En ese modo no se exige ni se mira nada que haya
debajo (la plantilla es una sola línea), así que no hay `SIN_MODELO` ni
`NO_ES_LO_ULTIMO`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# ------------------------------------------------------------------ Códigos

SIN_SIGUIENTE = "SIN_SIGUIENTE"
FORMATO_HEREDADO = "FORMATO_HEREDADO"
VARIAS_LINEAS_SIGUIENTE = "VARIAS_LINEAS_SIGUIENTE"
EN_BLOQUE_DE_CODIGO = "EN_BLOQUE_DE_CODIGO"
COMILLAS_EN_COMANDO = "COMILLAS_EN_COMANDO"
COMANDO_INTERNO = "COMANDO_INTERNO"
COMANDO_DESCONOCIDO = "COMANDO_DESCONOCIDO"
SIN_COMANDO = "SIN_COMANDO"
MARCADOR = "MARCADOR"
INSTRUCCION_VACIA = "INSTRUCCION_VACIA"
NO_ES_LO_ULTIMO = "NO_ES_LO_ULTIMO"
MODELO_MAL_FORMADO = "MODELO_MAL_FORMADO"

INSTRUCCION_VAGA = "INSTRUCCION_VAGA"
DEMASIADO_LARGA = "DEMASIADO_LARGA"
SIN_MODELO = "SIN_MODELO"
DECISION_EN_CICLO = "DECISION_EN_CICLO"
PROHIBICIONES = "PROHIBICIONES"
INSTRUCCION_LARGA = "INSTRUCCION_LARGA"
PALABRAS_PEGADAS = "PALABRAS_PEGADAS"

ERROR_CODES: tuple[str, ...] = (
    SIN_SIGUIENTE, FORMATO_HEREDADO, VARIAS_LINEAS_SIGUIENTE, EN_BLOQUE_DE_CODIGO, COMILLAS_EN_COMANDO, COMANDO_INTERNO,
    COMANDO_DESCONOCIDO, SIN_COMANDO, MARCADOR, INSTRUCCION_VACIA, NO_ES_LO_ULTIMO, MODELO_MAL_FORMADO,
)
WARNING_CODES: tuple[str, ...] = (  # 5.3.2 (FW-402): PROHIBICIONES e INSTRUCCION_LARGA avisan, no bloquean
    INSTRUCCION_VAGA, DEMASIADO_LARGA, SIN_MODELO, DECISION_EN_CICLO, PALABRAS_PEGADAS, PROHIBICIONES, INSTRUCCION_LARGA,
)
ALL_CODES: tuple[str, ...] = ERROR_CODES + WARNING_CODES

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "aviso"

MODES = ("live", "template")

# Formas de la línea de cierre.
FORM_COMMAND = "comando"
FORM_CICLO_FASE = "ciclo_fase"
FORM_NINGUNO = "ninguno"
FORM_DECISION = "decision"
FORM_SIN_COMANDO = "sin_comando"

# ------------------------------------------------------------ Listas cerradas

MAX_CLOSE_LINE_CHARS = 400
# FW-360: la instrucción es una acción, no un plan. Lo que va entre comillas
# invertidas cuenta como una palabra.
MAX_INSTRUCTION_WORDS = 40

# Instrucciones que, por sí solas, no dicen qué hacer. Se comparan sin tildes,
# en minúsculas y sin puntuación; una instrucción es vaga si, quitadas estas
# frases y las conjunciones «o»/«y», no queda nada.
VAGUE_PHRASES: tuple[str, ...] = (
    "o el comando que aplique",
    "cuando quieras",
    "continuar",
    "retomar",
)

# Primer verbo que delata una decisión pendiente (aprobar, decidir, confirmar,
# responder, elegir, esperar), en infinitivo e imperativo (tú/vosotros/usted),
# sin tildes y en minúsculas.
DECISION_VERBS: frozenset[str] = frozenset({
    "aprobar", "aprueba", "aprobad", "apruebe",
    "decidir", "decide", "decidid", "decida",
    "confirmar", "confirma", "confirmad", "confirme",
    "responder", "responde", "responded", "responda",
    "elegir", "elige", "elegid", "elija",
    "esperar", "espera", "esperad", "espere",
})

# FW-348: la misma decisión pendiente escrita sin verbo delante («Pendiente del
# builder: …», «Pendiente de tu aprobación …»). Se compara sin tildes y en
# minúsculas, y solo al principio: «Implementar lo pendiente del módulo» no lo es.
DECISION_PENDING_RE = re.compile(
    r"^\W*pendientes?\s+(?:"
    r"del\s+builder\b"
    r"|de\s+(?:tu\s+|la\s+|su\s+)?(?:decision|aprobacion|confirmacion|respuesta|visto\s+bueno)\b"
    r"|de\s+que\s+(?:el\s+builder\s+)?(?:decidas?|apruebes?|confirmes?|respondas?|elijas?)\b"
    r")"
)

CICLO_FASE_INSTRUCTIONS = ("fase", "fase --paralelo")

# FW-324: registro único del vocabulario retirado (término → qué usar). Lo
# cuentan la auditoría (`turn_close_audit.py --vocabulary`, que debe dar 0 en
# las superficies activas) y el doctor, que avisa en `AGENTS.md` y `CLAUDE.md`
# fuera del bloque gestionado (FW-313).
_TIER = "el tier (tier_0, tier_1, tier_2)"
_ONE_LINE = "una sola línea «Siguiente: /comando instrucción»"
RETIRED_VOCABULARY: dict[str, str] = {
    "Siguiente comando:": _ONE_LINE,
    "Prompt sugerido:": _ONE_LINE,
    "Medium/High": _TIER,
    "Low effort": _TIER,
    "High effort": _TIER,
    "ESFUERZO:": "«Esfuerzo: bajo|medio|alto|xhigh»",
    "Framework v3": "«el framework» (la versión vive en el manifest)",
    "Framework v4": "«el framework» (la versión vive en el manifest)",
    "workflows v3": "«los workflows de .agent/workflows/»",
    "declare-interface": "/enriquecer-ticket (paso 5.2)",
    "new_module_guard": "/enriquecer-ticket (paso 5.2)",
    "caveman.md": "nada: no existe",
    "/revisar": "los subagentes reviewer o preflight-reviewer",
    "respuesta de workflow": "«todo turno que devuelve el control»",
    "Workflow Antigravity": "el nombre del workflow, sin sufijo",
    "## Steps": "«## Pasos»",
    "FLOW OUTPUT": "«SALIDA»",
    "VERIFICATION ECHO": "«ECO DE VERIFICACIÓN»",
}

_EFFORT_EMOJI = "🟢🟡🟠🔴"
MODEL_LEVELS = ("strong", "standard", "limited")
EFFORT_LEVELS = ("bajo", "medio", "alto", "xhigh")
TIERS = ("tier_0", "tier_1", "tier_2")

# ----------------------------------------------------------------- Regex

_ADORN = r"[ \t]*(?:>[ \t]*)*(?:\*\*|__|\*|_)?`{0,2}"
_SIG_RE = re.compile(rf"^(?P<pre>{_ADORN})Siguiente(?P<mid>[*_]{{0,2}}):(?P<post>[*_]{{0,2}})(?P<rest>.*)$")
_LEGACY_RE = re.compile(rf"^{_ADORN}(?:Siguiente comando|Prompt sugerido)[*_]{{0,2}}[ \t]*:")
_MODEL_LINE_RE = re.compile(rf"^{_ADORN}Modelo[*_]{{0,2}}[ \t]*:")
_FENCE_RE = re.compile(r"^[ \t]*(?P<f>`{3,}|~{3,})(?P<tail>.*)$")
_MODEL_FULL_RE = re.compile(
    r"^Modelo\s*:\s*(?P<modelo>strong|standard|limited)(?:\s*\((?P<detalle>[^()]*(?:\([^()]*\)[^()]*)*)\))?"
    r"\s*·\s*Esfuerzo\s*:\s*(?:[" + _EFFORT_EMOJI + r"]\ufe0f?\s*)?(?P<esfuerzo>bajo|medio|alto|xhigh)"
    r"\s*\((?P<tier>tier_[012])\)\s*$"
)
_CODE_SPAN_RE = re.compile(r"`[^`\n]*`")
_SQUARE_RE = re.compile(r"\[[^\[\]\n]*\](?!\()")  # no cuenta los enlaces markdown [txt](url)
_ANGLE_RE = re.compile(r"<(?P<inner>[^\s<>=][^<>\n]{0,80})>")
_NINGUNO_RE = re.compile(r"^ninguno\b", re.IGNORECASE)
_DECISION_RE = re.compile(r"^decisi[oó]n\b", re.IGNORECASE)
_SEPARATOR_RE = re.compile(r"^(?:\s*[—–]\s*|\s+-(?:\s+|$))(?P<text>.*)$", re.DOTALL)
_COMMAND_RE = re.compile(r"^/(?P<name>\S*)")
# FW-360: cláusulas «sin …» y los «ni» que las encadenan («sin integrar ni
# modificar …»). Palabra suelta: no cuentan `sin_tests.py` ni las rutas.
_SIN_RE = re.compile(r"(?<![\w/.-])sin(?![\w/.-])", re.IGNORECASE)
_NI_RE = re.compile(r"(?<![\w/.-])ni(?![\w/.-])", re.IGNORECASE)
# FW-362: palabra pegada a una sigla («antesCODE») o a cifras («sin8210»,
# «approvalrc0»), o cifras pegadas a una palabra («por8bugs»). Se aplica a cada
# palabra con `fullmatch`, nunca a la línea entera: así el escaneo es lineal.
_GLUED_RE = re.compile(r"[a-záéíóúüñ]{2,}(?:[A-Z]{2,}|\d+)\w*|\d+[a-záéíóúüñ]{3,}")
_GLUED_OK_RE = re.compile(
    r"(?:(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}"  # sha de git
    r"|macos|ipados|watchos|tvos|visionos|utf\d+|sha\d+|md5|base\d+|python\d[\d.]*|ipv[46]|x86|arm\d+"
    r"|mp[34]|win(?:32|64)|i18n|l10n|k8s|oauth2|http[23]|ssh2|py3"
    r"|\d+(?:px|ms|kb|mb|gb|min|seg|hrs|bits?|bytes?|tokens?|fps|dpi|rem|pt))",
    re.IGNORECASE,
)
_GLUED_MAX_WORD = 64  # una palabra más larga no es prosa: no se mira (la regex retrocede dentro de la palabra)
_PATH_SUFFIXES = (".py", ".md", ".json", ".jsonl", ".sh", ".toml", ".yaml", ".yml", ".txt", ".csv", ".js", ".ts",
                  ".html", ".log")


# ------------------------------------------------------------------ Tipos


@dataclass(frozen=True)
class Defect:
    code: str
    severity: str  # "error" | "aviso"
    line: int  # 1-based dentro del texto; 0 si no aplica
    fix: str  # frase accionable para el agente que debe corregir su respuesta


@dataclass(frozen=True)
class CloseParts:
    """Partes de la línea de cierre (ver `extract_close_parts`)."""

    found: bool = False  # hay una línea de cierre fuera de bloque de código
    line_no: int = 0
    raw: str = ""  # la línea tal cual, sin espacios finales
    form: str = ""  # comando | ciclo_fase | ninguno | decision | sin_comando
    command: str = ""  # nombre sin «/» (vacío si no hay comando)
    instruction: str = ""  # instrucción, o motivo en ninguno/decisión
    adornos: tuple[str, ...] = ()  # cita, negrita
    in_block_only: bool = False  # solo hay líneas Siguiente: dentro de bloques
    legacy: bool = False
    siguiente_count: int = 0  # líneas Siguiente: fuera de bloque
    model_line: str = ""
    model_line_no: int = 0
    modelo: str = ""
    esfuerzo: str = ""
    tier: str = ""
    model_ok: bool | None = None  # None si no hay línea de modelo


@dataclass(frozen=True)
class TurnCloseReport:
    parts: CloseParts
    defects: tuple[Defect, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------- Helpers


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def fold(value: str) -> str:
    """Minúsculas y sin tildes (la ñ también pierde su tilde: solo para comparar)."""
    decomposed = unicodedata.normalize("NFD", value.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def manifest_commands(manifest: dict[str, Any]) -> tuple[frozenset[str], frozenset[str]]:
    """(builder, internos) como conjuntos de nombres NFC sin «/»."""
    builder: set[str] = set()
    internal: set[str] = set()
    for entry in (manifest or {}).get("commands", []) or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        name = _nfc(str(entry["name"]))
        (builder if entry.get("builder_facing") else internal).add(name)
    return frozenset(builder), frozenset(internal)


def is_valid(defects: list[Defect] | tuple[Defect, ...]) -> bool:
    """True si no hay defectos de severidad `error` (los avisos no invalidan)."""
    return not any(d.severity == SEVERITY_ERROR for d in defects)


def _err(code: str, line: int, fix: str) -> Defect:
    return Defect(code, SEVERITY_ERROR, line, fix)


def _warn(code: str, line: int, fix: str) -> Defect:
    return Defect(code, SEVERITY_WARNING, line, fix)


def _words(instruction: str) -> int:
    return len(_CODE_SPAN_RE.sub("x", instruction).split())


def _prohibitions(instruction: str) -> int:
    """Cláusulas «sin …» más los «ni» que van detrás de la primera, fuera del código (FW-360)."""
    text = _CODE_SPAN_RE.sub(" ", instruction)
    first = _SIN_RE.search(text)
    if not first:
        return 0
    return len(_SIN_RE.findall(text)) + len(_NI_RE.findall(text, first.end()))


def _glued_words(lines: list[str]) -> tuple[int, list[str]]:
    """(primera línea, palabras pegadas) fuera de bloques de código, código en línea, URL y rutas (FW-362)."""
    found: list[str] = []
    first = 0
    fence_char = ""
    fence_len = 0
    for idx, line in enumerate(lines, start=1):
        fence = _FENCE_RE.match(line)
        if fence_char:
            if (
                fence
                and fence.group("f")[0] == fence_char
                and len(fence.group("f")) >= fence_len
                and not fence.group("tail").strip()
            ):
                fence_char, fence_len = "", 0
            continue
        if fence:
            fence_char, fence_len = fence.group("f")[0], len(fence.group("f"))
            continue
        hits = []
        for token in _CODE_SPAN_RE.sub(" ", line).split():
            word = token.strip(".,;:()[]{}«»\"'¿?¡!*_")
            if len(word) > _GLUED_MAX_WORD or "/" in word or "\\" in word or word.lower().endswith(_PATH_SUFFIXES):
                continue  # URL o ruta
            if _GLUED_RE.fullmatch(word) and not _GLUED_OK_RE.fullmatch(word):
                hits.append(word)
        if hits and not first:
            first = idx
        found.extend(hits)
    return first, found


def _scan_lines(lines: list[str]) -> tuple[list[tuple[int, re.Match[str]]], list[int], list[int]]:
    """Una pasada: líneas `Siguiente:` fuera de bloque, dentro de bloque y heredadas."""
    outside: list[tuple[int, re.Match[str]]] = []
    inside: list[int] = []
    legacy: list[int] = []
    fence_char = ""
    fence_len = 0
    for idx, line in enumerate(lines, start=1):
        fence = _FENCE_RE.match(line)
        if fence_char:
            if (
                fence
                and fence.group("f")[0] == fence_char
                and len(fence.group("f")) >= fence_len
                and not fence.group("tail").strip()
            ):
                fence_char, fence_len = "", 0
                continue
            if _SIG_RE.match(line):
                inside.append(idx)
            continue
        if fence:
            fence_char, fence_len = fence.group("f")[0], len(fence.group("f"))
            continue
        sig = _SIG_RE.match(line)
        if sig:
            outside.append((idx, sig))
        elif _LEGACY_RE.match(line):
            legacy.append(idx)
    return outside, inside, legacy


def _unwrap_ticks(rest: str, pre: str) -> tuple[str, bool]:
    """Quita las comillas invertidas que envuelven el comando o la línea."""
    wrapped = False
    if "`" in pre:
        wrapped = True
        if rest.count("`") % 2 == 1:
            pos = rest.rfind("`")
            rest = (rest[:pos] + rest[pos + 1:]).strip()
    elif rest.startswith("`"):
        wrapped = True
        match = re.match(r"^`([^`]*)`(.*)$", rest, re.DOTALL)
        if match:
            glue = " " if match.group(2) and not match.group(2)[0].isspace() else ""
            rest = (match.group(1) + glue + match.group(2)).strip()
        else:
            rest = rest.lstrip("`").strip()
    return rest, wrapped


def _attached_to_identifier(text: str, start: int) -> bool:
    """True si el carácter inmediatamente anterior es alfanumérico o «_» (items[0], Dict<str>)."""
    if start == 0:
        return False
    prev = text[start - 1]
    return prev.isalnum() or prev == "_"


def _find_marker(text: str, mode: str) -> str:
    """Primer marcador sin rellenar. Un `[...]` o `<...>` pegado a un identificador no lo es."""
    plain = _CODE_SPAN_RE.sub("", text)
    for match in _SQUARE_RE.finditer(plain):
        if not _attached_to_identifier(plain, match.start()):
            return match.group(0)
    for match in _ANGLE_RE.finditer(plain):
        if _attached_to_identifier(plain, match.start()):
            continue
        inner = match.group("inner").strip()
        if mode == "template":
            low = inner.lower()
            if low.startswith(("instrucción", "instruccion")) or low in ("módulo", "modulo"):
                continue
        return match.group(0)
    return ""


def _is_vague(instruction: str) -> bool:
    text = fold(instruction)
    text = re.sub(r"[^\w\s]", " ", text)
    for phrase in VAGUE_PHRASES:
        text = re.sub(rf"\b{re.escape(phrase)}\b", " ", text)
    text = re.sub(r"\b(?:o|y)\b", " ", text)
    return not text.strip() and bool(instruction.strip())


def _first_word(instruction: str) -> str:
    match = re.match(r"\s*[¿¡\W]*([\w]+)", instruction, re.UNICODE)
    return fold(match.group(1)) if match else ""


def _parse_model_line(line: str) -> tuple[bool, str, str, str]:
    text = line.strip()
    text = re.sub(r"^(?:>\s*)+", "", text).replace("**", "").replace("__", "").strip("` ")
    match = _MODEL_FULL_RE.match(text)
    if not match:
        return False, "", "", ""
    return True, match.group("modelo"), match.group("esfuerzo"), match.group("tier")


# ---------------------------------------------------------------- Núcleo


def analyze_turn_close(text: str, *, manifest: dict[str, Any], mode: str = "live") -> TurnCloseReport:
    """Una pasada sobre `text`: devuelve las partes del cierre y los defectos."""
    if mode not in MODES:
        raise ValueError(f"mode desconocido: {mode!r} (usa 'live' o 'template')")
    builder, internal = manifest_commands(manifest)
    lines = (text or "").rstrip().splitlines()
    defects: list[Defect] = []
    outside, inside, legacy = _scan_lines(lines)

    for lnum in legacy[:1]:
        defects.append(_err(
            FORMATO_HEREDADO, lnum,
            "Sustituye el formato heredado por una sola línea que empiece por «Siguiente: /<comando> <instrucción>» "
            "(contrato de decision_translation_core.md).",
        ))

    if not outside:
        if inside:
            defects.append(_err(
                EN_BLOQUE_DE_CODIGO, inside[-1],
                "Saca la línea «Siguiente:» del bloque de código: debe ir como texto normal, última línea de la respuesta.",
            ))
            parts = CloseParts(in_block_only=True, line_no=inside[-1], legacy=bool(legacy))
        else:
            defects.append(_err(
                SIN_SIGUIENTE, 0,
                "Termina la respuesta con una línea «Siguiente: /<comando> <instrucción concreta>» "
                "(o «Siguiente: ninguno — <motivo>» / «Siguiente: decisión — <qué contestar>»).",
            ))
            parts = CloseParts(legacy=bool(legacy))
        return TurnCloseReport(parts, tuple(defects))

    if len(outside) > 1:
        defects.append(_err(
            VARIAS_LINEAS_SIGUIENTE, outside[1][0],
            "Deja una sola línea «Siguiente:», la última de la respuesta; une o elimina las demás.",
        ))
    lnum, match = outside[-1]
    raw_line = lines[lnum - 1].rstrip()
    pre, mid, post = match.group("pre"), match.group("mid"), match.group("post")
    adornos: list[str] = []
    if ">" in pre:
        adornos.append("cita")
    if re.search(r"[*_]", pre + mid + post):
        adornos.append("negrita")

    rest = match.group("rest").strip()
    if re.search(r"[*_]", pre + mid + post):
        while rest.endswith(("**", "__")):
            rest = rest[:-2].rstrip()
    rest, wrapped = _unwrap_ticks(rest, pre)
    if wrapped:
        defects.append(_err(
            COMILLAS_EN_COMANDO, lnum,
            "Quita las comillas invertidas que envuelven el comando o la línea «Siguiente:»: debe poder copiarse tal cual.",
        ))

    form = FORM_SIN_COMANDO
    command = ""
    instruction = ""
    cmd_match = _COMMAND_RE.match(rest)
    if cmd_match:
        command = _nfc(cmd_match.group("name"))
        instruction = rest[cmd_match.end():].strip()
        if command == "ciclo" and re.sub(r"\s+", " ", instruction).lower() in CICLO_FASE_INSTRUCTIONS:
            form = FORM_CICLO_FASE
        else:
            form = FORM_COMMAND
        if command in internal:
            defects.append(_err(
                COMANDO_INTERNO, lnum,
                f"«/{command}» es maquinaria interna: cierra con un comando del builder "
                "(/arrancar, /reconciliar, /gycd, /diseñar, /nuevo-bug, /nuevo-proyecto, /migrar-proyecto, "
                "/actualizar-framework o /ciclo).",
            ))
        elif command not in builder:
            defects.append(_err(
                COMANDO_DESCONOCIDO, lnum,
                f"«/{command}» no existe en el manifest: usa un comando del builder o corrige el nombre.",
            ))
        if form == FORM_COMMAND and not instruction:
            defects.append(_err(
                INSTRUCCION_VACIA, lnum,
                f"Añade tras «/{command}» una instrucción concreta y autosuficiente (qué hacer, dónde, con qué límite).",
            ))
    else:
        for form_name, regex in ((FORM_NINGUNO, _NINGUNO_RE), (FORM_DECISION, _DECISION_RE)):
            head = regex.match(rest)
            if not head:
                continue
            tail = rest[head.end():]
            sep = _SEPARATOR_RE.match(tail)
            if sep:
                form, instruction = form_name, sep.group("text").strip()
            elif not tail.strip():
                form, instruction = form_name, ""
            else:
                break  # «ninguno» o «decisión» seguido de texto sin separador: no es esa forma
            if not instruction:
                what = "el motivo" if form == FORM_NINGUNO else "qué debe contestar el builder y dónde"
                defects.append(_err(
                    INSTRUCCION_VACIA, lnum,
                    f"Tras «{head.group(0)} —» escribe {what}.",
                ))
            break
        if form == FORM_SIN_COMANDO:
            defects.append(_err(
                SIN_COMANDO, lnum,
                "Empieza la instrucción por un comando «/…» del builder, o usa «ninguno — <motivo>» "
                "o «decisión — <qué contestar>».",
            ))

    marker = _find_marker(rest, mode)
    if marker:
        defects.append(_err(
            MARCADOR, lnum,
            f"Sustituye el marcador sin rellenar {marker!r} por el valor real.",
        ))

    if form in (FORM_COMMAND, FORM_DECISION, FORM_NINGUNO) and _words(instruction) > MAX_INSTRUCTION_WORDS:
        defects.append(_warn(
            INSTRUCCION_LARGA, lnum,
            f"La instrucción pasa de {MAX_INSTRUCTION_WORDS} palabras: deja una sola acción concreta (qué y dónde) "
            "y mueve el detalle al cuerpo de la respuesta o al plan.",
        ))
    if form == FORM_COMMAND and _prohibitions(instruction) > 1:
        defects.append(_warn(
            PROHIBICIONES, lnum,
            "«Siguiente:» propone una acción y no lleva prohibiciones («sin X ni Y»): quítalas, que las "
            "restricciones solo las escribe el builder. Si necesitas su permiso o un límite, pregúntale con "
            "«Siguiente: decisión — …».",
        ))

    # Línea de modelo y texto posterior.
    model_line = ""
    model_line_no = 0
    modelo = esfuerzo = tier = ""
    model_ok: bool | None = None
    if mode == "live":
        glued_line, glued = _glued_words(lines)
        if glued:
            defects.append(_warn(
                PALABRAS_PEGADAS, glued_line,
                "Separa con un espacio las palabras de las cifras y las siglas: "
                + ", ".join(list(dict.fromkeys(glued))[:3]) + ".",
            ))
        pending_after = None
        for off in range(lnum, len(lines)):
            if not lines[off].strip():
                continue
            if not model_line and _MODEL_LINE_RE.match(lines[off]):
                model_line, model_line_no = lines[off].rstrip(), off + 1
                continue
            pending_after = off + 1
            break
        if pending_after:
            defects.append(_err(
                NO_ES_LO_ULTIMO, pending_after,
                "No escribas nada después de la línea «Siguiente:» salvo la línea «Modelo: … · Esfuerzo: …».",
            ))
        if model_line:
            model_ok, modelo, esfuerzo, tier = _parse_model_line(model_line)
            if not model_ok:
                defects.append(_err(
                    MODELO_MAL_FORMADO, model_line_no,
                    "Escribe «Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier_0|tier_1|tier_2>)».",
                ))
        elif form in (FORM_COMMAND, FORM_CICLO_FASE):
            defects.append(_warn(
                SIN_MODELO, lnum,
                "Añade justo debajo de «Siguiente:» la línea «Modelo: … · Esfuerzo: … (tier)» cuando el tier sea conocido.",
            ))

    if form == FORM_COMMAND and instruction and _is_vague(instruction):
        defects.append(_warn(
            INSTRUCCION_VAGA, lnum,
            "La instrucción no dice qué hacer: nombra el objeto concreto (módulo, fichero, objetivo) para que cualquier modelo la ejecute sin contexto.",
        ))
    if len(raw_line) > MAX_CLOSE_LINE_CHARS:
        defects.append(_warn(
            DEMASIADO_LARGA, lnum,
            f"La línea de cierre supera {MAX_CLOSE_LINE_CHARS} caracteres: reduce la instrucción a lo necesario y suficiente.",
        ))
    if form == FORM_COMMAND and command == "ciclo" and (
        _first_word(instruction) in DECISION_VERBS or DECISION_PENDING_RE.match(fold(instruction))
    ):
        defects.append(_warn(
            DECISION_EN_CICLO, lnum,
            "Esto es una decisión pendiente, no trabajo para /ciclo: usa «Siguiente: decisión — <qué contestar y dónde>».",
        ))

    parts = CloseParts(
        found=True, line_no=lnum, raw=raw_line, form=form, command=command, instruction=instruction,
        adornos=tuple(adornos), in_block_only=False, legacy=bool(legacy), siguiente_count=len(outside),
        model_line=model_line, model_line_no=model_line_no, modelo=modelo, esfuerzo=esfuerzo, tier=tier,
        model_ok=model_ok,
    )
    return TurnCloseReport(parts, tuple(defects))


def validate_turn_close(text: str, *, manifest: dict[str, Any], mode: str = "live") -> list[Defect]:
    """Defectos del cierre de `text`; lista vacía si es válido y sin avisos."""
    return list(analyze_turn_close(text, manifest=manifest, mode=mode).defects)


def extract_close_parts(text: str, *, manifest: dict[str, Any], mode: str = "live") -> CloseParts:
    """Partes de la línea de cierre (forma, comando, instrucción, línea de modelo)."""
    return analyze_turn_close(text, manifest=manifest, mode=mode).parts


# ------------------------------------------- Plantillas de cierre en documentos
#
# Un workflow o una skill no emite un cierre real: lleva PLANTILLAS de cierre
# (la línea que ordena al agente qué escribir al terminar). Este escaneo les
# aplica el mismo contrato que a un cierre vivo, con `mode="template"`, más dos
# reglas propias de un documento (decisión D1: la lógica vive aquí, no en el
# doctor ni en la auditoría):
#
# - `EN_BLOQUE_DE_CODIGO` (código existente): la plantilla está dentro de un
#   bloque con valla; el agente la copiaría como código y no como cierre.
# - `SIN_MODELO` (aquí como error; en `live` sigue siendo aviso): la plantilla
#   tiene forma de comando y la siguiente línea no vacía del documento no es la
#   línea «Modelo: … · Esfuerzo: …». «ninguno» y «decisión» no la exigen.

_DOC_FENCE_RE = re.compile(r"^[ \t>]*(`{3,}|~{3,})")
_DOC_MODEL_NEXT_RE = re.compile(r"^[ \t>*_`-]*Modelo[*_]{0,2}[ \t]*:.*Esfuerzo")
_TEMPLATE_PROBE_RE = re.compile(r"(?:[/\[<]|ninguno\b|decisi[oó]n\b)", re.IGNORECASE)


@dataclass(frozen=True)
class TemplateFinding:
    """Una plantilla de cierre hallada en un documento."""

    line: int  # 1-based en el documento
    text: str  # texto limpio de la plantilla («Siguiente: …»)
    defects: tuple[Defect, ...] = ()  # todos (errores y avisos); `line` = línea del documento
    form: str = ""  # forma detectada (FORM_*)

    @property
    def errors(self) -> tuple[Defect, ...]:
        return tuple(d for d in self.defects if d.severity == SEVERITY_ERROR)

    @property
    def codes(self) -> list[str]:
        """Códigos de los defectos de severidad error, en orden."""
        return [d.code for d in self.errors]

    @property
    def defective(self) -> bool:
        return bool(self.errors)


def template_candidate(line: str) -> tuple[str, bool] | None:
    """(texto limpio de la plantilla, es_plantilla). None si la línea no contiene `Siguiente:`.

    Es plantilla si tras `Siguiente:` viene un comando, un marcador, `ninguno` o
    `decisión`; si no, es una mención en prosa (p. ej. «la línea `Siguiente:` con…»).
    """
    idx = line.find("Siguiente:")
    if idx < 0:
        return None
    prefix, after = line[:idx], line[idx + len("Siguiente:"):]
    probe = after.lstrip(" \t*_`")
    if not _TEMPLATE_PROBE_RE.match(probe):
        return line.strip(), False
    if prefix.rstrip().endswith("`") and after.count("`") % 2 == 1:
        pos = after.rfind("`")
        after = after[:pos] + after[pos + 1:]
    return "Siguiente:" + after.rstrip(), True


def template_mentions(text: str) -> list[int]:
    """Líneas (1-based) con `Siguiente:` en prosa: mencionan el cierre sin ser plantilla."""
    found: list[int] = []
    for number, raw in enumerate((text or "").splitlines(), start=1):
        cand = template_candidate(raw)
        if cand is not None and not cand[1]:
            found.append(number)
    return found


def scan_templates(text: str, *, manifest: dict[str, Any]) -> list[TemplateFinding]:
    """Plantillas de cierre de un documento markdown, cada una con sus defectos.

    Cada candidata (ver `template_candidate`) pasa por
    `validate_turn_close(..., mode="template")`; a eso se añaden `EN_BLOQUE_DE_CODIGO`
    y `SIN_MODELO` (ambos como error). El `line` de todos los defectos es la línea
    del documento. Las menciones en prosa no se validan (ver `template_mentions`).
    """
    lines = (text or "").splitlines()
    findings: list[TemplateFinding] = []
    in_fence = False
    for idx, raw in enumerate(lines):
        if _DOC_FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        cand = template_candidate(raw)
        if cand is None or not cand[1]:
            continue
        number = idx + 1
        report = analyze_turn_close(cand[0], manifest=manifest, mode="template")
        defects = [Defect(d.code, d.severity, number, d.fix) for d in report.defects]
        if in_fence:
            defects.append(_err(
                EN_BLOQUE_DE_CODIGO, number,
                "Saca la plantilla «Siguiente:» del bloque de código: debe ir como texto normal.",
            ))
        form = report.parts.form
        if form in (FORM_COMMAND, FORM_CICLO_FASE):
            following = next((ln for ln in lines[idx + 1:] if ln.strip()), "")
            if not _DOC_MODEL_NEXT_RE.match(following):
                defects.append(_err(
                    SIN_MODELO, number,
                    "Añade justo debajo de la plantilla la línea «Modelo: … · Esfuerzo: … (tier)».",
                ))
        findings.append(TemplateFinding(number, cand[0], tuple(defects), form))
    return findings
