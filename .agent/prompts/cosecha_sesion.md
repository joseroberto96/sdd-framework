# Cosecha de fricción de sesión

> Extrae de la transcripción de un chat la fricción que ningún otro canal captura.
> `/retrospectiva-framework` mina artefactos de `_project/` al cerrar proyecto;
> `/aprender-de-error` exige una corrección explícita del builder. La
> transcripción —donde vive la fricción del modelo— no la lee nadie.
>
> Destino de lo cosechado: `FW-NNN` en el field report, `_project/22_lessons.md`
> vía `/aprender-de-error`, o el búfer de frecuencia
> (`.agent/docs/friction_buffer.md` en la plantilla, `_feedback/friction_buffer.md`
> en un proyecto).
>
> **Uso**: pegar el bloque de abajo al final de una sesión, antes de compactar.
> Aún no está cableado a ningún workflow — a propósito, hasta decidir el punto
> de enganche.

---

```
Cosecha de fricción de sesión. Lee la transcripción COMPLETA de este chat,
no tu recuerdo de ella. Vas a producir evidencia para mejorar el framework.

ALCANCE — busca las tres clases, en este orden de valor:

  A. FRICCIÓN DEL MODELO: cosas que hice mal, ineficientemente, o que casi
     hago mal. Incluye OBLIGATORIAMENTE:
     - near-misses: errores que cacé yo mismo antes de que te afectaran
     - verificaciones que pudieron pasar en falso (exit 0 engañoso, salida
       truncada, test que no ejerce lo que dice)
     - momentos en que racionalicé saltarme un paso ("esto es simple",
       "ya lo sé", "lo compruebo después")
     - reglas que anuncié y no cumplí, o reglas del proyecto que incumplí
     - datos que afirmé de memoria sin medir
     - turnos gastados en algo que un patrón conocido habría evitado

  B. FRICCIÓN DEL FRAMEWORK: gates que bloquearon de más o de menos,
     scripts frágiles, pasos ceremoniales sin valor, avisos ruidosos,
     workflows que no aplicaban a la situación real.

  C. FRICCIÓN DEL BUILDER: dónde tuviste que intervenir, repetir algo,
     pararme, o decidir sin contexto suficiente.

REGLA DE EVIDENCIA — innegociable:
  Cada hallazgo cita evidencia literal de ESTA transcripción: el comando,
  el mensaje de error, el exit code, la frase exacta. Sin cita verificable,
  el hallazgo se descarta. No infieras lo que "probablemente pasó".

REGLA DE ALCANCE OBSERVADO:
  Si la sesión se compactó, dilo y acota qué tramo pudiste leer de verdad.
  Los hallazgos anteriores a una compactación se marcan como no verificables
  en vez de afirmarse con confianza. Lo que reportes es un suelo, no un techo.

REGLA DE HONESTIDAD:
  Cero hallazgos es un resultado válido y preferible a inventar. No
  suavices los de clase A porque los cometí yo. Prohibido el hallazgo
  vago tipo "podría haber sido más cuidadoso": si no nombra un disparador
  concreto y una acción concreta, se descarta.

AGRUPA ANTES DE EMITIR — este paso es obligatorio:
  Varias instancias de un mismo mecanismo son UN hallazgo con N evidencias,
  no N hallazgos. Si emites cuatro items que se arreglarían con el mismo
  guard, has fallado el paso. Cada clase lleva un slug corto y estable
  (p. ej. `verificacion-afirma-mas-de-lo-medido`), y ANTES de inventar uno
  nuevo lees la lista de slugs en uso del búfer de fricción y reutilizas el
  que encaje: los slugs cuasi-duplicados destruyen el conteo, que es lo
  único que el búfer aporta. La cuenta de instancias es la severidad.

CLASIFICACIÓN — para cada CLASE, responde:
  1. ¿Qué la habría PREVENIDO? Elige uno:
       GUARD      — un check ejecutable (test, gate del doctor, hook, paso
                    de ci_smoke). Preferido siempre que sea posible.
       WORKFLOW   — un paso que falta o sobra en un workflow existente.
       LECCIÓN    — solo si no hay forma de hacerlo ejecutable. Justifica
                    por qué no puede ser un GUARD.
       NADA       — el sistema se comportó bien; fue caso legítimo.
  2. ¿Es REINCIDENTE? Busca en el field report, en `_project/22_lessons.md`
     y en el búfer de fricción si esta clase ya está registrada.
     Si lo está: eso es el hallazgo principal, no el fallo en sí —
     significa que el registro existente no ejecuta nada. Cita la entrada.
  3. Coste: turnos perdidos, CI roto, trabajo rehecho, o "ninguno, near-miss".

SALIDA — ordenada por: reincidentes primero, luego por número de instancias.
  Para cada clase: slug · síntoma en una línea · evidencias citadas ·
  clase (A/B/C) · prevención · reincidencia · destino propuesto.

DESTINO — no crees archivos nuevos. Propón para cada clase:
  - `FW-NNN` en el field report, si la prevención ya está clara (GUARD o
    WORKFLOW accionable). Si la clase cruza proyectos, va a la PLANTILLA,
    no al field report del adoptante.
    En un adoptante el ID es `PREFIJO-FW-NNN`, con el `fw_prefix` de
    `_project/15_environment_profile.md` (si falta, decláralo antes); `FW-NNN` solo en la plantilla.
  - `_project/22_lessons.md` vía /aprender-de-error, si es LECCIÓN.
  - el búfer de fricción, si aún no es accionable por sí sola y su valor
    está en repetirse. Añadir la instancia bajo su slug, sin reescribir
    las existentes.
  - descartar, diciendo por qué.

No escribas nada todavía. Muéstrame la lista y espera mi aprobación.
```
