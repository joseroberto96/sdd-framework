# Guardrails v4.2 — Calidad y Contexto Maestro

> **System Prompt constitucional** para proyectos de software asistidos por IA.
> Versión: 4.2 | Capa constitucional sobre el framework operativo
> Compatible con los workflows operacionales de `.agent/workflows/`

---

## Cómo usar este documento

Este documento es la capa de invariantes del proceso de desarrollo. Define *qué garantías deben cumplirse* en todo momento y qué característica gana ante conflictos de calidad. Los workflows operacionales (`.agent/workflows/*.md`) definen *cómo ejecutarlas paso a paso*. Cuando hay conflicto, este documento tiene precedencia.

**Frontera de evolución (núcleo eterno / capa volátil):** los principios de este documento — specs como contrato, ACs verificables, invariantes, tiers por blast radius, anti-regresión, aprobación humana en destructivos, estado en archivos — forman el **núcleo eterno** del framework: no cambian por novedad de modelos ni de harness. La mecánica acoplada al ecosistema (adaptadores, scripts, MCP, wrappers) es **capa volátil** y evoluciona sin ceremonia extra. Reglas de cambio, cadencia y vigilancia: `.agent/docs/framework_evolution_policy.md`.

## Modelo de calidad en tres planos

1. **Gates** — filtro binario de entrada: legal/licencias, cumplimiento de datos y alcance activo. Se evalúan antes de las cualidades; si fallan, el trabajo se detiene.
2. **Cualidades del software** — correctitud verificable, preservación de contratos, seguridad y datos, confiabilidad/observabilidad, mantenibilidad, usabilidad, eficiencia y release responsable.
3. **Meta-reglas** — precedencia define qué cualidad gana cuando chocan; graduación define cuánto rigor paga cada cambio según blast radius + reversibilidad.

**Carga obligatoria al inicio de sesión:** `_project/core.md` como fuente primaria; fallback legacy solo si falta `core.md`: `_project/00_session_brief.json` → `_project/03_project_state.json` → `_project/04_active_scope.md` → `_project/06_bug_fixes.md`.

**Protocolo de 3 Fases** antes de implementar cualquier módulo:
```
THINK (diseñar) → APPROVE (aprobación humana) → CODE + LOG (ejecutar con evidencia)
```

---

## Jerarquía de Precedencia de Calidad

> **Propósito:** define qué característica gana cuando dos entran en conflicto durante la generación o modificación de código. Es la **primera regla** que el agente consulta ante cualquier trade-off. No describe el producto: describe cómo decidir bajo conflicto.
>
> **Eje que cubre:** *precedencia* (qué gana). No cubre *graduación* (cuánto rigor aplicar según la vida esperada del código); eso se declara en bloques complementarios de NFRs, alcance o estrategia técnica.

### Capa 0 — Gates (binarios, NO se rankean)

Se evalúan **antes** de generar. Si un gate falla, el trabajo se detiene. Un gate no se compensa con calidad en otra dimensión: no existe "muy seguro pero con licencia incompatible".

- **Legal / licencias** — MUST: verificar compatibilidad de licencia de toda dependencia nueva contra la licencia del proyecto. NEVER: introducir dependencias con licencia incompatible o desconocida.
- **Cumplimiento de datos** — MUST: tratar PII según la política declarada del proyecto. NEVER: generar código que recolecte, registre o exponga datos personales sin base declarada.
- **Viabilidad de alcance** — MUST: si el requisito excede `04_active_scope.md`, detenerse y marcar. NEVER: implementar fuera del alcance activo "de paso".

### Orden de precedencia

Cuando dos requisitos chocan, **gana el de menor número**. Cada rango trae su regla dura.

**1. Correctitud verificable**
- MUST: implementar solo lo especificado, con evidencia de prueba antes de `ready_for_validation`.
- NEVER: cerrar por sensación. NEVER: asumir un requisito ambiguo; detener y preguntar.

**2. Preservación de contratos (anti-regresión)**
- MUST: inventariar qué se preserva antes de tocar código existente.
- NEVER: romper una interfaz/contrato público sin clasificar el cambio `DESTRUCTIVO` y obtener aprobación explícita. Ningún beneficio de rango inferior autoriza esto.

**3. Seguridad y datos**
- MUST: validar inputs y autorizar en cada borde de confianza.
- NEVER: interpolar input en SQL/shell. NEVER: hardcodear secretos. NEVER: exponer PII sin base declarada.

**4. Confiabilidad y observabilidad**
- MUST: manejar errores explícitos, timeouts, fallback y comportamiento degradado; dejar logs estructurados.
- NEVER: asumir que una dependencia externa responde. NEVER: tragar excepciones en silencio.

**5. Legibilidad / mantenibilidad**
- MUST: nombres descriptivos, una responsabilidad por función, archivos pequeños, deuda documentada.
- NEVER: abstracción especulativa (YAGNI). NEVER: código ingenioso donde sirve código obvio.

**6. Usabilidad**
- MUST: en UI, los 5 estados (ideal, vacío, cargando, error, parcial); NFRs declarados.
- NEVER: entregar una ruta de error sin estado visible para el usuario.

**7. Eficiencia**
- MUST: eficiente dentro de los NFR declarados.
- NEVER: optimizar antes de medir.

### Reglas que cruzan la jerarquía (overrides)

- **Legible > Eficiente por defecto.** Solo se invierte con una **medición** que demuestre que el rendimiento incumple un NFR declarado. Sin número, no hay micro-optimización.
- **DESTRUCTIVO siempre escala.** Sin importar cuánto mejore un rango inferior, un cambio destructivo requiere aprobación explícita.
- **Toda degradación se documenta.** Si para cumplir un rango superior se sacrifica uno inferior, se registra en journal/decisiones. **Sin registro, el sacrificio no está autorizado.**
- **Ambigüedad = detener.** Si no es claro qué rango aplica, el agente detiene y pregunta. Nunca resuelve el conflicto por su cuenta.

### Procedimiento de resolución

Lo que el agente ejecuta ante cualquier trade-off:

1. **¿Alguna opción viola un Gate?** → descártala. Si todas lo violan → detener y escalar.
2. **Identifica el rango más alto en el que las opciones difieren** → elige la que mejor lo cumple.
3. **¿Cumplir un rango inferior obliga a degradar uno superior?** → prevalece el superior; documenta el costo.
4. **¿No puedes satisfacer un rango superior sin más información?** → detente y pregunta. No asumas.

---

## Graduación de Rigor por Tier

> **Propósito:** define **cuánto** del framework paga un cambio. Evita que la ceremonia (inventario, ADR, 5 estados, `ready_for_validation`) mate la velocidad en trabajo que no la necesita, sin abrir la puerta a saltarse rigor donde sí importa.
>
> **Eje que cubre:** *graduación* (cuánto rigor). Es complementario a la *precedencia* (qué gana en un conflicto). Los **Gates** de la Capa 0 están por encima de ambos ejes y aplican siempre.

### Principio rector

El rigor se clasifica por **blast radius + reversibilidad**, NUNCA por número de líneas. Un cambio de una línea a un check de autorización es alto riesgo; un script de 300 líneas que se descarta tras leerlo es efímero. El instinto por defecto del agente —"cambio pequeño = poca ceremonia"— es exactamente el error a evitar.

### Los tres tiers

#### Tier 0 — Efímero

Spikes, prototipos, exploración para responder una pregunta, scripts de un solo uso que se descartan tras leerlos.

- **Exige:** Gates (Capa 0) + Correctitud (hace lo que dice). Nada más.
- **Omite:** ADR, 5 estados, journal, clasificación de cambio, `ready_for_validation`, actualización de state files.
- MUST: vivir en una zona designada e ignorada por VCS (por defecto `/_scratch/`) y marcarse explícitamente como efímero.
- NEVER: promover código Tier 0 a producción o al árbol del proyecto sin re-clasificarlo y pagar el tier completo.

#### Tier 1 — Estándar

El default. Features y cambios que viven en el proyecto, se mantienen y evolucionan.

- **Exige:** el framework completo — inventario de preservación, clasificación `ADITIVO`/`COMPATIBLE`/`DESTRUCTIVO`, contratos, journal, observabilidad, 5 estados si hay UI, paso `ready_for_validation` → `completed`.
- **En producción (FW-225):** con `lifecycle.stage = production` (fijado solo por el builder), un tier_0/tier_1 se trabaja como **cambio** (`.agent/workflows/mantenimiento.md`): PRESERVAR acotado, 1-3 criterios con evidencia, test o verificación real con `rc=0`, y el gate mecánico de `close_change` (evidencia, bugs críticos, STUB GATE, detector de superficies frágiles). Omite fases, scope y recibos de módulo. Todo tier_2 o destructivo **escala a módulo** con el rigor completo de este tier.

#### Tier 2 — Crítico

Cambios donde un error tiene costo alto o difícil de revertir.

- **Exige:** todo lo de Tier 1, **más**: ADR obligatorio, evaluación de impacto, plan de rollback probado, aprobación explícita, y revisión de segundo paso (NUNCA auto-cerrable por el mismo agente que lo generó).
- **Datos reales/PII (FW-083, mecánico):** si el módulo persiste o escribe datos reales/PII, declararlo con `handles_real_data: true` en la misma llamada a `add_work_tier` — dispara el DATA SAFETY GATE del cierre (`/cierre-modulo` paso 3.6): sin `record_data_safety_check` con `backup_verified: true`, `/cierre-modulo` bloquea antes de la primera escritura destructiva sobre esos datos.
- **Aprobación no redundante:** un driver puede consumir un `APPROVE_RECEIPT`
  vigente ligado al mismo scope, ACs, tier, riesgos y ADRs; no vuelve a preguntar
  por el mismo plan. Si cualquiera cambia, el recibo queda inválido. Una
  aprobación general nunca cubre destructivos, dependencias nuevas ni scope nuevo.

### Clasificador

El agente lo corre ANTES de empezar, en este orden:

**1. ¿Es Tier 2 (Crítico)?** Sí, si se cumple **CUALQUIERA**:
- toca autenticación, autorización o secretos
- modifica esquema de datos o requiere migración
- cambia un contrato/interfaz pública consumida fuera del módulo
- maneja dinero o transacciones
- está clasificado `DESTRUCTIVO`
- toca PII más allá de leerla (declarar `handles_real_data: true` en `add_work_tier` — dispara el DATA SAFETY GATE, FW-083)

**2. Si no, ¿es Tier 0 (Efímero)?** Sí, solo si se cumple **TODO**:
- no se persiste como código del proyecto (vive en zona scratch ignorada por VCS)
- no toca contratos compartidos
- no toca datos reales
- es reversible borrándolo
- declarado de un solo uso

**3. En cualquier otro caso → Tier 1 (Estándar).**

### Reglas duras

- **Duda = subir de tier.** Si no es claro qué tier aplica, se aplica el superior. El error barato es exceso de rigor; el caro es tratar como efímero algo que no lo era.
- **Los Gates no se gradúan.** Legal/licencias, cumplimiento de datos y alcance aplican en los tres tiers. Un script efímero tampoco puede usar una dependencia con licencia incompatible ni filtrar PII.
- **La precedencia no se gradúa.** El orden de qué-gana-en-conflicto es el mismo en todos los tiers. El tier cambia el *proceso*, no la *jerarquía de calidad*.
- **Promoción re-clasifica.** Mover código de un tier inferior a uno superior obliga a pagar la factura completa del nuevo tier antes de integrarlo. La exención no se hereda.
- **El tier se registra.** Cada unidad de trabajo que entra al proyecto declara su tier en `03_project_state.json → work_tiers`, con razón y fecha. Tier 0 solo se registra si deja artefactos fuera de scratch o se promueve.

### Graduación por capacidad del ejecutor (segundo eje)

La graduación por tier mide el *qué* (blast radius + reversibilidad). Este eje mide el *quién*: la capacidad del modelo que ejecuta. El mismo cambio corre más riesgo de juicio hecho por un modelo débil que por uno fuerte, así que el rigor también escala con el ejecutor. Es complementario, no sustituye al tier ni a la precedencia.

- Se declara en `03_project_state.json → execution_profile.capability_tier ∈ {strong, standard, limited}`. Es **agnóstico del proveedor**: el framework no fija el mapa modelo→tier (los modelos cambian). Orientación: `strong` = modelos tope; `standard` = intermedios; `limited` = pequeños/rápidos. El campo es opcional; si falta en un proyecto inicializado, el doctor lo advierte.
- **`limited` (enforced por `validate_closure.py`):** el módulo en cierre debe declarar `requires_second_review: true` en **todo tier** (no solo tier_2) — verificación independiente obligatoria que compensa el juicio más débil. Además, orientativo: preferir evidencia ejecutable sobre narrativa, presupuestos de diff más chicos en `/cambio-seguro`, y evitar cierres `AUTÓNOMO`.
- **`standard`:** comportamiento por defecto del framework.
- **`strong`:** puede asumir más autonomía donde los gates duros ya protegen.
- **Principio rector:** todo aquello en lo que se confíe para un modelo débil debe ser un **script, no una frase**. La parte decidible la enforce el script; el resto es orientación que un modelo fuerte aprovecha y uno débil compensa con verificación independiente. Esto iguala el *piso* y la *tasa de captura de defectos* entre modelos, no la capacidad bruta.

---

## Protocolo de 3 Fases

### Fase 1 — THINK: Diseño y Evaluación de Riesgos

Antes de escribir una línea de código:

0. **Clasificador de tier** — Declarar `Tier 0`, `Tier 1` o `Tier 2` según blast radius + reversibilidad. Si el cambio entra al proyecto, registrarlo en `03_project_state.json → work_tiers` antes de implementar.

1. **Verification Echo** — Declarar en voz alta: qué se implementa, por qué, qué NO entra, y cuáles son los criterios de éxito verificables.

2. **Risk Surface Map** — Identificar qué dimensiones toca esta tarea:
   - ¿Toca modelo de datos o esquema de BD? → Gate de migraciones obligatorio.
   - ¿Toca auth, sesiones, roles o inputs externos? → Gate de seguridad obligatorio.
   - ¿Introduce async, threads, colas o estado compartido? → Gate de side effects.
   - ¿Añade UI? → Gate de 5 estados.
   - ¿Toca API externa, operación masiva o ML? → Gate de FinOps/timeout.
   - ¿Afecta >1 módulo o la interfaz pública? → ADR pre-comprometido.

3. **ADR Pre-Commitment** — Si la decisión es no obvia, escribir el ADR *antes* de codificar. Formato mínimo:
   ```
   Contexto: [qué problema resuelve]
   Decisión: [qué se elige]
   Alternativas descartadas: [y por qué]
   Consecuencias: [trade-offs aceptados]
   ```

4. **Contrato primero** — Declarar la interfaz pública antes de implementar la lógica: entidades persistidas en `_project/02_technical_plan/data_model.md`, endpoints HTTP/REST en `_project/02_technical_plan/api_contract.yaml`, y contratos no-HTTP en `_project/03_contracts.md`.

5. **Output de THINK:** Un párrafo de "Diseño acordado" + checklist de gates activos para este módulo.

### Fase 2 — APPROVE: Aprobación Humana

El humano revisa:
- El alcance está acotado: ni más ni menos que lo acordado.
- Los riesgos identificados son aceptables o tienen mitigaciones declaradas.
- No hay dependencias nuevas sin aprobación.
- El ADR (si aplica) está completo y aprobado.

**Sin aprobación de APPROVE → no hay CODE.**
Aprobación general ≠ aprobación de cambios destructivos (esos requieren aprobación separada con impacto descrito).

### Fase 3 — CODE + LOG: Ejecución e Instrumentación

Durante la implementación:
1. Ejecutar gate de `cambio-seguro` antes de tocar código existente (inventario PRESERVAR).
2. Implementar la solución mínima que satisface los ACs — nada más.
3. Añadir logs estructurados en operaciones críticas (ver Dimensión 5).
4. Crear archivo de migración si hubo cambio de esquema.
5. Al terminar: test o check manual documentado en `05_test_history.md`, journal actualizado.
6. En cierre: `/cierre-modulo` con todos los gates del módulo.

---

## Specs vivas como contrato (evolución v3.x — ADR-011 a ADR-016)

Esta sección no altera la Jerarquía de Precedencia ni el Protocolo de 3 Fases. Añade una capa contractual que el Protocolo verifica.

### Principio

Cuando un módulo declara entidades persistidas o endpoints HTTP, esas declaraciones viven en specs formales **versionables, parseables y verificables**, no en prosa narrativa. La spec es contrato: el código se valida contra ella y el cierre depende formalmente de ella.

### Artefactos canónicos

- `_project/02_technical_plan/data_model.md` — modelo del dominio (Mermaid ER + atributos + restricciones).
- `_project/02_technical_plan/api_contract.yaml` — contrato HTTP/REST (OpenAPI 3.1).
- `_project/03_contracts.md` — índice narrativo humano para interfaces **no-HTTP** (CLI, bibliotecas, eventos). Para HTTP conserva solo línea-puntero al YAML.
- `_project/03_project_state.json → module_specs[módulo]` — clasificación por módulo: `data_model` y `api_contract` ∈ {`required`, `n/a`, `done`} con `justification` obligatoria cuando es `n/a`.

### Obligatoriedad condicional (ADR-013)

- `data_model` **requerido** cuando el módulo crea, persiste, lee o transforma entidades del dominio.
- `api_contract` **requerido** cuando el módulo expone o modifica endpoints HTTP/REST.
- `n/a` con justificación para UI pura, CLI interna, docs, config y scripts.

### Quién clasifica, quién valida, quién no toca

- **Clasifican** `module_specs`: `/scope-siguiente` (clasificación inicial), `/enriquecer-ticket` (refinado técnico), `/migrar-proyecto` (retroactivo), o `manual` por excepción.
- **Producen** las specs formales: `/diseñar`/`/tech-design-mvp` (proponen) y `/enriquecer-ticket` (refinado con aprobación del usuario).
- **Validan**: `/cierre-modulo` paso 3.7 SPEC GATE (bloquea con `module_specs_missing`, `data_model_missing`, `api_contract_missing`) y `/validate-arch` (coherencia código↔specs).
- **NO clasifica**: `/cierre-modulo`. Solo transiciona `required → done` cuando la verificación pasa (ADR-013).

### Reglas de cambio

- Añadir entidad o endpoint nuevo a la spec → `ADITIVO`.
- Añadir campo opcional → `ADITIVO`.
- Quitar/renombrar/cambiar shape de entidad o endpoint declarado → `DESTRUCTIVO`. Requiere aprobación explícita vía `/cambio-seguro`.

### Verificación ejecutable como cierre del feedback loop (ADR-015)

`/cierre-modulo` paso 3.8 EJECUTABLE GATE invoca `verificar-ejecutable` que:
- Intenta verificación automatizada con MCPs declarados en `active_mcps`.
- Cae en fallback manual reproducible (`_scratch/verify_<módulo>.md` confirmado por usuario) si no hay MCP.
- Resultados `ok_automatizada`/`ok_manual`/`no_aplica` permiten avanzar; `fallida`/`diferida` bloquean el cierre.
- La evidencia es **suplementaria** — no rescata ACs sin evidencia primaria del EVIDENCE GATE 1.c.
- Desde 4.21.0 (FW-144) el resultado **deja rastro**: `mutate_state
  record_execution_receipt` sella `kind`, `command`, `exit_code`, `evidence` y el
  `diff_hash` del momento en `workflow_receipts.executions`. `complete_module`
  exige ese receipt y `state_log.jsonl` lo respalda. Narrar el smoke ya no
  equivale a ejecutarlo, y una verificación anterior al último cambio de código
  queda `stale`.

### Fail-closed por defecto (invariante constitucional, FW-146)

Toda dependencia externa —modelo, renderer, red, disco, servicio de terceros—
que falle **debe propagar un error visible**. Devolver una estructura vacía, un
valor por defecto o un artefacto degradado y continuar como si hubiera
funcionado está **prohibido**: borra la única señal de que el producto no sirve
y convierte un fallo de integración en un cierre verde.

- Degradar de forma silenciosa exige **ADR aprobado por el builder** que nombre
  la dependencia, el modo degradado y cómo se le comunica al usuario final.
- Un dato de ejemplo, un identificador de relleno o una constante cableada en la
  ruta de producción es **implementación ausente**, no implementación simple.
- Ambos los adjudica mecánicamente `validate_no_stubs.py` (STUB GATE, FW-145)
  dentro de `complete_module`, con salida explícita `stub-ok: <razón>` por línea
  para el caso legítimo.

> Origen: `Example Service` (2026-08-26) cerró cinco fases con LLM y renderer PDF
> degradando en silencio. Nada en la constitución lo prohibía, así que el
> ejecutor eligió lo que maximizaba la apariencia de progreso.

### Supply-chain en MCPs

Plantilla `.agent/templates/mcp.json.base` declara ejemplos desactivados con marcador `<VERSION>` obligatorio. Activar un MCP exige versión exacta (semver, commit hash o tag verificado) o `source` con fuente aprobada. Versión implícita (`@latest`, paquete sin tag) está prohibida y la valida `framework_doctor.py`.

### Lecciones aprendidas separadas de la constitución (ADR-016)

`/aprender-de-error` escribe en `_project/22_lessons.md` on-demand con aprobación explícita. `CLAUDE.md` NO se modifica automáticamente — la promoción de una lección a regla del framework es decisión manual del usuario.

---

## Dimensión 1 — Memoria, Contexto y Toma de Decisiones

### Single Source of Truth (SSOT)

**✅ DO**
- Leer `03_project_state.json` antes de asumir qué existe en el proyecto.
- Si `00_session_brief.json` tiene más de 24 horas, re-validar con `03_project_state.json` antes de continuar.
- Al detectar contradicción entre contexto cargado y código existente: declarar el conflicto explícitamente antes de resolverlo.
- Registrar en `13_session_journal.md` cualquier cambio que no se haya persistido aún en los artefactos.

**❌ DON'T**
- Reconstruir contexto desde memoria conversacional. Si falta información, preguntar.
- Tratar un session_brief desactualizado como válido sin re-validación.
- Marcar tareas como completadas solo en memoria; siempre persistir en artefactos.
- Asumir interfaces, módulos o dependencias no declarados en `project_state.json`.

### ADRs — Decisión Antes de Ejecución

**✅ DO**
- Escribir el ADR *antes* de codificar cuando la decisión afecta >1 módulo, la interfaz pública, el modelo de datos o la estrategia de errores.
- Usar `03_arch_decisions.md` como registro permanente de ADRs.
- Incluir siempre las alternativas descartadas: sin ellas, el ADR no tiene valor futuro.

**❌ DON'T**
- Documentar decisiones arquitectónicas retroactivamente como rutina.
- Tomar decisiones de diseño no obvias sin declarar las alternativas descartadas.
- Asumir que "el código lo explica todo" — el código explica el QUÉ, el ADR explica el POR QUÉ.

### Chunking y Tamaño de Archivos

**✅ DO**
- Archivos de lógica: objetivo < 300 líneas. Si supera 500, revisar si debe dividirse antes de continuar.
- Cada módulo tiene una responsabilidad única y puede verificarse de forma aislada.
- Usar el workflow interno `lean-context` (`.agent/skills/prompt_lean.md`) para generar contexto mínimo antes de sesiones enfocadas en un módulo específico.

**❌ DON'T**
- Crear archivos "dios" que concentran lógica de múltiples dominios.
- Abrir PRD completo, knowledge map y todas las skills simultáneamente si `00_session_brief.json` es suficiente.

---

## Dimensión 2 — Arquitectura, Estado y Deuda Técnica

### Regla del Boy Scout Controlada

**✅ DO**
- Al tocar código existente, se permite corregir **1 smell claro** (nombre sin sentido, duplicado obvio menor) si el cambio es `ADITIVO` a la tarea actual. Registrar en journal.
- Para refactors que tocan >1 módulo o cambian interfaces: usar `/refactorizar-arquitectura` con gate anti-regresión completo.
- Si se detectan ≥3 smells en un módulo durante la sesión: crear tarea "Estabilización" en scope en lugar de parchear al vuelo.

**❌ DON'T**
- Refactorizar por gusto sin presión real ni relación directa con la tarea activa.
- "Aprovechar" un fix para reestructurar módulos adyacentes no relacionados.
- Proponer mejoras de diseño que no están en el scope acordado en APPROVE.

### Estado en Tiempo de Ejecución, Concurrencia y Side Effects

**✅ DO**
- Si el módulo introduce operaciones asíncronas, threads, colas o estado compartido mutable: declarar en el diseño técnico la estrategia de race conditions e idempotencia.
- Marcar explícitamente los side effects en el contrato correspondiente: `03_contracts.md` para funciones/comandos/eventos no-HTTP y `api_contract.yaml` para endpoints HTTP con efectos observables.
- Operaciones destructivas (borrado masivo, overwrite, truncate) deben ser idempotentes o requerir confirmación antes de ejecutarse.
- Operaciones que pueden invocarse múltiples veces deben especificar su comportamiento ante invocación duplicada.

**❌ DON'T**
- Introducir estado global mutable sin documentar quién lo escribe, quién lo lee y bajo qué condiciones de concurrencia.
- Asumir que operaciones async son seguras por defecto ante múltiples invocaciones simultáneas.
- Silenciar errores de operaciones con side effects — un side effect fallido sin registro es un bug invisible.

### Deuda Técnica con Aging

**✅ DO**
- Bugs `Menor` abiertos por más de 2 sprints se escalan a `Mayor` en el siguiente `/cierre-modulo`.
- Al acumular ≥3 ítems de deuda documentada en un módulo: abrir módulo "Estabilización" antes del siguiente feature.
- La deuda técnica arrastrada desde cierres previos se revisa en cada `/refactorizar-arquitectura`.

**❌ DON'T**
- Arrastrar bugs menores indefinidamente sin fecha de revisión.
- Cerrar módulos con deuda implícita — toda deuda debe quedar explícita en `04_active_scope.md`.
- Usar "lo arreglaremos después" sin documentarlo como ítem de deuda.

---

## Dimensión 3 — Datos, Seguridad y Compliance

### Evolución Segura del Esquema de Datos

**✅ DO**
- Todo cambio al modelo de entidades en `_project/02_technical_plan/data_model.md` requiere un bump de versión (`v1 → v2`) y un archivo de migración antes de su aplicación en cualquier entorno.
- Las migraciones son idempotentes por diseño (mecanismo de guarda equivalente a `IF NOT EXISTS`).
- Clasificación de cambios de esquema:
  - Campo añadido → `ADITIVO`
  - Campo renombrado, eliminado o con tipo cambiado → `DESTRUCTIVO` (requiere aprobación explícita + evaluación de impacto en datos existentes)
- Toda entidad con trazabilidad crítica (auditoría, transacciones, eventos) lleva timestamps de creación y modificación.

**❌ DON'T**
- Modificar el esquema de la base de datos en código sin un archivo de migración correspondiente.
- Renombrar campos sin migración compatible o período de deprecación declarado.
- Cambiar tipos de columnas sin evaluar el impacto en datos ya almacenados.
- Aplicar migraciones que no pueden revertirse sin documentar que son irreversibles y obtener aprobación explícita.

### Seguridad por Diseño

**✅ DO**
- Validar y sanitizar todo input externo (usuario, API, archivo, parámetro de URL) antes de usarlo en DB, filesystem, red o renderizado.
- Verificar autenticación Y autorización antes de cualquier operación de escritura o acceso a datos de otro usuario.
- Logs estructurados excluyen: tokens, passwords, PII, stack traces internos.
- Secretos exclusivamente en variables de entorno; `.env.example` documenta las claves sin valores reales.
- Ante una dependencia nueva: verificar que la versión específica no tiene CVEs conocidos antes de aprobarla.
- Fijar versión exacta de toda dependencia — nunca usar `latest`.

**❌ DON'T**
- Hardcodear credentials, API keys, IDs de producción o rutas de entorno en código fuente.
- Construir queries por concatenación de strings con input del usuario.
- Exponer stack traces o estructura interna de BD en mensajes de error visibles al usuario.
- Hacer push a repositorio sin gate de secretos (revisar archivos staged contra patrones de tokens/keys).
- Instalar dependencias sin fuente verificable, versión fija y registro en `14_skill_provenance.md`.

### PII y Retención de Datos

**✅ DO**
- Si el sistema maneja PII: declarar en `10_arch_constraints.md` la política de retención, acceso y borrado.
- Al diseñar exportaciones o reportes: declarar si contienen PII y qué controles de acceso aplican.
- Datos sensibles en backups deben tener los mismos controles de acceso que producción.

**❌ DON'T**
- Loggear datos de usuario sin verificar primero que no son PII.
- Exportar PII sin restricción de acceso o sin TTL declarado.
- Almacenar PII innecesario — si no se necesita, no se guarda.

---

## Dimensión 4 — Visión de Producto y NFRs

### Los 5 Estados de UI (obligatorio para todo módulo con interfaz)

Todo componente o vista debe diseñarse para estos 5 estados antes de implementar:

| Estado | Descripción | Requisito |
|--------|-------------|-----------|
| **Ideal** | Datos cargados, flujo feliz | Siempre presente |
| **Vacío** | Sin datos aún | Mensaje que guía al usuario — nunca pantalla en blanco sin contexto |
| **Cargando** | Operación en progreso | Indicador visible — nunca silencio al usuario |
| **Error** | Fallo de red, API o validación | Mensaje accionable: qué falló + qué puede hacer el usuario |
| **Parcial** | Datos incompletos o degradados | Advertencia visible — nunca mostrar datos desactualizados sin indicarlo |

**✅ DO**
- Diseñar los 5 estados en los ACs de cualquier módulo frontend antes de implementar.
- Estado Error: siempre ofrecer una acción (reintentar, contactar soporte, volver atrás).
- Estado Parcial: si una vista puede mostrar datos desactualizados o de una fuente degradada, indicarlo visualmente.

**❌ DON'T**
- Implementar solo el estado Ideal y asumir que los demás "se ven bien".
- Mostrar componentes vacíos sin contexto ni guía.
- Reutilizar el estado Error para validaciones del usuario — son categorías distintas.

### Performance y FinOps

**✅ DO**
- Declarar el NFR de rendimiento antes de implementar operaciones críticas. Opciones: `< 500ms`, `< 2s`, `no crítico — justificación`, `diferido conscientemente`.
- Si una operación toca un número potencialmente ilimitado de filas: usar paginación o cursor y declarar el bound en el diseño.
- Antes de introducir llamadas a APIs externas con costo, procesamiento masivo, exportaciones grandes o inferencia ML: estimar el costo computacional/infraestructura en el diseño técnico.
- Revisar N+1 explícitamente en todo módulo con vista de lista + detalle: verificar que no hay una query por ítem de la lista.

**❌ DON'T**
- Introducir queries sin límite (`SELECT *` sin `LIMIT`) en tablas de crecimiento ilimitado.
- Ejecutar operaciones costosas de forma síncrona en la ruta crítica si pueden diferirse o ejecutarse en background.
- Ignorar el costo de operaciones externas en proyectos con límites de API o facturación por uso.
- Procesar listas completas en memoria cuando el volumen puede crecer sin control.

### NFRs Explícitos — Sin Arquitectura Accidental

**✅ DO**
- Declarar explícitamente en `10_arch_constraints.md` los NFRs para este MVP:
  - **Rendimiento:** tiempo de respuesta aceptable para la operación principal.
  - **Disponibilidad:** downtime aceptable (horas / minutos / zero tolerance).
  - **Escalabilidad:** usuarios o volumen concurrente esperado.
  - **Mantenibilidad:** quién mantendrá el sistema y con qué frecuencia.
  - **Seguridad:** nivel de sensibilidad de los datos.
- Si un NFR se difiere conscientemente: documentar la razón y la fecha de revisión.

**❌ DON'T**
- Dejar NFRs sin declarar — un atributo sin declarar es una decisión tomada por accidente.
- Aspirar a NFRs hipotéticos de "futuro ideal" en lugar de los reales del MVP actual.
- Mezclar NFRs del MVP con los de una versión futura sin separación explícita.

---

## Dimensión 5 — Operaciones y Observabilidad

### Telemetría Estructurada

**✅ DO**
- Logs de operaciones críticas con formato estructurado mínimo: `{timestamp, level, module, event, context}`.
- Niveles semánticos:
  - `ERROR` — operación fallida, requiere atención.
  - `WARN` — degradación o condición anómala no fatal.
  - `INFO` — evento de negocio relevante (usuario autenticado, registro creado, exportación completada).
  - `DEBUG` — solo en desarrollo, nunca en producción por defecto.
- Toda llamada a sistema externo (API, BD, filesystem) debe loggear el evento y el resultado — no solo los errores.
- Declarar en `03_arch_decisions.md`: dónde van los logs, en qué formato y durante cuánto tiempo se retienen.

**❌ DON'T**
- Usar prints o logs no estructurados como único mecanismo de observabilidad en operaciones críticas.
- Loggear dentro de loops sin throttling o sampling — puede saturar el sistema de logs.
- Declarar "hay observabilidad" sin especificar el destino y formato real. Si la respuesta es "ningún lado", declararlo explícitamente como decisión aceptada con su riesgo.
- Loggear tokens, passwords o PII en ningún nivel.

### Estrategia de Errores — Sin Clasificación Accidental

**✅ DO**
- Clasificar todos los errores del módulo antes de implementar:
  - `FATAL` — detiene la operación, notificación visible al usuario.
  - `RECUPERABLE` — reintento o fallback + log estructurado.
  - `ESPERADO` — validación del usuario, mensaje claro sin traza interna.
  - `SILENCIOSO-OK` — edge case aceptado, requiere justificación explícita.
- Registrar la estrategia como ADR vía `mutate_state` (action `register_decision`; se proyecta en `03_arch_decisions.md`, que es de solo lectura). Todo módulo debe adherirse a ella.
- Todo manejador de error debe loggear lo suficiente para diagnosticar el problema sin traza interna al usuario.

**❌ DON'T**
- Usar bloques `catch` vacíos o demasiado amplios que silencian excepciones sin registrarlas.
- Mezclar errores de validación del usuario con errores internos del sistema.
- Retornar el mismo mensaje genérico para todos los tipos de error — dificulta el diagnóstico.
- Propagar errores internos al usuario sin sanitizar (stack traces, mensajes de BD, rutas del sistema).

### Versionado Semántico y Releases

**✅ DO**
- Versionar el software con `MAJOR.MINOR.PATCH`:
  - `MAJOR` — cambios que rompen contratos públicos o requieren migración.
  - `MINOR` — nuevas funcionalidades compatibles hacia atrás.
  - `PATCH` — correcciones de bugs sin cambio de comportamiento público.
- Actualizar la versión en `16_release_profile.md` en cada `/publicar-github`.
- Mantener un `CHANGELOG.md` con entradas por versión: qué cambió, qué se rompió, cómo migrar.

**❌ DON'T**
- Publicar sin versión declarada.
- Incluir breaking changes en un PATCH o MINOR sin bump de MAJOR.
- Omitir el `CHANGELOG` — es el contrato histórico con los usuarios del software.

### Feature Flags y Degradación Elegante

**✅ DO**
- Si una feature puede necesitar desactivarse post-release sin redeploy: declarar un flag en `10_arch_constraints.md` con su mecanismo (variable de entorno, archivo de config, tabla de BD).
- Toda integración con sistema externo debe tener un timeout declarado y un comportamiento de fallback (modo degradado o error visible — nunca silencio infinito).
- Si el sistema tiene dependencias críticas externas: declarar en `10_arch_constraints.md` qué funcionalidad opera en modo degradado cuando esa dependencia no está disponible.

**❌ DON'T**
- Hacer features no desactivables si el riesgo de fallo post-release es alto.
- Asumir disponibilidad total de integraciones externas — sin timeout ni fallback declarado.
- Propagar la indisponibilidad de un servicio secundario como fallo total del sistema.

### Estrategia de Rollback

**✅ DO**
- En cada `/publicar-github`: documentar en `16_release_profile.md` el procedimiento de rollback en 1-2 pasos.
- Si hubo migración de BD: el rollback debe incluir el paso para revertir el esquema, o declarar explícitamente que la migración es irreversible con decisión del usuario registrada.
- Antes de un despliegue con cambios `DESTRUCTIVO`: tener el rollback documentado y probado (o marcado como no reversible).

**❌ DON'T**
- Publicar sin saber cómo revertir ante un fallo crítico.
- Asumir que "revertir el commit" es rollback suficiente cuando hay migraciones de datos aplicadas.
- Omitir el rollback de esquema en releases que incluyen cambios de BD.

### Health Checks

**✅ DO**
- Si el módulo añade un servicio o servidor: exponer un endpoint de salud o equivalente que confirme estado operativo.
- El health check debe verificar dependencias críticas: conexión a BD, acceso a servicios externos requeridos.
- Documentar el health check y su interpretación en `10_arch_constraints.md`.

**❌ DON'T**
- Asumir que "sin errores en logs = servicio disponible".
- Omitir health check en servicios que otros sistemas consultan para decidir si enrutar tráfico.

---

## Gates de Verificación Consolidados

### Gate THINK — antes de implementar
- [ ] ¿El tier de rigor fue clasificado por blast radius + reversibilidad y registrado en `03_project_state.json → work_tiers` si entra al proyecto?
- [ ] ¿El Verification Echo fue declarado (qué, por qué, qué NO, criterios de éxito)?
- [ ] ¿El Risk Surface Map identificó qué dimensiones activa esta tarea?
- [ ] ¿El ADR está pre-comprometido si la decisión afecta >1 módulo o es no obvia?
- [ ] ¿Los NFRs de este módulo están declarados en `10_arch_constraints.md`?
- [ ] ¿El modelo de entidades está en `_project/02_technical_plan/data_model.md` antes del primer módulo que persiste datos?
- [ ] ¿La estrategia de errores (FATAL/RECUPERABLE/ESPERADO/SILENCIOSO-OK) está declarada?
- [ ] ¿La estrategia de observabilidad (dónde van los logs) está declarada?
- [ ] ¿Las operaciones con side effects están identificadas y marcadas en contratos?
- [ ] ¿Los 5 estados de UI están diseñados en los ACs (si aplica)?
- [ ] ¿Hay riesgo de N+1 o query sin bound en este módulo?

### Gate APPROVE — antes de codificar
- [ ] El alcance está acotado y el humano lo revisó explícitamente.
- [ ] Los riesgos identificados en el Risk Surface Map son aceptables.
- [ ] No hay dependencias nuevas sin aprobación y verificación de CVEs.
- [ ] Los riesgos de seguridad y datos fueron declarados y aceptados.
- [ ] El ADR (si aplica) está aprobado.

### Gate CODE + LOG — durante y al cerrar
- [ ] `cambio-seguro` ejecutado antes de tocar código existente (inventario PRESERVAR).
- [ ] Tests o checks manuales documentados en `05_test_history.md`.
- [ ] Logs estructurados en operaciones críticas con los niveles correctos.
- [ ] Archivo de migración creado si hubo cambio de esquema.
- [ ] `03_arch_decisions.md` actualizado con ADRs comprometidos.
- [ ] `10_arch_constraints.md` actualizado con NFRs si cambiaron.
- [ ] `16_release_profile.md` actualizado con versión y rollback si hubo publicación.
- [ ] Checklist de `security_core.md` ejecutado si el módulo toca auth, inputs, BD o datos sensibles.
- [ ] Criterio de parada verificado: ¿hay bugs críticos o deuda que bloquea nuevos módulos?

---

## Resolución de Conflictos

Cuando hay conflicto entre velocidad de entrega y una regla de este framework, no se usa una tabla paralela de prioridades. Se aplica la jerarquía canónica definida en `Jerarquía de Precedencia de Calidad`:

1. descartar cualquier opción que viole Gates;
2. elegir por el rango más alto donde las opciones difieren;
3. documentar cualquier degradación aceptada de una cualidad inferior;
4. detenerse y preguntar si el rango aplicable no es claro.

---

## Integración con el framework operativo

Este documento no reemplaza los workflows operacionales de `.agent/workflows/`. Los complementa con invariantes de nivel superior.

| Este documento declara... | El framework operativo ejecuta con... |
|---------------------------|------------------------------|
| ADR pre-commitment | `/enriquecer-ticket` (paso 5.2), `/tech-design-mvp` |
| Gate de migraciones | `cierre-modulo` Step 4, `tech-design-mvp` Step 3 |
| 5 estados de UI | `quality_testing_core.md`, `08_module_acceptance.md` |
| Logs estructurados | Estrategia declarada en `03_arch_decisions.md` |
| Rollback en release | `/publicar-github`, `16_release_profile.md` |
| Aging de deuda | `cierre-modulo` Step 8 (Death Criteria Check) |
| N+1 y FinOps | `tech-design-mvp` Step 3 (NFRs y performance) |
| Versionado semántico | `16_release_profile.md`, `/publicar-github` |
| Feature flags | `10_arch_constraints.md` |
| Health checks | `10_arch_constraints.md`, `api_contract.yaml` para HTTP o `03_contracts.md` para no-HTTP |

---

*Guardrails v4.2 — capa constitucional compatible con los workflows de `.agent/workflows/`*
*Para evolucionar este documento: proponer cambio como ADR en `03_arch_decisions.md` del framework repo.*
