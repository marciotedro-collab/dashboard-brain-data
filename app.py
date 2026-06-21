"""
app.py — Dashboard PKM (Fase 1: Roadmap + Pulso de avance)
Lee directo de Cloudflare R2. No toca el pipeline de captura/clasificacion.
"""

import json
import boto3
import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="PKM Dashboard", page_icon="🧠", layout="wide")

# ─── Espejo de src/config.py — si agregas/cambias proyectos alla, actualiza aqui tambien ───
PROYECTOS = {
    "P-DM": {"nombre": "Delivery Mercado", "fases": ["Exploracion", "Validacion", "Construccion", "Escala"]},
    "P-RL": {"nombre": "Reventa Lacteos", "fases": ["Exploracion", "Validacion", "Construccion", "Escala"]},
    "P-TC": {"nombre": "Tech Competitor Tracker", "fases": ["Exploracion", "Validacion", "Construccion", "Escala"]},
    "P-AL": {"nombre": "Optimizacion Almacen", "fases": ["Exploracion", "Validacion", "Construccion", "Escala"]},
}

REGISTRO_KEY = "00_Inbox/Registro-Procesamiento.md"
FASES_KEY = "_meta/fases-proyectos.json"
BUCKET = st.secrets["R2_BUCKET_NAME"]


@st.cache_resource
def conectar_r2():
    return boto3.client(
        "s3",
        endpoint_url=st.secrets["R2_ENDPOINT"],
        aws_access_key_id=st.secrets["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=st.secrets["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def leer_texto(r2, key: str, default: str = "") -> str:
    try:
        obj = r2.get_object(Bucket=BUCKET, Key=key)
        return obj["Body"].read().decode("utf-8")
    except r2.exceptions.NoSuchKey:
        return default
    except Exception:
        return default


def leer_fases_actuales(r2) -> dict:
    try:
        return json.loads(leer_texto(r2, FASES_KEY, "{}"))
    except json.JSONDecodeError:
        return {}


def guardar_fases_actuales(r2, fases: dict) -> None:
    r2.put_object(
        Bucket=BUCKET,
        Key=FASES_KEY,
        Body=json.dumps(fases, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


def parsear_registro(texto: str) -> pd.DataFrame:
    """Convierte la tabla markdown del registro central en un DataFrame."""
    filas = []
    for linea in texto.split("\n"):
        linea = linea.strip()
        if not linea.startswith("|") or linea.startswith("|---") or linea.startswith("| Fecha"):
            continue
        partes = [p.strip() for p in linea.strip("|").split("|")]
        if len(partes) == 5:
            filas.append({
                "fecha": partes[0], "tipo": partes[1],
                "origen": partes[2], "destino": partes[3], "nota": partes[4],
            })
    df = pd.DataFrame(filas)
    if not df.empty:
        df["fecha_dt"] = pd.to_datetime(df["fecha"], errors="coerce")
    return df


def proyecto_de_destino(destino: str):
    for codigo in PROYECTOS:
        if codigo in destino:
            return codigo
    return None


# ─── Carga de datos ─────────────────────────────────────────────────────────
r2 = conectar_r2()
df = parsear_registro(leer_texto(r2, REGISTRO_KEY, ""))
fases_actuales = leer_fases_actuales(r2)

st.title("🧠 PKM Dashboard")
st.caption("Panorama de tus proyectos")

tab_roadmap, tab_pulso = st.tabs(["🗺️ Roadmap", "📊 Pulso de avance"])

# ─── TAB 1: ROADMAP ──────────────────────────────────────────────────────────
with tab_roadmap:
    st.subheader("¿En qué fase está cada proyecto?")
    cambios = {}

    for codigo, info in PROYECTOS.items():
        fases = info["fases"]
        actual = fases_actuales.get(codigo, fases[0])
        if actual not in fases:
            actual = fases[0]
        idx_actual = fases.index(actual)

        col1, col2 = st.columns([3, 2])
        with col1:
            st.markdown(f"**{info['nombre']}** (`{codigo}`)")
            st.progress((idx_actual + 1) / len(fases), text=f"{actual} ({idx_actual + 1}/{len(fases)})")
        with col2:
            nueva = st.selectbox("Fase", fases, index=idx_actual, key=f"fase_{codigo}", label_visibility="collapsed")
            if nueva != actual:
                cambios[codigo] = nueva

    if cambios:
        fases_actuales.update(cambios)
        guardar_fases_actuales(r2, fases_actuales)
        st.success("Fase actualizada ✓")
        st.rerun()

    st.divider()
    st.subheader("Línea de tiempo")

    if df.empty:
        st.info("El registro central está vacío todavía.")
    else:
        filas_gantt = []
        for codigo, info in PROYECTOS.items():
            notas = df[df["destino"].str.contains(codigo, na=False)]
            if notas.empty:
                continue
            inicio, fin = notas["fecha_dt"].min(), notas["fecha_dt"].max()
            filas_gantt.append({
                "Proyecto": info["nombre"],
                "Inicio": inicio,
                "Fin": fin if fin > inicio else inicio + pd.Timedelta(days=1),
                "Fase actual": fases_actuales.get(codigo, info["fases"][0]),
            })

        if filas_gantt:
            fig = px.timeline(pd.DataFrame(filas_gantt), x_start="Inicio", x_end="Fin", y="Proyecto", color="Fase actual")
            fig.update_yaxes(autorange="reversed")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Aún no hay notas registradas para ningún proyecto.")

# ─── TAB 2: PULSO DE AVANCE ───────────────────────────────────────────────────
with tab_pulso:
    if df.empty:
        st.info("Todavía no hay datos. Procesa algunas notas primero.")
    else:
        col1, col2, col3 = st.columns(3)
        col1.metric("Notas totales", len(df))
        ultima = df["fecha_dt"].max()
        dias = (pd.Timestamp.now() - ultima).days if pd.notna(ultima) else None
        col2.metric("Última nota", ultima.strftime("%d %b") if pd.notna(ultima) else "—")
        col3.metric("Días sin procesar", dias if dias is not None else "—")

        st.divider()
        st.subheader("Notas por proyecto")
        df["proyecto"] = df["destino"].apply(proyecto_de_destino)
        conteo = df["proyecto"].value_counts().reset_index()
        conteo.columns = ["Proyecto", "Notas"]
        conteo["Proyecto"] = conteo["Proyecto"].map(lambda c: PROYECTOS.get(c, {}).get("nombre", c))
        if not conteo.empty:
            st.plotly_chart(px.bar(conteo, x="Proyecto", y="Notas"), use_container_width=True)

        st.divider()
        st.subheader("Constancia de captura")
        df_validas = df.dropna(subset=["fecha_dt"])
        if not df_validas.empty:
            por_dia = df_validas.groupby(df_validas["fecha_dt"].dt.date).size().reset_index(name="notas")
            st.plotly_chart(
                px.bar(por_dia, x="fecha_dt", y="notas", labels={"fecha_dt": "Fecha", "notas": "Notas procesadas"}),
                use_container_width=True,
            )
