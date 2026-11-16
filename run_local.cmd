@echo off
rem Start the app on port 8612, since 8501 is often taken by another Streamlit app.
cd /d "%~dp0"
streamlit run app.py --server.port 8612 %*
