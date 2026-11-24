#!/usr/bin/env bash
# Framework v3 — CI smoke / clean-clone contract.
#
# Proves, on a clean checkout, that:
#   1. framework_doctor.py passes,
#   2. the test suite passes,
#   3. sync_claude_commands.py is idempotent (no generated-output diff),
#   4. a clean Codex fixture can be configured and checked without credentials,
#   5. copying EXACTLY the README adoption block into a fresh root yields
#      `Framework doctor: OK` (the F1 clean-clone contract, end-to-end),
#   6. the suite also passes on a SHALLOW clone, the condition `actions/checkout`
#      creates and local runs never reproduce (FW-183, FW-193).
#
# Run locally: bash .agent/scripts/ci_smoke.sh
# Used by .github/workflows/framework-ci.yml so portability ("clean clone →
# doctor OK + tests") is proven on every push, not just asserted in prose.
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$REPO_ROOT"

# El intérprete no se asume: un proyecto con virtualenv no tiene sus deps en el
# `python3` del sistema, y el smoke fallaba por entorno, no por código (FW-034).
# Rescatado de Example Service, donde el parche vivía solo en local (FW-169).
resolve_python() {
    if [[ -n "${FRAMEWORK_PYTHON:-}" ]]; then
        if [[ -x "$FRAMEWORK_PYTHON" ]]; then
            printf '%s\n' "$FRAMEWORK_PYTHON"
            return
        fi
        if command -v "$FRAMEWORK_PYTHON" >/dev/null 2>&1; then
            command -v "$FRAMEWORK_PYTHON"
            return
        fi
        echo "ci_smoke: FRAMEWORK_PYTHON no es ejecutable ni está en PATH: $FRAMEWORK_PYTHON" >&2
        exit 1
    fi
    for candidate in "$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/venv/bin/python"; do
        if [[ -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return
        fi
    done
    command -v python3
}

PYTHON_BIN=$(resolve_python)

# El runner tampoco: un proyecto con pytest lo usa; el resto cae a unittest.
run_tests() {
    if [[ -f pyproject.toml || -f pytest.ini || -f setup.cfg ]] && "$PYTHON_BIN" -c 'import pytest' >/dev/null 2>&1; then
        "$PYTHON_BIN" -B -m pytest -q tests
    else
        "$PYTHON_BIN" -B -m unittest discover -s tests -p 'test_*.py'
    fi
}

echo "== 1/6 doctor =="
"$PYTHON_BIN" .agent/scripts/framework_doctor.py

echo "== 2/6 tests =="
run_tests

echo "== 3/6 sync idempotency =="
TMP_ROOT=$(mktemp -d)
trap 'rm -rf "$TMP_ROOT"' EXIT
SYNC_BEFORE="$TMP_ROOT/sync-before"
SYNC_OUTPUTS=(
    .claude/commands .claude/agents .claude/rules .agents/skills
    .opencode/commands .opencode/agents
    AGENTS.md CLAUDE.md
)
for item in "${SYNC_OUTPUTS[@]}"; do
    [[ -e "$item" ]] || continue   # una superficie ausente en un checkout parcial no rompe el smoke
    mkdir -p "$SYNC_BEFORE/$(dirname "$item")"
    cp -R "$item" "$SYNC_BEFORE/$item"
done
"$PYTHON_BIN" .agent/scripts/sync_claude_commands.py >/dev/null
sync_changed=0
for item in "${SYNC_OUTPUTS[@]}"; do
    if [[ ! -e "$SYNC_BEFORE/$item" && ! -e "$item" ]]; then
        continue
    fi
    if ! diff -qr "$SYNC_BEFORE/$item" "$item" >/dev/null 2>&1; then
        sync_changed=1
    fi
done
if [[ "$sync_changed" -ne 0 ]]; then
    echo "ci_smoke: sync NO es idempotente; hay diff en salidas generadas"
    for item in "${SYNC_OUTPUTS[@]}"; do
        diff -ru "$SYNC_BEFORE/$item" "$item" || true  # fail-open-declarado: impresión de diagnóstico; el fallo ya se decidió arriba y el bloque termina en exit 1
    done
    exit 1
fi

echo "== 4/6 Codex clean fixture (configuración sin credenciales) =="
CODEX_FIXTURE="$TMP_ROOT/codex-fixture"
mkdir -p "$CODEX_FIXTURE"
cp -R .agent .agents _project AGENTS.md "$CODEX_FIXTURE/"
"$PYTHON_BIN" - "$CODEX_FIXTURE" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
profile = root / "_project" / "15_environment_profile.md"
text = profile.read_text(encoding="utf-8")
for old in (
    "- Herramienta: [Codex | Claude Code | Antigravity | OpenCode | Otro]",
    "- Herramienta: Antigravity",
    "- Herramienta: Claude Code",
    "- Herramienta: OpenCode",
):
    text = text.replace(old, "- Herramienta: Codex")
profile.write_text(text, encoding="utf-8")
fake = root / "fake-codex"
fake.write_text(
    "#!/bin/sh\n"
    "if [ \"$1\" = \"--version\" ]; then echo codex-cli fixture; exit 0; fi\n"
    "if [ \"$1\" = \"mcp\" ] && [ \"$2\" = \"list\" ]; then echo state_manager; exit 0; fi\n"
    "exit 1\n",
    encoding="utf-8",
)
fake.chmod(0o755)
PY
"$PYTHON_BIN" .agent/scripts/ensure_codex_integration.py --root "$CODEX_FIXTURE" --from-profile
"$PYTHON_BIN" .agent/scripts/sync_codex_hooks.py --root "$CODEX_FIXTURE" --check
"$PYTHON_BIN" .agent/scripts/codex_runtime_smoke.py --root "$CODEX_FIXTURE" --codex-bin "$CODEX_FIXTURE/fake-codex" --json

echo "== 5/6 F1 clean-copy contract (copiar lo del README → doctor OK) =="
DEST="$TMP_ROOT/clean-copy"
mkdir -p "$DEST"
# Exactly the entries the README "Copia el framework" block lists.
COPY_BLOCK=(
    AGENTS.md CLAUDE.md GEMINI.md GUARDRAILS_MASTER.md README.md llms.txt
    .agent .agents .claude/settings.json .claude/commands .claude/agents .claude/rules _project
    .opencode/commands .opencode/agents
    tests/gate_calibration.py
    opencode.json
    .gitignore .gitattributes
)
for item in "${COPY_BLOCK[@]}"; do
    if [[ ! -e "$item" ]]; then
        echo "ci_smoke: el README lista '$item' pero no existe en el repo"
        exit 1
    fi
    mkdir -p "$DEST/$(dirname "$item")"
    cp -R "$item" "$DEST/$item"
done
# El bloque de adopción representa un clean clone. Al ejecutarlo desde un
# worktree local, no arrastrar caches/metadata ignorada dentro de la prueba F1.
find "$DEST" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$DEST" -type f \( -name .DS_Store -o -name '*.pyc' \) -exec rm -f {} +
"$PYTHON_BIN" -B "$DEST/.agent/scripts/framework_doctor.py"

echo "== 6/6 suite en clon superficial (FW-193) =="
# `actions/checkout` clona con profundidad 1. Un test que resuelva historia —
# el commit `release: X.Y.Z` de referencia, por ejemplo — pasa en local con
# historial completo y falla SOLO en Actions. Pasó dos veces en tres días
# (FW-183 y FW-193) y la nota en el field report no impidió la segunda: una
# lección que no ejecuta nada no es un gate. Aquí la condición se reproduce
# antes del push, no después.
#
# Limitación declarada: se clona el HEAD commiteado, así que este paso no ve
# trabajo sin commitear. Es un contrato de pre-push, no de pre-commit.
if [[ "$(git rev-parse --is-shallow-repository 2>/dev/null || echo unknown)" == "true" ]]; then
    echo "ci_smoke: el repo YA es superficial (p. ej. actions/checkout); el paso 2/6 ya ejerció esa condición"
elif ! git rev-parse --git-dir >/dev/null 2>&1; then
    echo "ci_smoke: sin repo git; no hay clon superficial que probar (degradación declarada)"
else
    SHALLOW="$TMP_ROOT/shallow"
    git clone -q --depth 1 "file://$REPO_ROOT" "$SHALLOW"
    profundidad=$(git -C "$SHALLOW" rev-list --count HEAD)
    if [[ "$profundidad" -ne 1 ]]; then
        echo "ci_smoke: el clon no salió superficial ($profundidad commits); el paso no probaría nada"
        exit 1
    fi
    # FW-198: el clon superficial reproducía la HISTORIA de Actions pero heredaba
    # el `~/.gitconfig` del portátil. Un test que llamara a `git commit` sin
    # identidad propia pasaba aquí y moría en el runner con `empty ident name` —
    # el guard de FW-193 no cubría esta cara de la clase. Neutralizar la config
    # global y exigir `useConfigOnly` reproduce también el entorno, no solo la
    # historia: un fixture que dependa de la identidad ambiente falla AQUÍ.
    #
    # `LC_ALL=C` por el mismo motivo: el git de un portátil en español dice
    # "Identidad del autor desconocido" donde el runner dice "empty ident name".
    # Hoy ningún test afirma sobre texto de herramienta; el día que alguien lo
    # escriba, esta línea hace que diverja aquí y no en Actions.
    (
        cd "$SHALLOW" \
        && GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null \
           GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=user.useConfigOnly GIT_CONFIG_VALUE_0=true \
           LC_ALL=C LANG=C \
           run_tests
    )
fi

echo "ci_smoke: OK"
