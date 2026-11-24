#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production Command Guard — frontera mecánica contra comandos destructivos.

FW-082 / ADR-051. El caso Replit/Lemkin demostró que una regla en prosa ("no
toques la BD"), aunque se escriba 11 veces, se degrada en contexto grande y el
agente la ignora. Este guardia NO es prosa: intercepta el comando en runtime
(hook `PreToolUse` de Claude Code, `permission.bash` de OpenCode) ANTES de que
se ejecute, y lo deniega si es destructivo.

Diseño (aprobado por el builder 2026-07-05):
- Patrones **estrechos** para no reincidir en el villano histórico (FW-034/037/
  038/040 — el falso positivo que hace que el guardia se desactive).
- `rm -rf` en **punto medio**: destinos efímeros conocidos (node_modules, dist,
  build…) pasan en silencio; rutas absolutas/raíz/home, variables sin expandir,
  globs amplios y rutas relativas desconocidas requieren el marcador.
- Escotilla: marcador explícito `# APPROVED-DESTRUCTIVE: <razón>` en el
  comentario real del comando (FW-228: dentro de un dato entrecomillado no vale).
  El agente no puede ponerlo sin el ok del builder (política, no mecanismo).
- **fail-closed** ante match positivo; **fail-open** si el evento es basura o
  no es un Bash (no brickear el entorno). FW-237 (decisión del builder
  2026-09-14): un Bash que no se llega a analizar — más de 128 KB, más de 20 s o
  un fallo del análisis — se deniega con motivo; salir sin veredicto dejaba
  pasar el comando.
- FW-228 (4.40.0): lexer con operadores como tokens (`;` pegado, salto de línea,
  paréntesis) y sustituciones `$(…)` evaluadas aparte. Se juzga TODO salvo lo
  demostrado dato: el cuerpo de un heredoc a `cat`/`tee`/`git`/`gh` y el
  argumento de un carrier, solo si nada en la línea puede ejecutarlos (pipe a un
  programa, sustitución de proceso, fichero escrito y ejecutado).

FW-236 (5.2, ADR-071): tres veredictos. `deny` para lo directo; `ask` (el host
pide confirmación) para la ejecución INDIRECTA de algo destructivo, en tres
clases por forma sintáctica: orden guardada en una variable y ejecutada,
decodificar (`base64 -d`, `xxd -r`…) y ejecutar, y fichero que otro programa
ejecuta sin nombrarlo (`Makefile` + `make`, `package.json` + `npm run`, hook de
git). `deny` gana siempre. Falsos positivos que dejan de bloquear, cada uno con su
prueba de seguridad: `grep 'rm -rf' … | sed -n`, heredoc de datos por `ssh` a un
`cat`/`tee` fijo en /tmp, `shellcheck` de un script escrito y `rm -rf <relativo>`
con el cwd del evento bajo un prefijo temporal. Codex no admite `ask`: su runner
lo traduce a `deny`.

S3 (5.3.1 L1): el guard juzga lo que ejecuta el shell. El código en línea para un
intérprete (`python3 -c`, `python3 - <<EOF`, `node -e`, `ruby -e`, `perl -e`,
`php -r`) es un script en fichero: no se buscan palabras dentro. Se juzga la
orden que lanza con argumentos literales (spawn) y, si su salida puede acabar en
un shell, el código entero. `wal_checkpoint(TRUNCATE)` nunca cuenta.

Límites declarados (no es un sandbox): no ve código ofuscado de otras formas,
scripts ya existentes ejecutados por ruta (`bash limpiar.sh`), una orden guardada
en una variable de un comando anterior, ni lo que hace un intérprete salvo las
órdenes literales que lanza (borrados con su API, SQL de un driver, ficheros que
escribe). La resolución de rutas relativas depende de
que el `cwd` del evento sea el del shell (no verificado empíricamente) y no se
aplica si el comando puede cambiar de directorio. OpenCode no ejecuta este script:
usa los globs de `opencode.json`, que no ven la ejecución indirecta.

Uso como hook (Claude Code PreToolUse): lee el JSON del evento por stdin y emite
`hookSpecificOutput.permissionDecision = "deny"` o `"ask"` si procede. Uso como
librería: `is_destructive(command, cwd=None) -> (blocked: bool, reason: str)`
(solo `deny`) y `judge(command, cwd=None) -> ("pass"|"ask"|"deny", reason)`.

NUNCA invocar este script por ruta relativa al cwd de la sesión (TL FW-013):
si el agente queda parado en un subdirectorio tras un `cd`, `python3
.agent/scripts/guard_destructive_command.py` no lo encuentra y, al ser el
hook `PreToolUse` que corre ANTES de cualquier Bash, todo Bash queda
bloqueado — incluido el `cd` de rescate para volver a la raíz (interbloqueo
real 2026-07-19, Example Lab). Invocar siempre por ruta absoluta: en
Claude Code, `.claude/settings.json` lo hace vía `$CLAUDE_PROJECT_DIR`
(variable que Claude Code expone a los hooks con la raíz del proyecto).
"""
from __future__ import annotations

import ast
import json
import re
import shlex
import signal
import sys

# --- Límites de análisis (FW-237) --------------------------------------------
# El guard corre como hook con tiempo limitado. 128 KB es 6 veces el mayor
# comando real medido (19,9 KB sobre 15.399) y acota el término cuadrático de
# `shlex` (<1 s en las formas medidas). Quedan formas superlineales raras por
# debajo del límite (muchos `<<A` en una línea, `$(` anidados, patrones
# `[^\n]*` en líneas enormes): las para el límite de tiempo, holgado bajo el del
# host. Sin `signal.setitimer` (Windows nativo, no soportado) no hay límite de
# tiempo.
_MAX_COMMAND_BYTES = 128 * 1024
_ANALYSIS_TIME_LIMIT_S = 20.0

# --- Escotilla de escape ----------------------------------------------------
# Marcador con razón no vacía tras los dos puntos.
_APPROVED_RE = re.compile(r"#\s*APPROVED-DESTRUCTIVE:\s*\S+", re.IGNORECASE)

# --- Patrones destructivos (BD / migraciones / prod) ------------------------
_DB_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # FW-203: `[\s:]` cubre `db push` y `db:push` — esta última es la convención
    # de nombres de script npm/pnpm/yarn y el comando exacto del incidente
    # Replit/Lemkin (`npm run db:push`), que `\bdb\s+push\b` dejaba pasar. Se
    # exige `push` literal tras el separador (NO `\w+`): `db:seed` / `db:migrate`
    # / `db:generate` son scripts rutinarios y no deben bloquear (lección FW-034).
    (re.compile(r"\bdb[\s:]+push\b", re.IGNORECASE),
     "empuja el esquema a la BD sin migración reversible (db push)"),
    (re.compile(r"\bdrizzle-kit\s+push", re.IGNORECASE),
     "drizzle-kit push reescribe el esquema en caliente"),
    # FW-203: subcomandos fijos, inequívocos — recrean la base descartando datos.
    (re.compile(r"\bsupabase\s+db\s+reset\b", re.IGNORECASE),
     "supabase db reset recrea la base local descartando todos los datos"),
    (re.compile(r"\bprisma\s+migrate\s+reset\b", re.IGNORECASE),
     "prisma migrate reset dropea y recrea la base"),
    (re.compile(r"\bDROP\s+(TABLE|DATABASE|SCHEMA)\b", re.IGNORECASE),
     "DROP destruye tablas/bases enteras"),
    (re.compile(r"\bTRUNCATE\b", re.IGNORECASE),
     "TRUNCATE vacía la tabla sin vuelta atrás"),
    (re.compile(r"\bmigrate\b[^\n]*--force\b", re.IGNORECASE),
     "migración forzada (--force) puede perder datos"),
    # FW-229 (decisión del builder 2026-09-14): pide aprobación todo push que
    # reescribe o borra el remoto. `--force-with-lease` (y `--force-if-includes`)
    # pasan: fallan si otro empujó entre medias, que es lo que protege la historia.
    (re.compile(r"\bgit\s+push\b[^\n]*(?:--force(?![-\w])|\s-[A-Za-z]*f[A-Za-z]*\b)", re.IGNORECASE),
     "git push --force reescribe la historia remota"),
    (re.compile(r"\bgit\s+push\b[^\n]*\s\+\S", re.IGNORECASE),
     "git push con refspec `+rama` fuerza la actualización y reescribe la historia remota"),
    (re.compile(r"\bgit\s+push\b[^\n]*\s--mirror\b", re.IGNORECASE),
     "git push --mirror sobrescribe y borra refs del remoto"),
    (re.compile(r"\bgit\s+push\b[^\n]*\s(?:--delete\b|-d\b|--prune\b|:\S)", re.IGNORECASE),
     "git push --delete / --prune / `:rama` borra ramas remotas"),
    # Sintaxis alternativas destructivas (double check FW-082, revisor independiente):
    (re.compile(r"\bfind\b[^\n]*\s-delete\b", re.IGNORECASE),
     "find -delete borra recursivamente (alternativa a rm -rf)"),
    (re.compile(r"\bdd\b[^\n]*\bof=\s*(?:/dev/|/proc/|/sys/)", re.IGNORECASE),
     "dd of=/dev/... sobrescribe un disco/dispositivo entero"),
    (re.compile(r"\b(?:mongo|mongosh)\b[^\n]*dropDatabase", re.IGNORECASE),
     "mongo dropDatabase destruye la base de datos entera"),
    (re.compile(r"\b(?:mongo|mongosh)\b[^\n]*deleteMany\s*\(\s*\{\s*\}\s*\)", re.IGNORECASE),
     "mongo deleteMany({}) sin filtro borra todos los documentos"),
    (re.compile(r"\bredis-cli\b[^\n]*\b(?:FLUSHALL|FLUSHDB)\b", re.IGNORECASE),
     "redis FLUSHALL/FLUSHDB vacía la base entera"),
    # FW-086: reset/clean destructivos (con calibración de falsos positivos —
    # --soft, --mixed, reset de unstage y clean --dry-run NO matchean).
    # 5.3.1 L1.4: sobre una rama compartida es del núcleo irreversible (regla nativa).
    (re.compile(r"\bgit\s+reset\b[^\n]*--hard\s+(?:origin/|upstream/|@\{u\}|@\{upstream\})", re.IGNORECASE),
     "git reset --hard sobre una rama compartida descarta el trabajo local para igualar el remoto"),
    (re.compile(r"\bgit\s+reset\b[^\n]*--hard\b", re.IGNORECASE),
     "git reset --hard descarta cambios/commits locales sin confirmación"),
    (re.compile(r"\bgit\s+clean\b[^\n]*\s(?:-[a-z]*f[a-z]*\b|--force\b)", re.IGNORECASE),
     "git clean -f borra archivos no rastreados irrecuperablemente"),
]

# DELETE FROM sin WHERE (heurística: no aparece WHERE en la sentencia).
_DELETE_RE = re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE)
_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)

# Sync dev→prod: rsync/scp cuyo destino menciona producción.
_SYNC_RE = re.compile(r"\b(rsync|scp)\b", re.IGNORECASE)
_PROD_TOKEN_RE = re.compile(r"\b(prod|production|prd)\b", re.IGNORECASE)

# --- rm -rf: allowlist de destinos efímeros ---------------------------------
_SAFE_RM_NAMES = {
    "node_modules", "dist", "build", ".next", "out", "target",
    "coverage", ".cache", ".pytest_cache", "__pycache__", ".turbo",
    ".parcel-cache", ".nuxt", ".svelte-kit",
}


# --- FW-228: lexer con puntuación y escáner de heredocs/sustituciones --------
# `shlex.split` dejaba `;`/`&&` PEGADOS al token anterior (`'hi;'`, `'ok&&rm'`) y
# convertía el salto de línea en espacio: `echo hi; rm -rf ~` era UN segmento que
# empezaba por `echo` y el atajo de carrier lo aprobaba. Con `punctuation_chars`
# los operadores salen como tokens propios y el salto de línea, fuera de
# comillas, es un separador más.
#
# Principio tras la revisión tier_2 de 4.40.0: TODO se juzga salvo lo DEMOSTRADO
# dato. El primer diseño hacía lo contrario (dato por defecto, código solo si
# reconocía al ejecutor) y reconocer ejecutores no tiene cierre: `sudo -u
# postgres psql`, `docker exec … psql`, `| tee log | sh` abrían 20 regresiones.
_PUNCTUATION = ";&|()\n<>`"
_SEPARATOR_CHARS = frozenset(";&|()\n`")
_FALLBACK_TOKEN_RE = re.compile(r"[;&|()\n`<>]+|[^\s;&|()`<>]+")
_MAX_DEPTH = 8
_SUB_PLACEHOLDER = "$FW228SUB{}_"
_SUB_PLACEHOLDER_RE = re.compile(r"\$FW228SUB(\d+)_")


def _lex(text: str) -> list[str]:
    """Tokens de shell con los operadores como tokens propios. Los comentarios ya
    los retiró `_scan`: aquí `#` es un carácter más (`~/x#y` no se trunca)."""
    lexer = shlex.shlex(text, posix=True, punctuation_chars=_PUNCTUATION)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        # comillas sin cerrar → troceo por operadores (fail-closed aguas arriba)
        return _FALLBACK_TOKEN_RE.findall(text)


def _is_separator(tok: str) -> bool:
    """`;`, `&&`, `||`, `|`, `&`, salto de línea, paréntesis, backtick y sus
    combinaciones pegadas (`;(`, `)|`). `<(`/`>(` abren una sustitución de
    proceso: también parten. `&>`, `2>` o `>&` son redirecciones."""
    if not tok:
        return False
    if set(tok) <= _SEPARATOR_CHARS:
        return True
    return "(" in tok and set(tok) <= _SEPARATOR_CHARS | {"<", ">"}


def _is_pipe_op(op: str) -> bool:
    """Operador que canaliza la salida: `|`, `|&`, y sus formas pegadas (`)|`,
    `|(`, `|` + salto de línea). `||` no canaliza."""
    return "|" in op.replace("||", "")


class _Scan:
    """Resultado de `_scan`: texto ejecutable y lo que hay que juzgar aparte."""

    def __init__(self) -> None:
        self.text = ""
        self.comment = ""
        # Sustituciones de nivel superior: el texto lleva `$FW228SUB<n>` en su sitio.
        self.substitutions: list[str] = []
        # (tokens del receptor, cuerpo, ¿expande `$(…)`/backticks?)
        self.heredocs: list[tuple[list[str], str, bool]] = []
        # Motivo si el texto tiene una forma que el escáner no delimita con fiabilidad.
        self.unparseable = ""


# Revisión 2026-09-15 (R2): el `)` de un patrón de `case` cierra la sustitución
# antes de tiempo para este escáner, pero no para bash ni zsh.
_CASE_START_RE = re.compile(r"(?:^|[\s;&|(`])case\s+\S+\s+in\b")
_ESAC_RE = re.compile(r"\besac\b")


def _truncated_case(substitution: str) -> bool:
    return bool(_CASE_START_RE.search(substitution)) and not _ESAC_RE.search(substitution)


# El delimitador es una palabra de shell: puede ir entrecomillado entero o a
# trozos (`E'OF'` es `EOF` para bash). Se lee la palabra y se le quitan comillas.
_HEREDOC_RE = re.compile(r"<<(-?)[ \t]*(?P<word>(?:'[^'\n]*'|\"[^\"\n]*\"|\\.|[^\s;&|()<>`'\"\\])+)")
_COMMENT_STARTERS = frozenset(" \t\n;&|(")


def _scan(command: str, *, first_substitution_only: bool = False) -> _Scan:
    """Recorre el comando respetando comillas y separa:

    - el **comentario real** (primer `#` a inicio de palabra, fuera de comillas):
      solo ahí vale el marcador APPROVED-DESTRUCTIVE;
    - el **cuerpo de cada heredoc**, que se retira del texto y juzga `_evaluate`;
    - cada **sustitución** `$(…)`/backtick de nivel superior, que se reemplaza por
      un marcador para saber en qué segmento está y se juzga recursivamente.

    Normaliza además lo que bash y `shlex` leerían distinto: continuación de
    línea, comillas ANSI-C, puntuación escapada o entrecomillada y here-strings.

    `first_substitution_only` para en cuanto cierra la primera sustitución de
    nivel superior: quien solo quiere esa no debe pagar el resto del texto
    (recorrerlo entero por cada `$(` de un heredoc era cuadrático, FW-237).
    """
    scan = _Scan()
    out: list[str] = []
    # ["D", inicio_out] comilla doble · ["P", inicio, profundidad, aritmética, inicio_out] · ["B", inicio, inicio_out]
    stack: list[list] = []
    pending: list[tuple[bool, str, list[str], bool, bool]] = []
    i, n = 0, len(command)

    def top_level() -> bool:
        # `"…"` y `${…}` no ocultan una sustitución: bash la ejecuta igual.
        return all(entry[0] in ("D", "V") for entry in stack)

    def close_substitution(start: int, out_start: int, end: int) -> None:
        if _truncated_case(command[start:end]):
            scan.unparseable = (
                "`case` dentro de una sustitución de comandos: el guard no sabe dónde termina; "
                "escríbelo en un script o sin sustitución"
            )
        if top_level():
            scan.substitutions.append(command[start:end])
            del out[out_start:]
            out.append(_SUB_PLACEHOLDER.format(len(scan.substitutions) - 1))

    while i < n and not (first_substitution_only and scan.substitutions):
        c = command[i]
        top = stack[-1][0] if stack else "N"
        if command.startswith("\\\n", i):
            i += 2  # continuación de línea: bash la elimina, no separa nada
            continue
        if top == "D":
            if c == "\\":
                out.append(command[i:i + 2])
                i += 2
            elif c == '"':
                start = stack.pop()[1]
                inner = "".join(out[start + 1:])
                if inner and set(inner) <= set(_PUNCTUATION):
                    del out[start:]
                    out.append("'\\" + inner + "'")  # `"("` es palabra, no separador
                else:
                    out.append(c)
                i += 1
            elif command.startswith("$(", i):
                stack.append(["P", i + 2, 0, False, len(out)])
                out.append("$(")
                i += 2
            elif c == "`":
                stack.append(["B", i + 1, len(out)])
                out.append(c)
                i += 1
            else:
                out.append(c)
                i += 1
            continue
        if c == "\\":
            nxt = command[i + 1:i + 2]
            # `\(` o `\;` son palabras para bash; para el lexer de puntuación, no.
            out.append("'\\" + nxt + "'" if nxt and nxt in _PUNCTUATION else command[i:i + 2])
            i += 2
            continue
        if command.startswith("$'", i):
            # Comillas ANSI-C: admiten `\'` dentro. Leídas como comillas simples,
            # `echo $'\''; rm -rf ~ #'` escondía el `rm` dentro de un "literal".
            end = i + 2
            while end < n and command[end] != "'":
                end += 2 if command[end] == "\\" else 1
            out.append(shlex.quote(command[i + 2:min(end, n)].replace("\\'", "'")))
            i = end + 1
            continue
        if c == "'":
            end = command.find("'", i + 1)
            end = n - 1 if end == -1 else end
            inner = command[i + 1:end]
            out.append("'\\" + inner + "'" if inner and set(inner) <= set(_PUNCTUATION) else command[i:end + 1])
            i = end + 1
            continue
        if c == '"':
            stack.append(["D", len(out)])
            out.append(c)
            i += 1
            continue
        if command.startswith("${", i):
            # Revisión 2026-09-15 (R1): dentro de `${…}` un `#` es parte de la
            # palabra (`${x:- #}`), no un comentario que oculte lo que sigue.
            stack.append(["V", 0])
            out.append("${")
            i += 2
            continue
        if top == "V" and c in "{}":
            if c == "{":
                stack[-1][1] += 1
            elif stack[-1][1] == 0:
                stack.pop()
            else:
                stack[-1][1] -= 1
            out.append(c)
            i += 1
            continue
        if c == "`":
            if top == "B":
                _, start, out_start = stack.pop()
                out.append(c)
                close_substitution(start, out_start, i)
            else:
                stack.append(["B", i + 1, len(out)])
                out.append(c)
            i += 1
            continue
        if command.startswith("$((", i):
            stack.append(["P", i + 3, 1, True, len(out)])
            out.append("$((")
            i += 3
            continue
        if command.startswith("$(", i):
            stack.append(["P", i + 2, 0, False, len(out)])
            out.append("$(")
            i += 2
            continue
        if top == "P" and c in "()":
            if c == "(":
                stack[-1][2] += 1
                out.append(c)
            elif stack[-1][2] == 0:
                _, start, _, _, out_start = stack.pop()
                out.append(c)
                close_substitution(start, out_start, i)
            else:
                stack[-1][2] -= 1
                out.append(c)
            i += 1
            continue
        if c == "#" and top != "V" and (i == 0 or (command[i - 1] in _COMMENT_STARTERS and command[i - 2:i - 1] != "\\")):
            end = command.find("\n", i)
            end = n if end == -1 else end
            if not stack:
                scan.comment += command[i:end] + "\n"
            i = end
            continue
        if command.startswith("<<<", i):
            out.append("<<<")  # here-string: no abre heredoc
            i += 3
            continue
        in_arithmetic = any(entry[0] == "P" and entry[3] for entry in stack)
        if command.startswith("<<", i) and not in_arithmetic:
            match = _HEREDOC_RE.match(command, i)
            if match:
                word = match.group("word")
                delimiter = re.sub(r"\\(.)", r"\1", word.replace("'", "").replace('"', ""))
                expands = not any(ch in word for ch in "'\"\\")
                pending.append((match.group(1) == "-", delimiter, _heredoc_receiver(_tail_after_separator(out)), expands, not stack))
                out.append(command[i:match.end()])
                i = match.end()
                continue
        if c == "\n" and pending:
            out.append(c)
            i += 1
            for strip_tabs, delimiter, receiver, expands, register in pending:
                body: list[str] = []
                while i < n:
                    end = command.find("\n", i)
                    end = n if end == -1 else end
                    line = command[i:end]
                    i = min(end + 1, n)
                    if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                        break
                    body.append(line)
                if register:  # dentro de una sustitución lo juzga su recursión
                    scan.heredocs.append((receiver, "\n".join(body), expands))
            pending = []
            continue
        out.append(c)
        i += 1
    scan.text = "".join(out)
    return scan


def _tail_after_separator(pieces: list[str]) -> str:
    """Lo que sigue al último separador de `"".join(pieces)`, sin unir el texto
    entero en cada heredoc (FW-237)."""
    tail: list[str] = []
    for piece in reversed(pieces):
        cut = max(piece.rfind(ch) for ch in _SEPARATOR_CHARS)
        if cut >= 0:
            tail.append(piece[cut + 1:])
            break
        tail.append(piece)
    return "".join(reversed(tail))


def _heredoc_receiver(prefix: str) -> list[str]:
    """Tokens del comando que recibe el heredoc: lo que va tras el último
    separador antes del `<<`."""
    cut = max(prefix.rfind(ch) for ch in _SEPARATOR_CHARS)
    return _lex(prefix[cut + 1:])


def _expanding_substitutions(body: str) -> list[str]:
    """Sustituciones que bash expande en un heredoc con delimitador sin comillas.

    En ese cuerpo `\\` solo escapa `$`, `` ` `` y `\\` (un `` \\`cmd\\` `` es texto)
    y las comillas son literales, así que no vale leerlo como comando."""
    subs: list[str] = []
    i, n = 0, len(body)
    while i < n:
        if body[i] == "\\" and i + 1 < n and body[i + 1] in "$`\\":
            i += 2
        elif body.startswith("$(", i):
            # `$((` también: si no cierra con `))`, bash lo reinterpreta como
            # sustitución de comandos (revisión 2026-09-15, R3). Juzgar una
            # aritmética como código no ejecuta nada.
            prefix = 3 if body.startswith("$((", i) else 2
            scanned = _scan(body[i:], first_substitution_only=True)
            inner = scanned.substitutions
            if not inner or scanned.unparseable:
                subs.append(body[i + 2:])  # sin cerrar o sin delimitar: se juzga el resto entero
                break
            subs.append(inner[0])
            i += prefix + len(inner[0]) + 1
        elif body[i] == "`":
            end = i + 1
            while end < n and body[end] != "`":
                end += 2 if body[end] == "\\" else 1
            subs.append(body[i + 1:end])
            i = end + 1
        else:
            i += 1
    return subs


# Wrappers que ejecutan el comando que les sigue: letras de flag corto y flags
# largos que consumen el token siguiente como argumento.
_WRAPPER_FLAGS_WITH_ARG: dict[str, tuple[str, set[str]]] = {
    "sudo": ("ughCpUDrtT", {"--user", "--group", "--host", "--close-from", "--prompt",
                            "--other-user", "--chdir", "--role", "--type", "--command-timeout"}),
    "env": ("uCS", {"--unset", "--chdir", "--split-string"}),
    "nice": ("n", {"--adjustment"}),
    "timeout": ("sk", {"--signal", "--kill-after"}),
    "stdbuf": ("ioe", {"--input", "--output", "--error"}),
    "nohup": ("", set()),
    "time": ("", set()),
    "command": ("", set()),
    "exec": ("a", set()),
}
_HEAD_WRAPPERS = set(_WRAPPER_FLAGS_WITH_ARG)


def _flag_takes_argument(tok: str, letters: str, long_flags: set[str]) -> bool:
    """Semántica de getopt: `-u`, `-iu` o `--user` consumen el siguiente token;
    `-uroot` (valor pegado), `-oL` o `--user=git` no."""
    if tok.startswith("--"):
        return tok in long_flags
    for pos, letter in enumerate(tok[1:], start=1):
        if letter in letters:
            return pos == len(tok) - 1  # solo si es la última letra; si no, el resto es su valor
    return False


def _strip_wrappers(tokens: list[str]) -> list[str]:
    """Quita asignaciones de entorno y wrappers con sus argumentos del inicio de
    un segmento: `sudo -iu postgres psql` → `psql`, `timeout 5m bash` → `bash`."""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if _ASSIGNMENT_RE.match(tok):
            i += 1
            continue
        base = tok.rsplit("/", 1)[-1]
        if base not in _WRAPPER_FLAGS_WITH_ARG:
            break
        letters, long_flags = _WRAPPER_FLAGS_WITH_ARG[base]
        i += 1
        while i < len(tokens) and (tokens[i].startswith("-") or _ASSIGNMENT_RE.match(tokens[i])):
            i += 2 if _flag_takes_argument(tokens[i], letters, long_flags) else 1
        if base == "timeout" and i < len(tokens):
            i += 1  # duración
        if base == "nice" and i < len(tokens) and tokens[i].lstrip("-").isdigit():
            i += 1
    return tokens[i:]


def _command_head(tokens: list[str]) -> str:
    """Basename del ejecutable real de un segmento."""
    rest = _strip_wrappers(tokens)
    return rest[0].rsplit("/", 1)[-1] if rest else ""


def _rm_tokens(command: str) -> list[str]:
    """Tokeniza con el lexer de puntuación (FW-228): los separadores salen como
    tokens propios y los destinos ya no arrastran un `;` pegado (`dist;`)."""
    return _lex(command)


def _find_rm_index(tokens: list[str]) -> int | None:
    """Localiza el token que invoca el ejecutable rm, tolerando rutas
    calificadas (/bin/rm, /usr/bin/rm, ./rm) y excluyendo flags como --rm."""
    for i, tok in enumerate(tokens):
        if tok.startswith("-"):
            continue
        if tok == "rm" or tok.rsplit("/", 1)[-1] == "rm":
            return i
    return None


def _rm_is_recursive_force(command: str) -> bool:
    """True si el comando invoca `rm` con recursivo Y force, cubriendo flags
    combinados (`-rf`), separados (`-r -f`) y largos (`--recursive --force`).
    El bug original solo veía flags en un mismo token (revisor FW-082)."""
    tokens = _rm_tokens(command)
    idx = _find_rm_index(tokens)
    if idx is None:
        return False
    recursive = force = False
    for tok in tokens[idx + 1:]:
        if _is_separator(tok):
            break
        if tok == "--recursive":
            recursive = True
        elif tok == "--force":
            force = True
        elif tok.startswith("--"):
            continue
        elif tok.startswith("-"):
            flags = tok[1:]
            if "r" in flags or "R" in flags:
                recursive = True
            if "f" in flags:
                force = True
    return recursive and force


def _rm_targets(command: str) -> list[str]:
    """Extrae los destinos (no-flags) de un `rm ...`."""
    tokens = _rm_tokens(command)
    idx = _find_rm_index(tokens)
    if idx is None:
        return []
    targets: list[str] = []
    for tok in tokens[idx + 1:]:
        if _is_separator(tok):
            break
        if tok.startswith("-"):
            continue
        targets.append(tok)
    return targets


# FW-226 (Example Tool FW-222): prefijos temporales del sistema. macOS resuelve /tmp
# a /private/tmp y el TMPDIR de usuario (y el scratchpad de sesión) vive bajo
# /private/tmp o /var/folders. Siguen pasando por normpath: `..` no escapa.
_SAFE_ABS_PREFIXES = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")
_ASSIGNMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_VAR_REF_RE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_FOR_VARS_KEY = "\x00FOR_VARS"
_SIMPLE_FOR_ITEM_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_CONDITIONAL_KEYWORDS = frozenset({"if", "then", "elif", "else", "fi", "case", "esac", "while", "until"})


def _known_literal_assignments(command: str) -> dict[str, str]:
    """Variables asignadas con un valor LITERAL en el propio comando para
    detectar ejecución indirecta (`c='rm -rf ~'; eval \"$c\"`)."""
    found: dict[str, str] = {}
    for tok in _lex(command):
        match = _ASSIGNMENT_RE.match(tok)
        if not match:
            continue
        if "$" not in match.group(2) and "`" not in match.group(2):
            found[match.group(1)] = match.group(2)
        else:
            found.pop(match.group(1), None)
    return found


def _known_assignments(
    command: str,
    tmpdir: str | None = None,
    substitutions: list[str] | None = None,
    any_literal: bool = False,
) -> dict[str, str]:
    """Variables asignadas una única vez a nivel superior fuera de condicionales
    cuyo valor es una ruta literal bajo `_SAFE_ABS_PREFIXES` o `$(mktemp -d)`.
    Con `any_literal`, cualquier ruta literal sin `..` (D3: `PY=.venv/bin/python`)."""
    import posixpath

    plain = re.sub(r"['\"\\]", "", command.replace("\\\n", ""))
    if _VAR_WRITER_RE.search(plain):
        return {}
    if substitutions is None:
        scanned = _scan(command)
        text = scanned.text
        substitutions = scanned.substitutions
    else:
        text = command

    if tmpdir is None:
        tmpdir = _trusted_tmpdir(command)
    tmpdir_mentioned = "TMPDIR" in plain

    tokens = _lex(text)
    segments = _segments_with_ops(tokens)
    counts: dict[str, int] = {}
    candidates: dict[str, str] = {}

    cond_depth = 0
    paren_depth = 0
    prev_op = ""

    for seg, op in segments:
        # Cualquier asignación dentro del segmento cuenta para detectar reasignación
        for tok in seg:
            m = _ASSIGNMENT_RE.match(tok)
            if m:
                var_name = m.group(1)
                counts[var_name] = counts.get(var_name, 0) + 1
        # Actualizar estado de condicionales por palabras clave del segmento
        seg_has_cond = any(t in _CONDITIONAL_KEYWORDS for t in seg)
        if seg and seg[0] in {"if", "case", "while", "until"}:
            cond_depth += 1
        if (
            cond_depth == 0
            and paren_depth == 0
            and not seg_has_cond
            and "||" not in prev_op
            and "(" not in prev_op
            and ")" not in prev_op
            and "(" not in op
            and ")" not in op
            and len(seg) == 1
        ):
            m = _ASSIGNMENT_RE.match(seg[0])
            if m:
                var_name, raw_val = m.group(1), m.group(2).strip().strip("'\"")
                sub_match = _SUB_PLACEHOLDER_RE.fullmatch(raw_val)
                if sub_match:
                    idx = int(sub_match.group(1))
                    sub_cmd = substitutions[idx].strip() if idx < len(substitutions) else ""
                    if (
                        _lex(sub_cmd) == ["mktemp", "-d"]
                        and not tmpdir_mentioned
                        and tmpdir is not None
                    ):
                        candidates[var_name] = tmpdir.rstrip("/") + "/__mktemp_d__"
                elif "$" not in raw_val and "`" not in raw_val and raw_val and (raw_val.startswith("/") or any_literal):
                    parts = raw_val.split("/")
                    if not any(p in (".", "..") for p in parts):
                        norm = posixpath.normpath(raw_val)
                        if any_literal or (norm + "/").startswith(_SAFE_ABS_PREFIXES):
                            candidates[var_name] = norm
        if any(t in {"fi", "esac"} for t in seg):
            cond_depth = max(0, cond_depth - 1)
        paren_depth += op.count("(") - op.count(")")
        prev_op = op

    return {k: v for k, v in candidates.items() if counts.get(k, 0) == 1}


def _safe_for_loop_vars(tokens: list[str], plain_command: str) -> dict[str, str]:
    """Variables `h` de bucles `for h in <items>; do ... done` a nivel superior
    donde `h` no se reasigna y todos los `<items>` son literales alfanuméricos
    simples `^[A-Za-z0-9_.-]+$` distintos de `.` y `..` y sin `/` ni `..`."""
    if _VAR_WRITER_RE.search(plain_command):
        return {}
    assign_counts: dict[str, int] = {}
    for tok in tokens:
        m = _ASSIGNMENT_RE.match(tok)
        if m:
            assign_counts[m.group(1)] = assign_counts.get(m.group(1), 0) + 1
    for_counts: dict[str, int] = {}
    found: dict[str, str] = {}
    i, n = 0, len(tokens)
    paren_depth = 0
    cond_depth = 0
    while i < n:
        tok = tokens[i]
        if _is_separator(tok):
            paren_depth += tok.count("(") - tok.count(")")
            i += 1
            continue
        at_start = i == 0 or _is_separator(tokens[i - 1])
        if at_start and tok in {"if", "case", "while", "until"}:
            cond_depth += 1
        elif at_start and tok in {"fi", "esac"}:
            cond_depth = max(0, cond_depth - 1)
        elif (
            at_start
            and paren_depth == 0
            and cond_depth == 0
            and tok == "for"
            and i + 2 < n
            and tokens[i + 2] == "in"
        ):
            var = tokens[i + 1]
            for_counts[var] = for_counts.get(var, 0) + 1
            j = i + 3
            items: list[str] = []
            while j < n and tokens[j] != "do" and not _is_separator(tokens[j]):
                items.append(tokens[j].strip("'\""))
                j += 1
            while j < n and _is_separator(tokens[j]):
                j += 1
            has_do = j < n and tokens[j] == "do"
            has_done = "done" in tokens[j + 1:] if has_do else False
            if (
                has_do
                and has_done
                and items
                and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", var)
                and all(
                    _SIMPLE_FOR_ITEM_RE.match(it)
                    and it not in (".", "..")
                    and "/" not in it
                    and ".." not in it
                    for it in items
                )
            ):
                found[var] = items[0]
            i = j + 1
            continue
        i += 1
    return {
        k: v
        for k, v in found.items()
        if for_counts.get(k, 0) == 1 and assign_counts.get(k, 0) == 0
    }


# 5.2.2 L4 (PA FW-138, job FW-274): `$TMPDIR` viene del entorno, no del
# comando. Solo se confía si, normalizado y con su ruta real, cae bajo un
# prefijo temporal; solo en el comando raíz (no dentro de `bash -c`, heredocs
# ni sustituciones); y si el comando, ya sin comillas ni escapes, no nombra
# TMPDIR salvo para leerlo ni usa nada capaz de asignar una variable con un
# nombre construido (`export TMP''DIR=…`, `printf -v`, `read`, `eval`…).
# Supuesto: el TMPDIR del proceso del hook es el del shell de la herramienta.
# Clave no expandible por `_VAR_REF_RE`: se aplica aparte.
_TMPDIR_KEY = "\x00TMPDIR"
_TMPDIR_REF_RE = re.compile(r"\$(?:\{TMPDIR\}|TMPDIR(?![A-Za-z0-9_]))")
_VAR_WRITER_RE = re.compile(
    r"(?<![\w.-])(?:export|declare|typeset|local|readonly|read|readarray|mapfile|printf"
    r"|unset|source|eval|set|env|getopts|let|exec|wait|print|vared|zparseopts|coproc)(?![\w-])"
    r"|(?:^|[\s;&|(])\.(?:\s|$)"
    r"|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?[A-Za-z0-9_]*="
    # zsh: `${(P)n::=valor}` asigna a la variable cuyo nombre guarda `n`.
    r"|::=|\(P\)"
)


def _trusted_tmpdir(command: str) -> str | None:
    import os
    import posixpath
    tmp = os.environ.get("TMPDIR", "")
    if not tmp.startswith("/") or "\x00" in tmp:
        return None
    norm = posixpath.normpath(tmp)
    try:
        real = os.path.realpath(norm)
    except OSError:
        return None
    if not (norm + "/").startswith(_SAFE_ABS_PREFIXES) or not (real + "/").startswith(_SAFE_ABS_PREFIXES):
        return None
    plain = re.sub(r"['\"\\]", "", command.replace("\\\n", ""))
    if "TMPDIR" in _TMPDIR_REF_RE.sub("", plain) or _VAR_WRITER_RE.search(plain):
        return None
    return norm


def _expand_tmpdir(target: str, tmpdir: str | None) -> str:
    """`$TMPDIR/<sub>` → ruta absoluta, solo si queda ESTRICTAMENTE debajo de
    TMPDIR tras normalizar (`$TMPDIR` a secas o `$TMPDIR/../..` no se expanden
    y siguen bloqueados como variable sin resolver)."""
    if not tmpdir:
        return target
    import posixpath
    t = target.strip().strip("'\"")
    match = _TMPDIR_REF_RE.match(t)
    if not match or not t[match.end():].startswith("/"):
        return target
    import os
    rest = t[match.end():]
    if ".." in rest.split("/") or "$" in rest or "`" in rest:
        return target
    expanded = posixpath.normpath(tmpdir + rest)
    try:
        real, real_tmp = os.path.realpath(expanded), os.path.realpath(tmpdir)
    except OSError:
        return target
    # Tanto el texto como la ruta real (un enlace dentro de TMPDIR no debe
    # sacarlo de allí) quedan estrictamente debajo de TMPDIR.
    if not expanded.startswith(tmpdir.rstrip("/") + "/") or not real.startswith(real_tmp.rstrip("/") + "/"):
        return target
    return expanded


def _expand_known(target: str, known: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        val = known.get(name)
        return val if isinstance(val, str) else match.group(0)
    return _VAR_REF_RE.sub(repl, target)


def _expand_rm_target(target: str, known: dict[str, object]) -> str:
    t = target.strip().strip("'\"")
    m = _VAR_REF_RE.match(t)
    if not m:
        return target
    var_name = m.group(1) or m.group(2)
    base_val = known.get(var_name)
    if not isinstance(base_val, str) or not (base_val + "/").startswith(_SAFE_ABS_PREFIXES):
        return target
    rest = t[m.end():]
    if not rest:
        return base_val
    if not rest.startswith("/"):
        return target
    suffix = rest[1:]
    if not suffix:
        return base_val
    if "$" not in suffix:
        if ".." in suffix.split("/"):
            return target
        return base_val.rstrip("/") + "/" + suffix
    if any(bad in suffix for bad in ("/", "..", "*", "?", "[", "~", "`", "$(")) or _SUB_PLACEHOLDER_RE.search(suffix):
        return target
    for_vars = known.get(_FOR_VARS_KEY)
    if not isinstance(for_vars, dict) or not for_vars:
        return target

    def repl_for(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        val = for_vars.get(name)
        return val if isinstance(val, str) else match.group(0)

    expanded_suffix = _VAR_REF_RE.sub(repl_for, suffix)
    if "$" in expanded_suffix or ".." in expanded_suffix or "/" in expanded_suffix:
        return target
    return base_val.rstrip("/") + "/" + expanded_suffix


_PLAIN_RELATIVE_RE = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_./@+-]*$")


def _relative_target_is_ephemeral(target: str, cwd: str) -> bool:
    """FW-236: un destino relativo se resuelve contra el cwd del evento. Solo es
    efímero si, tanto en el cwd tal cual como en su ruta real (un enlace
    simbólico bajo /tmp no debe sacarlo de allí), cae bajo un prefijo temporal.
    `cwd` llega ya validado (absoluto, sin nada que pueda cambiar de directorio)."""
    import os
    import posixpath
    if set(target) <= {".", "/"} or ".." in target.split("/"):
        # `.`, `..`, `./`: el propio directorio o su padre. Y `lnk/../x`: el kernel
        # resuelve `lnk` antes de aplicar `..`, así que normalizar el texto miente.
        return False
    try:
        roots = {cwd, os.path.realpath(cwd)}
        # El destino entero también se resuelve: `lnk/sub` con `lnk` -> ~/algo
        # cae fuera de /tmp aunque el texto diga otra cosa.
        candidates = {posixpath.normpath(posixpath.join(root, target)) for root in roots}
        candidates |= {os.path.realpath(c) for c in candidates}
    except OSError:
        return False
    return all(c.startswith(_SAFE_ABS_PREFIXES) for c in candidates)


def _resolve_rm_targets(seg: list[str], cwd: str | None) -> list[str]:
    """FW-236: en un segmento cuyo ejecutable ES `rm` (sin wrappers: `ssh h rm`,
    `docker exec c rm`, `find -exec rm {}` o `sudo rm` no corren en este cwd),
    los destinos relativos simples que resuelven bajo un prefijo temporal pasan
    a su ruta absoluta; el resto de comprobaciones los ve ya como efímeros."""
    if not cwd or not seg or seg[0].rsplit("/", 1)[-1] != "rm":
        return seg
    import posixpath
    out = [seg[0]]
    for tok in seg[1:]:
        if not tok.startswith("-") and _PLAIN_RELATIVE_RE.match(tok) and _relative_target_is_ephemeral(tok, cwd):
            out.append(posixpath.normpath(posixpath.join(cwd, tok)))
        else:
            out.append(tok)
    return out


def _rm_target_is_safe(target: str) -> bool:
    """Un destino de rm -rf es seguro solo si es un dir efímero conocido o
    vive bajo un prefijo temporal del sistema. Todo lo demás (absoluto, ~, .,
    variables sin resolver, globs, relativas desconocidas) NO es seguro →
    requiere marcador."""
    t = target.strip().strip("'\"").rstrip("/")
    if not t:
        return False
    if "$" in t or "`" in t:          # variable/subshell sin expandir
        return False
    # C2: normalizar ANTES de la allowlist — "/tmp/../Users" debe evaluarse
    # como "/Users", no colarse por el prefijo /tmp/.
    if t.startswith("/"):
        import posixpath
        t = posixpath.normpath(t)
    if t in (".", "..", "~", "/", "*"):
        return False
    if t.startswith("~"):
        return False
    if t.startswith(_SAFE_ABS_PREFIXES) and "*" not in t:
        return True
    if t.startswith("/"):             # absoluta no efímera
        return False
    if "*" in t:                       # glob amplio
        return False
    base = t.split("/")[-1] if "/" in t else t
    # Relativa: solo segura si el nombre base (la carpeta que de verdad se
    # borra) está en la allowlist y no hay componentes de escape.
    #
    # Example Desktop FW-046: antes se exigía que TODOS los segmentos de la ruta
    # estuvieran en la allowlist (`packaging/build` rechazado porque
    # "packaging" no es un nombre efímero conocido) — pero borrar
    # `packaging/build` es exactamente igual de seguro que borrar `build` en
    # la raíz: en ambos casos lo único que se destruye es un directorio
    # llamado "build" (regenerable, gitignored), sin importar bajo qué
    # carpeta viva. Exigir que el PADRE también esté en la allowlist no
    # protegía contra ningún ataque real — el `..`-escape y las rutas
    # absolutas/glob ya se rechazan por separado, arriba — y bloqueaba el
    # paso de build documentado de un proyecto real (mismo villano histórico
    # FW-034/037/038/040/051: falso positivo que entrena --no-verify /
    # "APPROVED-DESTRUCTIVE" por reflejo).
    if ".." in t.split("/"):
        return False
    return base in _SAFE_RM_NAMES


# --- Endurecimiento por segmentos (triaje 2026-07-09 C2) --------------------
# Los bypasses reproducidos: /bin/rm, segundo comando tras &&/;, sh -c "...",
# rutas con /tmp/../, y flags globales de git (-C/-c) antes del subcomando.

_SEPARATORS = {"&&", "||", ";", "|"}
_SHELL_WRAPPERS = {"sh", "bash", "zsh", "dash", "ksh"}
_GIT_GLOBAL_FLAGS_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--exec-path"}


# --- FW-087: carrier de texto (tokens como dato, no como ejecución) ---------
# Comandos que transportan texto sin ejecutarlo (mensajes de commit, echo,
# búsquedas). Un token destructivo dentro de su argumento es DATO, salvo que
# el comando lo canalice a un shell/eval (sink de ejecución).
_TEXT_CARRIERS_SIMPLE = {
    "echo", "printf", "grep", "egrep", "fgrep", "rg", "ag", "cat",
    # FW-117: destinos de SOLO LECTURA. Ninguno puede ejecutar lo que recibe,
    # así que `grep ... | head` sigue siendo transporte de texto. Quedan
    # FUERA a propósito `tee` (escribe archivos), `awk` y `sed` (pueden
    # ejecutar: `sed -e 's/x/y/e'`, `awk 'system(...)'`) y, tras el hallazgo
    # Critical de revisión (ronda 1), también `sort` y `uniq`: ambos aceptan
    # un archivo de SALIDA por argumento (`sort -o archivo`, `uniq in out`)
    # y esta función no es flag-aware, así que tratarlos como carrier abría
    # un bypass de escritura arbitraria de archivo.
    "head", "tail", "wc", "nl", "cut",
}
_GIT_TEXT_SUBCMDS = {"commit", "tag", "merge", "stash"}
# FW-228: el cuerpo de un PR, issue o release es texto (`--body "$(cat <<'EOF'…)"`).
_GH_TEXT_SUBCMDS = {"pr", "issue", "release"}
# FW-117: los sinks se detectan por TOKEN, no con una regex sobre la línea
# cruda. `shlex.split` ya respetó el entrecomillado; mirar el texto crudo lo
# deshacía y convertía un `\|` de alternancia regex, dentro del patrón de
# búsqueda, en un "pipe de shell" inexistente.
_EXEC_SINK_TOKENS = {"eval", "xargs"}
# Hallazgo Critical de revisión (ronda 1, FW-117): `shlex` deja el descriptor
# de archivo numérico pegado al operador cuando no hay espacio de por medio
# (`1>/dev/tcp/host/4444` y `2>/dev/tcp/...` son UN solo token que empieza
# con el dígito, no con `>`), así que `tok.startswith(">/dev/")` no lo veía.
# Confirmado con shlex.split: 'echo x 1>/dev/tcp/h/4444' -> ['echo', 'x',
# '1>/dev/tcp/h/4444']. La regex acepta un fd numérico opcional antes de `>`.
# Ronda de revisión final (hallazgo 2): la regex original solo cubría `>` y
# `1>`, dejando pasar 4 de las 6 formas reales de redirección a /dev/tcp:
# `>>` (append), `1>>` (append con fd), y `&>` (combinada stdout+stderr).
# `>` o `>>` tras un fd opcional y un `&` opcional, seguido de `/dev/`.
_DEV_REDIRECT_RE = re.compile(r"^\d*&?>>?/dev/")


# FW-236: `grep 'rm -rf' … | sed -n 1,5p` bloqueaba de más. `sed` no es un
# carrier general (`e` y `w` ejecutan y escriben, `-i` edita, `-f` carga un
# script), así que se acepta solo una gramática CERRADA que no puede hacer nada
# de eso: flags de solo lectura y órdenes `p`, `d`, `q`, `=` o `s///` con flags
# `g p I i 0-9` (sin `e` ni `w`). Todo lo que no encaje sigue siendo un
# programa que se juzga.
_SED_FLAGS = {"-n", "-E", "-r", "-s", "-u", "--quiet", "--silent", "--regexp-extended"}
_SED_ADDR = r"(?:\d+|\$|/(?:[^/\n\\]|\\.)*/)"
_SED_CMD = (
    r"\s*(?:" + _SED_ADDR + r"(?:\s*,\s*" + _SED_ADDR + r")?)?\s*!?\s*"
    r"(?:[pdq=]|s/(?:[^/\n\\]|\\.)*/(?:[^/\n\\]|\\.)*/[gpIi0-9]*)\s*"
)
_SED_SCRIPT_RE = re.compile(r"^" + _SED_CMD + r"(?:;" + _SED_CMD + r")*;?\s*$")


def _sed_is_read_only(args: list[str]) -> bool:
    """El primer argumento que no es flag es el script; el resto, ficheros."""
    if any(a.startswith("-") and a not in _SED_FLAGS for a in args):
        return False
    scripts = [a for a in args if not a.startswith("-")]
    return bool(scripts) and _SED_SCRIPT_RE.match(scripts[0]) is not None


def _is_text_carrier(tokens: list[str]) -> bool:
    """True si el segmento solo transporta texto (sus tokens no se ejecutan).

    Recibe TOKENS ya divididos por `shlex`, nunca una línea re-unida: unir con
    espacios pierde las comillas y reintroduce el falso positivo de FW-117.
    """
    if not tokens:
        return False
    for i, tok in enumerate(tokens):
        if tok in _EXEC_SINK_TOKENS:
            return False
        if tok in _SHELL_WRAPPERS and "-c" in tokens[i + 1:]:
            return False
        # Ronda de revisión final (hallazgo 2): forma de dos tokens (operador
        # y ruta separados por espacio) — también cubre `>>` (append) y `&>`
        # (combinada), no solo `>` simple.
        if tok in (">", ">>", "&>") and i + 1 < len(tokens) and tokens[i + 1].startswith("/dev/"):
            return False
        if _DEV_REDIRECT_RE.match(tok):
            return False
    head = tokens[0].rsplit("/", 1)[-1]
    if head in _TEXT_CARRIERS_SIMPLE:
        return True
    if head == "sed" and _sed_is_read_only(tokens[1:]):
        return True
    if head == "git" and len(tokens) > 1 and tokens[1] in _GIT_TEXT_SUBCMDS:
        return True
    if head == "gh" and len(tokens) > 1 and tokens[1] in _GH_TEXT_SUBCMDS:
        return True
    return False


def _segments(tokens: list[str]) -> list[list[str]]:
    """Divide la línea tokenizada en comandos individuales por separadores."""
    return [seg for seg, _ in _segments_with_ops(tokens)]


def _segments_with_ops(tokens: list[str]) -> list[tuple[list[str], str]]:
    """Segmentos con los operadores que los siguen, concatenados: en
    `(echo x) | psql` el operador tras `echo x` es `)|`, no `)`."""
    out: list[tuple[list[str], str]] = []
    seg: list[str] = []
    for tok in tokens:
        if _is_separator(tok):
            if seg:
                out.append((seg, tok))
                seg = []
            elif out:
                out[-1] = (out[-1][0], out[-1][1] + tok)
        else:
            seg.append(tok)
    if seg:
        out.append((seg, ""))
    return out


_DB_CLIENTS = {"psql", "mysql", "mariadb", "sqlite3", "mongo", "mongosh", "redis-cli"}
_KEYWORDLESS_CLIENTS = ("mongosh", "mongo", "redis-cli")  # sus patrones exigen el cliente en la línea
_SCRIPT_RUNNERS = _SHELL_WRAPPERS | {"source", ".", "eval"}
_OUTPUT_REDIRECT_RE = re.compile(r"^\d*&?>>?\|?$")
# Consumidores de un fichero recién escrito que nunca lo ejecutan. Cualquier otro
# (`bash s.sh`, `./s.sh`, `psql -f q.sql`, `sqlite3 db < q.sql`…) cuenta como
# ejecución: lista cerrada de lo seguro, no lista abierta de ejecutores.
_SAFE_FILE_CONSUMERS = {
    "git", "gh", "cat", "less", "more", "head", "tail", "wc", "grep", "egrep", "fgrep", "rg", "ag",
    "diff", "cmp", "cp", "mv", "ls", "chmod", "chown", "stat", "file", "open", "code", "pbcopy",
    "echo", "printf", "test", "[", "touch", "du", "sort", "uniq", "nl", "cut", "jq", "mkdir",
    "realpath", "dirname", "basename", "shasum", "sha256sum", "md5",
    # FW-236: analiza el script sin ejecutarlo.
    "shellcheck",
}


def _path_key(tok: str) -> str:
    return tok[2:] if tok.startswith("./") else tok


def _mentions_path(text: str, path: str) -> bool:
    """¿`text` usa la ruta escrita? Por la ruta o por su nombre base, entre
    límites de palabra: `.read /tmp/q.sql`, `./limpiar.sh` tras `cd dir`, o la
    copia `./l.sh` de `/tmp/l.sh`. `layout` no menciona un fichero `out`."""
    names = {path, path.rsplit("/", 1)[-1]}
    return any(
        name and re.search(r"(?<![\w.-])" + re.escape(name) + r"(?![\w-])", text)
        for name in names
    )


def _unsafe_flow(segments: list[tuple[list[str], str]], scan: _Scan) -> bool:
    """¿Puede el texto de un carrier o de un heredoc acabar ejecutándose?

    Sí si algo canaliza hacia un programa que no es carrier (`| sh`, `| docker
    exec … psql`, `| tee log | sh`), si hay sustitución de proceso, o si un
    fichero escrito en la línea (`>`, `>>`, `tee`) lo usa después un comando que
    no está entre los consumidores seguros. Ante un sí se juzga todo, como hacía
    el guard anterior.

    5.3.1 L1: `| tee fichero` no basta por sí solo (su fichero cuenta como escrito
    y lo que venga detrás se sigue mirando), y un fichero escrito que solo
    ejecutan intérpretes de la lista es un script en fichero (D3)."""
    written: set[str] = set()
    known: dict[str, str] | None = None
    for k, (seg, op) in enumerate(segments):
        if "<(" in op or ">(" in op:
            return True
        if _is_pipe_op(op) and k + 1 < len(segments) and not _is_text_carrier(segments[k + 1][0]) \
                and _command_head(segments[k + 1][0]) != "tee":
            return True
        rest = _strip_wrappers(seg)
        head = rest[0].rsplit("/", 1)[-1] if rest else ""
        # `./shellcheck` escrito antes por el propio comando no es el analizador.
        if written and (head not in _SAFE_FILE_CONSUMERS or (head == "shellcheck" and "/" in rest[0])):
            # Las sustituciones cuentan con su contenido: `bash -c "$(cat x.sh)"`.
            text = _SUB_PLACEHOLDER_RE.sub(
                lambda m: scan.substitutions[int(m.group(1))] if int(m.group(1)) < len(scan.substitutions) else "",
                " ".join(rest),
            )
            used = [path for path in written if _mentions_path(text, path)]
            if used:
                if known is None:
                    known = _known_assignments(scan.text, substitutions=scan.substitutions, any_literal=True)
                if not _runs_as_script(rest, known) or any(
                    _EXEC_RELEVANT_FILE_RE.search(p) or _HOOK_FILE_RE.search(p) for p in used
                ):
                    return True
        targets = [seg[idx + 1] for idx, tok in enumerate(seg) if _OUTPUT_REDIRECT_RE.match(tok) and idx + 1 < len(seg)]
        if head == "tee":
            targets += [tok for tok in rest[1:] if not tok.startswith("-")]
        # D1: /dev/null, /dev/stdout… no son ficheros que luego se ejecuten.
        written.update(_path_key(t) for t in targets if not t.startswith("/dev/") and not t.startswith("&"))
    return False


def _runs_as_script(rest: list[str], known: dict[str, str]) -> bool:
    """D3: el consumidor es un intérprete de la lista (Python, `pytest`, Node,
    Ruby, Perl o PHP), por su nombre o por una variable asignada una sola vez a
    nivel superior y fuera de condicionales (`PY=…; $PY fichero`). Los shells,
    `source`, `make`, los clientes de base de datos, `xargs` y `./fichero` no."""
    if not rest:
        return False
    head = rest[0]
    variable = re.fullmatch(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", head)
    if variable:
        head = known.get(variable.group(1), "")
    base = head.rsplit("/", 1)[-1]
    return bool(base) and (_interpreter_kind(base) is not None or base == "pytest")


# --- S3 (5.3.1 L1): código en línea para un intérprete ----------------------
# El guard juzga lo que ejecuta el shell. El código en línea para un intérprete
# (`python3 -c`, `python3 - <<EOF`, `node -e`, `ruby -e`, `perl -e`, `php -r`) se
# trata como un script en fichero, que el guard nunca leyó: no se buscan palabras
# dentro. Buscarlas daba falsos positivos sin protección real (TL-FW-322,
# PA-FW-001, TL-FW-312, BIB-FW-001, TALLO-FW-236): el mismo código escrito con
# Write y ejecutado por ruta pasaba. Condiciones del dictamen `security`
# (2026-10-21):
# - Mitigación de spawn: la orden que el código lanza con argumentos literales
#   (`os.system`, `subprocess.run([...])`, `execSync`, `system`, comillas
#   invertidas…) se juzga como una orden de shell.
# - Si la salida puede acabar en un shell (flujo inseguro) o el código está
#   dentro de `sh -c`, no hay exención: se juzga entero, como antes.
# - Las sustituciones `$(…)` que expande el propio shell se juzgan siempre.
# - Las opciones del intérprete se leen solo hasta el primer argumento
#   posicional o `-m`: `python3 run.py -c "…"` no es código en línea.
_PYTHON_HEAD_RE = re.compile(r"^python(?:\d+(?:\.\d+)*)?$")
_PYTHON_STDIN_FLAGS = {"-", "-u", "-B", "-I", "-E", "-s", "-S", "-q", "-O", "-OO"}
_OTHER_INTERPRETERS = {"node": "node", "nodejs": "node", "bun": "node", "ruby": "ruby", "perl": "perl", "php": "php"}
# (opciones exactas con el código detrás, prefijos `--opción=código`, letras de
# un grupo corto sin argumento, letras que llevan el código, opciones con argumento)
_INLINE_SPECS: dict[str, tuple[set[str], tuple[str, ...], str, str, set[str]]] = {
    "python": ({"-c"}, (), "bBdEhiIOPqsSuvxR", "c", {"-W", "-X", "--check-hash-based-pycs"}),
    "node": ({"-e", "--eval", "-p", "--print"}, ("--eval=", "--print="), "", "",
             {"-r", "--require", "--import", "--loader", "--experimental-loader", "--input-type", "-C", "--conditions"}),
    "ruby": ({"-e"}, (), "nplacdvwW", "e", {"-r", "-I", "-C", "-E", "-F"}),
    "perl": ({"-e", "-E"}, (), "nplaswtTWX", "eE", {"-I", "-M", "-m"}),
    "php": ({"-r"}, (), "", "", {"-d", "-c", "-z"}),
}


def _interpreter_kind(token: str) -> str | None:
    base = token.rsplit("/", 1)[-1]
    if _PYTHON_HEAD_RE.match(base):
        return "python"
    return _OTHER_INTERPRETERS.get(base)


def _inline_code_index(seg: list[str]) -> tuple[int, str] | None:
    """(posición en `seg` del token con el código en línea, intérprete) o None."""
    rest = _strip_wrappers(seg)
    kind = _interpreter_kind(rest[0]) if rest else None
    if kind is None:
        return None
    inline, prefixes, letters, code_letters, with_arg = _INLINE_SPECS[kind]
    base = len(seg) - len(rest)
    i = 1
    while i < len(rest):
        tok = rest[i]
        if tok in {"-", "--", "-m"} or not tok.startswith("-"):
            return None
        if prefixes and tok.startswith(prefixes):
            return (base + i, kind)
        if tok in inline or (
            code_letters and not tok.startswith("--") and len(tok) > 2
            and tok[-1] in code_letters and all(ch in letters for ch in tok[1:-1])
        ):
            return (base + i + 1, kind) if i + 1 < len(rest) else None
        i += 2 if tok in with_arg else 1
    return None


def _interpreter_stdin_kind(receiver: list[str]) -> str | None:
    """`python3 -`, `python3 <<EOF`, `node -`: el intérprete lee el programa de su stdin."""
    rest = _strip_wrappers(receiver)
    kind = _interpreter_kind(rest[0]) if rest else None
    if kind is None:
        return None
    allowed = _PYTHON_STDIN_FLAGS if kind == "python" else {"-"}
    return kind if all(tok in allowed for tok in rest[1:]) else None


# Mitigación de spawn en Python: por AST, por el nombre final de la llamada
# (cubre `import subprocess as sp` y `from subprocess import run as r`).
_PY_SPAWN_NAMES = frozenset({
    "system", "popen", "run", "call", "check_call", "check_output", "Popen", "getoutput", "getstatusoutput",
    "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
    "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
    "posix_spawn", "posix_spawnp", "create_subprocess_exec", "create_subprocess_shell",
})
_PY_SPAWN_MODULES = {"os", "subprocess", "asyncio", "pty", "commands"}
# Otros intérpretes, por forma: llamadas que lanzan procesos y literales de orden.
_SPAWN_CALL_RE = re.compile(
    r"(?P<name>\bexecSync|\bexecFileSync|\bspawnSync|\bexecFile|\bspawn|\bexec|\bsystem|\bIO\s*\.\s*popen|\bpopen"
    r"|\bshell_exec|\bpassthru|\bproc_open)\s*\("
)
# Perl y Ruby lanzan órdenes también sin paréntesis: `system "…"`, `exec "…", "…"`.
_SPAWN_BAREWORD_RE = re.compile(r"\b(?:system|exec)\s+(?=['\"])")
# En Node, `exec(`, `system(` y `popen(` genéricos son métodos de otras librerías
# (`db.exec('…')`): solo cuentan si el código usa `child_process`.
_NODE_GENERIC_SPAWN = {"exec", "system", "popen"}
_SPAWN_QUOTE_RE = re.compile(r"\bqx\s*([{(\[/|])|%x\s*([{(\[|])")
_LITERAL_RE = re.compile(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"|`((?:[^`\\]|\\.)*)`")
_CLOSERS = {"(": ")", "{": "}", "[": "]", "/": "/", "|": "|"}


def _python_spawned(code: str) -> list[str] | None:
    """Órdenes con argumentos literales que lanza un programa Python; None si no
    se puede leer como Python."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        return None
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in _PY_SPAWN_MODULES:
            aliases.update({a.asname: a.name for a in node.names if a.asname})
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        if aliases.get(name, name) not in _PY_SPAWN_NAMES:
            continue
        args = list(node.args) + [kw.value for kw in node.keywords if kw.arg in {"args", "cmd", "command"}]
        strings = [a.value for a in args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        for a in args:
            if isinstance(a, (ast.List, ast.Tuple)) and a.elts and all(
                isinstance(e, ast.Constant) and isinstance(e.value, str) for e in a.elts
            ):
                found.append(shlex.join(e.value for e in a.elts))
        if len(strings) == 1:
            found.append(strings[0])
        elif strings:
            found.append(shlex.join(strings))
    return found


def _balanced(code: str, start: int, opener: str) -> str:
    """Texto desde `start` hasta el cierre de `opener`, saltando literales."""
    closer, depth, i = _CLOSERS[opener], 1, start
    while i < len(code):
        ch = code[i]
        if ch in "'\"`":
            match = _LITERAL_RE.match(code, i)
            i = match.end() if match else i + 1
            continue
        if ch == opener and opener != closer:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return code[start:i]
        i += 1
    return code[start:]


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


def _regex_spawned(code: str, kind: str) -> list[str]:
    found: list[str] = []
    calls = [(m, _balanced(code, m.end(), "(")) for m in _SPAWN_CALL_RE.finditer(code)
             if not (kind == "node" and m.group("name") in _NODE_GENERIC_SPAWN and "child_process" not in code)]
    if kind in {"ruby", "perl"}:
        calls += [(m, re.split(r"[;\n]", code[m.end():], maxsplit=1)[0]) for m in _SPAWN_BAREWORD_RE.finditer(code)]
    for match, args in calls:
        literals = [_unescape(next(g for g in m.groups() if g is not None)) for m in _LITERAL_RE.finditer(args)]
        if len(literals) == 1:
            found.append(literals[0])
        elif literals:
            found.append(shlex.join(literals))
    for match in _SPAWN_QUOTE_RE.finditer(code):
        opener = match.group(1) or match.group(2)
        found.append(_balanced(code, match.end(), opener))
    if kind in {"ruby", "perl", "php"}:
        found.extend(_unescape(m.group(3)) for m in _LITERAL_RE.finditer(code) if m.group(3) is not None)
    return found


def _spawn_reason(code: str, kind: str, depth: int) -> str:
    """Motivo si el código en línea lanza una orden que el guard deniega."""
    commands = _python_spawned(code) if kind == "python" else None
    if commands is None:
        commands = _regex_spawned(code, kind)
    for command in commands[:200]:
        blocked, why = _evaluate(command, allow_marker=False, depth=depth + 1, code_context=False)
        if blocked:
            return f"(el código en línea lanza una orden) {why}"
    return ""


# Opciones de ssh que no ejecutan ni redirigen nada (`-o LocalCommand=`, `-F`,
# `-J`, `-S`, `-W`, `-L`… sí). Lista cerrada: cualquier otra hace que el heredoc
# deje de ser dato.
_SSH_PLAIN_FLAGS = set("TtqxanCv46")
_SSH_VALUE_FLAGS = set("pil")
_SSH_SAFE_O_KEYS = {
    "stricthostkeychecking", "batchmode", "connecttimeout", "port", "user", "identityfile",
    "userknownhostsfile", "loglevel", "serveraliveinterval", "serveralivecountmax",
}
_SSH_REMOTE_WORD_RE = re.compile(r"^[\w./@:+,=%-]+$")
_SSH_TEMP_DIRS = ("/tmp/", "/var/tmp/")


def _ssh_safe_options(rest: list[str]) -> int | None:
    """Índice del host tras las opciones si todas son de la lista cerrada."""
    i = 1
    while i < len(rest) and rest[i].startswith("-") and len(rest[i]) > 1:
        tok = rest[i]
        letter = tok[1]
        if letter == "o":
            value = tok[2:] if len(tok) > 2 else (rest[i + 1] if i + 1 < len(rest) else "")
            if value.split("=", 1)[0].strip().lower() not in _SSH_SAFE_O_KEYS:
                return None
            i += 1 if len(tok) > 2 else 2
        elif letter in _SSH_VALUE_FLAGS:
            i += 1 if len(tok) > 2 else 2
        elif all(ch in _SSH_PLAIN_FLAGS for ch in tok[1:]):
            i += 1
        else:
            return None
    return i if i < len(rest) else None


def _ssh_remote_is_fixed_writer(receiver: list[str]) -> bool:
    """`ssh host 'cat > /tmp/f' <<EOF`: el programa remoto es un `cat`/`tee` fijo,
    sin shell, sustituciones ni operadores, que escribe (o nada) en un directorio
    temporal: recibe el cuerpo como datos y no lo ejecuta ni lo deja donde otro
    programa lo ejecute (cron, rc, authorized_keys)."""
    rest = _strip_wrappers(receiver)
    if not rest or rest[0].rsplit("/", 1)[-1] != "ssh":
        return False
    i = _ssh_safe_options(rest)
    if i is None:
        return False
    remote = rest[i + 1:]  # rest[i] es el host
    if not remote:
        return False  # sin orden remota: shell interactivo que lee el cuerpo
    words = _lex(" ".join(remote))
    if not words or words[0] not in {"cat", "tee"}:
        return False
    import posixpath
    for w in words[1:]:
        if w in {">", ">>"} or re.match(r"^--?[A-Za-z][A-Za-z-]*$", w):
            continue
        if not _SSH_REMOTE_WORD_RE.match(w) or not w.startswith(_SSH_TEMP_DIRS) or posixpath.normpath(w) != w:
            return False
    return True


def _heredoc_is_data(receiver: list[str]) -> bool:
    """El heredoc lo recibe un transportador de texto: `cat`/`tee` y lectores,
    `git commit|tag|notes|apply`, `gh pr|issue|release|api`, `docker build`.
    Si el receptor contiene un shell, un cliente de BD o `ssh`, nunca es dato
    (`sudo -iu git bash`, `gh codespace ssh`)."""
    # D4: `.` y `source` solo ejecutan en posición de comando; como argumento `.`
    # es un directorio (`docker build -f- .`) o un filtro (`jq .`).
    if _ssh_remote_is_fixed_writer(receiver):
        return True
    runners = (_SCRIPT_RUNNERS - {".", "source"}) | _DB_CLIENTS | {"ssh"}
    if any(tok.rsplit("/", 1)[-1] in runners for tok in receiver) or _command_head(receiver) in {".", "source"}:
        return False
    nseg = _normalize_segment(receiver)
    if not nseg:
        return False
    head, sub = nseg[0], (nseg[1] if len(nseg) > 1 else "")
    if head in _TEXT_CARRIERS_SIMPLE or head in {"tee", "pbcopy", "jq"}:
        return True
    if head == "git":
        return sub in _GIT_TEXT_SUBCMDS | {"notes", "apply", "am"}
    if head == "gh":
        return sub in _GH_TEXT_SUBCMDS | {"api"}
    if head in {"docker", "podman"}:
        return sub in {"build", "buildx"}
    return False


def _pipe_client(segments: list[tuple[list[str], str]], k: int) -> str | None:
    """Cliente sin palabra clave propia (mongo, redis) al que canaliza el segmento k."""
    seg, op = segments[k]
    if not _is_pipe_op(op) or k + 1 >= len(segments):
        return None
    return _keywordless_client(segments[k + 1][0])


def _keywordless_client(tokens: list[str]) -> str | None:
    for tok in tokens:
        base = tok.rsplit("/", 1)[-1]
        if base in _KEYWORDLESS_CLIENTS:
            return base
    return None


def _normalize_segment(seg: list[str]) -> list[str]:
    """Canonicaliza un segmento: sin wrappers ni asignaciones, basename del
    ejecutable (/bin/rm → rm) y, para git, sin flags globales antes del
    subcomando (`git --no-pager -C x reset` → `git reset`)."""
    rest = _strip_wrappers(seg)
    if not rest:
        return rest
    exe = rest[0].rsplit("/", 1)[-1]
    tail = rest[1:]
    if exe == "git":
        i = 0
        while i < len(tail) and tail[i].startswith("-"):
            i += 2 if tail[i] in _GIT_GLOBAL_FLAGS_WITH_ARG else 1
        tail = tail[i:]
    return [exe] + tail


_WAL_CHECKPOINT_TRUNCATE_RE = re.compile(r"\bwal_checkpoint\s*\(\s*TRUNCATE\s*\)", re.IGNORECASE)


def _neutral_wal(command: str) -> str:
    """S3: `wal_checkpoint(TRUNCATE)` nunca cuenta. Se neutraliza antes del lexer,
    que partía los paréntesis (TL-FW-322, heredoc a sqlite3), sin tocar comillas."""
    return _WAL_CHECKPOINT_TRUNCATE_RE.sub("wal_checkpoint_neutro", command) if isinstance(command, str) else command


def _check_one_line(
    line: str,
    known: dict[str, object] | None = None,
    *,
    sql: bool = True,
) -> tuple[bool, str]:
    """Chequeos de patrón sobre una línea (completa o segmento normalizado).

    `known`: asignaciones literales del comando COMPLETO. El chequeo por
    segmento ya no ve la asignación (`DIR=/tmp/x; rm -rf "$DIR"` se parte en
    dos), así que se le pasa desde fuera. `sql`: el DELETE sin WHERE se juzga
    por sentencia y solo en la línea completa, donde un WHERE en la línea
    siguiente sigue perteneciendo a la misma sentencia."""
    db_line = _neutral_wal(line)
    for pattern, reason in _DB_PATTERNS:
        if pattern.search(db_line):
            return (True, reason)
    if sql:
        for statement in line.split(";"):
            if _DELETE_RE.search(statement) and not _WHERE_RE.search(statement):
                return (True, "DELETE FROM sin WHERE borra la tabla entera")
    if _SYNC_RE.search(line) and _PROD_TOKEN_RE.search(line):
        return (True, "sync/copia con destino de producción")
    if _rm_is_recursive_force(line):
        targets = _rm_targets(line)
        if not targets:
            return (True, "rm -rf sin destino claro (posible variable/glob)")
        known_map: dict[str, object] = {**(known or {})}
        tmpdir = known_map.get(_TMPDIR_KEY)
        tmpdir_str = tmpdir if isinstance(tmpdir, str) else None
        unsafe = [
            t
            for t in targets
            if not _rm_target_is_safe(_expand_tmpdir(_expand_rm_target(t, known_map), tmpdir_str))
        ]
        if unsafe:
            return (True, f"rm -rf sobre destino no efímero: {', '.join(unsafe)}")
    return (False, "")


def _strip_for_items(tokens: list[str], hidden_texts: list[str] | tuple[str, ...] = ()) -> list[str] | None:
    """Quita los ítems literales de `for VAR in a b c; do` si el cuerpo no los
    ejecuta. Devuelve None si no hay bucle o si la variable llega a un sink
    (cabeza de comando, eval, xargs o shell -c): entonces el ítem ES código.
    Solo reconoce `for` a inicio de comando: "Checking for changes in repo" es
    prosa y no puede comerse el `&& git reset --hard` que la sigue.

    `hidden_texts`: sustituciones y cuerpos de heredoc que `_scan` retiró del
    texto. Si la variable aparece ahí (`psql -c "$(printf %s "$q")"`, un heredoc
    a psql con `$q`), no se puede demostrar que sea dato."""
    if "for" not in tokens or "in" not in tokens:
        return None
    out: list[str] = []
    changed = False
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        at_start = i == 0 or _is_separator(tokens[i - 1]) or tokens[i - 1] in {"do", "then", "else"}
        if tok == "for" and at_start and i + 2 < len(tokens) and tokens[i + 2] == "in":
            var = tokens[i + 1]
            j = i + 3
            while j < len(tokens) and tokens[j] != "do" and not _is_separator(tokens[j]):
                j += 1
            body_start = j
            ref_re = re.compile(r"\$\{?" + re.escape(var) + r"(?![A-Za-z0-9_])")
            body = tokens[body_start:]
            executes = False
            # Dato solo si TODAS las referencias a la variable están en carriers
            # que no canalizan a nada: `psql -c "$q"` o `echo "$c" | sh` la ejecutan.
            for bseg, bop in _segments_with_ops(body):
                if bseg and bseg[0] in {"do", "then", "else", "done", "fi"}:
                    bseg = bseg[1:]
                if any(ref_re.search(btok) for btok in bseg) and (
                    not bseg or not _is_text_carrier(bseg) or _is_pipe_op(bop)
                ):
                    executes = True
            for k, btok in enumerate(body):
                if btok in _EXEC_SINK_TOKENS or (btok in _SHELL_WRAPPERS and "-c" in body[k + 1:]):
                    executes = True
            if any(ref_re.search(text) for text in hidden_texts):
                executes = True
            if executes:
                return None
            out.extend(tokens[i:i + 3])
            changed = True
            i = j
            continue
        out.append(tok)
        i += 1
    return out if changed else None


def is_destructive(command: str, cwd: object = None) -> tuple[bool, str]:
    """Devuelve (blocked, reason). reason == "" cuando no se bloquea. Solo `deny`:
    la confirmación (`ask`) de FW-236 la da `judge`."""
    size = len(command.encode("utf-8", "surrogatepass")) if isinstance(command, str) else 0
    if size > _MAX_COMMAND_BYTES:
        # FW-237: por encima del límite no se analiza (`shlex` es cuadrático en la
        # longitud de un token). El escáner sí es lineal: el marcador real vale.
        if _APPROVED_RE.search(_scan(command).comment):
            return (False, "")
        return (True, (
            f"comando de {size // 1024} KB: supera el máximo que el guard analiza "
            f"({_MAX_COMMAND_BYTES // 1024} KB); mueve el contenido grande a un fichero y "
            "ejecuta un comando corto"
        ))
    command = _neutral_wal(command)
    return _evaluate(command, allow_marker=True, depth=0, code_context=False, cwd=_effective_cwd(command, cwd))


def _evaluate(command: str, *, allow_marker: bool, depth: int, code_context: bool,
              cwd: str | None = None) -> tuple[bool, str]:
    """Motor del guard: todo se juzga salvo lo demostrado dato.

    `allow_marker` solo es True en el nivel superior: un marcador dentro de un
    heredoc o una sustitución no desbloquea nada. `code_context`: el texto
    evaluado acabará ejecutándose (sustitución en posición de comando, dentro de
    `sh -c`, canalizada a un programa…), así que nada en él es dato."""
    if not command or not command.strip():
        return (False, "")
    if depth > _MAX_DEPTH:
        return (True, "anidamiento de sustituciones demasiado profundo para evaluarlo")

    scan = _scan(command)
    # Escotilla: marcador explícito con razón, en el comentario real del comando.
    if allow_marker and _APPROVED_RE.search(scan.comment):
        return (False, "")
    if scan.unparseable:
        return (True, scan.unparseable)

    tokens = _lex(scan.text)
    segments = _segments_with_ops(tokens)
    # El flujo se calcula ANTES de retirar nada: `for …; done | bash` ejecuta
    # los ítems aunque el cuerpo solo los imprima.
    unsafe = code_context or _unsafe_flow(segments, scan)
    plain_cmd = re.sub(r"['\"\\]", "", command.replace("\\\n", ""))
    for_vars = _safe_for_loop_vars(tokens, plain_cmd) if not unsafe else {}
    # FW-226: los ítems de `for VAR in ...` son DATO mientras el cuerpo no
    # ejecute la variable (`$VAR` como comando, eval, sh -c, un argumento de un
    # no-carrier); con un flujo inseguro no se retira nada.
    if not unsafe:
        hidden = list(scan.substitutions) + [body for _, body, _ in scan.heredocs]
        stripped = _strip_for_items(tokens, hidden)
        if stripped is not None:
            tokens = stripped
            segments = _segments_with_ops(tokens)

    # S3 (5.3.1 L1): el código en línea para un intérprete es un script en
    # fichero. Se juzga la orden que lance con argumentos literales (spawn). Con
    # un flujo seguro se retira de lo que se juzga (línea, segmento y DELETE por
    # token); con uno inseguro o dentro de `sh -c` se juzga entero y, además,
    # como órdenes de shell, porque su salida puede acabar en un shell.
    for k, (seg, op) in enumerate(segments):
        found = _inline_code_index(seg)
        if found is None:
            continue
        index, kind = found
        why = _spawn_reason(seg[index], kind, depth)
        if why:
            return (True, why)
        if unsafe:
            blocked, reason = _evaluate(seg[index], allow_marker=False, depth=depth + 1, code_context=True, cwd=cwd)
            if blocked:
                return (True, f"(la salida del código en línea puede acabar en un shell) {reason}")
        else:
            segments[k] = (seg[:index] + ["__codigo_en_linea__"] + seg[index + 1:], op)

    # Sustituciones: siempre se ejecutan. Su salida es código si están en un
    # comando que no es carrier o en posición de comando; dato si son argumento
    # de un carrier (`git commit -m "$(cat <<'EOF' … EOF)"`).
    sub_is_code: dict[int, bool] = {}
    for seg, _ in segments:
        carrier = _is_text_carrier(seg)
        for pos, tok in enumerate(seg):
            for match in _SUB_PLACEHOLDER_RE.finditer(tok):
                index = int(match.group(1))
                sub_is_code[index] = sub_is_code.get(index, False) or unsafe or not carrier or pos == 0
    for index, inner in enumerate(scan.substitutions):
        blocked, reason = _evaluate(
            inner, allow_marker=False, depth=depth + 1, code_context=sub_is_code.get(index, True), cwd=cwd,
        )
        if blocked:
            return (True, f"(dentro de una sustitución de comandos) {reason}")

    # Heredocs: dato solo si lo recibe un transportador de texto y nada en la
    # línea puede ejecutarlo. Con delimitador sin comillas bash expande `$(…)`
    # y backticks del cuerpo aunque sea dato.
    for receiver, body, expands in scan.heredocs:
        if not unsafe and _heredoc_is_data(receiver):
            if expands:
                # La sustitución se ejecuta, pero su salida acaba en el fichero: se
                # juzga lo que corre (`$(rm -rf ~)`), no el texto que imprime.
                for inner in _expanding_substitutions(body):
                    blocked, reason = _evaluate(inner, allow_marker=False, depth=depth + 1, code_context=False, cwd=cwd)
                    if blocked:
                        # job FW-274: bash ejecuta `…` y $(…) de un heredoc sin
                        # comillas; si solo se quería escribir el texto, basta
                        # con entrecomillar el delimitador.
                        return (True, f"(sustitución dentro de un heredoc) {reason}. Si el cuerpo es solo texto, "
                                      "entrecomilla el delimitador (<<'EOF'): así bash no ejecuta `…` ni $(…)")
            continue
        kind = _interpreter_stdin_kind(receiver)
        if kind:
            # S3: el programa de un heredoc a un intérprete es un script en fichero.
            why = _spawn_reason(body, kind, depth)
            if why:
                return (True, why)
            if not unsafe:
                for inner in _expanding_substitutions(body) if expands else ():
                    blocked, reason = _evaluate(inner, allow_marker=False, depth=depth + 1, code_context=False, cwd=cwd)
                    if blocked:
                        return (True, f"(sustitución dentro de un heredoc) {reason}. Si el cuerpo es solo código, "
                                      "entrecomilla el delimitador (<<'EOF'): así bash no ejecuta `…` ni $(…)")
                continue
        client = _keywordless_client(receiver)
        text = "\n".join(f"{client} {line}" for line in body.splitlines()) if client else body
        blocked, reason = _evaluate(
            text,
            allow_marker=False,
            depth=depth + 1,
            code_context=True,
            cwd=cwd,
        )
        if blocked:
            return (
                True,
                f"(heredoc que se ejecuta: contiene texto que parece un comando destructivo) {reason}. "
                "Si solo quieres escribir o editar un fichero con ese texto, usa la herramienta de edición de ficheros (Edit / Write).",
            )

    # FW-087/C2: se juzga lo que se ejecuta. Con flujo seguro, el dato de un
    # carrier (patrón de grep, mensaje de commit) no bloquea por compartir
    # línea con otro comando (FW-228); con flujo inseguro se juzga todo.
    judged = [
        (k, _resolve_rm_targets(seg, cwd))
        for k, (seg, _) in enumerate(segments) if unsafe or not _is_text_carrier(seg)
    ]
    if not judged:
        return (False, "")
    tmpdir = _trusted_tmpdir(command) if depth == 0 else None
    known: dict[str, object] = dict(_known_assignments(scan.text, tmpdir=tmpdir, substitutions=scan.substitutions))
    if tmpdir:
        known[_TMPDIR_KEY] = tmpdir
    if for_vars:
        known[_FOR_VARS_KEY] = for_vars

    # Chequeo por línea (patrones multi-token). Cada segmento en su propia línea
    # y con sus comillas: `[^\n]*` no cruza de un comando al siguiente; un `;`
    # que separaba sentencias SQL se conserva para juzgarlas una a una.
    full_line = "\n".join(
        " ".join(shlex.quote(tok) for tok in seg) + (" ;" if ";" in segments[k][1] else "")
        for k, seg in judged
    )
    blocked, reason = _check_one_line(full_line, known)
    if blocked:
        return (True, reason)

    # Chequeo por segmento normalizado (C2): sin wrappers, git sin flags globales
    # y descenso a shells anidados (`sh -c "..."`). Unir sin comillas es a
    # propósito: así `echo 'rm -rf ~' | bash` se juzga como lo que ejecuta.
    for k, seg in judged:
        # Si la normalización se come el segmento entero, se juzga tal cual.
        nseg = _normalize_segment(seg) or seg
        if nseg[0] in _SHELL_WRAPPERS:
            flag = next(
                (p for p, tok in enumerate(nseg) if tok == "-c" or
                 (tok.startswith("-") and not tok.startswith("--") and "c" in tok[1:])),
                None,
            )
            if flag is not None and flag + 1 < len(nseg):
                blocked, reason = _evaluate(nseg[flag + 1], allow_marker=False, depth=depth + 1, code_context=True, cwd=cwd)
                if blocked:
                    return (True, f"(dentro de {nseg[0]} -c) {reason}")
        # DELETE dentro de un argumento (`psql -c "DELETE FROM x"`): su sentencia
        # es el propio token, así que un WHERE de otro comando no la cubre.
        for tok in seg:
            if any(_DELETE_RE.search(st) and not _WHERE_RE.search(st) for st in tok.split(";")):
                return (True, "DELETE FROM sin WHERE borra la tabla entera")
        joined = " ".join(nseg)
        client = _pipe_client(segments, k)
        blocked, reason = _check_one_line(
            f"{client} {joined}" if client else joined,
            known,
            sql=False,
        )
        if blocked:
            return (True, reason)

    return (False, "")


# --- FW-236: ejecución indirecta → ask ---------------------------------------
# Lo que el guard no puede probar destructivo por su texto, pero que ejecuta algo
# que sí lo es por un camino indirecto, pide confirmación (`ask`) en vez de pasar
# en silencio. Tres clases, todas por FORMA sintáctica (lista cerrada):
#   1. una orden guardada en una variable y ejecutada;
#   2. decodificar (base64, xxd -r…) y ejecutar;
#   3. un fichero que otro programa ejecuta sin nombrarlo (Makefile + make…).
# La cuarta (`python -c` con borrados) se retiró en la 5.3.1 (S3).
# `deny` gana siempre: `ask` solo se emite si nada bloquea.

_DECODE_SHORT = {"d", "D"}
_INTERPRETER_RE = re.compile(r"^(?:python(?:\d+(?:\.\d+)*)?|perl|ruby|node|php)$")


def _is_decoder(seg: list[str]) -> bool:
    rest = _strip_wrappers(seg)
    if not rest:
        return False
    head, args = rest[0].rsplit("/", 1)[-1], rest[1:]
    if head in {"base64", "base32", "basenc"}:
        return any(
            a == "--decode" or (a.startswith("-") and not a.startswith("--") and set(a[1:]) & _DECODE_SHORT)
            for a in args
        )
    if head == "uudecode":
        return True
    if head == "xxd":
        return any(a == "-revert" or (a.startswith("-") and not a.startswith("--") and "r" in a[1:]) for a in args)
    if head == "openssl":
        return "-d" in args and any(a in {"enc", "base64"} for a in args)
    return False


def _is_stdin_executor(rest: list[str]) -> bool:
    """Un shell o intérprete que ejecuta lo que recibe por stdin: sin `-c` ni
    fichero de script (`sh`, `bash -s`, `python3 -`). `python3 -m json.tool` o
    `bash script.sh` reciben datos, no un programa."""
    if not rest:
        return False
    head = rest[0].rsplit("/", 1)[-1]
    args = rest[1:]
    if head in _SHELL_WRAPPERS:
        return all(a.startswith("-") and "c" not in a[1:] and "n" not in a[1:] for a in args)
    if _INTERPRETER_RE.match(head):
        return all(a in _PYTHON_STDIN_FLAGS for a in args)
    return False


def _text_has_decoder(text: str) -> bool:
    scanned = _scan(text)
    return any(_is_decoder(seg) for seg, _ in _segments_with_ops(_lex(scanned.text)))


def _var_names(text: str) -> set[str]:
    return {m.group(1) or m.group(2) for m in _VAR_REF_RE.finditer(text)}


def _executed_texts(segments: list[tuple[list[str], str]]) -> list[list[str]]:
    """Tokens que el segmento ejecuta como código: la orden en posición de
    comando cuando es una variable o una sustitución, el argumento de `eval` y el
    de `sh -c`."""
    out: list[list[str]] = []
    for seg, _ in segments:
        rest = _strip_wrappers(seg)
        if not rest:
            continue
        head = rest[0].rsplit("/", 1)[-1]
        if head == "eval":
            out.append(rest[1:])
        elif head in _SHELL_WRAPPERS:
            flag = next(
                (p for p, tok in enumerate(rest) if tok == "-c" or
                 (tok.startswith("-") and not tok.startswith("--") and "c" in tok[1:])),
                None,
            )
            if flag is not None and flag + 1 < len(rest):
                out.append([rest[flag + 1]])
        if _VAR_REF_RE.search(rest[0]) or _SUB_PLACEHOLDER_RE.search(rest[0]):
            out.append(rest)
    return out


# Ficheros que un programa ejecuta sin que el comando los nombre.
_EXEC_RELEVANT_FILE_RE = re.compile(
    r"(?:^|/)(?:Makefile|makefile|GNUmakefile|[^/]+\.mk|package\.json|justfile|Justfile|"
    r"Taskfile\.ya?ml|Rakefile|Dockerfile|docker-compose\.ya?ml|setup\.py|conftest\.py|"
    r"noxfile\.py|tox\.ini|\.envrc|\.bashrc|\.zshrc|\.profile|\.zprofile|\.bash_profile|"
    r"\.pre-commit-config\.yaml)$"
)
_HOOK_FILE_RE = re.compile(r"(?:^|/)(?:\.git/hooks|\.husky)/[^/]+$")
_GIT_HOOK_TRIGGERS = {"commit", "push", "merge", "checkout", "switch", "rebase", "pull", "am", "cherry-pick", "gc"}


def _redirect_targets(seg: list[str]) -> list[str]:
    return [seg[i + 1] for i, tok in enumerate(seg) if _OUTPUT_REDIRECT_RE.match(tok) and i + 1 < len(seg)]


def _written_files(segments: list[tuple[list[str], str]], scan: _Scan) -> list[tuple[int, str, str]]:
    """(índice de segmento, ruta, texto escrito) de cada fichero que el comando
    escribe con `>`, `>>`, `tee` o un heredoc."""
    out: list[tuple[int, str, str]] = []

    def keep(target: str) -> bool:
        return not target.startswith("/dev/") and not target.startswith("&")

    for k, (seg, _) in enumerate(segments):
        rest = _strip_wrappers(seg)
        head = rest[0].rsplit("/", 1)[-1] if rest else ""
        targets = _redirect_targets(seg)
        if head == "tee":
            targets += [tok for tok in rest[1:] if not tok.startswith("-")]
        redirected = set(targets)
        content = " ".join(
            tok for i, tok in enumerate(seg[1:], start=1)
            if tok not in redirected and not _OUTPUT_REDIRECT_RE.match(tok)
        )
        if head == "tee" and k > 0 and _is_pipe_op(segments[k - 1][1]):
            content = " ".join(segments[k - 1][0][1:])
        for target in targets:
            if keep(target):
                out.append((k, _path_key(target), content))
    for receiver, body, _ in scan.heredocs:
        rtargets = _redirect_targets(receiver)
        if receiver and receiver[0].rsplit("/", 1)[-1] == "tee":
            rtargets += [tok for tok in receiver[1:] if not tok.startswith("-")]
        index = next((k for k, (seg, _) in enumerate(segments) if seg[:len(receiver)] == receiver), 0)
        for target in rtargets:
            if keep(target):
                out.append((index, _path_key(target), body))
    return out


def _content_destructive(text: str, depth: int) -> str:
    """Motivo si el texto escrito, leído como órdenes, es destructivo. Se juzga
    por líneas y, dentro de una línea, por trozos entre comillas y llaves (un
    `package.json` lleva la orden dentro de una cadena)."""
    text = text.replace("\\n", "\n").replace("\\t", "\t")
    pieces: list[str] = []
    for line in text.split("\n")[:400]:
        line = line.strip()
        if not line:
            continue
        pieces.append(line)
        if any(ch in line for ch in "\"{}[],"):
            pieces.extend(p.strip() for p in re.split(r"[\"{}\[\],]+", line) if p.strip())
    for piece in pieces[:400]:
        blocked, why = _evaluate(piece, allow_marker=False, depth=depth + 1, code_context=True)
        if blocked:
            return why
    return ""


def _implicit_executor_after(segments: list[tuple[list[str], str]], index: int, path: str) -> bool:
    for seg, _ in segments[index + 1:]:
        rest = _strip_wrappers(seg)
        if not rest:
            continue
        head = rest[0].rsplit("/", 1)[-1]
        if _HOOK_FILE_RE.search(path):
            sub = next((t for t in rest[1:] if not t.startswith("-")), "")
            if head == "pre-commit" or (head == "git" and sub in _GIT_HOOK_TRIGGERS):
                return True
        elif _EXEC_RELEVANT_FILE_RE.search(path) and (
            head not in _SAFE_FILE_CONSUMERS or (head == "shellcheck" and "/" in rest[0])
        ):
            return True
    return False


def _indirect_reason(command: str, *, depth: int, cwd: str | None = None) -> str:
    """Motivo por el que el comando ejecuta algo destructivo de forma indirecta,
    o "" si no se ve ninguna de las tres clases."""
    if not command or not command.strip() or depth > _MAX_DEPTH:
        return ""
    scan = _scan(command)
    if scan.unparseable:
        return ""
    segments = _segments_with_ops(_lex(scan.text))
    known = _known_literal_assignments(scan.text)

    # Lo que se ejecuta dentro de sustituciones, `sh -c` y heredocs a un shell.
    for inner in scan.substitutions:
        why = _indirect_reason(inner, depth=depth + 1)
        if why:
            return why
    for seg, _ in segments:
        nseg = _normalize_segment(seg) or seg
        if nseg and nseg[0] in _SHELL_WRAPPERS:
            flag = next(
                (p for p, tok in enumerate(nseg) if tok == "-c" or
                 (tok.startswith("-") and not tok.startswith("--") and "c" in tok[1:])),
                None,
            )
            if flag is not None and flag + 1 < len(nseg):
                why = _indirect_reason(nseg[flag + 1], depth=depth + 1)
                if why:
                    return why
    for receiver, body, _ in scan.heredocs:
        if any(tok.rsplit("/", 1)[-1] in _SHELL_WRAPPERS for tok in receiver):
            why = _indirect_reason(body, depth=depth + 1)
            if why:
                return why

    # 2. decodificar y ejecutar: por tubería, por sustitución de proceso, por
    # sustitución en posición de ejecución o por variable.
    chain = procsub = False
    for seg, op in segments:
        rest = _strip_wrappers(seg)
        decoder = _is_decoder(seg)
        if (procsub and decoder) or (chain and _is_stdin_executor(rest)):
            return "decodifica contenido y lo ejecuta (base64, xxd -r…): no se puede ver qué corre"
        chain = (chain or decoder) and _is_pipe_op(op)
        if "<(" in op and _is_stdin_executor(rest):
            procsub = True
    decoded_subs = {i for i, inner in enumerate(scan.substitutions) if _text_has_decoder(inner)}
    decoded_vars: set[str] = set()
    for seg, _ in segments:
        for tok in seg:
            match = _ASSIGNMENT_RE.match(tok)
            if match and any(int(m.group(1)) in decoded_subs for m in _SUB_PLACEHOLDER_RE.finditer(match.group(2))):
                decoded_vars.add(match.group(1))

    # 1. orden guardada en una variable (o decodificada) y ejecutada.
    for tokens in _executed_texts(segments):
        joined = " ".join(tokens)
        if any(int(m.group(1)) in decoded_subs for m in _SUB_PLACEHOLDER_RE.finditer(joined)) or (
            _var_names(joined) & decoded_vars
        ):
            return "ejecuta contenido decodificado (base64, xxd -r…): no se puede ver qué corre"
        expanded = " ".join(_expand_known(tok, known) for tok in tokens)
        if expanded != joined:
            blocked, why = _evaluate(expanded, allow_marker=False, depth=depth + 1, code_context=True)
            if blocked:
                return f"ejecuta una orden guardada en una variable y es destructiva ({why})"

    # 3. fichero escrito y ejecutado por otro programa sin nombrarlo.
    for index, path, text in _written_files(segments, scan):
        if not (_EXEC_RELEVANT_FILE_RE.search(path) or _HOOK_FILE_RE.search(path)):
            continue
        if not _implicit_executor_after(segments, index, path):
            continue
        why = _content_destructive(text, depth)
        if why:
            return f"escribe {path}, que otro programa ejecuta, con contenido destructivo ({why})"
    # La clase 4 (código Python que borra o ejecuta) se retiró en la 5.3.1 (S3):
    # la orden que lanza el código en línea la juzga `_evaluate` (spawn).
    return ""


def _effective_cwd(command: str, cwd: object) -> str | None:
    """`cwd` del evento si es fiable para resolver rutas relativas: absoluto y sin
    nada en el comando que pueda cambiar de directorio o esconderlo."""
    leading = _LEADING_TEMP_CD_RE.match(command) if isinstance(command, str) else None
    if not leading and (not isinstance(cwd, str) or not cwd.startswith("/") or "\x00" in cwd):
        return None
    # El shell quita comillas y escapes antes de ejecutar: `c''d`, `c\d` y
    # `p''ushd` son `cd` y `pushd`. Se mira el texto ya sin ellos.
    if leading:
        # 5.2.2 L4 (PA FW-138): `cd <ruta absoluta literal bajo prefijo
        # temporal> && …`. Con `&&`, si el cd falla lo demás no corre; con `;`
        # correría en el cwd original, así que no se acepta. El resto del
        # comando no puede volver a cambiar de directorio.
        import posixpath
        raw = leading.group("q1") or leading.group("q2") or leading.group("bare")
        # `..`/`.` en la ruta del cd: normpath los quita como texto, pero el
        # kernel resuelve un enlace antes de aplicar `..` (mismo criterio que
        # `_relative_target_is_ephemeral` para los destinos).
        if any(part in (".", "..") for part in raw.split("/")):
            return None
        target = posixpath.normpath(raw)
        rest = command[leading.end():]
        # Solo una cadena `&&`: tras `;`, `||`, `&` o un salto de línea el
        # resto corre aunque el cd haya fallado, en el cwd original.
        if (target + "/").startswith(_SAFE_ABS_PREFIXES) and not _LIST_BREAK_RE.search(rest) and not _CWD_TAINT_RE.search(
            re.sub(r"['\"\\]", "", rest.replace("\\\n", ""))
        ):
            return target
        return None
    if _CWD_TAINT_RE.search(re.sub(r"['\"\\]", "", command.replace("\\\n", ""))):
        return None
    return cwd


_TEMP_CD_PATH = r"/[A-Za-z0-9_./@+-]+"
_LIST_BREAK_RE = re.compile(r";|\|\||\n|(?<!&)&(?!&)")
_LEADING_TEMP_CD_RE = re.compile(
    rf"^\s*cd\s+(?:'(?P<q1>{_TEMP_CD_PATH})'|\"(?P<q2>{_TEMP_CD_PATH})\"|(?P<bare>{_TEMP_CD_PATH}))\s*&&"
)
_CWD_TAINT_RE = re.compile(
    r"\$|`|(?<![\w.-])(?:cd|pushd|popd|chdir|source|eval|exec)(?![\w-])|(?:^|[\s;&|(])\.(?:\s|$)"
)


def judge(command: str, cwd: object = None) -> tuple[str, str]:
    """Veredicto tri-estado: ("pass"|"ask"|"deny", motivo). `deny` gana a `ask`."""
    command = _neutral_wal(command)
    # `is_destructive` sigue siendo el punto de análisis del hook (los tests de
    # cableado lo sustituyen): el `deny` sale siempre de ahí.
    blocked, reason = is_destructive(command) if cwd is None else is_destructive(command, cwd)
    if blocked:
        return ("deny", reason)
    size = len(command.encode("utf-8", "surrogatepass")) if isinstance(command, str) else 0
    if size > _MAX_COMMAND_BYTES or not command.strip() or _APPROVED_RE.search(_scan(command).comment):
        return ("pass", "")
    why = _indirect_reason(command, depth=0, cwd=_effective_cwd(command, cwd))
    return ("ask", why) if why else ("pass", "")


# --- Adaptador Claude Code (hook PreToolUse) --------------------------------

# 5.3.1 L1.4: motivos del núcleo irreversible (`.agent/rules/limites_duros.json`).
# Esas órdenes también las bloquea una regla nativa del arnés, que no mira el
# marcador: su salida es que las ejecute el builder.
_CORE_REASON_MARKS = (
    "git push --force", "git push con refspec", "git push --mirror", "(db push)", "drizzle-kit push",
    "supabase db reset", "prisma migrate reset", "DROP destruye", "TRUNCATE vacía", "migración forzada",
    "sobre una rama compartida",
)


def _deny_payload(reason: str) -> str:
    if any(mark in reason for mark in _CORE_REASON_MARKS):
        exit_text = (
            "Es una orden del núcleo irreversible (.agent/rules/limites_duros.json): no se desbloquea con "
            "el marcador. Si el builder la autoriza, la ejecuta él (en Claude Code, con !orden en el prompt; "
            "en Codex, en su terminal)."
        )
    else:
        exit_text = ("Si es intencional y está autorizado por el builder, reejecuta añadiendo al final: "
                     "# APPROVED-DESTRUCTIVE: <razón>.")
    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"Production Command Guard (FW-082): comando destructivo bloqueado — {reason}. {exit_text}"
            ),
        }
    })


def _ask_payload(reason: str) -> str:
    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": (
                f"Production Command Guard (FW-236): ejecución indirecta de algo potencialmente "
                f"destructivo — {reason}. Confirma solo si es intencional. Si ya lo autorizó el "
                f"builder, reejecuta añadiendo al final: # APPROVED-DESTRUCTIVE: <razón>."
            ),
        }
    })


def main() -> int:
    """Hook PreToolUse: lee el evento por stdin; deniega lo destructivo y pide
    confirmación (`ask`) ante la ejecución indirecta de algo destructivo.

    Fail-open solo ante un evento ilegible o que no es un Bash (stdout vacío);
    el análisis de un Bash deniega si falla o no termina (FW-237).
    """
    try:
        raw = sys.stdin.read()
        event = json.loads(raw)
    except Exception:  # noqa: BLE001 — fail-open ante basura
        return 0

    if not isinstance(event, dict):
        return 0
    if event.get("tool_name") != "Bash":
        return 0
    command = (event.get("tool_input") or {}).get("command", "")
    if not isinstance(command, str):
        return 0

    # FW-237 (decisión del builder 2026-09-14): un Bash que el guard no llega a
    # analizar no está demostrado seguro. Salir sin veredicto no es neutro: el
    # host deja pasar un hook que falla o agota su tiempo.
    try:
        verdict, reason = _analyze_with_time_limit(command, event.get("cwd"))
    except _AnalysisTimeout:
        verdict, reason = "deny", (
            f"el análisis no terminó en {_ANALYSIS_TIME_LIMIT_S:g} s; parte el comando o mueve el "
            "contenido grande a un fichero"
        )
    except Exception as exc:  # noqa: BLE001 — cualquier fallo del análisis deniega
        verdict, reason = "deny", f"el guard falló al analizar el comando ({type(exc).__name__}); regístralo como FW"
    if verdict == "deny":
        sys.stdout.write(_deny_payload(reason))
    elif verdict == "ask":
        sys.stdout.write(_ask_payload(reason))
    return 0


class _AnalysisTimeout(Exception):
    pass


def _analyze_with_time_limit(command: str, cwd: object = None) -> tuple[str, str]:
    """`judge` con límite de tiempo. El límite de tamaño acota lo conocido;
    este cubre un término superlineal que aún no conozcamos."""
    if not hasattr(signal, "setitimer"):
        return judge(command, cwd)

    def _expired(_signum, _frame):
        raise _AnalysisTimeout

    previous = signal.signal(signal.SIGALRM, _expired)
    signal.setitimer(signal.ITIMER_REAL, _ANALYSIS_TIME_LIMIT_S)
    try:
        return judge(command, cwd)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


if __name__ == "__main__":
    sys.exit(main())
