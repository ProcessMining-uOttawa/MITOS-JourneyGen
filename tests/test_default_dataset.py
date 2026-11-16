"""The dataset in ``out/`` is what the command line gives for seed 1, on every
platform, as long as the catalogue is the one it was made with."""
import json
import os

import pytest

import generate as g

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_out_is_reproduced_by_seed_1(tmp_path):
    with open(os.path.join(ROOT, "out", "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    cat = g.Catalogue(os.path.join(ROOT, "catalogue.json.gz"),
                      os.path.join(ROOT, "translations_en.json"))
    if cat.meta["retrieved"] != manifest["catalogue"]["retrieved"]:
        pytest.skip("the catalogue has been refreshed since out/ was generated; check out "
                    f"the tag catalogue-{manifest['catalogue']['retrieved']} to reproduce it")
    with open(os.path.join(ROOT, "scenarios.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    args = manifest["arguments"]
    run = g.Generator(cat, cfg, args["seed"], args["rate_scale"])
    run.run(args["citizens"])
    run.write(str(tmp_path), args["lang"], args)
    for name in ("events.csv", "truth_instances.csv", "truth_episodes.csv", "services.csv"):
        with open(os.path.join(ROOT, "out", name), "rb") as f:
            kept = f.read()
        assert (tmp_path / name).read_bytes() == kept, name
