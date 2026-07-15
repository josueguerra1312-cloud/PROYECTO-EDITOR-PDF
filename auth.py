from __future__ import annotations
import hashlib
import hmac
import time
import tomllib
from pathlib import Path
import streamlit as st

USERS_FILE = Path(__file__).with_name("users.toml")
MAX_ATTEMPTS = 5
LOCK_SECONDS = 60


def _load_users() -> dict:
    with USERS_FILE.open("rb") as fh:
        return tomllib.load(fh).get("users", {})


def _valid(username: str, password: str):
    user = _load_users().get(username)
    if not user:
        # Realiza trabajo comparable para no revelar usuarios por tiempo.
        hashlib.pbkdf2_hmac("sha256", password.encode(), b"0" * 16, 310000)
        return None
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(user["salt"]), int(user["iterations"])
    ).hex()
    return user if hmac.compare_digest(digest, user["password_hash"]) else None


def require_login() -> None:
    if st.session_state.get("authenticated"):
        with st.sidebar:
            st.write(f"Usuario: **{st.session_state['username']}**")
            if st.button("Cerrar sesion", use_container_width=True):
                for key in ("authenticated", "username", "role", "login_attempts", "locked_until"):
                    st.session_state.pop(key, None)
                st.rerun()
        return

    st.title("Acceso al editor PDF")
    locked_until = float(st.session_state.get("locked_until", 0))
    remaining = int(locked_until - time.time())
    if remaining > 0:
        st.error(f"Acceso bloqueado temporalmente. Intenta de nuevo en {remaining} segundos.")
        st.stop()

    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("Usuario")
        password = st.text_input("Contrasena", type="password")
        submitted = st.form_submit_button("Ingresar", type="primary", use_container_width=True)

    if submitted:
        user = _valid(username.strip(), password)
        if user:
            st.session_state.authenticated = True
            st.session_state.username = username.strip()
            st.session_state.role = user.get("role", "usuario")
            st.session_state.login_attempts = 0
            st.rerun()
        attempts = int(st.session_state.get("login_attempts", 0)) + 1
        st.session_state.login_attempts = attempts
        if attempts >= MAX_ATTEMPTS:
            st.session_state.locked_until = time.time() + LOCK_SECONDS
            st.session_state.login_attempts = 0
            st.error("Demasiados intentos. Acceso bloqueado durante 60 segundos.")
        else:
            st.error("Usuario o contrasena incorrectos.")
    st.stop()
