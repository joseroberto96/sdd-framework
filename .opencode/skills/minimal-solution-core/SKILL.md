---
name: "minimal-solution-core"
description: "Skill core. Se activa ante sobreingenieria, bloat, abstracciones especulativas, dependencias innecesarias o al disenar/implementar la solucion minima correcta."
---

<!-- GENERATED: framework wrapper; edit manifest/source, then run sync_claude_commands.py -->

# Solucion Minima Correcta

**Activacion:** sobreingenieria, bloat, boilerplate, dependencias nuevas, abstracciones con una sola implementacion, UI que podria usar controles nativos, scripts o helpers que podrian vivir en stdlib/plataforma, refactors donde se busca reducir superficie, o cuando el usuario pida "lo minimo", "simple", "YAGNI", "do less" o mencione Ponytail.

## Precedencia

Esta skill es una heuristica de mantenibilidad y eficiencia. Nunca supera:

1. Correctitud verificable.
2. Preservacion de contratos y anti-regresion.
3. Seguridad y datos.
4. Confiabilidad y observabilidad.
5. Peticiones explicitas del usuario.

Si choca con `GUARDRAILS_MASTER.md`, `.agent/rules/architecture_constraints.md`, `CLAUDE.md`, `AGENTS.md`, contratos o ACs, pierde esta skill.

## Escalera

Antes de escribir codigo nuevo, parar en el primer escalon que resuelva el caso:

1. **No construir:** si la necesidad es especulativa, omitirla y decirlo en una linea.
2. **Stdlib:** usar la biblioteca estandar antes que codigo propio.
3. **Plataforma nativa:** usar HTML/CSS/DB/shell/runtime existente antes que wrappers o librerias.
4. **Dependencia instalada:** usar lo ya declarado en `03_project_state.json -> dependencies` antes de proponer otra dependencia.
5. **Una linea:** si una expresion clara alcanza, usarla.
6. **Minimo correcto:** solo entonces implementar la menor solucion que cumpla ACs y preserve contratos.

La escalera no autoriza saltarse `/cambio-seguro`, `/tdd`, specs, ACs ni verificacion ejecutable. Reduce superficie, no rigor.

## Reglas

- No crear interfaces, factories, adapters, config o capas con un solo consumidor salvo que protejan un contrato publico declarado.
- No instalar dependencias para resolver lo que stdlib, plataforma nativa o una dependencia ya instalada cubren razonablemente.
- Preferir borrar o no escribir codigo cuando eso preserve comportamiento y contratos.
- Si una simplificacion tiene techo conocido, dejar un comentario breve con trigger de revision: `# minimal: O(n), indexar si supera 10k filas`.
- Si el usuario insiste en la version completa tras conocer la alternativa minima, construirla sin re-discutir.

## Nunca Recortar

- Validacion de inputs en bordes de confianza.
- Manejo de errores que evita perdida de datos.
- Seguridad, permisos, secretos o PII.
- Accesibilidad basica.
- Observabilidad exigida por Guardrails.
- Calibracion/configuracion necesaria para hardware, entorno real o integraciones externas.
- Tests/checks de logica no trivial.
- Descomposicion por responsabilidad y cohesion. Partir una UI o un modulo en componentes con responsabilidad unica NO es sobreingenieria. La escalera reduce abstraccion especulativa (interfaces/factories/adapters sin consumidor), nunca modularidad legitima: un archivo que mezcla vista, estado, fetch y validacion se parte aunque sea "menos codigo" dejarlo junto.

## Verificacion

Logica no trivial (branch, loop, parser, transformacion de datos, seguridad, dinero o filesystem) deja el menor check runnable que falle si se rompe: un test focalizado, un `assert` autocontenido o el check existente mas cercano. One-liners triviales pueden no necesitar test, pero el cambio completo si debe pasar la verificacion anti-regresion aplicable.

## Relacion con Ponytail

Inspirada en Ponytail (`DietrichGebert/ponytail`), fijado como referencia externa en `_project/14_skill_provenance.md` con tag/commit exacto. El framework no instala el plugin ni sus hooks por defecto; importa el criterio como skill core subordinada a Guardrails.
