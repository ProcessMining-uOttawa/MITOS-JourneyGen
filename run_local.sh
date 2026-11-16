#!/bin/sh
# Start the app on port 8612, since 8501 is often taken by another Streamlit app.
cd "$(dirname "$0")" || exit 1
exec streamlit run app.py --server.port 8612 "$@"
