# Matriz de Degradación por Capacidad

Generaliza el patrón `SECUENCIAL_NO_AISLADO` (degradación de subagentes) a **toda
capacidad del entorno**. Cada adaptador declara su conformidad apuntando aquí con
un marcador `**Degradación:**`. Si una capacidad no existe, se **degrada de forma
explícita y se declara** — nunca se finge (Enmienda 9 §3.32).

> Las capacidades reales del entorno activo se registran en
> `_project/15_environment_profile.md`. Esta matriz define qué hacer cuando falta
> cada una.

| Capacidad | Si existe | Si NO existe — degradación declarada | Nombre / dónde se declara |
|---|---|---|---|
| Slash commands | Usar `/comando` nativo | Ejecutar `.agent/workflows/<comando>.md` paso a paso | `WORKFLOW_MANUAL` · adapter |
| Hooks (PostToolUse, pre-commit) | Claude: `.claude/settings.json`; Codex CLI/Desktop: `.codex/hooks.json` gestionado por `sync_codex_hooks.py`, certificado solo por eventos/marcadores observados; pre-commit en `.git/hooks` | Recordatorios en `13_session_journal.md` + confirmación manual en `/gycd` | `HOOKS_MANUAL` · `15_environment_profile.md` |
| Subagentes de revisión | Reviewer + Security con aislamiento read-only | Si hay dispatcher sin enforcement: agente distinto + contrato no-write + diff pre/post; si no hay dispatcher: auditoría secuencial | `INDEPENDENT_SUBAGENT_NO_ISOLATION` o `SECUENCIAL_NO_AISLADO` · cierre-modulo |
| Ciclo de espera de subagentes | El adaptador observa terminales reales del dispatcher antes de cerrar la espera | `WAIT_TIMEOUT_NONTERMINAL`: timeout solo cierra polling; continuar hasta `PASS`, `FAIL`, `NEEDS_ATTENTION` o `UNAVAILABLE` según `SUBAGENT_WAIT_LIFECYCLE` | `SUBAGENT_WAIT_LIFECYCLE` · `.agent/docs/extended_protocol.md` + adapter |
| Compactación nativa | Usar la del entorno | persistir estado con `/gycd` y, tras compactar, `/reconciliar` | `COMPACT_FALLBACK` · adapter |
| Carga progresiva y respuesta acotada | `state_manager.py query` selecciona startup/implementation/closure/recovery, pagina y liga cursores a fingerprint de estado y fuentes; el workflow activa solo la etapa necesaria | `NEEDS_INPUT` si falta o es ambigua una fuente; `STALE_CURSOR` si cambia la instantánea; respuesta grande se pagina o fragmenta; no se convierte una medición privada de ventana en permiso | `get_work_context` / `COMPACT_FALLBACK` · adapter Codex |
| Browser / automation | Verificación UI automatizada (MCP/herramienta) | Verificación UI manual en `05_test_history.md` | `UI_MANUAL` · cierre-modulo paso 3.8 |
| Shell / búsqueda | `rg`/shell para localizar archivos | Pasar archivos concretos; usar `11_file_dependencies.md` | `SHELL_NONE` · `15_environment_profile.md` |
| git / gh | Publicación y gates de commit normales | Publicación manual; pre-commit como check manual | `VCS_MANUAL` · publicar-github |
| Motor de estado (MCP) | Claude: `.mcp.json`; Codex CLI/Desktop: `.codex/config.toml` nativo con `state_manager`; `.mcp.json` es espejo opcional y no prueba invocabilidad | `python3 .agent/scripts/state_manager.py mutate --action <a> --payload '<json>'` por shell | `MCP_FALLBACK` · adapter |
| Descubrimiento de skills | Skills nativas del entorno (`.opencode/skills`, `.claude/skills`, `.agents/skills`) | Referenciar skills como archivos de `.agent/skills/` desde los workflows | `SKILLS_DISCOVERY` · adapter |
| UI de elección estructurada | Selector nativo (cajas seleccionables, p. ej. AskUserQuestion en Claude Code) | Opciones numeradas en texto; elegir tecleando `A/B/C` | `CHOICE_TEXT` · adapter |
| Ejecutores paralelos (subagentes con escritura + `git worktree`) | `/ciclo fase --paralelo`: máx. 3 ejecutores en worktrees aislados (contrato `.agent/subagents/executor.md`) | Modo fase SECUENCIAL; declararlo — no fingir paralelismo | `PARALLEL_EXEC` → `SECUENCIAL_NO_AISLADO` · ciclo P3 |
| Grafo de código | Adaptador consulta blast radius (evidencia real desde `detect_changes`) para `/cambio-seguro` | INVENTARIO manual leyendo archivos/specs (comportamiento actual, sin cambios) | `CODE_GRAPH_MANUAL` · `15_environment_profile.md` |
| Verificación runtime Codex CLI/Desktop | Ejecutar `codex_runtime_smoke.py --surface cli|desktop` con modelo y esfuerzo explícitos; registrar estados independientes y exigir solo las superficies declaradas | `surfaces.<surface>=unavailable`; la compatibilidad estructural permanece informativa y el fallback shell queda documentado | `CODEX_RUNTIME_UNAVAILABLE` · `15_environment_profile.md` |
| Menú `/` sin duplicados | Un entorno lee una sola superficie de comandos (o filtra las demás por permiso, p. ej. `permission.skill` en OpenCode/FW-043) | El harness lee ≥2 superficies a la vez sin mecanismo de ignore conocido (Antigravity: `.claude/commands/` + `.agents/skills/`, FW-135): el builder usa las entradas con nombre y descarta las `source-command-*`, confiando en la auto-carga por `description` (FW-137) para el ruteo real | `MENU_DUPLICADO` · adapter |

## Regla de conformidad

- Toda degradación usada en una sesión se **declara** (en la respuesta del workflow
  y/o en `_project/15_environment_profile.md`). Sin declaración, la capacidad se
  asume presente y verificada — y eso es deshonesto si no lo está.
- `framework_doctor.validate_adapters` exige que cada adaptador declare un marcador
  `**Soporte:**` (verified|experimental) **y** `**Degradación:**` (puntero a esta
  matriz), de modo que la conformidad por entorno sea explícita y verificable.
- Verificar un entorno nuevo (pasar de `experimental` a `verified`) requiere: correr
  `framework_doctor.py` en ese entorno, ejercer el ciclo de sesión real y registrar
  evidencia. La nota de portabilidad no sube sin esa evidencia.
