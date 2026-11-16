"""MITOS-JourneyGen in the browser.

    streamlit run app.py

Edit the life-event scenarios against the MITOS catalogue, generate a log
with its ground truth, look at it, and score a method that searches it for
journeys. Everything the app does goes through ``generate.py`` and the
scripts in ``prototype/``; it adds no behaviour of its own.
"""
from __future__ import annotations

import copy
import gzip
import io
import json
import os
import sys
import tempfile
import time
import zipfile

import altair as alt
import pandas as pd
import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "prototype"))

import build_catalogue  # noqa: E402
import catalogue_changes  # noqa: E402
import generate as gen  # noqa: E402

MAX_CITIZENS = int(os.environ.get("MITOS_MAX_CITIZENS", "20000"))
VIEWS = ["Start", "Scenarios", "Population", "Generate", "Inspect", "Score a method",
         "Bundles to integrate", "Catalogue"]
MITOS = ":green-badge[From MITOS]"
YOURS = ":orange-badge[Your assumption]"

st.set_page_config(page_title="MITOS-JourneyGen", layout="wide")


if st.query_params.get("guide") == "methods":
    with open(os.path.join(HERE, "docs", "methods.md"), encoding="utf-8") as _f:
        st.markdown(_f.read())
    st.caption("This guide is docs/methods.md in the MITOS-JourneyGen repository. Close this "
               "tab to return to the app.")
    st.stop()


# ----------------------------------------------------------------- resources
@st.cache_resource(show_spinner="Loading the MITOS catalogue")
def catalogue() -> gen.Catalogue:
    return gen.Catalogue(os.path.join(HERE, "catalogue.json.gz"),
                         os.path.join(HERE, "translations_en.json"))


@st.cache_data(show_spinner=False)
def search_index() -> pd.DataFrame:
    cat = catalogue()
    rows = []
    for pid, p in cat.procedures.items():
        if p["steps"] or p["digital_steps"]:
            name = p["title_en"] or cat.extra_titles.get(pid) or p["title"]
            rows.append((pid, name, f"{name} {p['title']} {pid}".lower()))
    return pd.DataFrame(rows, columns=["id", "name", "text"])


def bundled() -> dict:
    with open(os.path.join(HERE, "scenarios.json"), encoding="utf-8") as f:
        return json.load(f)


def title(pid: str) -> str:
    cat = catalogue()
    if pid not in cat.procedures:
        return f"{pid} (not in the catalogue)"
    p = cat.procedures[pid]
    return p["title_en"] or cat.extra_titles.get(pid) or p["title"]


def shape(pid: str) -> str:
    p = catalogue().procedures.get(pid)
    if not p:
        return ""
    return f"{len(p['steps'])} steps" + (f", {len(p['digital_steps'])} digital"
                                         if p["digital_steps"] else "")


ss = st.session_state
if "cfg" not in ss:
    ss.cfg = bundled()
    ss.settings = {"citizens": 2000, "seed": 1, "rate_scale": 1.0, "format": "CSV",
                   "lang": "en"}
    ss.version = 0       # bumped whenever a table has to be rebuilt from the config
cfg = ss.cfg


def sticky(widget, label: str, key: str, default, **kwargs):
    """A widget that owns its value under ``key``.

    Giving a widget its current value as ``index`` or ``value`` on every run
    makes Streamlit see a new widget each time the value changes, and the
    first selection is then lost. Here the value lives in the session; it is
    put back when Streamlit has dropped it because the view was left.
    """
    if key not in ss:
        ss[key] = ss.get("keep:" + key, default)
    value = widget(label, key=key, **kwargs)
    ss["keep:" + key] = value
    return value


def forget(*keys: str) -> None:
    for key in keys:
        ss.pop(key, None)
        ss.pop("keep:" + key, None)


SETTING_KEYS = ("w_citizens", "w_seed", "w_rate_scale", "w_format", "w_lang")


def table(name: str, build, **kwargs) -> pd.DataFrame:
    """An editable table whose edits are written back to the config.

    The table is rebuilt from the config when its structure changes (version)
    or when Streamlit has dropped its state because the view was left.
    """
    key = f"{name}:{ss.version}"
    if key not in ss or f"base:{key}" not in ss:
        ss[f"base:{key}"] = build()
    return st.data_editor(ss[f"base:{key}"], key=key, hide_index=True,
                          use_container_width=True, **kwargs)


def pick_procedure(label: str, key: str) -> str | None:
    """Search the catalogue; returns the chosen procedure id, if any."""
    query = st.text_input(label, key=f"{key}:q",
                          placeholder="survivor's pension, διαβατήριο, 144400")
    if not query.strip():
        return None
    index = search_index()
    words = query.lower().split()
    hits = index[index.text.map(lambda t: all(w in t for w in words))].head(60)
    if hits.empty:
        st.caption("No procedure with steps matches.")
        return None
    return st.selectbox(f"{len(hits)} matches" + (" (first 60)" if len(hits) == 60 else ""),
                        hits.id, format_func=lambda i: f"{title(i)}  ·  {shape(i)}  ·  {i}",
                        key=f"{key}:hit")


def steps_table(pid: str) -> None:
    cat = catalogue()
    p = cat.procedures[pid]
    st.caption(f"{MITOS} [{pid} on MITOS](https://id.mitos.gov.gr/{pid}) · "
               f"owner: {p['org_en'] or p['org'] or 'not given'} · "
               f"validity of the result: {p['validity'] or 'not given'}")
    for label, steps in (("At the counter", p["steps"]), ("Digital", p["digital_steps"])):
        if not steps:
            continue
        st.markdown(f"**{label}**")
        st.dataframe(pd.DataFrame([{
            "step": s["n"], "name": cat.en(s["title"]), "who": cat.en(s["role"]),
            "how": cat.en(s["how"]), "shortest": s["min"] or "", "longest": s["max"] or "",
            "alternative after": s["parent"] if s["parent"] is not None else "",
            "ends the procedure": "yes" if s["exit"] else ""} for s in steps]),
            hide_index=True, use_container_width=True)


# ------------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("MITOS-JourneyGen")
    st.caption("Synthetic logs of citizens using Greek public services, with ground truth.")
    view = st.radio("View", VIEWS, key="view", label_visibility="collapsed",
                    format_func=lambda v: "Start here" if v == "Start" else v)
    st.divider()
    st.download_button(
        "Save project", json.dumps({"mitos_journeygen_project": 1, "config": cfg,
                                    "settings": ss.settings}, ensure_ascii=False, indent=1),
        file_name="journeygen_project.json", mime="application/json",
        help="The scenarios, population and generation settings, as one file.")
    up = st.file_uploader("Load project", type="json", key=f"load:{ss.version}")
    if up is not None:
        try:
            doc = json.load(up)
            loaded = doc["config"] if "config" in doc else doc
            for part in ("window", "population", "behaviour", "background", "scenarios"):
                loaded[part]
            ss.cfg = loaded
            ss.settings.update(doc.get("settings", {}))
            forget("scenario_pick", "editing", *SETTING_KEYS)
            ss.version += 1
            ss.pop("result", None)
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(f"That file is not a project or a scenarios file: {exc}")
    if st.button("Reset to the bundled scenarios"):
        ss.cfg = bundled()
        forget("scenario_pick", "editing")
        ss.version += 1
        ss.pop("result", None)
        st.rerun()
    st.divider()
    st.caption(f"Catalogue of {catalogue().meta['retrieved']}, "
               f"{len(catalogue().procedures):,} procedures. "
               "Procedure data: MITOS, CC BY-SA 4.0.")

problems = gen.check(catalogue(), cfg)
if problems and view != "Catalogue":
    st.error("These must be fixed before a log can be generated. The catalogue always follows "
             "the latest MITOS, so a procedure can disappear under a scenario.\n\n- "
             + "\n- ".join(problems))


# ------------------------------------------------------------------ guidance
GLOSSARY = """
- **Citizen.** A made-up person. Nothing in a log is real.
- **Procedure** (or service). One of the 4,416 administrative procedures published in MITOS,
  such as *Issuance of a Greek passport*.
- **Service instance.** One citizen using one procedure once. It is the *case* of the log;
  its events are the steps of the procedure.
- **Life event.** Something that happens to a citizen and makes them start several
  procedures: a death in the family, a birth, a job loss.
- **Scenario.** The recipe for one kind of life event: which procedures it starts, how
  likely each one is, in what order and after what wait.
- **Episode.** One life event of one citizen, with all the service instances it caused.
  Episodes are the ground truth.
- **Evidence.** A procedure that another procedure requires, according to MITOS: a birth
  allowance needs a family-status certificate. Evidence is added to an episode by itself.
- **Background.** Service instances that belong to no life event.
- **Journey.** What a method under test *believes* is an episode. Scoring compares journeys
  with episodes.
"""


def remarks(where: str) -> list:
    s = ss.settings
    return [r for r in gen.review(catalogue(), cfg, int(s["citizens"]), float(s["rate_scale"]))
            if r["where"] == where]


def show_remarks(rows: list) -> None:
    for r in rows:
        if r["level"] == "warning":
            st.warning(r["text"], icon=":material/warning:")
        else:
            st.info(r["text"], icon=":material/info:")


def listing(words: list) -> str:
    words = [str(w) for w in words]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def who(scen: dict) -> str:
    ages = [a for a in cfg["population"]["age_groups"] if scen["rate_per_year"].get(a, 0) > 0]
    if not ages:
        return "nobody (its rate is zero everywhere)"
    if len(ages) == len(cfg["population"]["age_groups"]):
        return "citizens of any age"
    return "citizens aged " + listing(ages)


def per_thousand(scen: dict) -> float:
    shares = cfg["population"]["age_groups"]
    total = sum(shares.values()) or 1.0
    return 1000 * sum(shares[a] / total * scen["rate_per_year"].get(a, 0) for a in shares)


def chance_words(p: float) -> str:
    if p >= 0.995:
        return "always"
    if p <= 0:
        return "never"
    return f"{p:.0%} of the time"


def story(scen: dict) -> str:
    """A scenario in sentences: what happens, how likely, when."""
    lines = []
    number = {x["id"]: n for n, x in enumerate(scen["procedures"], 1)}
    for n, x in enumerate(scen["procedures"], 1):
        lo, hi = x["delay_days"]
        wait = f"{lo:g} to {hi:g} days" if hi > lo else f"{lo:g} days"
        after = [a for a in x.get("after", []) if a in number]
        when = (f"{wait} after step {listing([number[a] for a in after])} succeeds" if after
                else f"{wait} after the life event")
        lines.append(f"{n}. **{title(x['id'])}**: {chance_words(x['p'])}, {when}.")
    return "\n".join(lines)


def evidence_of(scen: dict) -> dict:
    """Evidence procedures the register attaches to a scenario's procedures."""
    need: dict = {}
    for x in scen["procedures"]:
        for r in catalogue().procedures.get(x["id"], {}).get("requires", []):
            p = catalogue().procedures.get(r["id"])
            if p and (p["steps"] or p["digital_steps"]):
                need.setdefault(r["id"], {"by": [], "alt": True})
                need[r["id"]]["by"].append(x["id"])
                need[r["id"]]["alt"] &= r["alternative"]
    return need


def goto(view: str) -> None:
    ss.view = view


def delete_scenario(at: int) -> None:
    cfg["scenarios"].pop(at)
    forget("scenario_pick")
    ss.version += 1


# --------------------------------------------------------------------- start
def view_start() -> None:
    st.header("Start here")
    st.markdown(
        "This tool makes **synthetic logs of citizens using Greek public services**. Some "
        "services are used together because of a life event, such as a death in the family; "
        "others are used for unrelated reasons. The log says nothing about which is which. "
        "A separate file does, so a method that tries to find the services used together "
        "can be checked.\n\n"
        "Nothing in a log is real, except the procedures themselves: their steps, durations "
        "and the evidence they require come from [MITOS](https://mitos.gov.gr), the Greek "
        "National Register of Administrative Procedures.")
    st.subheader("Four steps to a first result")
    st.caption("Everything is already set up with eight life events. You do not need to "
               "change anything to begin.")
    steps = [
        ("1. Generate a log", "Press one button. With the settings as they are it takes a few "
         "seconds and gives about 77,000 events.", "Generate", "Go to Generate"),
        ("2. Look at it", "See how many episodes each life event produced, how long they last, "
         "and one citizen's services on a time line.", "Inspect", "Go to Inspect"),
        ("3. Score a method", "Upload the journeys your own method found, or run one of the "
         "methods included, and compare with the truth.", "Score a method", "Go to Score"),
        ("4. See what to integrate", "Which services are used together and in what order, "
         "with a log you can open in PM4Py-UCM.", "Bundles to integrate", "Go to Bundles"),
    ]
    for col, (head, text, view, label) in zip(st.columns(4), steps):
        with col.container(border=True):
            st.markdown(f"**{head}**")
            st.write(text)
            st.button(label, on_click=goto, args=(view,), use_container_width=True,
                      type="primary" if view == "Generate" else "secondary")
    st.subheader("Then make it yours")
    c1, c2 = st.columns(2)
    with c1.container(border=True):
        st.markdown("**Scenarios**")
        st.write("Read what each life event does, change it, or build a new one from any "
                 "MITOS procedure. The page explains itself as you go.")
        st.button("Go to Scenarios", on_click=goto, args=("Scenarios",),
                  use_container_width=True)
    with c2.container(border=True):
        st.markdown("**Population**")
        st.write("Who the citizens are, how they behave when they need a certificate, and how "
                 "much unrelated use surrounds the life events.")
        st.button("Go to Population", on_click=goto, args=("Population",),
                  use_container_width=True)
    st.subheader("What is set up now")
    st.dataframe(pd.DataFrame([{
        "life event": s["label"], "happens to": who(s),
        "per 1,000 citizens a year": round(per_thousand(s), 1),
        "procedures": len(s["procedures"]),
        "evidence procedures": len(evidence_of(s))} for s in cfg["scenarios"]]),
        hide_index=True, use_container_width=True)
    with st.expander("The words this tool uses"):
        st.markdown(GLOSSARY)


# ----------------------------------------------------------------- scenarios
def view_scenarios() -> None:
    st.header("Scenarios")
    st.markdown(
        "A **scenario** is the recipe for one kind of life event: which procedures a citizen "
        "starts when it happens, how likely each one is, and when. The log is built by letting "
        "these life events happen to citizens.")
    with st.expander("New here? How this page works", expanded=not ss.get("scenarios_seen")):
        st.markdown(
            "1. **Read first.** Pick a life event below. The page tells its story in plain "
            "sentences. You can generate a log without changing anything.\n"
            "2. **Edit only if you want to.** Switch on *Edit this scenario*. Four numbered "
            "steps follow: how often it happens, which procedures it starts, the evidence "
            "those procedures need, and a look at their steps.\n"
            "3. **Watch the remarks.** Yellow boxes point at values that look unrealistic or "
            "that will not do what you probably mean.\n\n"
            f"Throughout, {MITOS} marks what the register says and {YOURS} marks what you "
            "(or we) chose. Rates, chances and waits are assumptions, not measurements.")
    with st.expander("The words used here"):
        st.markdown(GLOSSARY)
    ss.scenarios_seen = True

    scenarios = cfg["scenarios"]
    st.subheader("All life events")
    st.dataframe(pd.DataFrame([{
        "life event": s["label"], "happens to": who(s),
        "per 1,000 citizens a year": round(per_thousand(s), 1),
        "procedures": len(s["procedures"]),
        "remarks": len(remarks(f"scenario:{n}"))} for n, s in enumerate(scenarios)],
        columns=["life event", "happens to", "per 1,000 citizens a year", "procedures",
                 "remarks"]), hide_index=True, use_container_width=True)

    left, right = st.columns([3, 1])
    with right:
        st.write("")
        if st.button("New life event", use_container_width=True,
                     help="Starts an empty scenario. You then add procedures by searching "
                          "the catalogue."):
            scenarios.append({"id": f"scenario_{len(scenarios) + 1}",
                              "label": f"New life event {len(scenarios) + 1}",
                              "rate_per_year": {a: 0.02 for a in cfg["population"]["age_groups"]},
                              "procedures": []})
            forget("scenario_pick", "editing")
            ss["keep:scenario_pick"] = len(scenarios) - 1
            ss["keep:editing"] = True
            ss.version += 1
            st.rerun()
    if not scenarios:
        st.info("There is no life event yet. Create one, or use *Reset to the bundled "
                "scenarios* in the sidebar.")
        return
    labels = [s["label"] for s in scenarios]
    if ss.get("scenario_pick", 0) >= len(scenarios) or ss.get(
            "keep:scenario_pick", 0) >= len(scenarios):
        forget("scenario_pick")
    with left:
        at = sticky(st.selectbox, "Pick a life event to read or edit", "scenario_pick", 0,
                    options=range(len(scenarios)), format_func=lambda i: labels[i])
    scen = scenarios[at]

    # ---- the story ---------------------------------------------------------
    with st.container(border=True):
        st.markdown(f"#### {scen['label']}")
        st.markdown(f"Happens to **{who(scen)}**, about **{per_thousand(scen):.1f} times per "
                    "1,000 citizens a year** over the whole population. When it does, the "
                    "citizen starts these procedures:")
        if scen["procedures"]:
            st.markdown(story(scen))
            need = evidence_of(scen)
            if need:
                st.caption(f"On top of these, MITOS says they require {len(need)} other "
                           f"procedures as evidence, for example *{title(next(iter(need)))}*. "
                           "The citizen requests them just before, unless they already hold a "
                           "valid one.")
        else:
            st.info("No procedure yet. Switch on *Edit this scenario* and add one in step 2.")
    show_remarks(remarks(f"scenario:{at}"))

    edit = sticky(st.toggle, "Edit this scenario", "editing", False,
                  help="Everything below is optional. The bundled scenarios work as they are.")
    if not edit:
        st.caption("Happy with it? Go to **Generate** in the sidebar.")
        return

    # ---- step 1 ------------------------------------------------------------
    st.subheader("Step 1. Name it and say how often it happens")
    c1, c2 = st.columns([3, 1])
    scen["label"] = c1.text_input("Name", scen["label"], key=f"label:{at}:{ss.version}")
    with c2:
        st.write("")
        st.button("Delete this life event", use_container_width=True,
                  on_click=delete_scenario, args=(at,))
    st.markdown(f"**Life events per citizen and year, by age group** {YOURS}")
    st.caption("0.05 means 5 of every 100 citizens of that age each year. Leave 0 for ages it "
               "does not concern. For orientation: Greece has roughly 12 deaths and 7 births per "
               "1,000 inhabitants a year, but one death concerns several relatives.")
    cols = st.columns(len(cfg["population"]["age_groups"]))
    for col, age in zip(cols, cfg["population"]["age_groups"]):
        rate = col.number_input(age, 0.0, 5.0, float(scen["rate_per_year"].get(age, 0.0)),
                                0.01, format="%.3f", key=f"rate:{at}:{age}:{ss.version}")
        if rate > 0:
            scen["rate_per_year"][age] = rate
        else:
            scen["rate_per_year"].pop(age, None)

    # ---- step 2 ------------------------------------------------------------
    st.subheader("Step 2. Choose the procedures it starts")
    st.caption(f"{MITOS} the procedures and how many steps they have.  {YOURS} the chance, "
               "the order and the wait.")
    with st.expander("Add a procedure from the catalogue", expanded=not scen["procedures"]):
        st.caption("Type a few words in English or Greek, or a MITOS id. Only procedures with "
                   "published steps are offered. A new procedure arrives with a chance of 50% "
                   "and a wait of 0 to 30 days, which you then adjust in the table.")
        pid = pick_procedure("Search the MITOS procedures", f"add:{at}")
        if pid and st.button("Add to this life event", type="primary"):
            if pid not in [x["id"] for x in scen["procedures"]]:
                scen["procedures"].append({"id": pid, "p": 0.5, "delay_days": [0, 30]})
                ss.version += 1
                st.rerun()
            st.warning("This life event already has that procedure.")

    def build():
        return pd.DataFrame([{
            "id": x["id"], "procedure": title(x["id"]), "in MITOS": shape(x["id"]),
            "chance": float(x["p"]), "after": ", ".join(x.get("after", [])),
            "wait from (days)": float(x["delay_days"][0]),
            "wait to (days)": float(x["delay_days"][1])} for x in scen["procedures"]],
            columns=["id", "procedure", "in MITOS", "chance", "after", "wait from (days)",
                     "wait to (days)"])

    if scen["procedures"]:
        edited = table(f"procs:{at}", build, num_rows="dynamic",
                       disabled=["id", "procedure", "in MITOS"],
                       column_config={
                           "chance": st.column_config.NumberColumn(
                               min_value=0.0, max_value=1.0, step=0.05,
                               help="Chance that the citizen starts this procedure. 1 = always."),
                           "after": st.column_config.TextColumn(
                               help="Leave empty if it follows the life event directly. "
                                    "Otherwise the id of the procedure it waits for (several "
                                    "ids separated by commas). That procedure must be listed "
                                    "above this one."),
                           "wait from (days)": st.column_config.NumberColumn(
                               min_value=0.0, help="Shortest wait before the citizen starts it."),
                           "wait to (days)": st.column_config.NumberColumn(
                               min_value=0.0, help="Longest wait. Most citizens act nearer "
                                                   "the shortest.")})
        kept = []
        known = [x["id"] for x in scen["procedures"]]
        for row in edited.to_dict("records"):
            pid = str(row["id"]) if row["id"] is not None else ""
            if pid not in known:
                continue  # a blank row added in the table; procedures are added by search
            lo = float(row["wait from (days)"] or 0)
            item = {"id": pid, "p": float(row["chance"] if row["chance"] is not None else 1.0),
                    "delay_days": [lo, max(lo, float(row["wait to (days)"] or 0))]}
            after = [a.strip() for a in str(row["after"] or "").split(",") if a.strip()]
            if after:
                item["after"] = after
            kept.append(item)
        scen["procedures"] = kept
        st.caption("Double-click a cell to change it. To remove a procedure, tick the box at "
                   "the left of its row and press Delete. The order of the rows is the order "
                   "in which the citizen considers them. A procedure that waits for another is "
                   "dropped when that one is skipped or fails.")

    # ---- step 3 ------------------------------------------------------------
    st.subheader("Step 3. Check the evidence (optional)")
    st.caption(f"{MITOS} which procedure requires which.  {YOURS} the chance that the citizen "
               "has to request it. You can leave this alone. Set a chance to 0 to remove "
               "evidence that applies only in special cases: MITOS lists a death certificate "
               "for a birth allowance, for instance.")
    b, over = cfg["behaviour"], scen.setdefault("evidence", {})
    need = evidence_of(scen)

    def build_ev():
        return pd.DataFrame([{
            "id": rid, "evidence": title(rid),
            "required by": ", ".join(title(i) for i in v["by"]),
            "chance": float(over.get(rid, b["alternative_evidence_probability"] if v["alt"]
                                     else b["evidence_probability"]))}
            for rid, v in need.items()], columns=["id", "evidence", "required by", "chance"])

    if need:
        ev = table(f"evidence:{at}:{len(need)}", build_ev,
                   disabled=["id", "evidence", "required by"],
                   column_config={"chance": st.column_config.NumberColumn(
                       min_value=0.0, max_value=1.0, step=0.05)})
        for row in ev.to_dict("records"):
            v = need[row["id"]]
            default = (b["alternative_evidence_probability"] if v["alt"]
                       else b["evidence_probability"])
            if row["chance"] is not None and abs(row["chance"] - default) > 1e-9:
                over[row["id"]] = float(row["chance"])
            else:
                over.pop(row["id"], None)
    else:
        st.caption("None of these procedures requires another one.")

    # ---- step 4 ------------------------------------------------------------
    st.subheader("Step 4. Look inside a procedure (optional)")
    st.caption("The steps an instance will go through, as MITOS publishes them. Where a step "
               "has alternatives, one is chosen; a failure alternative is chosen rarely.")
    ids = [x["id"] for x in scen["procedures"] if x["id"] in catalogue().procedures]
    if ids:
        steps_table(st.selectbox("Procedure", ids, format_func=title,
                                 key=f"steps:{at}:{ss.version}"))
    st.caption("Done? Go to **Generate** in the sidebar.")


# ---------------------------------------------------------------- population
def view_population() -> None:
    st.header("Population and behaviour")
    st.markdown("Who the citizens are, how they behave when a procedure needs a certificate, "
                "and how much unrelated use surrounds the life events. The values set here "
                "work as they are; change them to make the problem easier or harder.")
    st.caption(f"{YOURS} everything on this page.")
    show_remarks(remarks("population"))
    pop, b, win, bg = cfg["population"], cfg["behaviour"], cfg["window"], cfg["background"]
    c1, c2 = st.columns(2)
    win["start"] = str(c1.date_input("Life events start from",
                                     pd.Timestamp(win["start"]).date()))
    win["years"] = c2.number_input(
        "for this many years", 1, 10, int(win["years"]),
        help="Life events start inside this window. Their procedures run to the end, so the "
             "log extends past it.")

    st.subheader("Citizens")
    c1, c2 = st.columns(2)
    with c1:
        ages = table("ages", lambda: pd.DataFrame({
            "age group": list(pop["age_groups"]),
            "share": [float(v) for v in pop["age_groups"].values()],
            "uses the digital channel": [float(pop["digital_propensity"][a])
                                         for a in pop["age_groups"]]}),
            disabled=["age group"])
        for row in ages.to_dict("records"):
            pop["age_groups"][row["age group"]] = max(float(row["share"] or 0), 0.0)
            pop["digital_propensity"][row["age group"]] = min(max(float(
                row["uses the digital channel"] or 0), 0.0), 1.0)
        st.caption("Shares are relative: they need not add up to 1. *Uses the digital "
                   "channel* is the chance that a citizen of that age takes the digital path "
                   "of a procedure that has one. Scenario rates are given per age group, so "
                   "the groups themselves are fixed.")
    with c2:
        regions = table("regions", lambda: pd.DataFrame({
            "region": list(pop["regions"]),
            "weight": [float(v) for v in pop["regions"].values()]}), num_rows="dynamic")
        got = {str(r["region"]): float(r["weight"] or 0) for r in regions.to_dict("records")
               if r["region"] and (r["weight"] or 0) > 0}
        if got:
            pop["regions"] = got
        st.caption("Weights are relative, here populations in thousands. Regions only label "
                   "citizens; they do not change behaviour.")

    st.subheader("How citizens behave")
    st.caption("These decide how messy the log is. Lower reuse and higher failure make more "
               "repeated requests; they do not change which services go together.")
    show_remarks(remarks("behaviour"))
    fields = [
        ("evidence_probability", "Chance that required evidence must be requested", 0.0, 1.0),
        ("alternative_evidence_probability", "Same, for evidence MITOS marks as alternative",
         0.0, 1.0),
        ("reuse_evidence_probability", "Chance of reusing a valid result already held", 0.0, 1.0),
        ("evidence_validity_days", "Validity of a result where MITOS gives none, days", 1.0,
         3650.0),
        ("abandon_probability", "Chance that an instance is abandoned part-way", 0.0, 1.0),
        ("negative_branch_weight", "Weight of a failure branch against a normal one", 0.0, 1.0),
        ("retry_probability", "Chance of retrying after a failure", 0.0, 1.0),
    ]
    cols = st.columns(2)
    for n, (key, label, lo, hi) in enumerate(fields):
        b[key] = cols[n % 2].number_input(label, lo, hi, float(b[key]),
                                          0.01 if hi <= 1 else 1.0, key=f"b:{key}:{ss.version}")
    b["max_attempts"] = cols[1].number_input("Attempts at most", 1, 10, int(b["max_attempts"]),
                                             key=f"b:max:{ss.version}")

    st.subheader("Background use")
    st.caption("Unrelated one-off uses of popular services. They are often the same services "
               "that scenarios pull in as evidence, which is what makes the problem hard. "
               "More background makes bundles harder to see; none makes the problem trivial.")
    show_remarks(remarks("background"))
    bg["rate_per_year"] = st.number_input("Uses per citizen and year", 0.0, 50.0,
                                          float(bg["rate_per_year"]), 0.1)
    services = table(f"bg:{len(bg['services'])}", lambda: pd.DataFrame({
        "id": list(bg["services"]), "service": [title(i) for i in bg["services"]],
        "weight": [float(v) for v in bg["services"].values()]}),
        num_rows="dynamic", disabled=["id", "service"])
    got = {str(r["id"]): float(r["weight"] or 0) for r in services.to_dict("records")
           if r["id"] in bg["services"] and (r["weight"] or 0) > 0}
    if got:
        bg["services"] = got
    with st.expander("Add a background service"):
        pid = pick_procedure("Search the MITOS procedures", "bgadd")
        if pid and st.button("Add as background"):
            bg["services"].setdefault(pid, 1)
            ss.version += 1
            st.rerun()


# ------------------------------------------------------------------ generate
class Stopped(Exception):
    pass


@st.cache_data(show_spinner="Estimating from a short trial run")
def estimated(cfg_json: str, citizens: int, rate_scale: float) -> dict:
    return gen.estimate(catalogue(), json.loads(cfg_json), citizens, rate_scale)


def duration(seconds: float) -> str:
    if seconds < 90:
        return f"{max(seconds, 1):.0f} seconds"
    return f"{seconds / 60:.0f} minutes"


def view_generate() -> None:
    st.header("Generate")
    st.markdown("Choose how large a log to make and press **Generate**. The defaults give a "
                "small log in a few seconds, which is the right place to start.")
    s = ss.settings
    c1, c2, c3 = st.columns(3)
    s["citizens"] = sticky(c1.number_input, "Citizens", "w_citizens",
                           min(int(s["citizens"]), MAX_CITIZENS), min_value=10,
                           max_value=MAX_CITIZENS, step=500,
                           help=f"At most {MAX_CITIZENS:,} here. Larger logs: run "
                                "generate.py on your own machine.")
    s["seed"] = sticky(c2.number_input, "Seed", "w_seed", int(s["seed"]), min_value=0,
                       max_value=10 ** 9,
                       help="The same seed, settings and catalogue give the same log.")
    s["rate_scale"] = sticky(
        c3.number_input, "Multiply life-event rates by", "w_rate_scale",
        float(s["rate_scale"]), min_value=0.1, max_value=20.0, step=0.5,
        help="Above 1, the episodes of one citizen overlap more often: a stress test.")
    c1, c2 = st.columns(2)
    formats = ["CSV", "XES, gzipped", "CSV and XES"]
    s["format"] = sticky(c1.radio, "Log format", "w_format",
                         s["format"] if s["format"] in formats else formats[0],
                         options=formats, horizontal=True,
                         help="XES holds a trace per service instance; every event has its "
                                "completion in time:timestamp and its start in start_timestamp. "
                                "The ground truth is CSV either way.")
    langs = {"en": "English", "el": "Greek", "both": "English and Greek"}
    s["lang"] = sticky(c2.radio, "Labels", "w_lang", s["lang"], options=list(langs),
                       format_func=langs.get, horizontal=True,
                       help="English falls back to Greek where MITOS has no English.")
    if problems:
        return
    est = estimated(json.dumps(cfg, sort_keys=True), int(s["citizens"]), float(s["rate_scale"]))
    size = {"CSV": est["csv_mb"], "XES, gzipped": est["xes_gz_mb"],
            "CSV and XES": est["csv_mb"] + est["xes_gz_mb"]}[s["format"]]
    long_run = est["seconds"] > 20
    st.info(f"Expect about {est['events']:,} events in {est['instances']:,} service instances "
            f"and {est['episodes']:,} episodes, a log of about {size:,.0f} MB, in about "
            f"{duration(est['seconds'])} on this machine. This is an estimate from a short "
            "trial run." + (" Progress is shown while it runs, and it can be stopped."
                            if long_run else ""))

    todo = [(r, s2["label"]) for n, s2 in enumerate(cfg["scenarios"])
            for r in remarks(f"scenario:{n}")]
    todo += [(r, name) for where, name in (("population", "Population"),
                                           ("behaviour", "Behaviour"),
                                           ("background", "Background use"))
             for r in remarks(where)]
    scenario_inst = max(est["instances"] - est["background_instances"], 1)
    ratio = est["background_instances"] / scenario_inst
    if ratio > 8:
        todo.append(({"level": "warning", "text": f"About {ratio:.0f} background instances "
                      "for every instance of a life event. Bundles will be very hard to see; "
                      "lower the background rate or raise the life-event rates."}, "The mix"))
    elif 0 < ratio < 0.25:
        todo.append(({"level": "note", "text": "Background use is less than a quarter of the "
                      "life-event instances. Real logs are mostly unrelated use, so this is "
                      "an easy problem."}, "The mix"))
    if est["shared_instances"] < 50:
        todo.append(({"level": "note", "text": f"Expect only about {est['shared_instances']} "
                      "instances shared between episodes. That is enough to generate and "
                      "inspect, too few to judge how well a method finds shared instances; "
                      "for that use more citizens."}, "Shared instances"))
    for r in remarks("run"):
        todo.append((r, "This run"))
    warnings = [x for x in todo if x[0]["level"] == "warning"]
    if todo:
        with st.expander(f"Before you generate: {len(warnings)} warnings and "
                         f"{len(todo) - len(warnings)} notes on the values chosen",
                         expanded=bool(warnings)):
            st.caption("None of these stops a run. Warnings point at values that will "
                       "distort the log; notes are things to be aware of.")
            for r, name in sorted(todo, key=lambda x: x[0]["level"] != "warning"):
                (st.warning if r["level"] == "warning" else st.info)(
                    f"**{name}.** {r['text']}",
                    icon=":material/warning:" if r["level"] == "warning"
                    else ":material/info:")
    c1, c2, _ = st.columns([1, 1, 4])
    go = c1.button("Generate", type="primary", use_container_width=True)
    stop = c2.button("Stop", use_container_width=True,
                     help="Stops a run in progress. Nothing is kept from a stopped run.")
    # One slot for the progress of a run and then for its result, so that a
    # new run replaces what the previous one left on the page.
    area = st.empty()
    if stop:
        area.warning("Stopped. No log was produced.")
        return
    if go:
        with area.container():
            run_generation(est)
    if "result" in ss:
        r = ss.result
        m = r["manifest"]
        box = area.container()
        box.success(f"{m['events']:,} events, {m['instances']:,} service instances by "
                   f"{m['citizens']:,} citizens, {m['episodes']:,} episodes, generated in "
                   f"{duration(r['seconds'])}.")
        box.download_button("Download the dataset (zip)", r["zip"],
                           file_name=f"journeygen_{m['arguments']['citizens']}c_"
                                     f"seed{m['arguments']['seed']}.zip",
                           mime="application/zip", type="primary")
        box.caption("The zip holds " + ", ".join(r["files"]) + ". Keep the truth files away "
                   "from the method under test. See Inspect and Score a method.")


def run_generation(est: dict) -> None:
    s = ss.settings
    for stale in ("result", "scores", "found", "kinds"):
        ss.pop(stale, None)
    fmt = {"CSV": "csv", "XES, gzipped": "xes", "CSV and XES": "both"}[s["format"]]
    stages = ["Simulating citizens", "Sorting instances", "Writing the log and the ground truth",
              "Packing the download"]
    title_at = st.empty()
    bar = st.progress(0.0)
    line = st.empty()
    m1, m2, m3 = st.columns(3)
    boxes = [m1.empty(), m2.empty(), m3.empty()]
    listing = st.empty()

    def show_stage(n: int) -> None:
        title_at.subheader(stages[n])
        listing.markdown("\n".join(
            f"- {':material/check:' if i < n else ':material/progress_activity:' if i == n else ':material/circle:'} {name}"  # noqa: E501
            for i, name in enumerate(stages)))

    began = time.perf_counter()
    simulation_share = 0.45  # of the whole run; the rest is sorting and writing
    show_stage(0)

    def progress(done, total, instances, episodes, events):
        elapsed = time.perf_counter() - began
        frac = done / total
        if done:
            whole = elapsed / (frac * simulation_share)
            left = f"about {duration(max(whole - elapsed, 1))} left"
        else:
            left = f"about {duration(est['seconds'])} expected"
        bar.progress(min(frac * simulation_share, 1.0))
        line.markdown(f"**{done:,} of {total:,} citizens** · {elapsed:.0f} s elapsed · {left}")
        boxes[0].metric("Instances so far", f"{instances:,}")
        boxes[1].metric("Episodes so far", f"{episodes:,}")
        boxes[2].metric("Events so far", f"{events:,}")
        if done == total:
            show_stage(1)

    g = gen.Generator(catalogue(), copy.deepcopy(cfg), int(s["seed"]), float(s["rate_scale"]))
    g.run(int(s["citizens"]), progress)
    show_stage(2)
    sim_seconds = time.perf_counter() - began

    def writing(frac: float) -> None:
        elapsed = time.perf_counter() - began
        bar.progress(min(simulation_share + 0.5 * frac, 0.95))
        left = ""
        if frac > 0.05:
            left = f" · about {duration(max((elapsed - sim_seconds) * (1 - frac) / frac, 1))} left"
        line.markdown(f"**Writing the log, {100 * frac:.0f}%** · {elapsed:.0f} s elapsed{left}")

    with tempfile.TemporaryDirectory() as tmp:
        manifest = g.write(tmp, s["lang"], {
            "citizens": int(s["citizens"]), "seed": int(s["seed"]), "lang": s["lang"],
            "rate_scale": float(s["rate_scale"]), "format": fmt, "scenarios": "scenarios.json"},
            fmt, True, writing)
        with open(os.path.join(tmp, "scenarios.json"), "w", encoding="utf-8",
                  newline="\n") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        inst = pd.read_csv(os.path.join(tmp, "truth_instances.csv"), dtype=str).fillna("")
        eps = pd.read_csv(os.path.join(tmp, "truth_episodes.csv"), dtype=str).fillna("")
        show_stage(3)
        bar.progress(0.96)
        line.markdown(f"**Packing the download** · {time.perf_counter() - began:.0f} s elapsed")
        files = sorted(os.listdir(tmp))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
            for name in files:
                # a gzipped file gains nothing from being deflated again
                z.write(os.path.join(tmp, name), name,
                        zipfile.ZIP_STORED if name.endswith(".gz") else zipfile.ZIP_DEFLATED)
    ss.result = {"manifest": manifest, "zip": buffer.getvalue(), "inst": inst, "eps": eps,
                 "files": files, "seconds": time.perf_counter() - began}


# ------------------------------------------------------------------- inspect
def need_result() -> bool:
    if "result" not in ss:
        st.info("Generate a log first.")
        return False
    return True


def view_inspect() -> None:
    st.header("Inspect")
    if not need_result():
        return
    r = ss.result
    m, inst, eps = r["manifest"], r["inst"], r["eps"]
    labels = {s["id"]: s["label"] for s in cfg["scenarios"]}
    cols = st.columns(5)
    cols[0].metric("Events", f"{m['events']:,}")
    cols[1].metric("Service instances", f"{m['instances']:,}")
    cols[2].metric("Citizens", f"{m['citizens']:,}")
    cols[3].metric("Episodes", f"{m['episodes']:,}")
    shared = m["instances_shared_between_episodes"] + m["background_instances_reused_by_an_episode"]
    cols[4].metric("Shared instances", f"{shared:,}",
                   help="Instances that belong to more than one episode, or background uses "
                        "that an episode reused.")
    st.caption(f"{m['instances_by_origin']['scenario']:,} instances belong to episodes and "
               f"{m['instances_by_origin']['background']:,} are background. Outcomes: "
               + ", ".join(f"{v:,} {k}" for k, v in m["outcomes"].items()) + ".")

    eps = eps.assign(span=eps.span_days.astype(float), scenario=eps.scenario.map(labels).fillna(
        eps.scenario))
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Episodes per scenario")
        counts = eps.groupby("scenario").size().reset_index(name="episodes")
        st.altair_chart(alt.Chart(counts).mark_bar().encode(
            x=alt.X("episodes:Q", title="Episodes"),
            y=alt.Y("scenario:N", sort="-x", title=None),
            tooltip=["scenario", "episodes"]), use_container_width=True)
    with c2:
        st.subheader("How long an episode lasts")
        box = eps.groupby("scenario").span.quantile([0.1, 0.5, 0.9]).unstack().reset_index()
        box.columns = ["scenario", "p10", "median", "p90"]
        base = alt.Chart(box).encode(y=alt.Y("scenario:N", sort=alt.SortField("median"),
                                             title=None))
        st.altair_chart(
            base.mark_rule(strokeWidth=3).encode(
                x=alt.X("p10:Q", title="Days from first start to last end (10% to 90%, "
                                       "dot = median)"), x2="p90:Q")
            + base.mark_point(filled=True, size=70, color="black").encode(
                x="median:Q", tooltip=["scenario", "p10", "median", "p90"]),
            use_container_width=True)
        st.caption("No single time window fits every scenario.")

    st.subheader("One citizen's timeline")
    per = inst[inst.episode_id != ""].groupby("citizen_id").episode_id.nunique()
    order = per.sort_values(ascending=False).index[:300].tolist()
    if not order:
        st.caption("No citizen has an episode in this log.")
        return
    who = st.selectbox("Citizen", order, format_func=lambda c: f"{c}: {per[c]} episodes, "
                       f"{(inst.citizen_id == c).sum()} instances")
    mine = inst[inst.citizen_id == who].copy()
    mine["start"], mine["end"] = pd.to_datetime(mine.start), pd.to_datetime(mine.end)
    # an instance of a few minutes would be invisible on a scale of months
    width = max((mine.end.max() - mine.start.min()) / 150, pd.Timedelta(hours=6))
    mine["shown_end"] = mine[["end"]].assign(m=mine.start + width).max(axis=1)
    scen_of = dict(zip(eps.episode_id, eps.scenario))
    mine["belongs to"] = [f"{e}: {scen_of.get(e, '')}" if e else "background"
                          for e in mine.episode_id]
    mine["also in"] = mine.shared_with
    mine["shared"] = (mine.shared_with != "").map({True: "shared", False: "not shared"})
    chart = alt.Chart(mine).mark_bar(height=14).encode(
        x=alt.X("start:T", title=None), x2="shown_end:T",
        y=alt.Y("service:N", sort=alt.SortField("start"), title=None,
                axis=alt.Axis(labelLimit=330, labelOverlap=False)),
        color=alt.Color("belongs to:N", legend=alt.Legend(orient="bottom", columns=2,
                                                          labelLimit=400)),
        stroke=alt.Stroke("shared:N", scale=alt.Scale(domain=["shared", "not shared"],
                                                      range=["black", "transparent"]),
                          legend=None),
        strokeWidth=alt.value(2),
        tooltip=["case_id", "service", "start", "end", "role", "outcome", "belongs to",
                 "also in"]).properties(height=alt.Step(22))
    st.altair_chart(chart, use_container_width=True)
    st.caption("Each bar is one service instance, coloured by the true episode it belongs to. "
               "A black outline marks an instance that another episode reused. A method sees "
               "none of the colours: finding them is the task.")


# --------------------------------------------------------------------- score
def view_score() -> None:
    st.header("Score a method")
    if not need_result():
        return
    import register as rg
    import score as sc

    st.markdown(
        "A **method** reads only the log and says which service instances belong together; "
        "each group is a *journey*. Here its journeys are compared with the true episodes.")
    with st.expander("How to read the scores"):
        st.markdown(
            "- **Precision.** Of the pairs of instances the method put in the same journey, "
            "the share that truly share an episode. Low precision: it glues unrelated things "
            "together.\n"
            "- **Recall.** Of the pairs that truly share an episode, the share the method put "
            "together. Low recall: it splits episodes.\n"
            "- **F1.** One number combining the two.\n"
            "- **Episodes whole.** True episodes that fit entirely inside one journey.\n"
            "- **Journeys clean.** Journeys that hold nothing from outside one episode.\n"
            "- **Shared found.** Some instances truly belong to two episodes, because a "
            "certificate was reused. This is the share of them the method placed in two "
            "journeys. A method that puts every instance in one journey scores 0.\n\n"
            "A **fixed gap** is the simplest method: a citizen's instances form one journey "
            "until they have been inactive for that many days. It is the baseline to beat.")

    inst_all = ss.result["inst"]
    inst = inst_all.assign(start=pd.to_datetime(inst_all.start), end=pd.to_datetime(inst_all.end))
    truth, shared = sc.truth_groups(inst)
    found = ss.setdefault("found", {})     # label -> journeys of each method run so far
    scores = ss.setdefault("scores", {})
    if not any(k.startswith("fixed gap") for k in scores):
        with st.spinner("Scoring the fixed-gap baselines"):
            for days in (7, 30, 90, 180):
                scores[f"fixed gap, {days} days"] = sc.score(truth, sc.fixed_gap(inst, days),
                                                             shared)

    st.subheader("Your method")
    st.markdown("Upload its answer as a CSV with the columns `case_id` and `journey_id`. An "
                "instance may appear in several journeys.")
    up = st.file_uploader("Journeys found by your method", type="csv")
    if up is not None:
        try:
            mine = pd.read_csv(up, dtype={"case_id": str})[["case_id", "journey_id"]]
            unknown = ~mine.case_id.isin(inst.case_id)
            if unknown.any():
                st.warning(f"{unknown.sum():,} rows name a case_id that is not in this log; "
                           "they are ignored. Was the file made from another log?")
            missing = (~inst.case_id.isin(mine.case_id)).sum()
            if missing:
                st.info(f"{missing:,} instances of the log are in no journey of this file. "
                        "They count as journeys of their own.")
            found[f"uploaded: {up.name}"] = mine[~unknown]
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not read that file: {exc}")

    st.subheader("Methods included")
    c1, c2 = st.columns([3, 1])
    c1.caption("Three methods that read only the log, and one that uses what MITOS says. The "
               "guide describes each one: what it assumes, how it decides, what it is good "
               "and bad at, and a table comparing them all.")
    c2.link_button("Open the method guide", "?guide=methods", use_container_width=True,
                   help="Opens in a new tab. The same text is docs/methods.md in the "
                        "repository.")
    log = inst[["case_id", "citizen_id", "service_id", "service", "start", "end"]]
    n = len(log)
    c1, c2, c3, c4 = st.columns(4)
    if c1.button("Pair mixture", use_container_width=True,
                 help=f"Learns a time window for every pair of services. About "
                      f"{duration(max(2, n / 2200))} on this log."):
        import discover
        with st.status("Fitting a delay mixture for every pair of services", expanded=False):
            out = discover.discover(log)
        found["pair mixture, overlapping"] = out[2]
        found["pair mixture, partition"] = out[3]
    if c2.button("Hawkes process", use_container_width=True,
                 help=f"Each instance is spontaneous or triggered by an earlier one. About "
                      f"{duration(max(2, n / 2500))} on this log."):
        import hawkes
        with st.status("Fitting the Hawkes process, 150 iterations at most", expanded=False):
            out = hawkes.fit(log)
        found["Hawkes, overlapping"] = out[1]
        found["Hawkes, partition"] = out[2][["case_id", "journey_id"]]
    if c3.button("Latent life events", use_container_width=True,
                 help=f"Learns the kinds of life event themselves. The slowest: about "
                      f"{duration(max(5, n / 750))} on this log."):
        import latent
        with st.status("Fitting the latent life-event model, 120 iterations",
                       expanded=False):
            out = latent.fit(log)
        found["latent life events, overlapping"] = out[1]
        ss.kinds = out[0]
    if c4.button("Register only", use_container_width=True,
                 help="Nothing learnt: links an instance to the evidence MITOS says it "
                      "requires. A few seconds."):
        with st.status("Linking instances by the register's evidence rules", expanded=False):
            reg_log, reg = register_tools(rg, log)
            out = rg.register_only(reg_log, rg.uses(reg_log, reg))
        found["register only, overlapping"] = out[0]
        found["register only, partition"] = out[1]

    add = sticky(
        st.toggle, "Also show every method with the register's links added", "add_register",
        False,
        help="Where a journey holds a procedure but none of the evidence MITOS says it "
             "requires, and the citizen holds a valid earlier one, that instance is added to "
             "the journey as well. This is what finds shared instances.")
    for label, journeys in found.items():
        if label not in scores:
            scores[label] = sc.score(truth, journeys, shared)
        plus = f"{label} + register"
        if add and plus not in scores and not label.startswith("register only"):
            reg_log, reg = register_tools(rg, log)
            scores[plus] = sc.score(truth, rg.add_register(journeys, reg_log, reg)[0], shared)

    st.subheader("Scores")
    shown = {k: v for k, v in scores.items() if add or not k.endswith("+ register")}
    table_ = pd.DataFrame(shown).T
    count = table_.pop("in 2+ journeys").astype(int)
    table_ = (table_ * 100).round(1)
    table_["in 2+ journeys"] = count
    table_ = table_.sort_values("F1", ascending=False)
    st.dataframe(table_, use_container_width=True, column_config={
        "precision": st.column_config.NumberColumn(format="%.1f%%"),
        "recall": st.column_config.NumberColumn(format="%.1f%%"),
        "F1": st.column_config.NumberColumn(format="%.1f%%"),
        "whole": st.column_config.NumberColumn("episodes whole", format="%.1f%%"),
        "clean": st.column_config.NumberColumn("journeys clean", format="%.1f%%"),
        "shared found": st.column_config.NumberColumn(format="%.1f%%"),
        "in 2+ journeys": st.column_config.NumberColumn(
            help="Instances the method placed in more than one journey.")})

    if len(shared) < 50:
        st.warning(
            f"This log has only {len(shared):,} shared instances, so *shared found* is a "
            "percentage of very few and moves a lot by chance. To judge it, generate a larger "
            "log first (20,000 citizens give a few hundred). Multiplying the life-event rates "
            "gives more still, at the price of unrealistically busy citizens.",
            icon=":material/warning:")
    else:
        st.caption(f"This log has {len(shared):,} shared instances.")
    if any("register" in k for k in shown):
        st.info(
            "Read the register rows with care. The generator builds its bundles from the same "
            "evidence links, so the register fits these logs better than it would fit real "
            "ones, where not every requirement is exercised and not every dependency is "
            "written down.", icon=":material/info:")
    if "kinds" in ss:
        with st.expander("Bundles the latent model found"):
            st.caption("Each kind is a bundle the model discovered: the services it brings, "
                       "how many per life event, and when.")
            st.dataframe(ss.kinds, hide_index=True, use_container_width=True)


def register_tools(rg, log: pd.DataFrame):
    """The log and the register in the form the register rules use, built once
    per generated log."""
    if "register_tools" not in ss.result:
        ss.result["register_tools"] = (rg.Log(log), rg.Register(
            os.path.join(HERE, "catalogue.json.gz")))
    return ss.result["register_tools"]


# ------------------------------------------------------------------- bundles
def view_bundles() -> None:
    st.header("Bundles to integrate")
    if not need_result():
        return
    import bundles as bd

    st.markdown(
        "This is the answer the whole exercise is for: **which services citizens use "
        "together, in what order, and which would gain most from being integrated.** "
        "It changes the level of the log. Until here a case was one service instance and an "
        "activity was a step. Here **a case is a journey and an activity is a service**.")
    inst = ss.result["inst"]
    found = ss.get("found", {})
    truth_label = "The true episodes (what a perfect method would find)"
    sources = [truth_label] + [k for k in found if "partition" not in k]
    c1, c2 = st.columns([3, 1])
    if ss.get("bundle_source") not in sources or ss.get("keep:bundle_source") not in sources:
        forget("bundle_source")
    source = sticky(
        c1.selectbox, "Journeys to use", "bundle_source", truth_label, options=sources,
        help="Methods you have run in Score a method are listed here. On real data there are "
             "no true episodes: a method's journeys are all you would have.")
    min_size = c2.number_input("Smallest journey", 2, 10, 2,
                               help="A journey of a single instance is not a bundle.")
    if source == truth_label:
        members = bd.true_journeys(inst)
        st.caption("Using the ground truth. Journeys are labelled with their real life event. "
                   "This is the best possible answer, not what a method would give.")
    else:
        members = found[source]
        if "kinds" in ss and source.startswith("latent"):
            st.caption("The latent model's journeys. Run it in Score a method to see the "
                       "kinds it found.")
        st.caption(f"Using the journeys of *{source}*. They carry no life-event label, so "
                   "everything is one kind; compare with the true episodes to see what the "
                   "method changes.")
    log = journey_log_cached(source, int(min_size), len(members))
    if log.empty:
        st.info("No journey has that many instances.")
        return
    cols = st.columns(4)
    cols[0].metric("Journeys", f"{log['case:concept:name'].nunique():,}")
    cols[1].metric("Service instances", f"{len(log):,}")
    cols[2].metric("Services", f"{log['concept:name'].nunique():,}")
    cols[3].metric("Kinds", f"{log['case:kind'].nunique():,}")

    st.subheader("Pairs of services used together")
    st.caption("Each row: citizens who use the first service go on to use the second, in the "
               "same journey. Sorted by how many journeys do so. These are the candidates for "
               "integration: one application instead of two, or the second service fetching "
               "the first one's result by itself.")
    cand = bd.candidates(log)
    only_cross = st.toggle("Only pairs that cross organisations",
                           help="Pairs inside one organisation are easier to integrate; pairs "
                                "across organisations are where citizens carry documents "
                                "between counters.")
    shown = cand[cand.different_organisations] if only_cross else cand
    st.dataframe(shown.head(200), hide_index=True, use_container_width=True, column_config={
        "journeys": st.column_config.NumberColumn(
            help="Journeys where the first service is followed, at any distance, by the "
                 "second."),
        "b_follows_a": st.column_config.NumberColumn(
            "second follows first", format="percent",
            help="Of the journeys of this kind with the first service, the share where the "
                 "second follows."),
        "lift": st.column_config.NumberColumn(
            format="%.1f", help="Above 1: the two go together more than the second one's "
                                "popularity explains. Near 1: probably coincidence."),
        "median_wait_days": st.column_config.NumberColumn(
            "median wait (days)", help="From the end of the first to the start of the second."),
        "citizen_days_waiting": st.column_config.NumberColumn(
            "citizen-days waiting", help="That wait added up over all these journeys.")})
    if cand.empty:
        st.caption("No pair occurs in 10 journeys or more. Generate a larger log.")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Most common sequences")
        st.caption("Whole journeys, as the services in order. A sequence many citizens "
                   "follow is a bundle that could be offered as one service.")
        st.dataframe(bd.variants(log, 8), hide_index=True, use_container_width=True,
                     column_config={"share_of_kind": st.column_config.NumberColumn(
                         "share of kind", format="percent")})
    with c2:
        st.subheader("Requested again")
        st.caption("Services a citizen requests more than once inside one journey. Each "
                   "extra request is a duplication that integration would remove.")
        st.dataframe(bd.repeated(log), hide_index=True, use_container_width=True,
                     column_config={"share_repeating": st.column_config.NumberColumn(
                         "share repeating", format="percent")})

    st.subheader("Take it to PM4Py-UCM")
    st.markdown(
        "The journey-level log is an ordinary event log, so PM4Py-UCM mines it into a map of "
        "the services citizens chain together.\n\n"
        "1. Download the XES below and load it in PM4Py-UCM.\n"
        "2. It is read as an interval log: each service has a start and an end, so service "
        "and waiting times are available. The organisations come in as roles and appear as "
        "components, which shows where a journey crosses from one body to another.\n"
        "3. Every journey carries its kind as the case attribute `kind`. Mine one model per "
        "kind with a model family partitioned on it, or filter to one kind. Mining all kinds "
        "into one model gives a map too complicated to read.\n"
        "4. Raise the noise threshold above 0 if the map is cluttered: rare orders of "
        "otherwise unordered services add many paths.")
    c1, c2, c3 = st.columns(3)
    c1.download_button("Journey log (XES, gzipped)", bd.to_xes(log),
                       file_name="journey_log.xes.gz", mime="application/gzip", type="primary",
                       use_container_width=True)
    c2.download_button("Journey log (CSV)", bd.to_csv(log), file_name="journey_log.csv",
                       mime="text/csv", use_container_width=True,
                       help="A CSV import reads one timestamp per row; use the XES to keep "
                            "both the start and the end.")
    c3.download_button("Integration candidates (CSV)",
                       cand.to_csv(index=False).encode("utf-8"),
                       file_name="integration_candidates.csv", mime="text/csv",
                       use_container_width=True)
    st.caption("Remember what the log is: synthetic. The pairs above say what this generator "
               "was told plus what the method recovered, not what Greek citizens do.")


def journey_log_cached(source: str, min_size: int, n_members: int) -> pd.DataFrame:
    """The journey-level log for one source of journeys, built once per log."""
    import bundles as bd

    cache = ss.result.setdefault("journey_logs", {})
    key = (source, min_size, n_members)
    if key not in cache:
        inst = ss.result["inst"]
        if source.startswith("The true episodes"):
            members = bd.true_journeys(inst)
            names = {s["id"]: s["label"] for s in cfg["scenarios"]}
            members = members.assign(kind=members.kind.map(names).fillna(members.kind))
        else:
            members = ss.found[source]
        cache[key] = bd.journey_log(inst, members, bd.organisations(inst.service_id), min_size)
    return cache[key]


# ----------------------------------------------------------------- catalogue
def runs_locally() -> bool:
    """A refresh writes the catalogue to disk and sends MITOS some 5,000
    requests, so it is offered only where the app runs on the user's machine."""
    if os.environ.get("MITOS_ALLOW_REFRESH"):
        return os.environ["MITOS_ALLOW_REFRESH"] not in ("0", "false", "no")
    try:
        host = st.context.headers.get("Host", "")
    except Exception:  # noqa: BLE001
        return False
    return host.rsplit(":", 1)[0] in ("localhost", "127.0.0.1", "[::1]")


def refresh_from_mitos() -> None:
    if not runs_locally():
        return
    with st.expander("Refresh from MITOS now"):
        st.caption("Downloads every procedure again (about 5,000 small documents, a quarter "
                   "of an hour) and replaces the catalogue if its content differs. The "
                   "scheduled job does the same every Monday; use this to be ahead of it.")
        c1, c2, _ = st.columns([1, 1, 4])
        go = c1.button("Refresh", type="primary", use_container_width=True)
        stop = c2.button("Stop", key="stop_refresh", use_container_width=True,
                         help="Stops a download in progress. What was fetched is kept for "
                              "the rest of the day, so a new refresh resumes.")
        area = st.empty()
        if stop:
            area.warning("Stopped. The catalogue was not changed.")
            return
        if "refreshed" in ss:
            area.success(ss.pop("refreshed"))
        if not go:
            return
        # Fresh every day, so that a refresh never reuses an older download.
        cache = os.environ.get("MITOS_CACHE") or os.path.join(
            tempfile.gettempdir(), f"mitos_raw_{pd.Timestamp.today():%Y-%m-%d}")
        with area.container():
            stage = st.empty()
            bar = st.progress(0.0)
            line = st.empty()
            began, shown = time.perf_counter(), [0.0]
            stage.subheader("Reading the lists of procedures")

            def progress(done, total, failed):
                now = time.perf_counter() - began
                if not total or (now - shown[0] < 0.3 and done < total):
                    return
                shown[0] = now
                stage.subheader("Downloading procedures")
                bar.progress(0.97 * done / total)
                left = now * (total - done) / max(done, 1)
                line.markdown(f"**{done:,} of {total:,} documents** · {now:.0f} s elapsed · "
                              f"about {duration(max(left, 1))} left"
                              + (f" · {failed} failed" if failed else ""))

            failed = build_catalogue.download(cache, progress)
            if failed:
                area.error(f"{len(failed)} documents could not be fetched, so the catalogue "
                           "was not changed: a missing procedure would look withdrawn. Press "
                           "Refresh again; what was fetched is kept.")
                return
            stage.subheader("Comparing with the current catalogue")
            bar.progress(0.98)
            new = build_catalogue.build(cache)
            target = os.path.join(HERE, "catalogue.json.gz")
            old = catalogue_changes.load(target)
            r = catalogue_changes.compare(old, new, cfg)
        if catalogue_changes.too_many_withdrawn(r, old):
            area.error(f"{len(r['withdrawn']):,} procedures are missing from the download. "
                       "That looks like a failure at MITOS's end, so the catalogue was not "
                       "changed.")
            return
        if not r["any"]:
            area.success("MITOS content is unchanged. The catalogue stays as it is.")
            return
        with gzip.open(target, "wt", encoding="utf-8") as f:
            json.dump(new, f, ensure_ascii=False, separators=(",", ":"))
        catalogue_changes.record(r, os.path.join(HERE, "catalogue_changes.json"),
                                 os.path.join(HERE, "CATALOGUE_CHANGES.md"))
        catalogue.clear()
        search_index.clear()
        estimated.clear()
        ss.pop("result", None)
        ss.version += 1
        ss.refreshed = (f"Catalogue replaced: {len(r['new'])} new, {len(r['withdrawn'])} "
                        f"withdrawn, {len(r['changed'])} changed. Details are below.")
        st.rerun()


def view_catalogue() -> None:
    st.header("Catalogue")
    cat = catalogue()
    linked = sum(1 for p in cat.procedures.values() if p["requires"])
    c1, c2, c3 = st.columns(3)
    c1.metric("Snapshot of MITOS", cat.meta["retrieved"])
    c2.metric("Procedures", f"{len(cat.procedures):,}")
    c3.metric("With evidence links", f"{linked:,}")
    st.caption("A scheduled job downloads MITOS again every Monday and replaces the catalogue "
               "when its content differs. The app always uses the latest; each refresh is "
               "tagged `catalogue-<date>` in the repository, so an older one can be checked "
               "out to reproduce an earlier dataset.")
    refresh_from_mitos()
    path = os.path.join(HERE, "catalogue_changes.json")
    if not os.path.exists(path):
        st.info("No refresh has changed the catalogue since this snapshot.")
    else:
        with open(path, encoding="utf-8") as f:
            r = json.load(f)
        st.subheader(f"What changed on {r['to']}")
        st.write(f"Compared with {r['from']}: {len(r['new'])} new, {len(r['withdrawn'])} "
                 f"withdrawn and {len(r['changed'])} changed procedures.")
        if r["scenarios"]:
            st.markdown("**Bundled scenarios affected**")
            st.dataframe(pd.DataFrame([{
                "scenario": x["scenario"], "id": x["id"], "procedure": x["title"],
                "what changed": "; ".join(x["what"]),
                "needs attention": "yes" if x["action"] else ""} for x in r["scenarios"]]),
                hide_index=True, use_container_width=True)
        else:
            st.write("No bundled scenario is affected.")
        for key, head in (("new", "New"), ("withdrawn", "Withdrawn"), ("changed", "Changed")):
            if r[key]:
                with st.expander(f"{head}: {len(r[key])}"):
                    st.dataframe(pd.DataFrame([{
                        "id": x["id"], "procedure": x["title"],
                        **({"what changed": "; ".join(x["what"])} if "what" in x else {})}
                        for x in r[key]]), hide_index=True, use_container_width=True)
    history = os.path.join(HERE, "CATALOGUE_CHANGES.md")
    if os.path.exists(history):
        with st.expander("Earlier refreshes"):
            with open(history, encoding="utf-8") as f:
                st.markdown(f.read())
    with st.expander("Look up a procedure"):
        pid = pick_procedure("Search the MITOS procedures", "lookup")
        if pid:
            steps_table(pid)


{"Start": view_start, "Scenarios": view_scenarios, "Population": view_population,
 "Generate": view_generate, "Bundles to integrate": view_bundles,
 "Inspect": view_inspect, "Score a method": view_score, "Catalogue": view_catalogue}[view]()
