"""The Tragic Algebra Analyzer moved: it grew into the Investor Toolkit.

This app exists only to hold the old subdomain and send visitors to the
new one. Deployed as its own Streamlit Cloud app with main file
`redirect_app.py`, subdomain `tragic-algebra-analyzer`.
"""
import streamlit as st
import streamlit.components.v1 as components

NEW_URL = "https://investor-toolkit.streamlit.app/"

st.set_page_config(page_title="Moved — Investor Toolkit", page_icon="🎯")

st.title("This tool has moved")
st.markdown(
    f"The **Tragic Algebra Analyzer** grew into the **Investor Toolkit** — "
    f"seven pages now, same method, same code repository.\n\n"
    f"### 👉 [{NEW_URL.replace('https://', '').rstrip('/')}]({NEW_URL})\n\n"
    f"You should be redirected automatically in a few seconds. "
    f"Please update your bookmark."
)
components.html(
    f'<meta http-equiv="refresh" content="3;url={NEW_URL}">'
    f'<script>setTimeout(function() {{ window.top.location.href = "{NEW_URL}"; }}, 3000);</script>',
    height=0,
)
