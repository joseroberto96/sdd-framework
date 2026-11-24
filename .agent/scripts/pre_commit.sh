#!/usr/bin/env bash
# Framework v3 pre-commit hook
#
# Install once per clone:
#   bash .agent/scripts/pre_commit.sh install
#
# El hook instalado es un lanzador que ejecuta este script del árbol (TL-FW-320).
# Rechaza el commit si fallan framework_doctor, la suite o los gates de estado.
# FW-194: sin pipefail, `cmd | filtro` devuelve el exit del filtro — una
# herramienta ausente o reventada pasa por verde. Deliberadamente SIN `set -e`:
# este hook captura exit codes a mano (`doctor_status=$?`) para poder explicar
# el bloqueo, y abortar en la asignación se comería ese mensaje.
set -o pipefail

# Texto exacto del lanzador; el doctor lo lee de aquí sin ejecutar el script.
# Sin el script en el árbol, bloquea con su propio mensaje (D9, FW-273).
launcher_text() {
    cat <<'LAUNCHER'
#!/usr/bin/env bash
# Framework pre-commit launcher (5.3.1 L2.3, TL-FW-320): ejecuta el pre_commit.sh del árbol.
f="$(git rev-parse --show-toplevel)/.agent/scripts/pre_commit.sh"; [ -f "$f" ] || { echo "pre-commit: falta $f en este árbol; restaura .agent/ desde git (o quita el hook si el proyecto ya no usa el framework) (FW-273)"; exit 1; }; exec bash "$f" "$@"
LAUNCHER
}

if [[ "$1" == "print-launcher" ]]; then
    launcher_text
    exit 0
fi

if [[ "$1" == "install" ]]; then
    REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
    if [[ -z "$REPO_ROOT" ]]; then
        echo "pre_commit: not inside a git repo"
        exit 1
    fi
    SOURCE="$REPO_ROOT/.agent/scripts/pre_commit.sh"
    # FW-207: destino resuelto con git (en un worktree `.git` es un puntero); los
    # hooks viven en el common dir, compartidos por los worktrees.
    HOOK_PATH=$(git -C "$REPO_ROOT" rev-parse --git-path hooks/pre-commit 2>/dev/null)
    case "$HOOK_PATH" in
        /*) TARGET="$HOOK_PATH" ;;
        "") TARGET="$REPO_ROOT/.git/hooks/pre-commit" ;;
        *)  TARGET="$REPO_ROOT/$HOOK_PATH" ;;
    esac
    mkdir -p "$(dirname "$TARGET")" || exit 1
    if [[ ! -f "$SOURCE" ]]; then
        echo "pre_commit: source not found at $SOURCE"
        exit 1
    fi
    if [[ -f "$TARGET" ]] && launcher_text | cmp -s - "$TARGET"; then
        echo "pre_commit: el lanzador ya está instalado en $TARGET"
    else
        if [[ -f "$TARGET" ]]; then
            # Bot FW-102: un hook del framework (copia antigua o lanzador de otra
            # versión) se cambia con respaldo; un hook propio nunca se pisa.
            if grep -Eq "^# Framework (v[0-9]+ pre-commit hook|pre-commit launcher)" "$TARGET"; then
                BACKUP="$TARGET.bak-$(date +%Y%m%d%H%M%S)"
                cp "$TARGET" "$BACKUP" || exit 1
                echo "pre_commit: el hook de $TARGET es del framework (copia antigua o lanzador de otra versión) — se cambia por el lanzador (respaldo: $BACKUP)"
            else
                echo "pre_commit: existing hook at $TARGET differs from framework source"
                echo "pre_commit: aborting to avoid overwriting manual hook"
                exit 1
            fi
        fi
        launcher_text > "$TARGET" || exit 1
        chmod +x "$TARGET" || exit 1
        echo "pre_commit: lanzador instalado en $TARGET (ejecuta .agent/scripts/pre_commit.sh del árbol)"
    fi
    # ADR-139/FW-201: instala también el pre-push; si falla, solo avisa.
    PUSH_SOURCE="$REPO_ROOT/.agent/scripts/pre_push.sh"
    if [[ -f "$PUSH_SOURCE" ]]; then
        bash "$PUSH_SOURCE" install || echo "pre_commit: aviso — no se pudo instalar pre-push (no bloquea)"
    fi
    exit 0
fi
# Siempre desde la raíz del árbol, también el subcomando `suite` (revisión security de L2).
repo_top=$(git rev-parse --show-toplevel 2>/dev/null) && cd "$repo_top"
# Interpreter resolution (FW-034): a Python project's deps live in its virtualenv,
# not in the system python3. Honor an explicit $FRAMEWORK_PYTHON (for layouts whose
# venv is not at .venv/), else prefer .venv/bin/python, else fall back to python3.
# Using the system python3 makes the suite fail with ModuleNotFoundError and trains
# the operator to bypass the hook with --no-verify, disarming every gate.
PYTHON_BIN="${FRAMEWORK_PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
    if [[ -x .venv/bin/python ]]; then
        PYTHON_BIN=".venv/bin/python"
    elif [[ -x venv/bin/python ]]; then
        # Cosecha 2026-07-09 (Boton): el layout venv/ (sin punto) es común y el
        # hook lo ignoraba, cayendo al python3 del sistema sin las deps.
        PYTHON_BIN="venv/bin/python"
    else
        # FW-283: un worktree no trae el venv del repo principal. Probar el del repo
        # común (`git rev-parse --git-common-dir`) antes de caer al python3 del sistema.
        MAIN_ROOT=""
        COMMON_DIR="$(git rev-parse --git-common-dir 2>/dev/null || true)"  # fail-open-declarado: sin git o sin repo común solo se pierde la búsqueda del venv; cae a python3
        if [[ -n "$COMMON_DIR" ]]; then
            MAIN_ROOT="$(cd "$COMMON_DIR/.." 2>/dev/null && pwd -P || true)"  # fail-open-declarado: ruta ilegible equivale a no haber repo principal; cae a python3
        fi
        if [[ -n "$MAIN_ROOT" && "$MAIN_ROOT" != "$(pwd -P)" && -x "$MAIN_ROOT/.venv/bin/python" ]]; then
            PYTHON_BIN="$MAIN_ROOT/.venv/bin/python"
        elif [[ -n "$MAIN_ROOT" && "$MAIN_ROOT" != "$(pwd -P)" && -x "$MAIN_ROOT/venv/bin/python" ]]; then
            PYTHON_BIN="$MAIN_ROOT/venv/bin/python"
        else
            PYTHON_BIN="python3"
        fi
    fi
fi
# L2.4 (TL-FW-319, D8): valor de una clave del perfil, leído del ÍNDICE (el
# subcomando `suite`, del árbol). El marcador de plantilla `[…]` cuenta como vacío.
PROFILE_REL="_project/15_environment_profile.md"
PROFILE_FROM_TREE=0
profile_value() {
    local text line value
    if [[ "$PROFILE_FROM_TREE" == 1 ]]; then
        text=$(cat "$PROFILE_REL" 2>/dev/null) || return 0
    else
        text=$(git show ":$PROFILE_REL" 2>/dev/null) || return 0
    fi
    line=$(printf '%s\n' "$text" | grep -m1 -E "^[[:space:]]*-[[:space:]]*\`?$1\`?[[:space:]]*:") || return 0
    value="${line#*:}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    [[ "$value" == \[*\] ]] && value=""
    printf '%s' "$value"
}

FW099_HINT="-> Si este bloqueo es friccion del framework (falso positivo, gate injusto): registrala AHORA como PREFIJO-FW-NNN en _feedback/field_report.md (PREFIJO = fw_prefix de _project/15_environment_profile.md; solo en modo template: FW-NNN en .agent/docs/field_report.md). Mencionarla solo en el chat la pierde. (FW-099)"

# SUITE_RUNNER = cmd | pytest | unittest | none. `suite_command` (D8): sin eval, se
# parte por espacios; el primer token es una ruta relativa del repo, sin `..`, que
# existe (`.py` con el Python del proyecto; si no, ejecutable). Inválido → bloquea.
# Sin él: pytest si hay señal (conftest.py, pytest.ini, .pytest.ini, [tool.pytest];
# obligatorio, L9), unittest si no; sin tests/, no hay suite (FW-038).
select_runner() {
    SUITE_COMMAND=$(profile_value suite_command)
    if [[ -n "$SUITE_COMMAND" ]]; then
        read -r -a suite_argv <<< "$SUITE_COMMAND"
        local suite_bin="${suite_argv[0]}" reason=""
        [[ "$suite_bin" != *.py && ! -x "$suite_bin" ]] && reason="no es ejecutable: $suite_bin (chmod +x o un script .py)"
        [[ -f "$suite_bin" ]] || reason="no existe en el repositorio: $suite_bin"
        [[ "/$suite_bin/" == */../* ]] && reason="la ruta tiene un segmento .."
        [[ "$suite_bin" == /* ]] && reason="ruta absoluta; debe ser relativa al repositorio"
        if [[ -n "$reason" ]]; then
            echo "pre_commit: suite_command del perfil no válido ($reason): $SUITE_COMMAND"
            echo "-> Corrige suite_command en $PROFILE_REL (p. ej. scripts/run_tests.sh --full) o déjalo vacío para usar pytest/unittest."
            exit 1
        fi
        SUITE_RUNNER="cmd"
        return 0
    fi
    if [[ ! -d tests ]]; then
        SUITE_RUNNER="none"
        return 0
    fi
    local pytest_signal=0 toml_status
    if [[ -f tests/conftest.py ]] || [[ -f conftest.py ]] || [[ -f pytest.ini ]] || [[ -f .pytest.ini ]]; then
        pytest_signal=1
    elif [[ -f pyproject.toml ]]; then
        "$PYTHON_BIN" -c 'import pathlib, sys
try:
    import tomllib
    data = tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))
except (ImportError, OSError, UnicodeError, ValueError):
    sys.exit(2)
tool = data.get("tool", {})
if not isinstance(tool, dict) or ("pytest" in tool and not isinstance(tool["pytest"], dict)):
    sys.exit(2)
sys.exit(0 if "pytest" in tool else 10)' >/dev/null 2>&1
        toml_status=$?
        if [[ $toml_status -eq 0 ]]; then
            pytest_signal=1
        elif [[ $toml_status -ne 10 ]]; then
            echo "pre_commit: pyproject.toml no se pudo leer como TOML válido: no se sabe si el proyecto usa pytest. Corrige el TOML (o ejecuta el hook con un Python >= 3.11) y reintenta."
            exit 1
        fi
    fi
    if [[ $pytest_signal -eq 1 ]] && ! "$PYTHON_BIN" -c "import pytest" >/dev/null 2>&1; then
        echo "pre_commit: el proyecto declara pytest (conftest.py, pytest.ini, .pytest.ini o [tool.pytest] en pyproject.toml) pero pytest no es importable con $PYTHON_BIN. Instala pytest en ese entorno o exporta FRAMEWORK_PYTHON=<ruta al python del venv>; no se cae a unittest porque daria un falso verde."
        exit 1
    fi
    if [[ $pytest_signal -eq 1 ]]; then SUITE_RUNNER="pytest"; else SUITE_RUNNER="unittest"; fi
}

# FW-106: la suite no hereda las GIT_* del hook (operaría sobre el índice real).
# Exit 5 («no tests collected»): éxito con unittest; con pytest, solo sin ficheros.
exec_suite() {
    case "$SUITE_RUNNER" in
        cmd)
            if [[ "${suite_argv[0]}" == *.py ]]; then
                "$PYTHON_BIN" "./${suite_argv[0]}" "${suite_argv[@]:1}"
            else
                "./${suite_argv[0]}" "${suite_argv[@]:1}"
            fi
            ;;
        pytest) "$PYTHON_BIN" -B -m pytest -q tests ;;
        unittest) "$PYTHON_BIN" -B -m unittest discover -s tests ;;
    esac
}

run_suite() {
    tests_output=$(
        unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR \
              GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES \
              GIT_CEILING_DIRECTORIES GIT_PREFIX
        exec_suite 2>&1
    )
    tests_status=$?
    [[ $tests_status -eq 5 && "$SUITE_RUNNER" == unittest ]] && tests_status=0
    if [[ $tests_status -eq 5 && "$SUITE_RUNNER" == pytest ]]; then
        if [[ -z "$(find tests \( -name 'test_*.py' -o -name '*_test.py' \) -print -quit 2>/dev/null)" ]]; then
            tests_status=0
        else
            tests_output="pytest no recogio ningun test (exit 5) pero tests/ contiene ficheros test_*.py o *_test.py: revisa la configuracion de pytest (testpaths, python_files, markers).
$tests_output"
        fi
    fi
}

fail_suite() {
    echo "pre_commit: tests failed${SUITE_COMMAND:+ (suite_command: $SUITE_COMMAND)}"
    echo "$tests_output"
    echo "-> Si el commit solo toca documentación, declara esas rutas en documentary_paths del perfil (_project/15_environment_profile.md); con un informe verde del mismo árbol (bash .agent/scripts/pre_commit.sh suite) la suite no se repite."
    echo "$FW099_HINT"
    exit 1
}

# L2.5 (TL-FW-298): la suite corre una vez por árbol. El informe verde vive en el
# git dir del worktree, una línea `<árbol> <runner|python|versión>` por suite. Se
# puede escribir a mano: equivale a --no-verify, que tampoco es un límite duro.
suite_signature() {
    local py_version
    py_version=$("$PYTHON_BIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null)
    printf '%s%s|%s|%s' "$SUITE_RUNNER" "${SUITE_COMMAND:+:$SUITE_COMMAND}" "$PYTHON_BIN" "$py_version"
}

# El árbol de trabajo como tree, con una copia del índice activo (no toca el real).
worktree_tree() {
    local index tmp_index tree=""
    index="${GIT_INDEX_FILE:-$(git rev-parse --git-path index 2>/dev/null)}"
    tmp_index=$(mktemp) || return 0
    if cp "$index" "$tmp_index" 2>/dev/null \
       && GIT_INDEX_FILE="$tmp_index" git add -A -- . >/dev/null 2>&1; then
        tree=$(GIT_INDEX_FILE="$tmp_index" git write-tree 2>/dev/null)
    fi
    rm -f "$tmp_index"
    printf '%s' "$tree"
}

record_green_suite() {
    local report line tmp
    report=$(git rev-parse --git-path framework-suite-green 2>/dev/null)
    [[ -n "$report" && -n "$1" ]] || return 0
    line="$1 $(suite_signature)"
    tmp=$(mktemp "$report.XXXXXX" 2>/dev/null) || return 0
    { grep -vxF -- "$line" "$report" 2>/dev/null; echo "$line"; } | tail -n 20 > "$tmp" && mv "$tmp" "$report" || rm -f "$tmp"
}

green_report_has() {
    local report
    report=$(git rev-parse --git-path framework-suite-green 2>/dev/null)
    [[ -n "$report" && -n "$1" && -f "$report" ]] && grep -qxF -- "$1 $(suite_signature)" "$report"
}

# Subcomando `suite` (FW-358): la suite sobre el árbol de trabajo, sin doctor ni
# gates; si sale verde, registra el informe que el commit reutiliza.
if [[ "$1" == "suite" ]]; then
    PROFILE_FROM_TREE=1
    select_runner
    if [[ "$SUITE_RUNNER" == none ]]; then
        echo "pre_commit suite: no hay suite (ni tests/ ni suite_command en el perfil)"
        exit 0
    fi
    suite_tree=$(worktree_tree)
    run_suite
    [[ $tests_status -ne 0 ]] && fail_suite
    record_green_suite "$suite_tree"
    echo "pre_commit suite: verde; informe registrado para el árbol ${suite_tree:0:12}"
    exit 0
fi

# C3 (triaje 2026-07-09): el doctor escanea los BLOBS DEL ÍNDICE en contexto
# pre-commit — lo que el commit va a llevar, no el worktree.
export FRAMEWORK_PRECOMMIT=1
doctor_output=$("$PYTHON_BIN" .agent/scripts/framework_doctor.py 2>&1)
doctor_status=$?
# Exit 2 = SOLO drift de sincronización de wrappers/menú (regenerable) —
# auto-reparar con el sync y reintentar UNA vez en vez de bloquear el commit
# por causas ajenas al producto (cosecha: PA FW-038, Bot FW-001, TL FW-004).
if [[ $doctor_status -eq 2 ]]; then
    # TL-FW-315: solo se vuelven a preparar las salidas del sync, y solo si no
    # tienen cambios sin preparar (si los tienen, se aborta).
    GEN_PATHS=(.claude/commands .claude/agents .claude/rules .opencode/commands .opencode/agents .opencode/skills .agents/skills CLAUDE.md AGENTS.md GEMINI.md)
    if ! gen_dirty=$(git diff --name-only -- "${GEN_PATHS[@]}" 2>/dev/null) \
       || ! gen_untracked=$(git ls-files --others --exclude-standard -- "${GEN_PATHS[@]}" 2>/dev/null); then
        echo "pre_commit: no se pudo comprobar si las salidas de sync_claude_commands.py tienen cambios sin preparar; no se regeneran (TL-FW-315)"
        exit 1
    fi
    gen_dirty="$gen_dirty${gen_untracked:+$'\n'$gen_untracked}"
    if [[ -n "$gen_dirty" ]]; then
        echo "pre_commit: el doctor pide regenerar los wrappers, pero estas salidas de sync_claude_commands.py tienen cambios sin preparar:"
        while IFS= read -r gen_line; do echo "  - $gen_line"; done <<< "$gen_dirty"
        echo "-> No se regeneran para no meter esos cambios en el commit (TL-FW-315). Prepáralos (git add) o apártalos y reintenta; o ejecuta python3 .agent/scripts/sync_claude_commands.py y prepara tú lo que cambie."
        exit 1
    fi
    echo "pre_commit: drift de wrappers detectado — regenerando con sync_claude_commands.py"
    "$PYTHON_BIN" .agent/scripts/sync_claude_commands.py >/dev/null 2>&1 || true  # fail-open-declarado: autorreparación best-effort; el veredicto lo da el doctor que se re-ejecuta abajo
    # C3: re-stagear lo regenerado, ruta a ruta (una que no existe no aborta las demás).
    for gen_path in "${GEN_PATHS[@]}"; do
        if [[ -e "$gen_path" ]] || git ls-files --error-unmatch -- "$gen_path" >/dev/null 2>&1; then
            git add -A -- "$gen_path" 2>/dev/null || true  # fail-open-declarado: re-stage best-effort; el doctor posterior decide
        fi
    done
    doctor_output=$("$PYTHON_BIN" .agent/scripts/framework_doctor.py 2>&1)
    doctor_status=$?
fi
if [[ $doctor_status -ne 0 ]]; then
    echo "pre_commit: framework_doctor failed"
    echo "$doctor_output"
    echo "-> Si este bloqueo es friccion del framework (falso positivo, gate injusto): registrala AHORA como PREFIJO-FW-NNN en _feedback/field_report.md (PREFIJO = fw_prefix de _project/15_environment_profile.md; solo en modo template: FW-NNN en .agent/docs/field_report.md). Mencionarla solo en el chat la pierde. (FW-099)"
    exit 1
fi
# `documentary_paths`: patrones del perfil que amplían la lista documental; se
# ignoran con aviso los que esconderían el framework, los tests o la suite entera.
DOC_PATTERNS=()
documentary_raw=$(profile_value documentary_paths)
if [[ -n "$documentary_raw" ]]; then
    IFS=',' read -r -a documentary_items <<< "$documentary_raw"
    for documentary_item in "${documentary_items[@]}"; do
        pat="${documentary_item#"${documentary_item%%[![:space:]]*}"}"
        pat="${pat%"${pat##*[![:space:]]}"}"
        [[ -z "$pat" ]] && continue
        first_segment="${pat%%/*}"
        reason=""
        case "$first_segment" in .agent|.claude|.codex|.opencode|.agents|.git|tests) reason="ruta del framework o de tests" ;; esac
        [[ "$first_segment" == *[*?]* ]] && reason="comodín en el primer segmento"
        [[ "/$pat/" == */../* ]] && reason="segmento .."
        [[ "$pat" == /* ]] && reason="ruta absoluta"
        [[ "$pat" =~ ^[A-Za-z0-9._/*?-]+$ ]] || reason="solo se admiten letras, cifras y . _ - / * ?"
        if [[ -n "$reason" ]]; then echo "pre_commit: documentary_paths ignora '$pat': $reason"; else DOC_PATTERNS+=("$pat"); fi
    done
fi

# Suite selectiva (BOTÓN FW-085/FW-086): solo un diff exclusivamente
# documental puede omitir pytest. La allowlist es deliberadamente explícita
# por ruta y extensión; estar dentro de _project/, _feedback/ o .agent/ no
# vuelve documental un archivo de código. Los renames inspeccionan ambos lados
# para que cruzar la frontera documental nunca relaje el gate.
is_documentary_path() {
    case "$1" in
        README.md) return 0 ;;
        _project/*.md|_project/*.json|_project/*.jsonl) return 0 ;;
        _feedback/*.md) return 0 ;;
        _scratch/*.txt|_scratch/*.md) return 0 ;;
    esac
    local pat
    for pat in "${DOC_PATTERNS[@]}"; do
        # shellcheck disable=SC2254  # el patrón del perfil se usa como glob a propósito
        case "$1" in $pat) return 0 ;; esac
    done
    return 1
}

staged=$(git diff --cached --name-status --find-renames 2>/dev/null)
staged_status=$?
skip_suite=1
if [[ $staged_status -ne 0 ]]; then
    # No es un repo git (o el comando falló por otra razón): no hay señal
    # fiable de qué se está commiteando — comportamiento actual (suite corre).
    skip_suite=0
elif [[ -n "$staged" ]]; then
    while IFS=$'\t' read -r status path destination; do
        [[ -z "$status" ]] && continue
        if ! is_documentary_path "$path"; then
            skip_suite=0
        fi
        case "$status" in
            R*|C*)
                if ! is_documentary_path "$destination"; then
                    skip_suite=0
                fi
                ;;
        esac
    done <<< "$staged"
fi
if [[ $skip_suite -eq 1 ]] && [[ ${#DOC_PATTERNS[@]} -gt 0 ]]; then
    echo "pre_commit: suite omitida (solo artefactos de sesión y rutas documentales del perfil) — gates de estado y doctor sí corren"
elif [[ $skip_suite -eq 1 ]]; then
    echo "pre_commit: suite omitida (solo artefactos de sesión) — gates de estado y doctor sí corren"
fi
# Suite (FW-038, TL-FW-298): si el árbol preparado es el de trabajo y tiene informe
# verde con la misma firma, no se repite; si corre y sale verde con los dos árboles
# iguales (medidos antes), se registra. Una suite roja nunca escribe el informe.
if [[ $skip_suite -eq 0 ]]; then
    select_runner
    if [[ "$SUITE_RUNNER" != none ]]; then
        tree_staged=$(git write-tree 2>/dev/null)
        tree_work=$(worktree_tree)
        same_tree=0
        [[ -n "$tree_staged" && "$tree_staged" == "$tree_work" ]] && same_tree=1
        if [[ $same_tree -eq 1 ]] && green_report_has "$tree_staged"; then
            echo "pre_commit: suite reutilizada — informe verde del mismo árbol (${tree_staged:0:12})"
        else
            run_suite
            [[ $tests_status -ne 0 ]] && fail_suite
            [[ $same_tree -eq 1 ]] && record_green_suite "$tree_staged"
        fi
    fi
fi
# Closure integrity check: reject illegal module state transitions in the diff
# between HEAD and the staged _project/03_project_state.json (e.g. blocked→completed,
# in_progress→completed). No-op when the state file is unchanged or absent in HEAD/index.
STATE_REL="_project/03_project_state.json"
if git rev-parse --verify HEAD >/dev/null 2>&1 \
   && git cat-file -e "HEAD:$STATE_REL" 2>/dev/null \
   && git cat-file -e ":$STATE_REL" 2>/dev/null; then
    prev_tmp=$(mktemp)
    new_tmp=$(mktemp)
    git show "HEAD:$STATE_REL" > "$prev_tmp" 2>/dev/null
    git show ":$STATE_REL" > "$new_tmp" 2>/dev/null
    # FW-037: el state_log admite caminos legales multi-paso; sin log, adyacencia
    # estricta. TL-FW-315: el log del ÍNDICE, el que viaja con el estado preparado.
    log_arg=()
    if git cat-file -e ":_project/state_log.jsonl" 2>/dev/null; then
        log_arg=("$(mktemp)")
        git show ":_project/state_log.jsonl" > "${log_arg[0]}" 2>/dev/null
    fi
    transition_output=$("$PYTHON_BIN" .agent/scripts/validate_transition.py "$prev_tmp" "$new_tmp" "${log_arg[@]}" 2>&1)
    transition_status=$?
    rm -f "$prev_tmp" "$new_tmp" "${log_arg[@]}"
    if [[ $transition_status -ne 0 ]]; then
        echo "pre_commit: transición de estado de módulo ilegal (compara HEAD:$STATE_REL contra el ÍNDICE/staged, no el working tree — si acabas de editar el estado, recuerda 'git add' antes de reintentar)"
        echo "$transition_output"
        echo "-> Si este bloqueo es friccion del framework (falso positivo, gate injusto): registrala AHORA como PREFIJO-FW-NNN en _feedback/field_report.md (PREFIJO = fw_prefix de _project/15_environment_profile.md; solo en modo template: FW-NNN en .agent/docs/field_report.md). Mencionarla solo en el chat la pierde. (FW-099)"
        exit 1
    fi
fi
# L7 (N2): estado respaldado por la bitácora. El estado (y el log) staged deben ser
# lo que la bitácora respalda respecto a HEAD: un estado editado a mano, un evento
# borrado o un estado staged sin su log no pasan. ERROR bloquea; WARN informa.
# Escape documentado para migraciones legítimas: la acción auditada
# `acknowledge_state_delta` (queda en la bitácora; no hay variable de entorno).
LOG_REL="_project/state_log.jsonl"
if git rev-parse --verify HEAD >/dev/null 2>&1 \
   && git cat-file -e "HEAD:$STATE_REL" 2>/dev/null \
   && git cat-file -e ":$STATE_REL" 2>/dev/null \
   && ! git diff --cached --quiet -- "$STATE_REL" "$LOG_REL" 2>/dev/null; then
    head_state_tmp=$(mktemp)
    staged_state_tmp=$(mktemp)
    head_log_tmp=$(mktemp)
    staged_log_tmp=$(mktemp)
    git show "HEAD:$STATE_REL" > "$head_state_tmp" 2>/dev/null
    git show ":$STATE_REL" > "$staged_state_tmp" 2>/dev/null
    # Un log ausente en HEAD o en el índice se pasa como ruta vacía (el CLI lo interpreta).
    git show "HEAD:$LOG_REL" > "$head_log_tmp" 2>/dev/null || : > "$head_log_tmp"
    if git cat-file -e ":$LOG_REL" 2>/dev/null; then
        git show ":$LOG_REL" > "$staged_log_tmp" 2>/dev/null
    else
        rm -f "$staged_log_tmp"
        staged_log_tmp=""
    fi
    delta_output=$("$PYTHON_BIN" .agent/scripts/validate_state_delta.py \
        --head-state "$head_state_tmp" --head-log "$head_log_tmp" \
        --staged-state "$staged_state_tmp" --staged-log "$staged_log_tmp" 2>&1)
    delta_status=$?
    rm -f "$head_state_tmp" "$staged_state_tmp" "$head_log_tmp"
    [[ -n "$staged_log_tmp" ]] && rm -f "$staged_log_tmp"
    if [[ $delta_status -ne 0 ]]; then
        echo "pre_commit: el estado no está respaldado por la bitácora (compara HEAD contra el ÍNDICE; si acabas de mutar con el motor, haz 'git add' del estado Y de _project/state_log.jsonl)"
        echo "$delta_output"
        echo "-> Muta el estado solo con el motor (mutate_state / state_manager.py mutate) y revisa con /reconciliar. Migración legítima: registra acknowledge_state_delta con las rutas literales."
        echo "-> Si este bloqueo es friccion del framework (falso positivo, gate injusto): registrala AHORA como PREFIJO-FW-NNN en _feedback/field_report.md (PREFIJO = fw_prefix de _project/15_environment_profile.md; solo en modo template: FW-NNN en .agent/docs/field_report.md). Mencionarla solo en el chat la pierde. (FW-099)"
        exit 1
    elif [[ -n "$delta_output" ]]; then
        echo "pre_commit: estado respaldado por la bitácora — avisos (no bloquean):"
        echo "$delta_output"
    fi
fi
exit 0
