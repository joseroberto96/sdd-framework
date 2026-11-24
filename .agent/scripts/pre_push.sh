#!/usr/bin/env bash
# Framework v3 pre-push hook
#
# Install once per clone:
#   bash .agent/scripts/pre_push.sh install
# (también se instala automáticamente al correr `pre_commit.sh install`.)
#
# ADR-139/FW-201: GitHub avisa (GH001) a partir de 50 MB y bloquea duro a
# 100 MB. El aviso remoto no basta — no bloquea, y FW-168 documentó que un
# operador puede publicar varios pushes seguidos ignorándolo (recurrencia
# verificada 2026-09-01). Este hook mide ANTES de publicar: WARN a partir de
# 50 MB, bloquea (exit 1) a partir de 90 MB — margen bajo el límite duro de
# GitHub. Escape deliberado: ALLOW_LARGE_FILES=1 git push.
set -o pipefail

if [[ "$1" == "install" ]]; then
    REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
    if [[ -z "$REPO_ROOT" ]]; then
        echo "pre_push: not inside a git repo"
        exit 1
    fi
    SOURCE="$REPO_ROOT/.agent/scripts/pre_push.sh"
    # Mismo mecanismo worktree-safe que pre_commit.sh install (FW-207): resolver
    # el destino con git, no con la ruta literal .git/hooks — en un worktree
    # secundario $REPO_ROOT/.git es un archivo puntero.
    HOOK_PATH=$(git -C "$REPO_ROOT" rev-parse --git-path hooks/pre-push 2>/dev/null)
    case "$HOOK_PATH" in
        /*) TARGET="$HOOK_PATH" ;;
        "") TARGET="$REPO_ROOT/.git/hooks/pre-push" ;;
        *)  TARGET="$REPO_ROOT/$HOOK_PATH" ;;
    esac
    mkdir -p "$(dirname "$TARGET")" || exit 1
    if [[ ! -f "$SOURCE" ]]; then
        echo "pre_push: source not found at $SOURCE"
        exit 1
    fi
    if [[ -f "$TARGET" ]] && ! cmp -s "$SOURCE" "$TARGET"; then
        if grep -Eq "^# Framework v[0-9]+ pre-push hook" "$TARGET"; then
            BACKUP="$TARGET.bak-$(date +%Y%m%d%H%M%S)"
            cp "$TARGET" "$BACKUP" || exit 1
            echo "pre_push: hook at $TARGET is a framework-managed hook from a previous version — updating (backup: $BACKUP)"
        else
            echo "pre_push: existing hook at $TARGET differs from framework source"
            echo "pre_push: aborting to avoid overwriting manual hook"
            exit 1
        fi
    fi
    cp "$SOURCE" "$TARGET" || exit 1
    chmod +x "$TARGET" || exit 1
    echo "pre_push: hook installed at $TARGET"
    exit 0
fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
[[ -z "$REPO_ROOT" ]] && exit 0   # no es repo git: no-op, no bloquear

ALLOW_LARGE_FILES="${ALLOW_LARGE_FILES:-0}"
WARN_BYTES=$((50 * 1024 * 1024))
FAIL_BYTES=$((90 * 1024 * 1024))
blocked=0

_report_size() {
    # $1 = ruta descriptiva, $2 = tamaño en bytes
    local path="$1" size="$2" mb=$(( $2 / 1024 / 1024 ))
    if [[ "$size" -ge "$FAIL_BYTES" ]]; then
        echo "pre_push: $path es ${mb} MB (>= 90 MB, umbral duro — GitHub bloquea a 100 MB)"
        if [[ "$ALLOW_LARGE_FILES" != "1" ]]; then
            blocked=1
        else
            echo "pre_push: ALLOW_LARGE_FILES=1 — push permitido pese al tamaño"
        fi
    elif [[ "$size" -ge "$WARN_BYTES" ]]; then
        echo "pre_push: WARN — $path es ${mb} MB (>= 50 MB, umbral recomendado de GitHub, dispara GH001)"
    fi
}

# Artefactos conocidos del motor, comprobados contra HEAD (lo ya commiteado
# que se va a publicar), no el working tree.
for rel in "_project/state_log.jsonl" "_project/03_project_state.json"; do
    size=$(git -C "$REPO_ROOT" cat-file -s "HEAD:$rel" 2>/dev/null) || continue
    _report_size "$rel" "$size"
done

# Chequeo genérico: cualquier blob en el rango de commits que se va a
# publicar, no solo los dos artefactos conocidos del motor. Contrato stdin
# estándar de pre-push: una línea por ref a empujar
# "<local ref> <local sha1> <remote ref> <remote sha1>". Best-effort/fail-open
# — si `rev-list`/`cat-file` fallan por cualquier motivo, el hook no bloquea
# solo por eso (mismo criterio que otros puntos best-effort de pre_commit.sh).
zero_sha="0000000000000000000000000000000000000000"
while read -r local_ref local_sha remote_ref remote_sha; do
    [[ -z "$local_sha" || "$local_sha" == "$zero_sha" ]] && continue   # borrado de rama
    if [[ -z "$remote_sha" || "$remote_sha" == "$zero_sha" ]]; then
        # Rama nueva: no hay `remote_sha` que sirva de base. `local_sha` a
        # secas auditaría el historial COMPLETO alcanzable (miles de commits
        # ya en `origin/main`, cada tamaño histórico de cada blob) — ruido
        # masivo y, peor, un blob viejo ya conocido por el remoto podría
        # bloquear un push que no lo está subiendo de verdad. `--not
        # --remotes=origin` acota a lo que el remoto (según los refs
        # remote-tracking locales) todavía no tiene — aproximación barata sin
        # consultar el remoto en vivo, exacta si `git fetch` está al día.
        range_desc="$local_sha (rama nueva)"
        range_args=("$local_sha" "--not" "--remotes=origin")
    else
        range_desc="$remote_sha..$local_sha"
        range_args=("$remote_sha..$local_sha")
    fi
    while read -r blob_size blob_path; do
        [[ -z "$blob_path" ]] && continue
        _report_size "$blob_path (en $range_desc)" "$blob_size"
    done < <(git -C "$REPO_ROOT" rev-list --objects "${range_args[@]}" 2>/dev/null \
              | git -C "$REPO_ROOT" cat-file --batch-check='%(objecttype) %(objectname) %(rest) %(objectsize)' 2>/dev/null \
              | awk '$1=="blob"{size=$NF; $1=""; $2=""; $NF=""; sub(/^ +/,""); sub(/ +$/,""); print size, $0}')
              # %(rest) (la ruta) puede contener espacios: el split por defecto
              # de awk desplazaba los campos y $4 dejaba de ser el tamaño real.
              # %(objectsize) es siempre el ÚLTIMO campo ($NF) sin importar
              # cuántos campos ocupe la ruta; vaciar $1/$2/$NF fuerza a awk a
              # reconstruir $0 solo con los campos de la ruta (POSIX estándar,
              # no requiere gawk), y los sub() limpian los espacios sobrantes.
done

if [[ "$blocked" -eq 1 ]]; then
    echo "pre_push: push bloqueado por tamaño. Correr el shrink (ADR-139/FW-201):"
    echo "  python3 .agent/scripts/shrink_state_log_snapshots.py --write"
    echo "Escape deliberado (no recomendado salvo caso excepcional ya evaluado): ALLOW_LARGE_FILES=1 git push"
    exit 1
fi
exit 0
