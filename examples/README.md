# Ejemplo de continuidad entre sesiones

Desde la raíz del repositorio, ejecuta:

```bash
python3 examples/session-continuity.py
```

El ejemplo inicializa un proyecto sintético de notas, registra el próximo paso
mediante el motor de estado y lo recupera en un proceso nuevo. La prueba falla si
la consulta de arranque y la bitácora no reflejan la acción que se registró.
La carpeta temporal se limpia al terminar.

Para seguir el mismo recorrido en un proyecto real, usa `/nuevo-proyecto` al
iniciar, `/gycd` para guardar la continuidad y `/arrancar` en la próxima sesión.
El flujo `/ciclo` consulta el alcance y los criterios de aceptación del módulo
antes de modificar archivos y verifica la evidencia que exige su tipo de cambio.
