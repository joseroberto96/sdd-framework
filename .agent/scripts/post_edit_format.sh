#!/usr/bin/env bash
# Framework v3 — Hook PostToolUse stack-agnóstico (Fase 5 de MEJORAS_WORKSHOP.md).
#
# Se invoca desde .claude/settings.json tras cada Edit/Write/MultiEdit.
# Detecta la extensión del archivo modificado y elige formateador SI está instalado.
# Si ninguna herramienta está disponible o el archivo no es formateable: no-op.
#
# Contrato:
# - SIEMPRE sale con exit 0 (no bloquea el flujo aunque algo falle).
# - Cualquier error de formato se silencia con redirección a /dev/null.
# - No asume stack del proyecto. No instala nada. No reformatea archivos no escritos.
#
# Entrada esperada (Claude Code PostToolUse):
# - Variable de entorno CLAUDE_TOOL_FILE_PATH con la ruta del archivo escrito.
# - Si la variable no está, intenta leer la ruta del primer argumento.

set +e  # Nunca propagar errores. exit 0 al final, pase lo que pase.

target="${CLAUDE_TOOL_FILE_PATH:-${1:-}}"

# Sin archivo objetivo → no-op silencioso.
if [ -z "$target" ] || [ ! -f "$target" ]; then
  exit 0
fi

# Skip files outside the project tree (defensive).
# FW-235: Claude Code lanza el hook en el cwd de la sesión, que un `cd` desplaza a
# un subdirectorio; la raíz sale de CLAUDE_PROJECT_DIR. Codex fija cwd=raíz.
project_root="$(cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null && pwd -P)"
if [ -z "$project_root" ]; then
  exit 0
fi
case "$target" in
  /*) ;;
  *)  target="$(pwd)/$target" ;;
esac
target_dir="$(cd "$(dirname "$target")" 2>/dev/null && pwd -P)"
if [ -z "$target_dir" ]; then
  exit 0
fi
target="$target_dir/$(basename "$target")"

case "$target" in
  "$project_root"|"$project_root"/*) ;;
  *) exit 0 ;;
esac

# Skip generated wrappers para que sync_claude_commands.py mantenga su contrato.
case "$target" in
  *"/.claude/commands/"*|*"/.claude/agents/"*|*"/.claude/rules/"*|*"/.opencode/commands/"*|*"/.opencode/agents/"*|*"/.opencode/skills/"*|*"/.agents/skills/"*) exit 0 ;;
esac

extension="${target##*.}"

format_with() {
  # $1 = command, resto = args; redirige stderr/stdout para no romper hooks.
  command -v "$1" >/dev/null 2>&1 || return 1
  "$@" >/dev/null 2>&1
  return 0
}

# TL FW-252: un archivo que ya existía sin formato en HEAD no se reformatea; el
# diff de una edición puntual no debe arrastrar el archivo entero. Los archivos
# nuevos (o sin git, o sin formateador para comprobar) se formatean como siempre.
check_with() {
  # $1 = command, resto = args; lee el original por stdin. 0 = formateado.
  command -v "$1" >/dev/null 2>&1 || return 2
  "$@" >/dev/null 2>&1
}

head_unformatted() {
  local rel original status
  rel="${target#"$project_root"/}"
  original="$(git -C "$project_root" show "HEAD:$rel" 2>/dev/null)" || return 1
  case "$extension" in
    py)
      printf '%s\n' "$original" | check_with ruff format --check --stdin-filename "$rel" -
      status=$?
      if [ "$status" -eq 2 ]; then
        printf '%s\n' "$original" | check_with black --check -q -
        status=$?
      fi
      ;;
    ts|tsx|js|jsx|mjs|cjs|json|css|scss|md|yaml|yml)
      printf '%s\n' "$original" | check_with prettier --check --stdin-filepath "$rel"
      status=$?
      ;;
    go)
      command -v gofmt >/dev/null 2>&1 || return 1
      [ -n "$(printf '%s\n' "$original" | gofmt -l 2>/dev/null)" ] && return 0
      return 1
      ;;
    rs)
      printf '%s\n' "$original" | check_with rustfmt --check
      status=$?
      ;;
    sh|bash)
      printf '%s\n' "$original" | check_with shfmt -d
      status=$?
      ;;
    *) return 1 ;;
  esac
  [ "$status" -eq 1 ]
}

if head_unformatted; then
  exit 0
fi

case "$extension" in
  py)
    format_with ruff format "$target" \
      || format_with black --quiet "$target"
    ;;
  ts|tsx|js|jsx|mjs|cjs|json|css|scss|md|yaml|yml)
    format_with prettier --log-level silent --write "$target"
    ;;
  go)
    format_with gofmt -w "$target"
    ;;
  rs)
    format_with rustfmt --quiet "$target"
    ;;
  sh|bash)
    format_with shfmt -w "$target"
    ;;
  *)
    # Extensión no reconocida → no-op.
    ;;
esac

exit 0
