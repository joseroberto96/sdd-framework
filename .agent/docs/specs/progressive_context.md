# Contrato de contexto progresivo

Este documento define cómo el framework entrega el contexto necesario por etapa
sin convertir el resumen en una autorización de trabajo.

## Alcance Codex y enforcement

La política de tareas pequeñas, paralelización de encargos independientes y
espera sin cancelación automática es normativa para el adaptador Codex y vive
en `.agent/adapters/codex.md`, bajo `CODEX_SMALL_TASK_DELEGATION` y
`SUBAGENT_WAIT_LIFECYCLE`. El framework entrega instrucciones y contratos; el
host Codex ejecuta la coordinación. Las pruebas documentales confirman la
presencia y coherencia de esas instrucciones, pero no demuestran enforcement
real del host.

Este contrato no mide la ventana privada de contexto, no ofrece telemetría de
compactación ni promete su ausencia. Tampoco reintroduce el planificador de
tokens retirado. La política no se impone a los adaptadores Claude/OpenCode,
al executor genérico ni al núcleo común.

## Principios

- El estado canónico y las reglas completas siguen siendo la fuente de verdad.
- Una vista selecciona y pagina información; no ejecuta procesos ni decide que
  un módulo está cerrado.
- `complete` significa que terminó la paginación de una consulta.
- `required_context_complete` significa que las secciones obligatorias y las
  obligaciones activas de la vista se entregaron completas; una referencia
  ausente no se considera evidencia. Es distinto de `complete`, que solo
  indica que terminó la paginación.
- `required_obligations` enumera las obligaciones activas según
  `progressive_context_obligations.json`. Cada `ref` pendiente incluye su
  `obligation` cuando existe una evidencia o fuente requerida; si un validator
  entrega varios motivos, `validation_reasons` los conserva de forma acotada y
  `reason` mantiene la clasificación estable.
- `gate_status` está limitado a la vista (`startup`, `implementation`,
  `closure` o `recovery`). No es un receipt ni reemplaza validators,
  reviewers, hooks o aprobación humana.
- Los bloqueos globales se calculan sobre todo el estado aunque los detalles se
  filtren por módulo.
- Los cursores contienen el fingerprint de estado, aplicabilidad y fuentes
  usadas. Cambios posteriores obligan a consultar de nuevo.
- El objetivo implícito se resuelve antes de abrir una fuente documental; las
  secciones se seleccionan por ese objetivo, no después de cargar el documento
  completo.
- Las fuentes tienen tres clases: `module` para scope/ACs y selección por
  módulo; `shared` para reglas normativas; `global` para invariantes, contratos
  y journal. Solo la primera clase compara el nombre del módulo.
- La matriz de obligaciones es un contrato fail-closed. Sus IDs conocidos,
  activaciones, modalidades, source y selectores registrados se validan antes
  de leer las fuentes; una matriz duplicada, incompleta, no implementada o
  relajada es `SOURCE_MISSING`.
- UI y NFR requieren clasificación explícita dentro de la aceptación:
  `required` o `n/a` con justificación. La ausencia no se interpreta como
  `n/a`; cuando están activos, sus contenidos se contabilizan igual que los
  demás fragmentos obligatorios.
- La clasificación normativa no es evidencia del módulo. `PRESERVE` exige la
  entrada del módulo en `11_file_dependencies.md`; `UI_STATES` exige una
  subsección con `Ideal`, `Vacío`, `Cargando`, `Error` y `Parcial`; `NFR` exige
  objetivo, métrica, umbral y verificación. Un estado UI individual no
  aplicable debe declarar su propia razón. Los selectores de fuentes de módulo
  deben apuntar al módulo correcto; la selección rechaza duplicados, plantillas,
  comentarios y pendientes.
- La selección y la validación comparten `visible_markdown_lines(text)`, que
  conserva el número original de cada línea y oculta comentarios HTML, fences
  de backticks/tildes (también sin cierre) y código indentado. Los encabezados
  se resuelven solo sobre esa vista; el texto original de la sección y su
  fingerprint completo se conservan para la evidencia y la invalidación.
- **Alcance declarado de la evidencia documental.** `PRESERVE`, `UI_STATES` y
  `NFR` se validan sobre lo que es mecánicamente verificable: la sección tiene
  contenido visible y real, no plantilla de una lista cerrada de palabras
  (`completar`, `pendiente`, `por definir`, `ejemplo(s)`, `título`, `nombre`,
  `tbd`, `tba`, `fixme`, `xxx`) — un corchete ordinario (`datos[0]`, `[RFC
  9110]`, un enlace o una casilla de tarea) no lo es; si la sección no cita
  referencias etiquetadas, pasa si declara formalmente no-aplicabilidad con
  justificación no vacía (`n/a — justificación`, `no aplica: justificación`, etc.;
  un identificador o palabra compuesta como `n/a-storage` no exime); y las
  referencias se resuelven: la evidencia es lo que está citado dentro de una
  etiqueta de evidencia —`— evidencia:` o, en una sección `NFR`, el campo
  `Verificación:` (admitiendo listas numeradas, viñetas y negritas con dos puntos)—.
  Todo lo etiquetado se verifica contra el workspace —fichero existente o
  identificador **declarado**—; la prosa fuera de una etiqueta no se interpreta
  como referencia, ni para bloquear ni para pasar. Un identificador solo cuenta
  como declarado si aparece, con casilla **marcada** (`- [x] AC-1`) o como
  encabezado con espacio tras las almohadillas, en su documento canónico en la
  raíz de `_project/` (`AC` → `08_module_acceptance.md`, `BUG` →
  `06_bug_fixes.md`, `RN` → `03_invariants.md`, `ADR` → `03_arch_decisions.md`)
  — y esa búsqueda se hace directamente sobre el fichero canónico raíz y en su
  vista visible, sin recorrer subcarpetas y sin que un ejemplo dentro de un fence
  declare nada. Citar no declara, y un criterio pendiente tampoco. Un
  identificador con forma de `AC-*`/`RN-*`/`ADR-*`/`BUG-*` que aparece dentro de
  una ruta ya citada (`docs/ADR-007-cache.md`) no se extrae por separado.
  **Límite declarado, no resuelto:** el gate no puede distinguir un
  identificador recién añadido de uno inventado por quien escribe la
  evidencia — ambos son un autor escribiendo en el documento canónico. Esa
  distinción es de la revisión humana en `/cierre-modulo`, no del gate.
- **Lo que el gate NO juzga**, y lo dice en voz alta: la estructura interna ni
  la completitud de los inventarios de la sección. No comprueba si cada ítem de
  una lista cita su propia referencia, ni que estén los cinco estados UI, ni que
  cada NFR tenga sus cuatro campos, ni que una entrada no tome campos de otra.
  Eso pasa a la revisión humana de `/cierre-modulo`. La razón está medida, no
  supuesta: intentar que un parser automático juzgue si cada línea de prosa
  necesita o no evidencia generó diez rondas de falsos positivos y falsos
  negativos alternos. El contenido y el separador viajan por el mismo canal y
  ese canal tiene infinitas formas. El gate automático garantiza hechos duros
  (presencia, no-plantilla, integridad referencial de lo etiquetado y exención
  formal); la completitud semántica la revisa el humano.
- De las 16 obligaciones del contexto progresivo, las 13 que se validan contra
  el estado JSON —estructurado e inequívoco— no cambian y nunca fallaron.
- Reglas de diseño para cualquier cambio futuro en este validador, destiladas de
  los seis intentos: **una sola vista** del documento (todo lee
  `visible_markdown_lines`); **aceptar y verificar son la misma extracción**, sin
  transformar el texto antes; **ninguna exención por contexto adyacente** —lo
  irresoluble no es evidencia, no se le busca excusa—; **citar no es declarar**;
  y **medir el efecto inverso**, con un caso de prosa legítima que deba seguir
  pasando por cada regla que bloquea.
- Si `module_specs.data_model` o `module_specs.api_contract` es `required` o
  `done`, la vista debe cargar el documento formal correspondiente mediante
  selección acotada por el módulo activo (entidades propias/conectadas en
  `data_model.md` y operaciones/schemas alcanzables en `api_contract.yaml`, sin
  cargar el documento completo). El validador comprueba contenido real y
  ausencia de problemas estructurados (`spec_problems`); una referencia rota o
  externa sin resolver en entorno hermético devuelve
  `_state_obligation_delivered = False` y bloquea con `NEEDS_INPUT`. Los
  cambios en las fuentes de selección (`04_active_scope.md`, `03_contracts.md`,
  `11_file_dependencies.md`) invalidan de forma inmediata el cursor de la
  consulta (`STALE_CURSOR`) al incorporarse sus digests al fingerprint de la carga.
  La clasificación `done` no convierte una plantilla en spec.
- `preparation` es una fase de workflow: puede producir tier, specs,
  aplicabilidad, inventario, aprobación, ejecución o review. No es una vista
  pública ni un requisito que la propia vista de implementación deba satisfacer
  antes de poder producirlo. Después de cada preparativo se reconsulta la vista
  vigente y solo `complete=true` más `required_context_complete=true` permite
  entrar a CODE; en cierre se reconsulta antes de `complete_module`.
- Una selección explícita en `startup` carga el scope acotado para detectar un
  conflicto de objetivo; el arranque normal no abre scope ni cierre.
- La lectura normal valida el esquema canónico completo antes de calcular la
  vista. Un estado inválido o con `session.codex_execution` legacy produce un
  error clasificado y nunca un `PASS`; `get_full_state` queda reservado para
  diagnóstico explícito.
- En `implementation` y `closure`, `data.summary` es solo una previsualización
  acotada. `data.items` transporta completos `module_spec`, `stack`,
  `dependencies` y `architecture_constraints`, paginándolos o fragmentando un
  valor individual cuando sea necesario.
- CLI y MCP emiten el mismo JSON mediante `state_queries.serialize_context`;
  el límite de bytes se calcula sobre esa representación con espacios, no
  sobre una serialización compacta distinta.

## Vistas

`startup` entrega identidad del proyecto, fase, objetivo activo, tier,
continuidad y conteos de bloqueos. `implementation` agrega identidad del
módulo, precondiciones, specs, restricciones y referencias requeridas.
`closure` entrega rechazos, bugs/clarificaciones aplicables, specs y receipts;
el validator de cierre conserva la última palabra. `recovery` entrega
continuidad, journal, bloqueos y referencias de evidencia/migración para
reconstruir la sesión; la ausencia del journal deja la vista incompleta.

El transporte ejecuta en la misma lectura bajo lock las validaciones que
dependen del workspace: vigencia de APPROVE_RECEIPT para `tier_2`, review y
execution contra el diff actual, y respaldo real de resoluciones en la
bitácora. La consulta reutiliza esos resultados estructurados; no duplica
validadores ni convierte un marcador autoconsistente en evidencia. Cada
consulta usa un snapshot de lectura local: una dependencia que cambia durante
la consulta produce `SOURCE_CHANGED`, el snapshot no se comparte entre
consultas y los receipts se vuelven a validar en la reconsulta. El loader
deriva una sola vez por consulta el plan efímero de obligaciones, secciones
normativas/de evidencia, correspondencias y requisitos estructurales; loader,
referencias, fragmentos y completitud reutilizan ese plan sin persistirlo ni
exponerlo en el JSON público.

## Límites

La primera página de arranque apunta a 12 KiB y las vistas detalladas a 24 KiB,
medidos sobre el JSON de datos UTF-8, sin el envoltorio JSON-RPC. Ningún dato
necesario se descarta para caber: se pagina o se devuelve una condición que
requiere intervención.

La auditoría de volumen usa `.agent/scripts/context_measurement.py`: reporta
bytes por página, total del recorrido, bytes de wire, páginas, archivos leídos
únicos y fragmentos/documentos repetidos. En modo diagnóstico, activado por
`FRAMEWORK_CONTEXT_TRACE_DIR` o por su CLI, también reporta operaciones y bytes
leídos reales, hits de cache, relecturas, consumidor, fase de arranque/consulta
y subprocesos no observables. MCP mantiene una sesión por recorrido y separa
initialize/discovery de `tools/call`. Es una métrica del transporte y no una
medición de tokens o de la ventana privada del host Codex.

### Vistas que no empaquetan, en lectura (5.3.0 L8)

Nada de esto guarda datos nuevos: se recorta e indexa al leer.

- Si la cabecera de la respuesta (resumen y referencias, sin elementos) supera el
  límite, el resumen se recorta por orden (`warnings`, `receipts`, otros campos
  grandes) sustituyendo cada campo por un recuento y sus primeros elementos
  compactados. La respuesta lleva `row_clipped: true` y `summary.clipped_fields`
  con las rutas recortadas. Sin desborde esas claves no aparecen. El resumen
  del recibo de aprobación muestra `test_identities_count` y
  `test_identities_origin_count` en lugar de los mapas de hashes. El resumen
  también se recorta cuando la cabecera cabe pero no deja sitio al primer
  elemento (reserva de 4 KiB). Si ni recortada cabe, el error dice que es el
  encabezado; «Un elemento no puede empaquetarse» queda para un elemento solo.
- Sin módulo explícito, el objetivo es el de un `ciclo_started` vigente (se lee
  solo el final de `state_log.jsonl`, 64 KiB), si no el primer `in_progress` con
  estado `in_progress` y, si no, `modules.in_progress[0]`. `query_context` sigue
  siendo puro: recibe la pista como `active_cycle_module`. Un
  `set_session_pointer` posterior con `next_module` de otro módulo anula la
  pista aunque ese módulo no esté en curso: el log no sabe qué había en
  `in_progress`, y ante la duda se vuelve a la regla del estado.
- `recovery` con módulo explícito carga también su `scope`.
- Contratos sin propietario solo son pertinentes si nombran el módulo o una
  dependencia, si su archivo se cita, o si su nombre es un identificador técnico
  citado. `select_invariants` omite las reglas (hojas) que solo nombran a otros
  módulos; lo omitido sale en la ref `invariants` con `omitted_headings`.
- Cada texto se escanea una vez por consulta (índice en memoria, sin disco).

## Errores

Una vista, objetivo, estado o cursor inválido es un error explícito. Una fuente
ausente, ambigua o cambiada nunca se presenta como contexto completo. El CLI conserva
JSON en stdout y usa códigos 2 para argumentos y 3 para fuente/estado; un gate
`BLOCK` técnicamente consultable mantiene rc=0.
