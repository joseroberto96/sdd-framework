---
description: Núcleo permanente de obligaciones; los procedimientos se activan por etapa
---

# Núcleo de ejecución permanente

Este archivo es la carga corta común a todos los harnesses. La constitución
completa (`GUARDRAILS_MASTER.md`), las restricciones de arquitectura y los
workflows siguen siendo canónicos; este núcleo no los reemplaza ni los resume
de forma que elimine obligaciones.

Antes de actuar, el agente debe:

1. Tratar `_project/03_project_state.json` como estado canónico y usar el MCP
   `state_manager` para mutarlo. Las proyecciones son de solo lectura.
2. Confirmar el scope y el objetivo de la unidad actual. Si la conversación se
   interrumpió, se compactó de forma dudosa o cambió de intención, activar
   `/reconciliar` antes de escribir.
3. Clasificar el riesgo por blast radius y reversibilidad. Un cambio existente
   exige inventario `PRESERVAR`; un cambio destructivo requiere aprobación
   explícita y recuperación demostrable. Leer, medir y diagnosticar no son
   implementación: no piden recibo ni el gate de implementación; los límites
   duros siguen activos.
4. Mantener activos el guardia de comandos destructivos, la revisión de
   secretos, los contratos, las dependencias declaradas y las aprobaciones.
5. No presentar una comprobación incompleta como PASS, no cerrar sin
   validación/review exigibles y no fabricar evidencia ni terminalidad. Sin
   `builder_use` (lo registra `confirm_builder_use` a petición del builder) se
   dice «código listo, sin uso real», nunca «terminado» ni «listo».
6. Si prevés ausentarte más de ~1 h, cierra con `/gycd`; al volver, sesión nueva pegando el `Siguiente:`; si no tienes prompt o quieres ver el estado, `/arrancar`. Reanudar en frío una sesión larga cuesta 4–11× más que arrancar; el hook `Stop` avisa de sesión larga.
7. Higiene de sesión: una unidad de trabajo por sesión; tras dos correcciones fallidas del mismo problema, `/gycd` y sesión nueva con el prompt corregido (el hook `Stop` corta el bucle); modelo y esfuerzo fijos toda la sesión; exploración amplia fuera del contexto principal. Detalle y caché: adaptador del host (`SESSION_HYGIENE`).

## Árbol de decisión del builder

```
¿Empezando?      → pega el Siguiente: guardado · sin prompt: /arrancar (producto nuevo: /nuevo-proyecto · existente: /migrar-proyecto · fase difusa: /diseñar)
¿Construir?      → /ciclo      (el agente encadena la maquinaria; pausa solo ante decisiones tuyas o gates realmente bloqueantes)
¿Algo raro?      → /reconciliar (bug: /nuevo-bug; si bloquea, /ciclo)
¿Terminando?     → /gycd
```

En producción (FW-225): solo a pedido del builder (`set_lifecycle`); `/ciclo` → `.agent/workflows/mantenimiento.md`.

## Cierre de turno

Todo turno que devuelve el control al builder, con workflow o sin él, acaba en
su cierre: texto plano, lo último del mensaje, nada detrás, nunca en bloque
de código ni entre comillas invertidas. Sin cierre el turno está incompleto.

- Normal, dos líneas: «Siguiente: /ciclo Implementar el login en src/auth.py con sus tests» y debajo «Modelo: standard · Esfuerzo: 🟡 medio (tier_1)», sin texto tras el tier. Comando del builder e instrucción autosuficiente: una acción de hasta 40 palabras, sin prohibiciones (las restricciones las escribe el builder).
- Decisión pendiente, una línea: «Siguiente: decisión — responde A (recomendada) o B en este chat». Sin comando ni línea de modelo; `/ciclo` no aprueba ni decide.
- Espera o proceso largo: como el normal; la instrucción dice qué comprobar y qué hacer al retomar, y no empieza por «esperar».
- Bloqueo, una línea: «Siguiente: ninguno — falta la clave de la API en .env».
- Pregunta suelta: responder y repetir tal cual el cierre vigente de la sesión; si aún no hay, el que toque.

Comando por tipo de trabajo: `/ciclo` construye · `/diseñar` decide el rumbo de una fase difusa o la replantea (elegir entre opciones ya planteadas es una decisión pendiente) · `/reconciliar` si algo no cuadra · `/nuevo-bug` · `/gycd` cierra la sesión · `/arrancar` la abre sin prompt o muestra el estado.
Detalle: `.agent/skills/decision_translation_core.md`. Lo verifica `.agent/scripts/turn_close.py`; el hook `Stop` (`turn_close_hook.py`) lo aplica.

## Ruteo de maquinaria interna (invocada por el agente)

El agente rutea a estos workflows según los gates y el estado:
- Ambigüedad de negocio → `/clarificar` o `/entrevistar`
- HU sin detalle → `/enriquecer-ticket`
- Implementación tier_1/tier_2 con lógica que probar → `/tdd`
- Tocar código existente → `/cambio-seguro`
- Módulo terminado → `/cierre-modulo` (único camino a `completed`)
- Siguiente módulo → `/scope-siguiente`
- Hito de fase → `/validate-arch`
- MVP compartible → `/publicar-github`
- 3+ módulos sin higiene → `/refactorizar-arquitectura`

## Carga por etapa

`/arrancar` usa `get_work_context(view="startup")`. Al comenzar a implementar
activa `implementation` con el módulo explícito; al cerrar activa `closure`;
después de una interrupción activa `recovery`. Cada vista devuelve un resumen
acotado, referencias y cursores ligados al fingerprint. El agente abre el
workflow, spec o contrato completo solo cuando la obligación de esa etapa lo
requiere.

`complete` solo significa que terminó una página. `required_context_complete`
solo significa que las secciones obligatorias ya fueron entregadas. Ninguna de
las dos autoriza por sí misma una mutación o cierre.

## Presupuesto de respuesta

El límite de una vista acota el transporte, no la evidencia: si algo necesario
no cabe, se pagina o se devuelve `NEEDS_INPUT`. No se mide la ventana del host,
no se convierte contexto desconocido en cero y no existe un planificador de
tokens en este núcleo.

Durante la congelación, una fricción del framework se registra y se sigue con la salida declarada del gate; desde un proyecto no se diseña ni se aplica un arreglo del framework.

Shell macOS = zsh: entrecomilla `==`, `=palabra` y los `*`; exit ≠ 0 = salida incompleta.
