from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

from pdf_editor import edit_pdf

st.set_page_config(
    page_title="Editor PDF Tecnico",
    page_icon="PDF",
    layout="centered",
)

st.title("Editor de documentacion tecnica PDF")
st.caption("Elimina hojas auxiliares y prepara cada tarea para impresion a doble cara.")

with st.expander("Reglas aplicadas", expanded=False):
    st.markdown(
        """
- Elimina hojas **P/N Pre-Draw Print**.
- Conserva Task Cards multipagina y Engineering Orders completas.
- Elimina caratulas de una pagina antes de EO, Daily Check o Weekly Check.
- Agrega una hoja blanca cuando una tarea termina con cantidad impar de paginas.
- Genera el nombre de salida con **F** al final.
- Incluye un archivo JSON de auditoria.
        """
    )

uploaded_files = st.file_uploader(
    "Selecciona uno o varios archivos PDF",
    type=["pdf"],
    accept_multiple_files=True,
    help="Los archivos se procesan temporalmente y no se guardan en el repositorio.",
)

if uploaded_files:
    st.info(f"Archivos seleccionados: {len(uploaded_files)}")

    if st.button("Procesar archivos", type="primary", use_container_width=True):
        results: list[tuple[str, bytes, str, bytes]] = []
        errors: list[str] = []
        progress = st.progress(0, text="Preparando...")

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            input_dir = work / "input"
            output_dir = work / "output"
            input_dir.mkdir()
            output_dir.mkdir()

            total = len(uploaded_files)
            for index, uploaded in enumerate(uploaded_files, start=1):
                try:
                    safe_name = Path(uploaded.name).name
                    if not safe_name.lower().endswith(".pdf"):
                        raise ValueError("El archivo no tiene extension PDF.")

                    input_path = input_dir / safe_name
                    input_path.write_bytes(uploaded.getvalue())
                    output_path = edit_pdf(input_path, output_dir, audit=True)
                    audit_path = output_path.with_suffix(".audit.json")
                    results.append(
                        (
                            output_path.name,
                            output_path.read_bytes(),
                            audit_path.name,
                            audit_path.read_bytes(),
                        )
                    )
                except Exception as exc:
                    errors.append(f"{uploaded.name}: {exc}")
                finally:
                    progress.progress(index / total, text=f"Procesando {index} de {total}")

        progress.empty()

        if results:
            st.success(f"Proceso terminado. Archivos correctos: {len(results)}")

            bundle = io.BytesIO()
            with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for pdf_name, pdf_data, audit_name, audit_data in results:
                    archive.writestr(pdf_name, pdf_data)
                    archive.writestr(audit_name, audit_data)
            bundle.seek(0)

            st.download_button(
                "Descargar todos los resultados en ZIP",
                data=bundle.getvalue(),
                file_name="pdf_editados.zip",
                mime="application/zip",
                use_container_width=True,
            )

            st.subheader("Descargas individuales")
            for pdf_name, pdf_data, audit_name, audit_data in results:
                with st.container(border=True):
                    st.write(f"**{pdf_name}**")
                    col_pdf, col_json = st.columns(2)
                    col_pdf.download_button(
                        "Descargar PDF",
                        data=pdf_data,
                        file_name=pdf_name,
                        mime="application/pdf",
                        key=f"pdf-{pdf_name}",
                        use_container_width=True,
                    )
                    col_json.download_button(
                        "Descargar auditoria",
                        data=audit_data,
                        file_name=audit_name,
                        mime="application/json",
                        key=f"json-{audit_name}",
                        use_container_width=True,
                    )

        if errors:
            st.error("Algunos archivos no se pudieron procesar.")
            for error in errors:
                st.code(error)
else:
    st.warning("Carga al menos un archivo PDF para comenzar.")

st.divider()
st.caption("Recomendacion: revisa el PDF y su auditoria antes de enviar la documentacion a impresion.")
