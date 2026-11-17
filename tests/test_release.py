"""What a release and a hosted deployment rely on."""
import os
import re

import generate as g

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def test_the_changelog_opens_with_the_version_the_code_states():
    assert re.fullmatch(r"\d+\.\d+\.\d+", g.__version__)
    first = re.search(r"^## (\S+) - (\d{4}-\d{2}-\d{2})$", read("CHANGELOG.md"), re.M)
    assert first and first.group(1) == g.__version__


def test_the_committed_config_fixes_no_port():
    # a [server] port stops the app from starting on Streamlit Community Cloud
    config = "\n".join(line for line in read(".streamlit", "config.toml").splitlines()
                       if not line.lstrip().startswith("#"))
    assert "[server]" not in config and "port" not in config
    assert "--server.port 8612" in read("run_local.cmd")
    assert "--server.port 8612" in read("run_local.sh")


def test_a_release_moves_the_branch_the_deployed_app_watches():
    release = read(".github", "workflows", "release.yml")
    assert "types: [published]" in release and "refs/heads/release" in release
    assert "generate.py --version" in release          # a mistagged release is refused
    # and the monthly catalogue reaches that branch too
    assert "git push origin release" in read(".github", "workflows", "refresh-catalogue.yml")


def test_what_streamlit_cloud_installs_is_listed():
    wanted = {line.split(">=")[0].strip() for line in read("requirements.txt").splitlines()
              if line.strip() and not line.startswith("#")}
    assert {"streamlit", "pandas", "altair", "numpy", "scipy"} <= wanted
