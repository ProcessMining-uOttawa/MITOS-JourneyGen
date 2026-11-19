"""Build the API documentation as HTML, from the docstrings, with pdoc.

    pip install pdoc
    python docs/build_api_docs.py [output folder, default docs/api]

Documents the library modules and the prototype methods. ``app.py`` is left
out: it is a Streamlit page, which runs as soon as it is imported.
"""
import os
from pathlib import Path
import sys

import pdoc
import pdoc.render

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MODULES = ["generate", "build_catalogue", "catalogue_changes", "bundles",
           "discover", "hawkes", "latent", "register", "score"]


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "api")
    sys.path[:0] = [ROOT, os.path.join(ROOT, "prototype")]
    import generate  # noqa: E402 - for the version, once the path is set
    pdoc.render.configure(
        docformat="restructuredtext",
        footer_text=f"MITOS-JourneyGen {generate.__version__}",
        logo="https://raw.githubusercontent.com/ProcessMining-uOttawa/MITOS-JourneyGen/"
             "main/assets/icon.png",
        logo_link="https://github.com/ProcessMining-uOttawa/MITOS-JourneyGen",
        search=True, show_source=True)
    pdoc.pdoc(*MODULES, output_directory=Path(out))
    # Paths computed from a module's location (HERE) would show the folder
    # the docs were built in.
    spellings = {ROOT, repr(ROOT)[1:-1], ROOT.replace("\\", "/"),
                 os.path.join(ROOT, "prototype"), repr(os.path.join(ROOT, "prototype"))[1:-1]}
    for page in Path(out).glob("*.html"):
        text = page.read_text(encoding="utf-8")
        for path in sorted(spellings, key=len, reverse=True):
            text = text.replace(path, "&lt;repository&gt;")
        page.write_text(text, encoding="utf-8")
    print("wrote", os.path.join(out, "index.html"))


if __name__ == "__main__":
    main()
