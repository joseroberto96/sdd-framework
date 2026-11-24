# SUBAGENT_WAIT_LIFECYCLE

## Propósito

Definir el ciclo de espera de subagentes sin confundir un timeout de polling con un estado terminal del trabajo.

## Reglas normativas

1. `wait timeout` solo termina la ventana de polling del padre.
2. `WAIT_TIMEOUT_NONTERMINAL` es no terminal; el padre debe seguir consultando hasta observar un estado terminal real del subagente.
3. Un timeout no dispara `interrupt` ni `close_agent`. La única vía de interrupción es el tope de espera prolongado con decisión explícita (regla 8).
4. Una entrada del usuario puede devolver el turno al padre, pero no cancela al subagente.
5. Si el subagente desaparece, muere o queda inaccesible, el resultado debe reportarse como `UNAVAILABLE` o `NEEDS_ATTENTION`.
6. Nunca se debe inventar `FAIL` ni `PASS` cuando no existe un terminal real observado.
7. No se debe crear un supervisor duplicado cuando Codex no expone interceptación mecánica.
8. Tope de espera (ADR-072): pasado el tope declarado por el adaptador (en Codex, 20 min sin progreso observable), el padre puede decidir **de forma explícita** interrumpir al subagente y continuar en el hilo principal. El subagente interrumpido se registra como `NEEDS_ATTENTION`; nunca como `PASS` ni `FAIL`, y una revisión interrumpida no satisface una revisión Tier 2.

## Comportamiento esperado

- `timed_out` significa solo: se agotó la ventana de observación.
- Mientras no exista un terminal real, el padre conserva la responsabilidad de consultar de nuevo.
- La interrupción explícita es un acto separado y no una consecuencia automática del timeout.
- La compactación del subagente no equivale por sí misma a finalización terminal.

## Vocabulario de estados

`WAIT_TIMEOUT_NONTERMINAL` es no terminal y solo indica que terminó una ventana
de polling. El adaptador puede observar los siguientes estados antes de emitir
un resultado:

### Estados observacionales no terminales

- `RUNNING`
- `COMPACTING`

### Estados terminales de resultado

- `PASS`
- `FAIL`
- `NEEDS_ATTENTION`
- `UNAVAILABLE`

## Estado prohibido

- `timeout -> interrupt` (automático, sin decisión explícita)
- `timeout -> close_agent`
- `WAIT_TIMEOUT_NONTERMINAL -> PASS`
- `WAIT_TIMEOUT_NONTERMINAL -> FAIL`

## Transición permitida (ADR-072)

- `timeout prolongado (≥ tope, sin progreso observable) + decisión explícita -> interrupt -> NEEDS_ATTENTION`

Siguen prohibidos `timeout -> PASS/FAIL` y `timeout -> close_agent` automático; con progreso observable no hay tope.

## Notas de implementación

- El adaptador puede pausar y reanudar polling, pero no puede declarar terminalidad por tiempo vencido.
- La garantía mecánica final depende del dispatcher Codex disponible en el entorno.
