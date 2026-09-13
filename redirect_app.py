"""The Tragic Algebra Analyzer moved: it grew into the Investor Toolkit.

This app exists only to hold the old subdomain and point visitors to the
new one. Deployed as its own Streamlit Cloud app with main file
`redirect_app.py`, subdomain `tragic-algebra-analyzer`.

Note: an automatic redirect is not possible from here — Streamlit runs
custom HTML in a sandboxed iframe that may not navigate the top page —
so this page promises nothing it cannot do: it shows the link.
"""
import streamlit as st

NEW_URL = "https://investor-toolkit.streamlit.app/"

st.set_page_config(page_title="Moved — Investor Toolkit", page_icon="🎯")

st.title("This tool has moved")
st.markdown(
    "The **Tragic Algebra Analyzer** grew into the **Investor Toolkit** — "
    "same method, same code repository, new home."
)
st.link_button("Open the Investor Toolkit →", NEW_URL, type="primary")
st.caption("Please update your bookmark to investor-toolkit.streamlit.app.")
