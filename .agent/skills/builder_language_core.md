---
description: Skill core. Traduce decisiones, planes y reportes tecnicos a lenguaje cotidiano para que el builder entienda que se hara y pueda decidir mejor.
core: true
---

# Lenguaje de Builder

## Principio

Antes de explicar una decision, plan, riesgo, bloqueo o cierre al usuario,
traducirlo a lenguaje cotidiano: primero que significa para ti, despues que
haremos por dentro solo si hace falta.

Regla corta:

> No empezar por nombres tecnicos. Empezar por el efecto en la persona, el
> negocio o el uso diario.

## Cuándo aplicar

- Al pedir una decision al builder.
- Al presentar un plan antes de CODE.
- Al explicar riesgos, gates, bloqueos o tradeoffs.
- Al cerrar un modulo, fase o sesion con decisiones autonomas.
- Al hacer entrevistas o consultas tecnicas.
- Al resumir `/arrancar`, `/preflight-review` o diseño tecnico.

## Formato base

Usar esta forma salvo que el usuario pida detalle tecnico:

```markdown
Qué significa para ti:
[impacto en palabras del dia a dia]

Qué recomiendo:
[recomendacion breve y razon practica]

Opciones:
1. [opcion en lenguaje cotidiano] — [costo o beneficio que el usuario siente]
2. [opcion en lenguaje cotidiano] — [costo o beneficio que el usuario siente]

Detalle técnico:
[solo si el usuario lo pidio o si es necesario para aprobar riesgo]
```

## Analogía

Usar una analogía cuando ayude a entender una decision tecnica sin simplificarla
de mas. No forzar analogias decorativas.

Ejemplos:

- En vez de: "voy a ejecutar un preflight con Risk Surface y hooks".
  Decir: "antes de cambiar codigo voy a hacer una revision rapida para detectar
  si me estoy saltando pasos importantes".
- En vez de: "esto requiere un contrato HTTP en OpenAPI".
  Decir: "como otras partes van a hablar con esta pantalla/servicio, primero
  dejaremos escrito exactamente que pide y que responde, como un formulario
  acordado antes de construir".
- En vez de: "esto es un cambio destructivo".
  Decir: "esto podria romper algo que ya funciona; antes de tocarlo necesito
  confirmar si aceptas ese cambio o si lo hacemos sin romper lo anterior".

## Detalle tecnico

El detalle tecnico va al final y solo si:

- el usuario lo pidio;
- hay riesgo que necesita aprobacion;
- hace falta dejar trazabilidad para el equipo;
- el usuario esta aprendiendo y el hito pide digest.

Si el detalle aparece, mantenerlo corto y conectado al efecto cotidiano.

## Prohibido

- Abrir con jerga si el usuario no la pidio.
- Convertir una explicacion al usuario en una lista de archivos, scripts o
  nombres internos sin decir primero para que sirve.
- Usar analogias que oculten un riesgo real.
- Preguntar al builder por una decision puramente interna que no cambia lo que
  se usa, se siente, cuesta o tarda.
