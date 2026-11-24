# Política de Evolución del Framework — v2 "Evolución Sustractiva" (era v4.x)

> Memoria interna del framework (junto a CHANGELOG y framework_arch_decisions).
> Gobierna QUÉ cambios entran al framework, POR QUÉ y A QUÉ RITMO. La evolución
> es reactiva (empujada por el uso real, no por investigación proactiva) y,
> desde 4.12.0, también **sustractiva**: el tamaño de la superficie es una
> métrica a la baja, no al alza.

## Historia de la política

- **v1 (3.5.0 "Frozen Core")** declaró el feature freeze y que "la 4.0 solo
  tendría sentido ante un cambio de paradigma". El salto v4 ocurrió igualmente
  — y fue **legítimo**: lo empujó evidencia de campo (FW-071, superficie de dos
  planos), no una novedad externa. Pero dejó dos deudas: la política quedó
  contradicha por la práctica, y la cadencia de releases pasó a ser por-fix
  (4.9.1→4.11.2 en días), multiplicada por el costo de propagar cada release a
  todos los proyectos adoptantes.
- **v2 (4.12.0)** reconcilia: conserva el freeze de features proactivas y los
  criterios de retroporte, y añade cadencia por lotes, frontera
  núcleo-eterno/capa-volátil, vigilancia trimestral, delegación al harness y
  regla de resta. Origen: auditoría externa contra el estado del arte 2026
 .

## Estado del arte (fundamento del freeze)

El framework está alineado con la literatura SDD + agentes de 2026. Papers verificados:

| Paper | arXiv | Mapea a |
|---|---|---|
| Piskala, *Spec-Driven Development: From Code to Contract* (2026) | 2602.00180 | Niveles spec-first/anchored/as-source ↔ PRD + contratos + ACs + tiers |
| Marri, *Constitutional Spec-Driven Development* (2026) | 2602.02584 | Constitución versionada MUST/SHOULD/MAY ↔ GUARDRAILS_MASTER.md |
| Macedo et al., *From Prompt to Process* (2026) | 2606.04967 | Taxonomía 6D; trade-off profundidad↔portabilidad ↔ el framework eligió ambas |
| Parmar, *Separating Intelligence from Execution* (2026) | 2605.00827 | Motor MCP decisión/ejecución ↔ state_manager + workflows |

Hallazgo de Macedo et al.: ningún framework cubre fuerte las 6 dimensiones — hay
trade-off entre profundidad de proceso y portabilidad. Este framework eligió las
dos (profundo + portable + zero-dependency): está en la frontera, no por detrás.
La auditoría externa de 2026-07-19 reconfirmó la alineación (SDD, harness
engineering, context engineering por filesystem) y localizó el riesgo dominante
en el churn interno, no en quedarse atrás — de ahí la v2 de esta política.

## Feature freeze ≠ abandono

- **Congelado:** features proactivas. El core no se añade ni rediseña por novedad
  tecnológica ni por un paper. Un salto de versión mayor requiere evidencia de
  campo acumulada (como el v4) o un cambio de paradigma verificado en la
  vigilancia trimestral — nunca una noticia.
- **NUNCA congelado:** el mantenimiento reactivo. Un fallo objetivo siempre se
  corrige. "Está congelado" no es excusa para no arreglar un bug.

## Frontera: núcleo eterno vs capa volátil

Todo componente del framework pertenece a una de dos capas. La frontera decide
cuánta ceremonia paga un cambio y qué puede erosionar una novedad externa.

- **Núcleo eterno** — principios de ingeniería que no cambian con los modelos:
  specs como contrato, ACs verificables, invariantes de negocio, tiers por
  blast radius + reversibilidad, anti-regresión (PRESERVAR →
  ADITIVO/COMPATIBLE/DESTRUCTIVO), aprobación humana en cambios destructivos,
  estado del proyecto en archivos como fuente única de verdad.
  **Cambiarlo exige:** tier_2 + ADR + (fallo objetivo o evidencia en ≥2
  proyectos). Ninguna novedad de modelo/harness justifica por sí sola tocarlo.
- **Capa volátil** — mecánica acoplada al ecosistema, cambia sin ceremonia
  extra (tier_0/1 según alcance): adaptadores de harness (Claude Code, Codex,
  OpenCode, Antigravity), scripts, integración MCP, formato de prompts,
  wrappers de comandos.

Referencia constitucional: `GUARDRAILS_MASTER.md → Cómo usar este documento`.

## Criterios de retroporte (qué entra al core)

- **Fallo objetivo** (bug, pérdida de estado, regresión, hueco de seguridad,
  doctor/gate roto) → retroporte **inmediato**; basta **1 proyecto** (así se
  arreglaron FW-027…033 y FW-032/033).
- **Fricción subjetiva / mejora de DX / preferencia de flujo** → requiere que la
  misma fricción aparezca en **≥2 proyectos distintos** antes de tocar el core.
- **Paper / tecnología nueva** → backlog, nunca directo al core.
- **Dependencia inestable** (MCP/agente/API experimental) → adapter opcional.

> **Nota de criterio (2026-08-21, lote FW-127/128/129)**: un bug recurrente
> solo se promueve a regla mecánica del doctor si (a) recurrió en **≥2
> ocasiones documentadas** y (b) es detectable con **bajo falso positivo**. Si
> no cumple ambas, va a `_project/22_lessons.md` — que `/arrancar` ya lee sin
> bloquear — en vez de convertirse en un gate nuevo. Registrado para no
> reabrir FW-111 cada vez que alguien proponga un doctor nuevo.

## Cadencia por lotes (qué sale y cuándo)

El retroporte decide **qué** entra; la cadencia decide **cuándo** sale como
release. Son ejes independientes: un fix puede estar mergeado en el template y
esperar al lote para versionarse y propagarse.

- **Release inmediata** — solo fallo objetivo **crítico**: pérdida de estado o
  datos, gate roto que bloquea trabajo real, hueco de seguridad. Sale solo,
  con su patch, el mismo día.
- **Release de cosecha** — todo lo demás (fricción, DX, mejoras, fixes no
  bloqueantes) se acumula y sale en un solo lote al ejecutar
  `/retrospectiva-framework` o una vez al mes, lo que llegue primero.
- **Propagación por lotes** — los proyectos adoptantes se actualizan una vez
  por release de cosecha (vía `/actualizar-framework`), no fix a fix. Una
  release inmediata sí se propaga sola si el fallo afecta a los proyectos.

## Vigilancia trimestral de paradigma

Única ventana en la que una novedad externa puede proponer cambios al core.
Se revisa **una vez por trimestre** (próxima revisión: **2027-01**) si alguna de estas 3 señales se materializó
en los harnesses que el framework soporta:

1. **Memoria/estado de proyecto nativo del harness** — el harness persiste y
   reconcilia estado de proyecto entre sesiones por sí mismo.
2. **SDD nativo integrado** — el harness trae pipeline spec→plan→tareas→código
   propio (tipo Spec Kit) integrado de fábrica.
3. **Loops de planning/verificación nativos** — el harness planifica, verifica
   contra criterios y re-intenta sin orquestación externa.

Si una señal se cumple → aplicar el principio de delegación (abajo). Si no →
la novedad va al backlog sin más discusión. Fuera de esta ventana, ninguna
noticia, modelo o paper abre debate de core.

## Principio de delegación al harness

Cuando el harness hace nativa una mecánica que el framework implementa a mano,
el framework **borra su versión y delega** — no mantiene la propia "por si
acaso". Precedente: compactación de contexto (solo nativa desde 3.5.x; el
fallback portable se eliminó). Regla dura: **nunca dos implementaciones de la
misma mecánica**. La delegación es un cambio de capa volátil (tier_1 + ADR si
toca un workflow core), y mueve el framework hacia su estado final: pura capa
de proceso y criterio sobre mecánica ajena.

## Regla de resta

Una cosecha que añade N cambios debe evaluar qué resta. El pase de resta vive
en `/retrospectiva-framework` (paso PASE DE RESTA): inventaría workflows y
skills sin uso registrado desde la cosecha anterior y fuerza una decisión
explícita por candidato — `mantener` (con razón), `fusionar` o `borrar`. La
resta la decide el humano con datos delante; nunca un automatismo. Un
framework future-proof es uno que puede encogerse.

## Tier del cambio del framework

Se usa el mismo `tier_0/1/2` del framework (blast radius + reversibilidad):
- **Tier 0** — script/prompt/ajuste menor reversible: directo.
- **Tier 1** — workflow o skill core: requiere ADR del framework.
- **Tier 2** — modelo de estado o artefacto nuevo: validación con caso real + ADR.

## Flujo de evolución

```
Investigación académica / novedad externa → backlog (revisión trimestral)
Fricción real en proyecto → _feedback/field_report.md → /retrospectiva-framework
Fallo objetivo crítico (1 proyecto) → release inmediata + propagación
Fallo objetivo no crítico (1 proyecto) → mergea al template, sale en cosecha
Fricción subjetiva (≥2 proyectos) → cambio menor, sale en cosecha
Señal de paradigma verificada → delegación al harness (borrar lo propio)
Cosecha → release por lotes + pase de resta + propagación única
```

## Backlog (ideas aplazadas, no en el core)

| Idea | Origen | Valor | Coste | Riesgo sobreingeniería | Decisión |
|---|---|---|---|---|---|
| framework_analyzer.py sobre state_log | DeepSeek | Alto | Medio | Medio | esperar datos de ≥3 proyectos |
| Métricas de uso con umbrales | ChatGPT | Medio | Medio | Alto | solo si la cosecha lo pide |
| Traceability matrix req→test→code | ChatGPT/R2Code | Medio | Bajo | Bajo | si un proyecto lo necesita |
| **Capa de personalización del builder** (visión "framework como traductor"): skill core de comunicación que todos los workflows consultan — traducción producto↔técnico guiada por `20_builder_profile.md`, significado-para-el-producto primero, preguntas en lenguaje de usuario final, una sola voz, concisión, invocación por intención, referentes primero, autonomía creciente con veto del builder | Constructor (FW-036 en Research App, 2026-06-11) | Alto | Medio | Medio | fricción subjetiva → juntar evidencia en sesiones reales (M1+) y ≥2 proyectos; el detalle completo vive en el FW-036 del proyecto origen |
| **Linter EARS + detección de adjetivos vagos** en ACs | `sdd-template` (contraste 2026-08-21) | Bajo | Medio | Medio | **retirado a conciencia en v2** (`test_evolution_policy_v2.py:6,85`); cero entradas de campo sobre ACs ambiguos en 63 field reports → entra solo si algún proyecto registra fricción real de esta familia en su field report |
| **Drift de git post-cierre** (código diverge de lo cerrado sin re-abrir el módulo) | `sdd-template` (contraste 2026-08-21) | Medio | Medio | Bajo | hueco real pero cero evidencia de campo; los 2 hits de "drift" en el field report son de docs y wrappers, no de código → entra solo si algún proyecto registra fricción real de esta familia en su field report |
| **`REJECT_AS_PREPARATORY`** (rechazar cambios marcados como preparatorios sin AC propio) | Atomic Driven Development (contraste 2026-08-21) | Medio | Bajo | Medio | conceptualmente sano pero cero entradas de campo que lo respalden; bajo la política de retroporte (fricción subjetiva → ≥2 proyectos) no pasa → entra solo si algún proyecto registra fricción real de esta familia en su field report |

## Fixes diferidos (fallo objetivo, solución de diseño pendiente)

A diferencia del backlog de ideas, estos son fallos reales ya diagnosticados cuya
corrección correcta excede un fix mecánico y requiere diseño + ADR antes de tocar
el core. No se cierran "por simplicidad" debilitando una garantía.

- **FW-037 — RESUELTO (2026-06-15, ADR-029).** El transition gate
  (`validate_transition.py`) colapsaba una secuencia legal multi-paso del DIFF
  (`in_progress → ready_for_validation → completed` commiteada al final) en un salto
  atómico ilegal → falso `TRANSITION GATE: FAIL` que entrenaba `--no-verify`. Origen:
  Research App (2026-06-11). Se **descartó** la alcanzabilidad pura en el grafo
  (probada y revertida en la Oleada 1: reabría el hueco del salto directo a `completed`
  por hand-edit). **Solución adoptada:** validar contra `_project/state_log.jsonl`
  (regla "last-log-status" — el motor valida `is_legal` en cada mutación antes de
  registrarla, así que el último status del log es legal-alcanzable; un hand-edit que
  salta no deja rastro), con fallback a adyacencia estricta cuando no hay log. Caso de
  estudio del proceso: una corrección "objetiva" puede esconder una decisión de diseño
  de seguridad — se diferió, se diseñó con ADR y se cerró sin debilitar la garantía.
