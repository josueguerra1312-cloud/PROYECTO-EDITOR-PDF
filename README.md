# PDF Tech Editor v5

Correccion de dos problemas de Streamlit:

1. Los resultados ahora se conservan en `st.session_state`, por lo que no desaparecen al pulsar Descargar.
2. El ZIP multiple se construye una sola vez, usa Zip64 y mantiene los bytes despues de cada rerun.
3. La lectura PDF usa `strict=False` y valida el PDF generado antes de habilitar su descarga.

## Despliegue

Carga todos los archivos en la raiz del repositorio y configura:

```text
Main file path: pdf_editor.py
Python: 3.12
```

Luego usa **Manage app > Reboot app**.
