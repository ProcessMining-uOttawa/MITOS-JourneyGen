"""Score discovered journeys against the generator's ground truth.

    python score.py ../out

Scores every journey file found in the folder (written by discover.py,
hawkes.py, latent.py and register.py) and fixed-gap baselines. Journeys may overlap: an instance can be
in several journeys, as it can be in several true episodes.

* pairs: over all pairs of instances, precision and recall of "share a
  journey" against "share a true episode";
* whole: share of true episodes that fit entirely inside one journey;
* clean: share of the journeys touching an episode that fit entirely inside
  one episode;
* shared found: share of the truly shared instances (in two or more episodes,
  or a background instance an episode reused) that are in two or more
  journeys.
"""
from __future__ import annotations

import argparse
import os

import pandas as pd

def journey_files(folder: str) -> dict:
    """Label -> file, for every journeys*.csv in the folder."""
    out = {}
    for name in sorted(os.listdir(folder)):
        if not (name.startswith("journeys") and name.endswith(".csv")):
            continue
        stem = name[len("journeys"):-4].strip("_") or "mixture"
        if stem == "partition" or stem.startswith("+"):
            stem = "mixture_" + stem if stem == "partition" else "mixture" + stem
        method = stem.replace("_partition", "").replace("+register", "")
        label = method + (" + register" if "+register" in stem else "") + (
            ", partition" if "partition" in stem else ", overlapping")
        out[label] = name
    order = {"mixture": 0, "hawkes": 1, "latent": 2, "register": 3}
    return dict(sorted(out.items(), key=lambda kv: (order.get(kv[0].split(",")[0].split(" +")[0], 9),
                                                    "+ register" in kv[0],
                                                    "partition" in kv[0])))


def together(members: pd.DataFrame, group: str) -> pd.DataFrame:
    """Unordered pairs of instances that share a group."""
    m = members.merge(members, on=group, suffixes=("_a", "_b"))
    return m.loc[m.case_id_a < m.case_id_b, ["case_id_a", "case_id_b"]].drop_duplicates()


def contained(inner: pd.DataFrame, inner_key: str, outer: pd.DataFrame,
              outer_key: str) -> pd.Series:
    """For each inner group, whether some outer group holds all of it."""
    size = inner.groupby(inner_key).size()
    hits = inner.merge(outer, on="case_id").groupby([inner_key, outer_key]).size()
    best = hits.groupby(level=0).max().reindex(size.index, fill_value=0)
    return best == size


def score(truth: pd.DataFrame, found: pd.DataFrame, shared: set) -> dict:
    """How well ``found`` journeys match the ``truth`` episodes.

    Precision, recall and F1 over pairs of instances placed together; the
    share of episodes kept whole in one journey; the share of journeys that
    lie within one episode (clean); the share of ``shared`` instances placed
    in two or more journeys; and how many instances are in two or more."""
    t_pairs, f_pairs = together(truth, "episode"), together(found, "journey_id")
    hit = len(t_pairs.merge(f_pairs))
    prec, rec = hit / max(len(f_pairs), 1), hit / max(len(t_pairs), 1)
    touching = found[found.journey_id.isin(found.journey_id[found.case_id.isin(truth.case_id)])]
    multi = found.groupby("case_id").journey_id.nunique()
    return {"precision": prec, "recall": rec,
            "F1": 2 * prec * rec / max(prec + rec, 1e-9),
            "whole": contained(truth, "episode", found, "journey_id").mean(),
            "clean": contained(touching, "journey_id", truth, "episode").mean(),
            "shared found": multi.reindex(list(shared)).fillna(0).gt(1).mean() if shared
            else float("nan"),
            "in 2+ journeys": int((multi > 1).sum())}


def fixed_gap(inst: pd.DataFrame, days: float) -> pd.DataFrame:
    """The baseline: a citizen's next instance starts a new journey when it
    starts more than ``days`` after everything before it has ended."""
    t = inst.sort_values(["citizen_id", "start"])
    prev_end = t.groupby("citizen_id").end.cummax().groupby(t.citizen_id).shift()
    gap = (t.start - prev_end).dt.total_seconds() / 86400
    return pd.DataFrame({"case_id": t.case_id.to_numpy(),
                         "journey_id": (gap.isna() | (gap > days)).cumsum().to_numpy()})


def truth_groups(inst: pd.DataFrame):
    """(case_id, episode) memberships and the set of truly shared instances,
    from the rows of ``truth_instances.csv``."""
    eps = (inst.episode_id + ";" + inst.shared_with).str.strip(";").str.split(";")
    truth = pd.DataFrame({"case_id": inst.case_id, "episode": eps}).explode("episode")
    truth = truth[truth.episode != ""]
    n = truth.groupby("case_id").episode.nunique()
    shared = set(n.index[n > 1]) | set(inst.case_id[(inst.episode_id == "")
                                                     & (inst.shared_with != "")])
    return truth, shared


def load_truth(folder: str):
    """The instances of the truth file in ``folder``, the true episode of
    each, and the instances that belong to more than one."""
    inst = pd.read_csv(os.path.join(folder, "truth_instances.csv"), dtype=str).fillna("")
    inst["start"], inst["end"] = pd.to_datetime(inst.start), pd.to_datetime(inst.end)
    truth, shared = truth_groups(inst)
    return inst, truth, shared


def main() -> None:
    """The command line: ``python prototype/score.py <folder>``."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder", help="folder holding truth_instances.csv and the "
                                   "journeys*.csv files of the methods")
    folder = ap.parse_args().folder
    inst, truth, shared = load_truth(folder)
    print(f"{len(inst)} instances, {truth.episode.nunique()} episodes, "
          f"{len(shared)} truly shared instances")
    rows = {}
    files = journey_files(folder)
    for label, name in files.items():
        rows[label] = score(truth[["case_id", "episode"]], pd.read_csv(
            os.path.join(folder, name), dtype={"case_id": str})[["case_id", "journey_id"]],
            shared)
    for days in (7, 30, 90, 180):
        rows[f"fixed gap {days} d"] = score(truth[["case_id", "episode"]],
                                            fixed_gap(inst, days), shared)
    out = pd.DataFrame(rows).T
    count = out.pop("in 2+ journeys").astype(int)
    out = (out * 100).round(1)
    out["in 2+ journeys"] = count
    print(out.to_string())
    own = inst[inst.episode_id != ""].drop_duplicates("episode_id")
    scen = own.set_index("episode_id").scenario
    whole = {}
    for label, name in files.items():
        if "overlapping" in label and ("+" not in label or "hawkes" in label):
            found = pd.read_csv(os.path.join(folder, name),
                                dtype={"case_id": str})[["case_id", "journey_id"]]
            ok = contained(truth[["case_id", "episode"]], "episode", found, "journey_id")
            whole[label.split(",")[0]] = (ok.groupby(scen.reindex(ok.index).to_numpy())
                                          .mean() * 100).round(0)
    if whole:
        print("\nepisodes kept whole, %, overlapping variants")
        print(pd.DataFrame(whole).to_string())


if __name__ == "__main__":
    main()
