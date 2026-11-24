#!/usr/bin/env python3
"""code_graph.py - Adaptador CODE_GRAPH: shim de blast-radius para /cambio-seguro.

Envuelve codebase-memory-mcp (https://github.com/DeusData/codebase-memory-mcp)
y normaliza su salida al contrato del framework. Nunca escribe en el árbol del
proyecto y nunca invoca gestión de ADRs (03_arch_decisions.md es la única
fuente de decisiones arquitectónicas).

Esquema real de `detect_changes` verificado en el código fuente de la
herramienta (src/mcp/mcp.c: handle_detect_changes), no en su README:
  {"changed_files": [...], "changed_count": N,
   "impacted_symbols": [{"name","label","file"}, ...], "depth": N}
No expone riesgo ni clasificación ADITIVO/COMPATIBLE/DESTRUCTIVO: este shim no
las infiere (Enmienda 9 S3.32 -- no fingir una capacidad que la herramienta no
provee). callers requeriria llamadas trace_path adicionales por simbolo: fuera
de alcance de esta fase, declarado siempre via warning explicito.

detect_changes solo mide diff YA presente en el arbol de trabajo. Si se invoca
antes de editar (caso normal en /cambio-seguro paso 0.6), un diff vacio NO es
evidencia de "sin impacto": es evidencia de que el cambio aun no se ha hecho.
Por eso un diff vacio degrada explicitamente en vez de devolver "ok" con listas
vacias (ver consultar_blast_radius).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Pin de versión verificado (server.json de codebase-memory-mcp). Cambiar este
# valor es un cambio tier_1: implica reverificar los guardarraíles contra el
# binario instalado. El pin también está re-incrustado en
# .agent/scripts/doctor_wiring.json → features.code_graph_adapter →
# required_regex (busca "0.8.1") — actualizar AMBOS en el mismo commit, o el
# doctor fallará con un mensaje que no señala la causa real del mismatch.
TOOL_VERSION_PIN = "0.8.1"

TOOL_BINARY_NAME = "codebase-memory-mcp"

# auto_watch nunca se activa desde este shim: el framework no quiere un
# watcher en background reindexando sin control explícito de sesión.
auto_watch = False

CALLERS_WARNING = (
    "callers no se calcula en modo diff (detect_changes no los expone) — para "
    "callers de un símbolo concreto usar --symbol, que consulta trace_path "
    "inbound. No asumir ausencia de callers a partir de una lista vacía."
)

INDEX_FRESHNESS_WARNING = (
    "La evidencia proviene del índice de la herramienta, no del árbol de "
    "trabajo en vivo — si hubo cambios sin reindexar, puede estar incompleta."
)


def _binario_disponible() -> str | None:
    return shutil.which(TOOL_BINARY_NAME)


def _version_instalada(binario: str) -> str | None:
    try:
        out = subprocess.run(
            [binario, "--version"], capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    texto = (out.stdout or "").strip() or (out.stderr or "").strip()
    return texto or None


def _version_coincide_con_pin(version_output: str) -> bool:
    """Compara EXACTO contra TOOL_VERSION_PIN, no por substring: "0.8.1" in
    "0.8.10" sería True con `in`, aceptando una versión distinta por error.

    El prefijo `v` opcional (convención común en CLIs, "v0.8.1") se excluye
    explícitamente de la extracción: \\b no es frontera entre 'v' y '0' (ambos
    caracteres de palabra), así que un \\b(\\d+\\.\\d+\\.\\d+)\\b desnudo no
    matchea "v0.8.1" y degradaría el adaptador incluso con el pin correcto
    instalado."""
    match = re.search(r"(?:^|[^0-9.])v?(\d+\.\d+\.\d+)\b", version_output)
    if not match:
        return False
    return match.group(1) == TOOL_VERSION_PIN



def _ultimo_json(salida: str) -> dict | None:
    """La CLI de la herramienta emite logs (`level=info ...`) en stdout ANTES
    del JSON de resultado, así que `json.loads(stdout)` completo falla.
    Descubierto en el dogfood real (Example Desktop, 2026-07-10, binario 0.8.1).
    Devuelve el último objeto JSON top-level de la salida, o None si no
    hay ninguno. Escanea hacia adelante y salta la longitud completa de cada
    objeto encontrado (via raw_decode) para no descender a sus llaves
    internas como candidatos separados — necesario porque un valor anidado
    (p. ej. {"meta": {...}}) parsea con éxito por sí solo si se escanea de
    derecha a izquierda, capturando el sub-objeto en vez del resultado completo."""
    s = salida.strip()
    if not s:
        return None
    decoder = json.JSONDecoder()
    last = None
    i, n = 0, len(s)
    while i < n:
        while i < n and s[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        if s[i] != "{":
            i += 1
            continue
        try:
            obj, end = decoder.raw_decode(s, i)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict):
            last = obj
        i = end
    return last


def _resolver_nombre_proyecto(binario: str, root: str) -> str | None:
    """`project` NO es una ruta: es el nombre normalizado que la herramienta
    asigna al indexar (p. ej. `Users-x-Proyecto`). Se resuelve consultando su
    propio `list_projects` (root_path → name) en vez de replicar su
    normalización, que es detalle interno suyo. None = no indexado."""
    try:
        out = subprocess.run(
            [binario, "cli", "list_projects", "{}"],
            capture_output=True, text=True, timeout=30, check=False, cwd=root,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    datos = _ultimo_json(out.stdout or "")
    if not datos:
        return None
    root_resuelto = str(Path(root).resolve())
    for proyecto in datos.get("projects", []):
        if str(Path(proyecto.get("root_path", "")).resolve()) == root_resuelto:
            return proyecto.get("name")
    return None


def _invocar_detect_changes(binario: str, root: str, proyecto: str, diff_ref: str) -> dict | None:
    """Invoca `codebase-memory-mcp cli detect_changes` y devuelve el JSON ya
    parseado, o None si el subproceso falla, sale con error, o la salida no es
    JSON válido. Nunca lanza.

    FW-157: `project` NO es una ruta, es el nombre normalizado que la
    herramienta asigna al indexar. El arreglo se aplicó en su día a
    `_invocar_trace_path` pero no aquí, así que este camino seguía enviando la
    ruta absoluta y la herramienta no resolvía el proyecto: el shim degradaba a
    inventario manual siempre, sin que nada lo dijera. `proyecto` es el nombre
    canónico que devuelve `list_projects`; `root` se conserva como `cwd` para
    que el binario resuelva las operaciones git contra el árbol correcto y no
    contra el cwd heredado del proceso que invoca este shim."""
    payload = json.dumps({"project": proyecto, "base_branch": diff_ref, "scope": "symbols"})
    try:
        out = subprocess.run(
            [binario, "cli", "detect_changes", payload],
            capture_output=True, text=True, timeout=30, check=False, cwd=root,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return _ultimo_json(out.stdout or "")


def _contrato_degradado(warnings: list[str], tool_version: str = "no disponible") -> dict:
    return {
        "status": "degraded",
        "afectados": [],
        "callers": [],
        "riesgo": "unknown",
        "clasificacion_sugerida": None,
        "evidence": [],
        "tool_version": tool_version,
        "warnings": warnings,
        "source": f"code_graph_adapter/{TOOL_BINARY_NAME}",
    }


def consultar_blast_radius(root: str, diff_ref: str = "HEAD") -> dict:
    """Devuelve el contrato normalizado de blast-radius para /cambio-seguro.

    Nunca lanza: cualquier problema de disponibilidad, pin, subproceso o
    parseo se traduce en status="degraded" con un warning explícito.
    """
    warnings: list[str] = []
    binario = _binario_disponible()

    if binario is None:
        warnings.append(
            f"Binario '{TOOL_BINARY_NAME}' no encontrado en PATH — degradando a inventario manual (CODE_GRAPH_MANUAL)."
        )
        return _contrato_degradado(warnings)

    version = _version_instalada(binario)
    if version is None or not _version_coincide_con_pin(version):
        warnings.append(
            f"Versión instalada de '{TOOL_BINARY_NAME}' ({version or 'desconocida'}) no coincide exactamente con "
            f"el pin declarado ({TOOL_VERSION_PIN}) — degradando a inventario manual (CODE_GRAPH_MANUAL)."
        )
        return _contrato_degradado(warnings, tool_version=version or "desconocida")

    proyecto = _resolver_nombre_proyecto(binario, root)
    if proyecto is None:
        warnings.append(
            "El proyecto no está indexado por la herramienta (o el índice está corrupto). "
            f"Indexarlo primero: {TOOL_BINARY_NAME} cli index_repository '{{\"repo_path\": \"<ruta>\"}}'. "
            "Degradando a inventario manual (CODE_GRAPH_MANUAL)."
        )
        return _contrato_degradado(warnings, tool_version=version)

    salida = _invocar_detect_changes(binario, root, proyecto, diff_ref)
    if salida is None:
        warnings.append(
            "La invocación 'cli detect_changes' falló o no devolvió JSON válido — degradando a inventario "
            "manual (CODE_GRAPH_MANUAL)."
        )
        return _contrato_degradado(warnings, tool_version=version)

    impactados = salida.get("impacted_symbols", [])
    cambiados = salida.get("changed_files", [])

    if not impactados and not cambiados:
        # detect_changes solo mide diff YA presente en el árbol de trabajo. Si
        # /cambio-seguro invoca este shim antes de editar (paso 0.6, caso
        # normal), no hay diff que mapear todavía. Un "ok" con listas vacías se
        # leería falsamente como "confirmado sin impacto" -- degradar en su
        # lugar para que el inventario manual siga siendo la fuente de verdad.
        warnings.append(
            "No se detectó diff en el árbol de trabajo — detect_changes solo mide cambios ya presentes; si el "
            "cambio propuesto aún no se ha hecho, esto es esperado, no un fallo. Degradando a inventario manual "
            "(CODE_GRAPH_MANUAL) en vez de reportar 'ok' sin evidencia."
        )
        return _contrato_degradado(warnings, tool_version=version)

    afectados = [
        f"{s.get('label', '?')} {s.get('name', '?')} ({s.get('file', '?')})" for s in impactados
    ]
    evidence = [f"changed_files: {f}" for f in cambiados]
    warnings.append(CALLERS_WARNING)

    # Se construye sobre _contrato_degradado (única definición de la forma del
    # contrato) en vez de repetir las 9 claves a mano — evita que el camino
    # "ok" y el camino "degraded" diverjan en forma si el contrato cambia.
    return {
        **_contrato_degradado(warnings, tool_version=version),
        "status": "ok",
        "afectados": afectados,
        "evidence": evidence,
    }


def _invocar_trace_path(binario: str, root: str, proyecto: str, simbolo: str, depth: int) -> dict | None:
    """Invoca `codebase-memory-mcp cli trace_path` (inbound) y devuelve el JSON
    parseado, o None si el subproceso falla o la salida no es JSON. Nunca lanza.

    Esquema verificado en el fuente de la herramienta 0.8.1
    (src/mcp/mcp.c: handle_trace_call_path, dispatch "trace_path"):
      entrada {"project","function_name","direction","depth"}
      salida  {"function","direction","callers":[{"name","qualified_name","hop"}]}
      errores {"error": "...", "hint": "..."} (not found / ambigüedad)."""
    payload = json.dumps({
        "project": proyecto,
        "function_name": simbolo,
        "direction": "inbound",
        "depth": depth,
    })
    try:
        out = subprocess.run(
            [binario, "cli", "trace_path", payload],
            capture_output=True, text=True, timeout=30, check=False, cwd=root,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # Dogfood real (2026-07-10): ante símbolo no encontrado/ambiguo la
    # herramienta sale con rc=1 y emite el JSON de error (`error` + `hint`) por
    # STDERR, no por stdout. Descartarlo perdía esa guía y el shim reportaba
    # "no devolvió JSON válido", que es falso. Se busca en ambos flujos.
    return _ultimo_json(out.stdout or "") or _ultimo_json(out.stderr or "")


def consultar_afectados(root: str, simbolo: str, depth: int = 2) -> dict:
    """Blast radius PRE-edición (fase 2, FW-B): callers de un símbolo que aún
    no se ha tocado, vía trace_path inbound. Mismo contrato que
    consultar_blast_radius; la clasificación ADITIVO/COMPATIBLE/DESTRUCTIVO
    sigue siendo del agente (evidencia, no autoridad). Nunca lanza."""
    warnings: list[str] = []
    binario = _binario_disponible()

    if binario is None:
        warnings.append(
            f"Binario '{TOOL_BINARY_NAME}' no encontrado en PATH — degradando a inventario manual (CODE_GRAPH_MANUAL)."
        )
        return _contrato_degradado(warnings)

    version = _version_instalada(binario)
    if version is None or not _version_coincide_con_pin(version):
        warnings.append(
            f"Versión instalada de '{TOOL_BINARY_NAME}' ({version or 'desconocida'}) no coincide exactamente con "
            f"el pin declarado ({TOOL_VERSION_PIN}) — degradando a inventario manual (CODE_GRAPH_MANUAL)."
        )
        return _contrato_degradado(warnings, tool_version=version or "desconocida")

    proyecto = _resolver_nombre_proyecto(binario, root)
    if proyecto is None:
        warnings.append(
            "El proyecto no está indexado por la herramienta (o el índice está corrupto). "
            f"Indexarlo primero: {TOOL_BINARY_NAME} cli index_repository '{{\"repo_path\": \"<ruta>\"}}'. "
            "Degradando a inventario manual (CODE_GRAPH_MANUAL)."
        )
        return _contrato_degradado(warnings, tool_version=version)

    salida = _invocar_trace_path(binario, root, proyecto, simbolo, depth)
    if salida is None:
        warnings.append(
            "La invocación 'cli trace_path' falló o no devolvió JSON válido — degradando a inventario "
            "manual (CODE_GRAPH_MANUAL)."
        )
        return _contrato_degradado(warnings, tool_version=version)

    if "error" in salida or "callers" not in salida:
        # "function not found" o ambigüedad: la herramienta no resolvió el
        # símbolo a un nodo único — no adivinar, degradar con su hint.
        hint = salida.get("hint", "")
        warnings.append(
            f"trace_path no resolvió '{simbolo}' ({salida.get('error', 'sin callers en la respuesta')})."
            + (f" Hint de la herramienta: {hint}" if hint else "")
            + " Degradando a inventario manual (CODE_GRAPH_MANUAL)."
        )
        return _contrato_degradado(warnings, tool_version=version)

    callers = [
        f"{c.get('name', '?')} ({c.get('qualified_name', '?')}) [hop {c.get('hop', '?')}]"
        for c in salida.get("callers", [])
    ]
    evidence = [f"trace_path inbound '{simbolo}' depth={depth}: {len(callers)} callers"]
    # A diferencia del diff vacío en detect_changes, 0 callers con el símbolo
    # resuelto SÍ es evidencia (el nodo existe y nadie lo llama en el índice) —
    # pero condicionada a la frescura del índice, que se advierte siempre.
    warnings.append(INDEX_FRESHNESS_WARNING)

    return {
        **_contrato_degradado(warnings, tool_version=version),
        "status": "ok",
        "afectados": list(callers),
        "callers": callers,
        "evidence": evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Adaptador CODE_GRAPH: blast-radius shim para /cambio-seguro"
    )
    parser.add_argument("--root", default=".", help="Raíz del proyecto a consultar")
    parser.add_argument("--diff-ref", default="HEAD", help="Referencia git contra la que calcular el diff")
    parser.add_argument(
        "--symbol", default=None,
        help="Símbolo a consultar ANTES de editarlo (trace_path inbound) en vez del diff",
    )
    parser.add_argument("--depth", type=int, default=2, help="Profundidad del traversal inverso (solo con --symbol)")
    args = parser.parse_args()

    if args.symbol:
        resultado = consultar_afectados(args.root, args.symbol, args.depth)
    else:
        resultado = consultar_blast_radius(args.root, args.diff_ref)
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
