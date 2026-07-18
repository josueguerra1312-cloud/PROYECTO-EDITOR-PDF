# PDF Tech Editor v10

Version endurecida para descarga conjunta:

- El ZIP se crea una sola vez al terminar el lote.
- El ZIP se guarda como bytes estables en `st.session_state`.
- `on_click="ignore"` evita reruns durante la descarga.
- Usa `ZIP_STORED`, maxima compatibilidad con Windows Explorer.
- Verifica CRC, cantidad de archivos y apertura de todos los PDF internos.
- Muestra SHA256 y tamano final antes de descargar.
- Mantiene autenticacion obligatoria y sufijo ` D.pdf`.

Sube todos los archivos a la raiz y reinicia la app.
