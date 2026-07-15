# PDF Tech Editor v9 - autenticacion obligatoria

La autenticacion ahora esta integrada directamente en `pdf_editor.py`. No depende de `auth.py`.

## Archivos obligatorios

- `pdf_editor.py`
- `users.toml`
- `requirements.txt`
- `runtime.txt`

Sube todos a la raiz y elimina el antiguo `auth.py` para evitar confusion.

## Streamlit Cloud

```text
Main file path: pdf_editor.py
Python: 3.12
```

Tras actualizar GitHub, usa **Manage app > Reboot app**. La version v9 invalida cualquier sesion autenticada de versiones anteriores y obliga a mostrar el formulario.
