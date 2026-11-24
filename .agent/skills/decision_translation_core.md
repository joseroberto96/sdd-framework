---
description: Skill core. El framework decide lo técnico autónomo (con el expertise correcto) y convoca al builder solo cuando una decisión moldea el producto (cómo se usa/se siente), en lenguaje llano + analogía + recomendación + alternativas; enseña en cada hito.
core: true
---

# Traducción de Decisiones

> El framework como equipo de desarrollo: el agente decide lo técnico solo; el builder
> decide lo que moldea su idea. Subordinada a `GUARDRAILS_MASTER.md`, `CLAUDE.md`,
> `AGENTS.md`, `.agent/rules/architecture_constraints.md`, contratos y ACs: si choca, pierde esta skill.
> Toda pregunta o digest para el builder aplica primero `.agent/skills/builder_language_core.md`.

## Regla de clasificación (el corazón)

**"¿Se nota desde fuera — en cómo se usa o se siente? → es del builder. ¿Es cómo está hecho por dentro? → es del agente."**

- DECISIÓN DEL BUILDER si cambiar la respuesta cambia algo **observable por quien usa el producto** (pantalla, flujo, qué pasa en un caso que el usuario nota, el tono) **o** impone un tradeoff que el usuario **siente** (velocidad, coste, un límite).
- AUTÓNOMO (el agente) todo lo **interno**: base de datos, framework, librería, estructura, arquitectura — **aunque sea irreversible**. No es el campo del builder.

## Routing de expertise (antes de decidir o recomendar)

Clasifica el dominio y activa la skill/experto correcto. Nunca la lente equivocada (no decidir arquitectura con una skill de UI):
- Arquitectura/datos/refactor → `architecture_core`
- Seguridad/auth/datos sensibles → `security_core`
- Tests/regresiones/cobertura → `quality_testing_core`
- Contratos/interfaces/endpoints/ACs → `spec_contracts`
- UI/UX/copy visible → `frontend_design`
- Sobreingeniería/YAGNI → `minimal_solution_core`
- Dominio de negocio → experto en `_project/` (o `/añadir-experto` si falta)

Si el dominio necesita expertise que no existe, señalarlo vía `sugerir-skills` / `/añadir-experto` en vez de decidir a ciegas. Cada decisión queda trazable a la(s) skill(s) que la fundó.

## Modo A — fork de visión (preguntar)
La decisión cambia cómo se usa o se siente el producto. Ej.: "¿el usuario ve el contenido antes o después de registrarse?". No preguntar: "¿Postgres o SQLite?".

## Modo B — tradeoff sentido (preguntar)
Una decisión técnica impone una consecuencia que el usuario siente. Reformular a experiencia, no a tecnología: "puedo hacerlo instantáneo pero cuesta X, o barato pero tarda un poco — ¿qué pesa más?".

## Formato de la pregunta (A/B) — plantilla obligatoria (3.19.0, FW-074)

Todo checkpoint de builder usa esta estructura fija. No es estilo: es contrato
— el builder aprende en cada decisión porque el formato siempre le explica.

```
Decisión: [una frase llana — qué hay que decidir]
Qué significa para ti: [impacto en cómo se usa/se siente tu producto]
Analogía: [comparación cotidiana que cualquiera entienda]
Recomendación: [opción recomendada] — [porqué, 1-2 frases, fundado en la skill/experto de dominio]
Opciones:
A) [opción] — si eliges esto: [trade-off en términos de uso/coste/tiempo]
B) [opción] — si eliges esto: [trade-off en términos de uso/coste/tiempo]
```

- Aplicar `builder_language_core` en cada campo; 2–3 opciones, nunca más.
- Regla mecánica (usar cuando hay Python): `python3 .agent/scripts/validate_checkpoint.py <archivo|->`
  adjudica la ESTRUCTURA (etiquetas presentes con contenido real, 2-3 opciones
  con trade-off tras `—`) — exit 0 = OK, exit 1 = problemas accionables. La
  calidad de la analogía sigue siendo juicio del modelo; el gate impide que el
  formato degrade en silencio. Known-bad canónico: "¿Postgres o SQLite?" (pregunta
  técnica cruda) bloquea.
- Render: selector nativo donde exista (cajas); si no, `CHOICE_TEXT` (opciones numeradas, elegir tecleando A/B/C). Ver `.agent/adapters/_capability_matrix.md`.

## Flujo (no romper el flow)
Al toparse un fork A/B en medio del trabajo: avanzar todo lo independiente, pausar solo la rama dependiente, agrupar las decisiones para la "reunión" en el siguiente punto natural (fin de tarea/hito) — con el trabajo ya avanzado. Excepción: en forma reversible y de bajo impacto, seguir con la mejor apuesta marcada "asumido" y confirmar en el hito. La "reunión" agrupa decisiones A/B pendientes (tú eliges); el digest C (Modo C) es solo enseñanza en el hito, sin decisión que tomar. Son cosas distintas.

## Lenguaje de «terminado» (FW-275)
Sin `builder_use` en el módulo (lo registra `confirm_builder_use` cuando el builder dice que lo usó), decir «código listo, sin uso real»; nunca «terminado» ni «listo» para producto. No citar cifras de fixtures o datos de prueba como si fueran reales (PA Lección 2).

## Bloque de cierre de turno (4.20.1, FW-141/142; alcance y casos en 5.3.0, FW-314)

La versión corta, siempre cargada, es la sección «Cierre de turno» de
`.agent/rules/runtime_core.md`. Esta sección la detalla y no la contradice. La
definición ejecutable es `validate_turn_close`, en
`.agent/scripts/turn_close.py`: si este texto y el validador discrepan, manda
el validador y se corrige el texto.

**Alcance:** todo turno que devuelve el control al builder, con workflow o sin
él: también tras una pregunta suelta, una pausa o un bloqueo.

**Por qué al devolver el turno.** El agente no puede cambiar su modelo ni su
esfuerzo (viven en el arnés, del lado del builder), pero sí conoce el tier de
lo que viene. Ese dato solo sirve cuando el builder recupera el control:
mientras el agente trabaja, nadie puede ajustar nada sin interrumpir (FW-142).

**Forma.** Dos líneas de texto plano, lo último del mensaje y sin párrafos
intercalados: la línea `Siguiente:` y, justo debajo, la de modelo y esfuerzo.
Nunca dentro de un bloque de código ni entre comillas invertidas; nada detrás
de ellas; una sola línea `Siguiente:` por respuesta; sin marcadores sin
rellenar; una instrucción de 40 palabras como máximo (`INSTRUCCION_LARGA`, aviso;
lo que va entre comillas invertidas cuenta como una) y 400 caracteres en la línea
(aviso). Los formatos anteriores a 4.20.1, con
el comando y el prompt en líneas separadas, están retirados
(`FORMATO_HEREDADO`). Los ejemplos de esta sección van entre comillas latinas
solo para citarlos: en la respuesta se escriben sin ellas.

### Línea 1 — `Siguiente`: un solo string copiable

| Caso | Qué lleva | Ejemplo válido |
|---|---|---|
| Normal | comando del builder e instrucción autosuficiente | «Siguiente: /ciclo Implementar el motor de Tailoring en src/tailoring_engine.py con interfaz desacoplada de la persistencia» |
| Decisión pendiente del builder | qué contestar y dónde; sin comando ni línea de modelo, porque la respuesta llega en este mismo chat | «Siguiente: decisión — responde A (recomendada) o B en este chat» |
| Espera o proceso largo | forma normal: el comando para retomar y qué comprobar antes de seguir; la instrucción no empieza por «esperar» | «Siguiente: /ciclo Retomar el módulo de backtest cuando acabe la suite larga: comprobar que logs/suite.log termina en OK y seguir con la tarea 3» |
| Bloqueo | el motivo y qué lo desbloquea; sin comando ni línea de modelo | «Siguiente: ninguno — falta la clave de la API de pagos; añádela a .env y avísame» |
| Pregunta suelta | se responde y se repite, tal cual, el cierre vigente de la sesión, con su línea de modelo si la tenía; si aún no hay ninguno, el que corresponda al estado | el mismo que cerró el turno anterior |

`/ciclo fase` y `/ciclo fase --paralelo` son válidos sin más instrucción. En
«decisión» y «ninguno» el separador es la raya «—».

- El comando es un **verbo del builder** (`builder_facing: true`), elegido por
  tipo de trabajo: `/ciclo` construye; `/diseñar` decide el rumbo de una fase
  difusa o la replantea (elegir entre opciones ya planteadas es una decisión
  pendiente, no trabajo para `/diseñar`);
  `/reconciliar` cuando algo no cuadra; `/nuevo-bug` registra un fallo; `/gycd`
  cierra la sesión; `/arrancar` la abre sin prompt o muestra el estado. Si el
  candidato natural es maquinaria interna (`/tdd`, `/scope-siguiente`,
  `/cierre-modulo`…), es un bug del workflow que lo dejó ahí: corregirlo en
  la fuente, nunca traducirlo aquí en silencio.
- **`/ciclo` no aprueba, no decide y no espera al builder.** Si lo que falta es
  su respuesta, el cierre es la forma de decisión. Su pregunta va antes, con el
  «Formato de la pregunta (A/B)» de esta skill.
- La instrucción debe ser **autosuficiente**: concreta, suficiente y
  necesaria para que CUALQUIER modelo, sin el contexto de esta conversación,
  sepa exactamente qué hacer. `next_action` verbatim solo vale si ya cumple
  eso; si es vago, redactarlo bien aquí y persistir esa versión, no la vaga
  (`/arrancar` solo lo redacta: no muta el estado).
- **Una acción, sin prohibiciones (FW-360).** La línea propone qué hacer; no
  lleva «sin X ni Y»: las restricciones solo las escribe el builder, y una
  pegada en la sesión siguiente no es una petición suya. Una cláusula «sin»
  suelta vale; dos, o una con «ni», es `PROHIBICIONES`. Si hace falta su
  permiso, la forma es «decisión».

**Known-bad**, con el código que devuelve el validador:

| Cierre | Código |
|---|---|
| «Siguiente: /ciclo Aprobar el plan del módulo de pagos [A/B/C]» | `MARCADOR`, `DECISION_EN_CICLO` |
| «Siguiente: /ciclo Decidir el rumbo de la fase 2» | `DECISION_EN_CICLO` |
| «Siguiente: /ciclo Pendiente del builder: elegir proveedor de pagos» | `DECISION_EN_CICLO` |
| «Siguiente: /ciclo continuar» | `INSTRUCCION_VAGA` |
| «Siguiente: /ciclo» | `INSTRUCCION_VACIA` |
| «Siguiente: implementar el motor» | `SIN_COMANDO` |
| «Siguiente: /tdd Escribir los tests del motor» | `COMANDO_INTERNO` |
| «Siguiente: /gycd Guardar la guía sin commit, push ni ejecución financiera» | `PROHIBICIONES` |

Las tres primeras se corrigen igual: «Siguiente: decisión — responde A (recomendada), B o C en este chat».
`DECISION_EN_CICLO`, `INSTRUCCION_VAGA` y `PROHIBICIONES` son avisos; los demás, errores.
Sin línea de cierre el código es `SIN_SIGUIENTE`; dentro de un bloque de
código, `EN_BLOQUE_DE_CODIGO`; con el comando entre comillas invertidas,
`COMILLAS_EN_COMANDO`; con texto detrás, `NO_ES_LO_ULTIMO`.
`PALABRAS_PEGADAS` también avisa, sin rechazar, de palabras pegadas a cifras o
siglas fuera del código (FW-362). Los avisos se registran y no bloquean (FW-402).
Si el hook `Stop` bloquea por un error, se responde solo con las líneas de cierre
corregidas, sin repetir la respuesta anterior.

**Estado persistido.** La línea se compone siempre como
`{next_command} {next_action}`, así que comando e instrucción coinciden por
construcción (FW-141). `session.next_command` contiene únicamente un comando
`builder_facing` del `manifest.json` (o `/ciclo fase`, `/ciclo fase --paralelo`,
o el sentinel explícito `ninguno`) y `session.next_action`, únicamente la
instrucción completa. La proyección es la única capa autorizada a
componerlos; un estado antiguo con el prompt dentro de `next_command` falla de
forma explícita y no se migra en silencio. `session.next_action` cumple la
misma regla: hasta 40 palabras y sin prohibiciones; el detalle va al diario.

### Línea 2 — `Modelo` y `Esfuerzo`: dos ejes separados

Son decisiones distintas y se declaran aparte (FW-142): cambiar de modelo
invalida la caché de contexto; cambiar el esfuerzo la conserva o no según el
host (regla 7 de `runtime_core.md`, FW-320). Mezclarlas en un solo semáforo
obligaba a elegir entre avisar de una u otra.

Sintaxis: `Modelo: <strong|standard|limited> · Esfuerzo: <🟢 bajo|🟡 medio|🟠 alto|🔴 xhigh> (<tier_0|tier_1|tier_2>)`.
Ejemplo: «Modelo: standard · Esfuerzo: 🟡 medio (tier_1)». Tras el nivel de
modelo cabe el modelo concreto entre paréntesis: «Modelo: strong (Opus 5.5) · Esfuerzo: 🟠 alto (tier_2)».
**La línea acaba en el paréntesis del tier:** cualquier texto detrás da
`MODELO_MAL_FORMADO`, como en «Modelo: standard · Esfuerzo: 🟡 medio (tier_1) — tarea acotada».
En los cierres de decisión y de bloqueo no se exige; si aparece, debe estar
bien formada.

**Modelo — 3 niveles.** Mismo vocabulario que
`execution_profile.capability_tier` (GUARDRAILS → "Graduación por capacidad
del ejecutor"): **agnóstico del proveedor** por decisión A8 — el framework no
mapea modelo→tier porque los modelos cambian. El builder traduce a su modelo
concreto según el entorno.

- `limited` — tarea mecánica o acotada; un modelo rápido basta.
- `standard` — implementación normal con ACs claros. Por defecto.
- `strong` — arquitectura, dato sensible, o revisión independiente de `tier_2`.

**Esfuerzo — 4 niveles.** Mismo vocabulario que `/code-review` para el rigor
de revisión — un solo lenguaje de esfuerzo en todo el framework, no uno
inventado aparte:

| Situación | Esfuerzo |
|---|---|
| `tier_0` / tarea mecánica (renombrar, mover archivo, fix de una línea) | 🟢 `bajo` |
| `tier_1` / implementación estándar con ACs claros | 🟡 `medio` |
| `tier_2` / cambio de arquitectura o dato sensible | 🟠 `alto` |
| `tier_2` con `requires_second_review: true`, en el momento de la revisión independiente (paso 3 de `/cierre-modulo`, o al pedir `/code-review`); o situación crítica explícita (dato de producción real por primera vez, migración destructiva, hallazgo de seguridad, o el builder pide máximo rigor) | 🔴 `xhigh` |

**Regla de emisión** — corrige FW-110 (se omitía casi siempre) y FW-142
(llegaba tarde): **emitir siempre que el tier sea conocido**, sin la cláusula
"si no es detectable" que hacía desaparecer la línea, y **siempre al devolver
el turno**, nunca a mitad del trabajo. Solo se omite si genuinamente no hay
tier clasificado todavía para lo que viene, o en los cierres de decisión y de
bloqueo.

## Checkpoint por blast radius (cuándo pausar de forma operativa)

La regla de clasificación de arriba dice **quién** decide. Esta subsección dice
**cuándo** el agente pausa de forma operativa — independiente de si la decisión
es de builder o interna — para que un cambio de alto impacto nunca pase en
silencio. Se preserva intacta la regla "se nota desde fuera": el checkpoint no
convierte decisiones internas en decisiones de builder; añade un punto de control
cuando el costo de equivocarse es grande.

La graduación combina el `work_tiers` del motor de estado (clasificación previa
del cambio por blast radius + reversibilidad, regla 4 de la constitución) con
tres modalidades operativas:

- **`tier_0` — efímero / blast despreciable**: el agente decide y ejecuta. No
  interrumpe. Ej.: renombrar una variable local, ajustar un log.
- **`tier_1` — reversible, blast acotado**: el agente decide, registra la
  decisión en el digest C (Modo C ya existente) al cerrar el hito, y sigue. No
  interrumpe. Si la decisión tiene **consecuencia arquitectónica o de
  contrato** (toca estructura, datos, seguridad o una interfaz pública), la
  canoniza como ADR vía MCP `register_decision` — esa parte ya estaba.
- **`tier_2` — alto blast radius / irreversible estructural**: fuera de un driver
  autorizado, el agente pausa con resumen antes de ejecutar. Dentro de
  `/ciclo <módulo>`, un `APPROVE_RECEIPT` vigente del scope, riesgos y ADRs evita
  pedir la misma aprobación dos veces: el resumen pasa a **checkpoint informativo**
  (impacto, rollback y revisión independiente) y el agente
  **continúa en el mismo turno**. `/ciclo` solo no crea ese recibo. No cubre cambios
  destructivos, ampliación de scope, dependencias nuevas, migraciones
  irreversibles ni decisiones observables del producto; esos casos sí paran.

**Cómo se invoca en la práctica:** la clasificación del tier ya vive en
`03_project_state.json → work_tiers[id]` (constitutional rule 4). El agente la
lee antes de empezar; si es `tier_2`, escribe el resumen de checkpoint antes del
primer commit y aplica la regla anterior según exista o no un `/ciclo` autorizado.
La acción de checkpoint NO se registra como mutación (no es un
hecho de estado, es un momento del flujo) — pero su omisión en un `tier_2`
queda visible en el diff de cierre cuando `/cierre-modulo` exige el resumen
como evidencia del rigor.

Esta subsección no altera Modo A, Modo B, Modo C, ni la regla "se nota desde
fuera". Formaliza el "asumido reversible de bajo impacto" de la sección *Flujo*
en una escala explícita y agrega el punto de control `tier_2`: informativo con
`APPROVE_RECEIPT` vigente, bloqueante cuando la aprobación falta o quedó stale.

## Estimación de coste antes de aprobar (5.2.3, ESTIMACION_DE_COSTE)

Todo plan con **3 o más tareas**, o con **algún paso de más de 10 min**
(benchmark, suite completa repetida, E2E, build largo), presenta esto **antes
de pedir aprobación**, en lenguaje del builder y en una sola línea o bloque
corto:

- **Duración estimada:** un rango honesto, no una cifra exacta. Si existe una
  medición previa (`python3 .agent/scripts/session_cost.py --project <ruta>`),
  usarla como referencia. Sin base para estimar, decirlo («sin base de
  estimación») en vez de inventar un número.
- **Subagentes previstos:** 0 por defecto. Si son más, cuántos y por qué
  (ADR-072: en Codex no se delega por defecto).
- **Pasos largos marcados como opcionales u obligatorios,** cada uno con su
  coste, para que el builder pueda soltar los opcionales antes de aprobar.

Ejemplo: «Esto son unas 3 horas; el benchmark añade 1 h y es opcional.
Subagentes: ninguno.»

La estimación informa, no sustituye la aprobación: con un `APPROVE_RECEIPT`
vigente dentro de `/ciclo` no se vuelve a preguntar; se emite como línea
informativa. Un paso largo que el plan no estimó no se ejecuta por inercia:
se avisa y se pide decisión.

## Modo C — digest de enseñanza (en hitos)
En `/cierre-modulo` y transición de fase: por cada decisión técnica autónoma relevante, **un párrafo máximo** aplicando `builder_language_core`: qué decidí, qué significa para ti, por qué (concreto) y un ejemplo sencillo para aprender. Sin decisión que tomar.

## Registro
Cuando el builder elige en A/B:
- Si la elección tiene **consecuencia arquitectónica o de contrato** (toca estructura, datos, seguridad o una interfaz pública), canonizarla como ADR vía MCP `register_decision` (la mutación queda trazada en `state_log.jsonl`).
- Si es una decisión de **forma/experiencia sin impacto arquitectónico**, basta registrarla en el **digest C** — no inflar `03_arch_decisions.md` con forks de UX.
