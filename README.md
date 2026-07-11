# PDF Tech Editor - repositorio plano

Este repositorio no requiere crear carpetas dentro de GitHub. Todos los archivos del proyecto se colocan directamente en la raiz.

## Archivos

- `pdf_editor.py`: programa completo.
- `requirements.txt`: dependencia necesaria.
- `.gitignore`: evita publicar PDF tecnicos y resultados.
- `LICENSE`: licencia MIT.

## Instalacion

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Uso

Procesar un PDF:

```bash
python pdf_editor.py "N533VL.pdf" -o salida
```

Procesar todos los PDF de una carpeta:

```bash
python pdf_editor.py entrada -o salida
```

El resultado se guarda como `NOMBRE F.pdf` y se genera un archivo `NOMBRE F.audit.json`.

## Logica incluida

- Elimina hojas `P/N Pre-Draw Print`.
- Conserva Task Cards multipagina.
- Conserva Engineering Orders completas.
- Elimina caratulas de una pagina antes de EO, Daily Check o Weekly Check.
- Elimina anexos no reconocidos.
- Agrega una hoja blanca al final de cada tarea impar.
- No vuelve a procesar archivos cuyo nombre ya termina en ` F.pdf`.

## Subir a GitHub sin crear carpetas

1. Cree un repositorio vacio en GitHub.
2. Abra el repositorio.
3. Seleccione **Add file > Upload files**.
4. Arrastre directamente estos cinco archivos.
5. Seleccione **Commit changes**.

No cargue PDF tecnicos reales en un repositorio publico.
