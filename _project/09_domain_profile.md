---
description: Perfil de dominio del proyecto — activa conocimiento especializado en el agente
---

# Perfil de Dominio — [NOMBRE_PROYECTO]

> Este archivo se crea durante `/nuevo-proyecto`.
> El agente lo lee en cada `/arrancar` para activar el contexto de dominio correcto.
> Sección "Expertos activos": Claude crea la base inicial; el desarrollador la alimenta con fuentes reales.

---

## Identificación del dominio

- **Dominio principal:** [trading | agricultura | salud | logística | industria | educación | finanzas | otro]
- **Sub-dominio:** [descripción específica — ej: "trading algorítmico de futuros", "trazabilidad agrícola"]
- **Usuarios objetivo:** [quién usa el sistema — ej: operadores de planta, traders, médicos]
- **Criticidad:** [baja | media | alta | crítica] — [justificación breve]

---

## Vocabulario técnico del dominio

> Términos que el agente debe conocer para entender los requisitos sin ambigüedad.
> Si esta sección contiene `[COMPLETAR]`, ningún módulo de negocio Medium/High puede empezar sin `/entrevistar`.

| Término | Definición en el contexto del proyecto |
|---------|----------------------------------------|
| [término 1] | [definición] |
| [término 2] | [definición] |
| [COMPLETAR] | — |

---

## Invariantes de dominio

> Reglas de negocio que NUNCA cambian. Violarlas = bug crítico.
> Si esta sección contiene `[COMPLETAR]`, `/cierre-modulo` debe bloquear módulos de negocio hasta completarla.

- [ej trading: los decimales monetarios usan `Decimal`, nunca `float`]
- [ej salud: las dosis no se redondean — se truncan hacia abajo]
- [ej logística: un envío no puede retroceder de estado sin registro de excepción]
- [COMPLETAR antes de implementar el primer módulo de negocio]

---

## Criterios de calidad específicos del dominio

> Métricas y umbrales que definen "correcto" en este dominio.

- [ej trading: latencia de ejecución < 50ms en condiciones normales]
- [ej agricultura: trazabilidad completa de lote a origen — sin gaps]
- [ej salud: cero pérdida de datos en caso de fallo de red]
- [COMPLETAR según requisitos no funcionales del cliente]

---

## Riesgos críticos del dominio

> Errores que en este dominio tienen consecuencias graves (legal, seguridad, pérdida económica).

- [ej finanzas: doble procesamiento de transacción = pérdida económica directa]
- [ej salud: sobredosis por error de unidad = riesgo vital]
- [ej logística: status incorrecto = penalización contractual]
- [COMPLETAR — estos se verifican en `/cierre-modulo` gate de seguridad]

---

## Barrido de sub-dominios (4.1.0 endurecido, FW-131)

> Registro obligatorio de la detección de expertise (Parte A de
> `.agent/workflows/sugerir-skills.md`). Cada sub-dominio detectado en el
> objetivo del proyecto lleva un veredicto explícito — el barrido en sí es el
> artefacto: su ausencia es visible para el gate suave de `/cierre-modulo`
> (3.9), no un silencio que nadie nota.
> En `/nuevo-proyecto` (sin PRD aún) el ancla es la descripción del problema,
> el dominio/sub-dominio, los usuarios objetivo, el quick win y la
> criticidad declarados en la entrevista de inicialización. Tras `/diseñar`,
> el ancla pasa a ser `01_product_requirements.md` y el scope activo.

| Sub-dominio detectado | Veredicto | Cita que lo dispara | Fecha |
|---|---|---|---|
| [ej: seguridad de bots de mensajería] | propuesto → `domain/seguridad_bots.md` | [sección del PRD/entrevista citada] | [fecha] |
| [ej: clasificación financiera] | descartado — [razón concreta] | [sección citada] | [fecha] |
| [COMPLETAR tras `/nuevo-proyecto`] | — | — | — |

---

## Expertos activos

> Claude genera la base inicial con su conocimiento del dominio.
> El desarrollador la enriquece con fuentes reales: docs, papers, manuales, APIs del sector.
> Cada experto tiene su archivo en `.agent/skills/domain/`.

| Experto | Archivo | Estado | Última actualización |
|---------|---------|--------|----------------------|
| [ej: Experto en gestión de riesgo] | `domain/risk_management.md` | 🌱 Base IA | [fecha] |
| [ej: Experto en regulación sectorial] | `domain/regulation.md` | 🌱 Base IA | [fecha] |
| [COMPLETAR tras `/nuevo-proyecto`] | — | — | — |

**Estados:** 🌱 Base IA → 📚 Enriquecido con fuentes → ✅ Validado por experto humano

---

## Fuentes de referencia

> Documentación, APIs, estándares o normativas relevantes para el dominio.
> El desarrollador las añade aquí para que el agente las pueda referenciar.

- [ej: API de la bolsa — docs.exchange.com/api]
- [ej: Normativa fitosanitaria — reglamento CE 396/2005]
- [COMPLETAR con fuentes reales del cliente / sector]
