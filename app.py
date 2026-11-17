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

VIEWS = ["Start", "Scenarios", "Population", "Generate", "Inspect", "Score a method",
         "Bundles to integrate", "Catalogue"]
MITOS = ":green-badge[From MITOS]"
YOURS = ":orange-badge[Your assumption]"
ICONS = {
    "Start": ":material/flag:", "Scenarios": ":material/route:",
    "Population": ":material/groups:", "Generate": ":material/play_circle:",
    "Inspect": ":material/monitoring:", "Score a method": ":material/target:",
    "Bundles to integrate": ":material/hub:", "Catalogue": ":material/menu_book:",
}
# One colour per kind of recommendation (by its number) and per level of
# effort. Colour never stands alone: the number and the words stay.
KIND_COLOURS = {"1": "#7c3aed", "2": "#2563eb", "3": "#0891b2", "4": "#db2777",
                "5": "#a16207", "6": "#64748b"}
EFFORT_COLOURS = {"1": "#16a34a", "2": "#ca8a04", "3": "#ea580c", "4": "#dc2626"}
LIFE_PALETTE = ["#4c78a8", "#f58518", "#54a24b", "#e45756", "#72b7b2", "#b279a2",
                "#9d755d", "#eeca3b", "#ff9da6", "#bab0ac"]

st.set_page_config(page_title="MITOS-JourneyGen", layout="wide",
                   page_icon=os.path.join(HERE, "assets", "icon.png"))


def dark() -> bool:
    """Whether the viewer has the dark theme on (Settings in the app menu)."""
    try:
        return st.context.theme.type == "dark"
    except Exception:  # noqa: BLE001 - no theme outside a browser session
        return False


st.logo(os.path.join(HERE, "assets", "logo_dark.svg" if dark() else "logo_light.svg"),
        icon_image=os.path.join(HERE, "assets", "icon.svg"), size="large")


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


def accent() -> str:
    """The accent colour, lighter on a dark background so that it still reads."""
    return "#5AA9F0" if dark() else "#0D5EAF"


def life_colours() -> dict:
    """The colour of each life event, the same in every chart and table."""
    out = {}
    for n, scen in enumerate(ss.cfg["scenarios"]):
        out[scen["label"]] = out[scen["id"]] = LIFE_PALETTE[n % len(LIFE_PALETTE)]
    return out


def table(name: str, build, **kwargs) -> pd.DataFrame:
    """An editable table whose edits are written back to the config.

    The table is rebuilt from the config when its structure changes (version)
    or when Streamlit has dropped its state because the view was left.
    """
    key = f"{name}:{ss.version}"
    if key not in ss or f"base:{key}" not in ss:
        ss[f"base:{key}"] = build()
    return st.data_editor(ss[f"base:{key}"], key=key, hide_index=True,
                          width="stretch", **kwargs)


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
            hide_index=True, width="stretch")


# ------------------------------------------------------------------- sidebar
with st.sidebar:
    st.caption("Synthetic logs of citizens using Greek public services, with ground truth.")
    view = st.radio("View", VIEWS, key="view", label_visibility="collapsed",
                    format_func=lambda v: f"{ICONS[v]} "
                    + ("Start here" if v == "Start" else v))
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
    st.caption(f"Version {gen.__version__}. Catalogue of {catalogue().meta['retrieved']}, "
               f"{len(catalogue().procedures):,} procedures. "
               "Procedure data: MITOS, CC BY-SA 4.0.")
    st.caption(":material/contrast: Light or dark: open the menu at the top right, choose "
               "*Settings*, then the theme. *Use system setting* follows your computer.")

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
    st.header(f"{ICONS['Start']} Start here")
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
            st.markdown(f"**{ICONS[view]} {head}**")
            st.write(text)
            st.button(label, on_click=goto, args=(view,), width="stretch",
                      type="primary" if view == "Generate" else "secondary")
    st.subheader("Then make it yours")
    c1, c2 = st.columns(2)
    with c1.container(border=True):
        st.markdown(f"**{ICONS['Scenarios']} Scenarios**")
        st.write("Read what each life event does, change it, or build a new one from any "
                 "MITOS procedure. The page explains itself as you go.")
        st.button("Go to Scenarios", on_click=goto, args=("Scenarios",),
                  width="stretch")
    with c2.container(border=True):
        st.markdown(f"**{ICONS['Population']} Population**")
        st.write("Who the citizens are, how they behave when they need a certificate, and how "
                 "much unrelated use surrounds the life events.")
        st.button("Go to Population", on_click=goto, args=("Population",),
                  width="stretch")
    st.subheader("And keep it current")
    with st.container(border=True):
        st.markdown(f"**{ICONS['Catalogue']} Catalogue**")
        st.write("The procedures come from a snapshot of MITOS, refreshed every month. See "
                 "its date, what the last refresh changed and which life events that "
                 "touches, or look up any procedure and its steps.")
        st.button("Go to Catalogue", on_click=goto, args=("Catalogue",), width="stretch")
    st.caption("That is every entry of the menu on the left: *Generate*, *Inspect*, *Score a "
               "method* and *Bundles to integrate* are the four steps; *Scenarios*, "
               "*Population* and *Catalogue* shape what they work on.")
    st.subheader("What is set up now")
    st.dataframe(pd.DataFrame([{
        "life event": s["label"], "happens to": who(s),
        "per 1,000 citizens a year": round(per_thousand(s), 1),
        "procedures": len(s["procedures"]),
        "evidence procedures": len(evidence_of(s))} for s in cfg["scenarios"]]),
        hide_index=True, width="stretch")
    with st.expander("The words this tool uses"):
        st.markdown(GLOSSARY)


# ----------------------------------------------------------------- scenarios
def view_scenarios() -> None:
    st.header(f"{ICONS['Scenarios']} Scenarios")
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
                 "remarks"]), hide_index=True, width="stretch")

    left, right = st.columns([3, 1])
    with right:
        st.write("")
        if st.button("New life event", width="stretch",
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
    st.subheader(":material/looks_one: Step 1. Name it and say how often it happens")
    c1, c2 = st.columns([3, 1])
    scen["label"] = c1.text_input("Name", scen["label"], key=f"label:{at}:{ss.version}")
    with c2:
        st.write("")
        st.button("Delete this life event", width="stretch",
                  on_click=delete_scenario, args=(at,))
    st.markdown(f"**Life events per citizen and year, by age group** {YOURS}")
    st.caption("0.05 means 5 of every 100 citizens of that age each year. Leave 0 for ages it "
               "does not concern. For orientation: Greece has roughly 11 deaths and 7 births per "
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
    st.subheader(":material/looks_two: Step 2. Choose the procedures it starts")
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
    st.subheader(":material/looks_3: Step 3. Check the evidence (optional)")
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
    st.subheader(":material/looks_4: Step 4. Look inside a procedure (optional)")
    st.caption("The steps an instance will go through, as MITOS publishes them. Where a step "
               "has alternatives, one is chosen; a failure alternative is chosen rarely.")
    ids = [x["id"] for x in scen["procedures"] if x["id"] in catalogue().procedures]
    if ids:
        steps_table(st.selectbox("Procedure", ids, format_func=title,
                                 key=f"steps:{at}:{ss.version}"))
    st.caption("Done? Go to **Generate** in the sidebar.")


# ---------------------------------------------------------------- population
def view_population() -> None:
    st.header(f"{ICONS['Population']} Population and behaviour")
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
    st.header(f"{ICONS['Generate']} Generate")
    st.markdown("Choose how large a log to make and press **Generate**. The defaults give a "
                "small log in a few seconds, which is the right place to start.")
    s = ss.settings
    c1, c2, c3 = st.columns(3)
    s["citizens"] = sticky(c1.number_input, "Citizens", "w_citizens",
                           min(int(s["citizens"]), max_citizens()), min_value=10,
                           max_value=max_citizens(), step=500,
                           help=f"At most {max_citizens():,} here. Larger logs: run "
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
    go = c1.button("Generate", type="primary", width="stretch",
                   icon=":material/play_arrow:")
    stop = c2.button("Stop", width="stretch", icon=":material/stop:",
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
                            icon=":material/download:",
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
    st.header(f"{ICONS['Inspect']} Inspect")
    if not need_result():
        return
    r = ss.result
    m, inst, eps = r["manifest"], r["inst"], r["eps"]
    labels = {s["id"]: s["label"] for s in cfg["scenarios"]}
    cols = st.columns(5)
    cols[0].metric("Events", f"{m['events']:,}", border=True)
    cols[1].metric("Service instances", f"{m['instances']:,}", border=True)
    cols[2].metric("Citizens", f"{m['citizens']:,}", border=True)
    cols[3].metric("Episodes", f"{m['episodes']:,}", border=True)
    shared = m["instances_shared_between_episodes"] + m["background_instances_reused_by_an_episode"]
    cols[4].metric("Shared instances", f"{shared:,}", border=True,
                   help="Instances that belong to more than one episode, or background uses "
                        "that an episode reused.")
    st.caption(f"{m['instances_by_origin']['scenario']:,} instances belong to episodes and "
               f"{m['instances_by_origin']['background']:,} are background. Outcomes: "
               + ", ".join(f"{v:,} {k}" for k, v in m["outcomes"].items()) + ".")

    eps = eps.assign(span=eps.span_days.astype(float), scenario=eps.scenario.map(labels).fillna(
        eps.scenario))
    colours = life_colours()
    life_scale = alt.Scale(domain=sorted(eps.scenario.unique()),
                           range=[colours.get(x, "#888888")
                                  for x in sorted(eps.scenario.unique())])
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Episodes per scenario")
        counts = eps.groupby("scenario").size().reset_index(name="episodes")
        st.altair_chart(alt.Chart(counts).mark_bar().encode(
            x=alt.X("episodes:Q", title="Episodes"),
            y=alt.Y("scenario:N", sort="-x", title=None),
            color=alt.Color("scenario:N", scale=life_scale, legend=None),
            tooltip=["scenario", "episodes"]), width="stretch")
    with c2:
        st.subheader("How long an episode lasts")
        box = eps.groupby("scenario").span.quantile([0.1, 0.5, 0.9]).unstack().reset_index()
        box.columns = ["scenario", "p10", "median", "p90"]
        base = alt.Chart(box).encode(y=alt.Y("scenario:N", sort=alt.SortField("median"),
                                             title=None))
        st.altair_chart(
            base.mark_rule(strokeWidth=5, opacity=0.75).encode(
                x=alt.X("p10:Q", title="Days from first start to last end (10% to 90%, "
                                       "dot = median)"), x2="p90:Q",
                color=alt.Color("scenario:N", scale=life_scale, legend=None))
            + base.mark_point(filled=True, size=70,
                              color="white" if dark() else "black").encode(
                x="median:Q", tooltip=["scenario", "p10", "median", "p90"]),
            width="stretch")
        st.caption("No single time window fits every scenario. Each life event keeps "
                   "its colour across these charts and the tables of *Bundles to integrate*.")

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
                                                      range=["white" if dark() else "black",
                                                             "transparent"]),
                          legend=None),
        strokeWidth=alt.value(2),
        tooltip=["case_id", "service", "start", "end", "role", "outcome", "belongs to",
                 "also in"]).properties(height=alt.Step(22))
    st.altair_chart(chart, width="stretch")
    st.caption("Each bar is one service instance, coloured by the true episode it belongs to. "
               "An outline marks an instance that another episode reused. A method sees "
               "none of the colours: finding them is the task.")


# --------------------------------------------------------------------- score
def view_score() -> None:
    st.header(f"{ICONS['Score a method']} Score a method")
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
    c2.link_button("Open the method guide", "?guide=methods", width="stretch",
                   icon=":material/menu_book:",
                   help="Opens in a new tab. The same text is docs/methods.md in the "
                        "repository.")
    log = inst[["case_id", "citizen_id", "service_id", "service", "start", "end"]]
    n = len(log)
    c1, c2, c3, c4 = st.columns(4)
    if c1.button("Pair mixture", width="stretch",
                 help=f"Learns a time window for every pair of services. About "
                      f"{duration(max(2, n / 2200))} on this log."):
        import discover
        with st.status("Fitting a delay mixture for every pair of services", expanded=False):
            out = discover.discover(log)
        found["pair mixture, overlapping"] = out[2]
        found["pair mixture, partition"] = out[3]
    if c2.button("Hawkes process", width="stretch",
                 help=f"Each instance is spontaneous or triggered by an earlier one. About "
                      f"{duration(max(2, n / 2500))} on this log."):
        import hawkes
        with st.status("Fitting the Hawkes process, 150 iterations at most", expanded=False):
            out = hawkes.fit(log)
        found["Hawkes, overlapping"] = out[1]
        found["Hawkes, partition"] = out[2][["case_id", "journey_id"]]
    if c3.button("Latent life events", width="stretch",
                 help=f"Learns the kinds of life event themselves. The slowest: about "
                      f"{duration(max(5, n / 750))} on this log."):
        import latent
        with st.status("Fitting the latent life-event model, 120 iterations",
                       expanded=False):
            out = latent.fit(log)
        found["latent life events, overlapping"] = out[1]
        ss.kinds = out[0]
    if c4.button("Register only", width="stretch",
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
            scores[label] = sc.score(truth, journeys[["case_id", "journey_id"]], shared)
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
    baseline = table_[table_.index.str.startswith("fixed gap")].F1.max()
    others = table_[~table_.index.str.startswith("fixed gap")]
    cards = st.columns(3)
    cards[0].metric("Baseline to beat", f"{baseline:.1f}%", border=True,
                    help="The best F1 a fixed gap reaches on this log: "
                         + table_[table_.index.str.startswith("fixed gap")].F1.idxmax() + ".")
    if others.empty:
        cards[1].metric("Best method so far", "none yet", border=True,
                        help="Run a method above, or upload your own.")
    else:
        best = others.F1.idxmax()
        cards[1].metric("Best method so far", f"{others.F1.max():.1f}%",
                        f"{others.F1.max() - baseline:+.1f} points on the baseline",
                        border=True, help=best)
        mine = others[others.index.str.startswith("uploaded")]
        if not mine.empty:
            cards[2].metric("Your method", f"{mine.F1.max():.1f}%",
                            f"{mine.F1.max() - baseline:+.1f} points on the baseline",
                            border=True, help=mine.F1.idxmax())
        else:
            found_most = others["shared found"].idxmax()
            cards[2].metric("Most shared instances found",
                            f"{others['shared found'].max():.1f}%", border=True,
                            help=found_most)
    orders = {"F1": "F1", "Precision": "precision", "Recall": "recall",
              "Episodes whole": "whole", "Journeys clean": "clean",
              "Shared found": "shared found"}
    c1, _ = st.columns([1, 3])
    table_ = table_.sort_values(orders[c1.selectbox("Sort by", list(orders))], ascending=False)
    score_table(table_)
    st.caption("Colours: the bar and the number run from red (below 40%) through amber to "
               "green (85% and above). A star marks the best value of a column. Methods are "
               "tagged grey for the fixed-gap baselines, blue for those that read only the "
               "log, violet for those that use the register, pink for yours.")

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
            st.dataframe(ss.kinds, hide_index=True, width="stretch")


def grade(value: float) -> str:
    """A colour for a percentage: red when poor, green when good."""
    for floor, colour in ((85, "#16a34a"), (70, "#65a30d"), (55, "#ca8a04"), (40, "#ea580c")):
        if value >= floor:
            return colour
    return "#dc2626"


def score_table(scores: pd.DataFrame) -> None:
    """The scores as a table that shows at a glance what is good and what is not."""
    import html

    names = {"F1": "F1", "precision": "precision", "recall": "recall",
             "whole": "episodes whole", "clean": "journeys clean",
             "shared found": "shared found"}
    best = {c: scores[c].max() for c in names}

    def family(label: str) -> str:
        if label.startswith("fixed gap"):
            return "#64748b"
        if label.startswith("uploaded"):
            return "#db2777"
        return "#7c3aed" if "register" in label else "#2563eb"

    head = '<th style="width:26%">method</th>' + "".join(
        f'<th title="{html.escape(h, quote=True)}">{html.escape(h)}</th>'
        for h in names.values()) + '<th style="width:9%" title="in 2+ journeys">in 2+</th>'
    body = []
    for label, row in scores.iterrows():
        cells = [f'<td title="{html.escape(label, quote=True)}"><span class="jg-pill" '
                 f'style="--c:{family(label)}">{html.escape(label)}</span></td>']
        for c in names:
            v = row[c]
            if pd.isna(v):
                cells.append('<td title="no shared instance in this log">&ndash;</td>')
                continue
            star = ' <span title="best in this column">&#9733;</span>' if v == best[c] and len(
                scores) > 1 else ""
            cells.append(
                f'<td title="{v:.1f}%"><span class="jg-score"><b style="color:{grade(v)};'
                f'min-width:3.2em">{v:.1f}%</b><span class="jg-bar"><span style="width:{v:.0f}%;'
                f'background:{grade(v)}"></span></span>{star}</span></td>')
        cells.append(f'<td title="instances placed in more than one journey">'
                     f'{int(row["in 2+ journeys"]):,}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    st.html(
        '<style>.jg-table{width:100%;table-layout:fixed;border-collapse:collapse;'
        'font-size:0.85rem}.jg-table th,.jg-table td{padding:5px 8px;text-align:left;'
        'border-bottom:1px solid rgba(128,128,128,0.25);white-space:nowrap;overflow:hidden;'
        'text-overflow:ellipsis}.jg-table th{font-weight:600;'
        'border-bottom:1px solid rgba(128,128,128,0.6)}'
        '.jg-table tr:hover td{background:rgba(128,128,128,0.10)}'
        '.jg-pill{display:inline-block;max-width:100%;box-sizing:border-box;overflow:hidden;'
        'text-overflow:ellipsis;vertical-align:bottom;padding:0 8px;border-radius:9px;'
        'border-left:4px solid var(--c);'
        'background:color-mix(in srgb,var(--c) 16%,transparent)}'
        '.jg-score{display:flex;align-items:center;gap:6px}'
        '.jg-bar{flex:1;height:6px;border-radius:3px;background:rgba(128,128,128,0.2)}'
        '.jg-bar span{display:block;height:100%;border-radius:3px}</style>'
        f'<table class="jg-table"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table>')


def register_tools(rg, log: pd.DataFrame):
    """The log and the register in the form the register rules use, built once
    per generated log."""
    if "register_tools" not in ss.result:
        ss.result["register_tools"] = (rg.Log(log), rg.Register(
            os.path.join(HERE, "catalogue.json.gz")))
    return ss.result["register_tools"]


# ------------------------------------------------------------------- bundles
def view_bundles() -> None:
    st.header(f"{ICONS['Bundles to integrate']} Bundles to integrate")
    if not need_result():
        return
    import bundles as bd

    st.markdown(
        "This is the answer the whole exercise is for: **which services citizens use "
        "together, in what order, and which would gain most from being integrated.** It "
        "ends in recommendations you can hand on. "
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
        if "kind" in members.columns:
            st.caption(f"Using the journeys of *{source}*. The kinds are the ones this model "
                       "discovered by itself; nobody told it what the life events are. Each "
                       "is named after the services most typical of it. Compare with "
                       "the true episodes to see how well they match.")
        else:
            st.caption(f"Using the journeys of *{source}*. This method groups instances but "
                       "does not say what kind of life event a journey is, so everything is "
                       "one kind. The latent life-event model does; compare also with the "
                       "true episodes.")
    log = journey_log_cached(source, int(min_size), len(members))
    if log.empty:
        st.info("No journey has that many instances.")
        return
    cols = st.columns(4)
    cols[0].metric("Journeys", f"{log['case:concept:name'].nunique():,}", border=True)
    cols[1].metric("Service instances", f"{len(log):,}", border=True)
    cols[2].metric("Services", f"{log['concept:name'].nunique():,}", border=True)
    cols[3].metric("Kinds", f"{log['case:kind'].nunique():,}", border=True)

    # ---- recommendations ----------------------------------------------------
    st.subheader("Integration opportunities")
    st.markdown(
        "What an analyst could recommend, each with the evidence for it. A recommendation "
        "needs four things: that the services really go together, **what kind** of relation "
        "it is, what it costs citizens today, and how hard it would be.")
    with st.expander("The kinds of recommendation, and how each is decided"):
        meaning = {
            "life event": "The services most journeys of a kind hold, behind one door.",
            "one step": "Citizens always chain the two, in the same order. The second could "
                        "start by itself when the first ends.",
            "fetch": "The first is an input of the second: nearly every journey with the "
                     "second has the first before it. The second could obtain it itself.",
            "together": "Citizens need both, in either order. Pairs that the entry point "
                        "of their life event already covers are not repeated here.",
            "reuse": "A result the citizen already has is requested again in the same "
                     "journey.",
            "start": "Citizens request the first almost only for the second, but only some "
                     "need it: an occasional input.",
        }
        st.markdown("\n".join(f"- **{bd.TYPES[k]}.** {v}" for k, v in meaning.items()))
        st.caption("A pair must also occur in at least a tenth of the journeys of its kind. "
                   "Where the log says which requests failed or were abandoned, each "
                   "recommendation counts them among the requests it concerns, and they "
                   "weigh in the score: point at the evidence to read the figure.")
        st.caption("The numbers order the kinds by how much each typically changes for "
                   "citizens, 1 the most: a judgement, not a measurement. Effort is numbered "
                   "the same way, from 1 low (one organisation) to 4 very high (across "
                   "organisations, with a change of channel). The thresholds below are "
                   "choices too; someone who knows the services should set them.")
        c1, c2, c3 = st.columns(3)
        share = c1.slider("Share that counts as \"nearly always\"", 0.4, 0.95, 0.6, 0.05,
                          help="Of the journeys with one service, the share that must hold "
                               "the other.")
        need = c2.number_input("Journeys needed", 3, 500, 10,
                               help="A pair seen in fewer journeys is left out.")
        lift = c3.number_input("Lift needed", 1.0, 10.0, 1.5, 0.1,
                               help="How much more than chance the two must go together.")
    use_register = st.toggle(
        "Say whether MITOS confirms each dependency", value=True,
        help="Adds, for each pair, whether the register says the second procedure requires "
             "the first, and lets that count in the score. The recommendations themselves "
             "come from the log alone.")
    opp = bd.opportunities(log, bd.requirements() if use_register else None, share=share,
                           min_journeys=int(need), min_lift=float(lift))
    best = bd.top(opp) if not opp.empty else opp
    shown_cols = {"recommendation": "recommendation", "kind": "kind of journey",
                  "what": "what to do", "journeys": "journeys",
                  "requests_saved": "requests saved", "difficulty": "effort",
                  "register": "register", "evidence": "evidence"}
    if opp.empty:
        st.info("No opportunity meets these thresholds. Lower them, or generate a larger log.")
    else:
        st.markdown(f"**The {len(best)} best trade-offs**")
        st.caption("Ranked by a score from 0 to 100 that weighs how many journeys are "
                   "concerned and what citizens would save against how sure the finding is "
                   "and how much effort it takes. At most three per kind of journey and four "
                   "of one kind of recommendation, so the list is not ten versions of one "
                   "finding. Point at a cell to read all of it.")
        hover_table(best, {"rank": "rank", "score": "score", **shown_cols},
                    {"rank": 4, "score": 6, "recommendation": 13, "kind": 10, "what": 23,
                     "journeys": 6, "requests_saved": 7, "difficulty": 12, "register": 9,
                     "evidence": 10})
        with st.expander("How the score is made"):
            st.markdown(
                "- **Benefit.** Half is reach: the journeys concerned, against the largest "
                "in the table. 35% is the requests citizens would no longer make, 15% the "
                "citizen-days between the two services today. When the log says which "
                "requests failed or were abandoned, 15% goes to those among the requests "
                "concerned, taken evenly from the other three.\n"
                "- **Certainty.** Full when MITOS confirms the dependency, 85% when the "
                "register does not apply or was not consulted, 65% when the pair is not in "
                "MITOS. For pairs it is also reduced when the lift is below 2.\n"
                "- **Effort.** Full at level 1, then 85%, 70% and 55%.\n\n"
                "The score is the three multiplied. The weights are choices: they make the "
                "ranking reproducible and open to argument, not right.")
        st.markdown("**All opportunities**")
        counts = opp.recommendation.value_counts()
        c1, c2 = st.columns([3, 1])
        pick = c1.pills("Show", [t for t in bd.TYPES.values() if t in counts],
                        selection_mode="multi",
                        default=[t for t in bd.TYPES.values() if t in counts],
                        format_func=lambda t: f"{t} ({counts.get(t, 0)})")
        orders = {
            "Kind of recommendation": (["recommendation", "score"], [True, False]),
            "Score, highest first": (["score", "journeys"], [False, False]),
            "Journeys, most first": (["journeys", "score"], [False, False]),
            "Requests saved, most first": (["requests_saved", "score"], [False, False]),
            "Effort, lowest first": (["effort_level", "score"], [True, False]),
            "Effort, highest first": (["effort_level", "score"], [False, False]),
            "Kind of journey": (["kind", "score"], [True, False]),
        }
        by, ascending = orders[c2.selectbox("Sort by", list(orders))]
        view = opp[opp.recommendation.isin(pick or [])].sort_values(by, ascending=ascending)
        hover_table(view.head(150), {"score": "score", **shown_cols},
                    {"score": 6, "recommendation": 14, "kind": 10, "what": 24, "journeys": 6,
                     "requests_saved": 7, "difficulty": 13, "register": 9, "evidence": 11})
        st.caption("Colours: each kind of recommendation has its own; effort runs from green "
                   "(1 low) to red (4 very high); a green tick marks a pair MITOS confirms "
                   "and an amber question mark one it does not list. The bar beside a score "
                   "is its length against the best in the table.")
        if len(view) > 150:
            st.caption(f"The first 150 of {len(view)}. The CSV has them all.")
        if use_register and (opp.register == "not in MITOS").any():
            st.caption("A pair marked *not in MITOS* goes together in the log although the "
                       "register lists no requirement between the two. It may be a dependency "
                       "nobody wrote down, or a coincidence: worth a look either way.")
        ranked = opp.merge(best[["kind", "recommendation", "first", "then", "rank"]],
                           how="left", on=["kind", "recommendation", "first", "then"]).rename(
            columns={"rank": "top_rank"}).sort_values(
            ["top_rank", "score"], ascending=[True, False], na_position="last")
        c1, c2 = st.columns(2)
        c1.download_button("Report (Markdown)", bd.report(opp, log, source).encode("utf-8"),
                           icon=":material/description:",
                           file_name="integration_report.md", mime="text/markdown",
                           type="primary", width="stretch",
                           help="The best trade-offs and every recommendation written out, "
                                "to hand on.")
        c2.download_button("Opportunities (CSV)", ranked.to_csv(index=False).encode("utf-8"),
                           icon=":material/download:",
                           file_name="integration_opportunities.csv", mime="text/csv",
                           width="stretch")

    st.subheader("Pairs of services used together")
    st.caption("The raw material behind the recommendations. Each row: citizens who use the "
               "first service go on to use the second, in the same journey. Sorted by how "
               "many journeys do so.")
    cand = bd.candidates(log)
    only_cross = st.toggle("Only pairs that cross organisations",
                           help="Pairs inside one organisation are easier to integrate; pairs "
                                "across organisations are where citizens carry documents "
                                "between counters.")
    shown = cand[cand.different_organisations] if only_cross else cand
    st.dataframe(shown.head(200), hide_index=True, width="stretch", column_config={
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
        st.dataframe(bd.variants(log, 8), hide_index=True, width="stretch",
                     column_config={"share_of_kind": st.column_config.NumberColumn(
                         "share of kind", format="percent")})
    with c2:
        st.subheader("Requested again")
        st.caption("Services a citizen requests more than once inside one journey. Each "
                   "extra request is a duplication that integration would remove.")
        st.dataframe(bd.repeated(log), hide_index=True, width="stretch",
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
        "otherwise unordered services add many paths.\n"
        "5. The ranking above travels with the log. Every journey carries "
        "`top_opportunity` (the best-ranked opportunity it shows, or \"none\") and "
        "`opportunity_rank`, so a model family partitioned on `top_opportunity` sets the "
        "journeys that show an opportunity beside those that do not, and a filter keeps "
        "only the ones behind recommendation 1. Each journey also carries `requests`, "
        "`organisations` and `repeated_requests` as numbers, for dashboards. Each event "
        "carries `avoidable_request` and `repeated_request` (yes or no).")
    c1, c2, c3 = st.columns(3)
    tagged = bd.annotate(log, best) if not opp.empty else log
    c1.download_button("Journey log (XES, gzipped)", bd.to_xes(tagged),
                       icon=":material/download:",
                       file_name="journey_log.xes.gz", mime="application/gzip", type="primary",
                       width="stretch")
    c2.download_button("Journey log (CSV)", bd.to_csv(tagged), file_name="journey_log.csv",
                       mime="text/csv", width="stretch",
                       help="A CSV import reads one timestamp per row; use the XES to keep "
                            "both the start and the end.")
    c3.download_button("Integration candidates (CSV)",
                       cand.to_csv(index=False).encode("utf-8"),
                       file_name="integration_candidates.csv", mime="text/csv",
                       width="stretch")
    st.caption("Remember what the log is: synthetic. The pairs above say what this generator "
               "was told plus what the method recovered, not what Greek citizens do.")


def kind_names(kinds: pd.DataFrame | None) -> dict:
    """A readable name for each kind the latent model found: its number and the
    one or two services most typical of it. A service every kind brings, such
    as an authorisation, says little; one that mostly this kind brings says
    what the kind is."""
    if kinds is None or kinds.empty:
        return {}
    out = {}
    everywhere = kinds.groupby("service").per_event.transform("sum")
    kinds = kinds.assign(typical=kinds.per_event * kinds.per_event / everywhere)
    for kind, rows in kinds.groupby("kind"):
        top = rows.sort_values("typical", ascending=False).service.head(2)
        out[int(kind)] = f"Kind {int(kind)}: " + " + ".join(
            s if len(s) <= 42 else s[:40].rstrip() + "…" for s in top)
    return out


def hover_table(df: pd.DataFrame, columns: dict, widths: dict) -> None:
    """A table whose cells are cut to one line and show their whole content
    when pointed at. ``columns`` maps a column to its heading, ``widths`` to
    its share of the width. Kinds of recommendation, effort, the register and
    the score are drawn with a colour or a mark beside their text."""
    import html

    total = sum(widths.values())
    life = life_colours()
    best = max(int(df["score"].max()), 1) if "score" in df.columns and len(df) else 1
    head = "".join(f'<th style="width:{100 * widths[c] / total:.1f}%" '
                   f'title="{html.escape(h, quote=True)}">{html.escape(h)}</th>'
                   for c, h in columns.items())

    def pill(text: str, colour: str) -> str:
        return f'<span class="jg-pill" style="--c:{colour}">{text}</span>'

    def draw(column: str, text: str) -> str:
        safe = html.escape(text)
        if column == "recommendation":
            return pill(safe, KIND_COLOURS.get(text[:1], "#64748b"))
        if column == "difficulty":
            return pill(safe, EFFORT_COLOURS.get(text[:1], "#64748b"))
        if column == "score":
            return (f'<span class="jg-score"><b>{safe}</b><span class="jg-bar"><span '
                    f'style="width:{100 * int(text) / best:.0f}%"></span></span></span>')
        if column == "rank":
            return f'<span class="jg-rank">{safe}</span>'
        if column == "register":
            mark, colour = {"confirmed by MITOS": ("&#10003;", "#16a34a"),
                            "not in MITOS": ("?", "#d97706")}.get(text, ("&ndash;", "#94a3b8"))
            return f'<b style="color:{colour}">{mark}</b> {safe}'
        if column == "kind" and text in life:
            return f'<span class="jg-dot" style="background:{life[text]}"></span>{safe}'
        return safe

    body = []
    for row in df.itertuples(index=False):
        cells = []
        for c in columns:
            value = getattr(row, c)
            text = f"{value:,}" if isinstance(value, int) and not isinstance(
                value, bool) and c not in ("rank", "score") else str(value)
            cells.append(f'<td title="{html.escape(text, quote=True)}">{draw(c, text)}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    st.html(
        '<style>.jg-table{width:100%;table-layout:fixed;border-collapse:collapse;'
        'font-size:0.85rem}.jg-table th,.jg-table td{padding:5px 8px;text-align:left;'
        'border-bottom:1px solid rgba(128,128,128,0.25);white-space:nowrap;overflow:hidden;'
        'text-overflow:ellipsis}.jg-table th{font-weight:600;'
        'border-bottom:1px solid rgba(128,128,128,0.6)}'
        '.jg-table tr:hover td{background:rgba(128,128,128,0.10)}'
        '.jg-pill{display:inline-block;max-width:100%;box-sizing:border-box;overflow:hidden;'
        'text-overflow:ellipsis;vertical-align:bottom;padding:0 8px;border-radius:9px;'
        'border-left:4px solid var(--c);'
        'background:color-mix(in srgb,var(--c) 16%,transparent)}'
        '.jg-score{display:flex;align-items:center;gap:6px}.jg-score b{min-width:1.5em}'
        '.jg-bar{flex:1;height:6px;border-radius:3px;background:rgba(128,128,128,0.2)}'
        '.jg-bar span{display:block;height:100%;border-radius:3px;background:var(--jg)}'
        '.jg-rank{display:inline-block;min-width:1.6em;text-align:center;font-weight:700;'
        'border-radius:50%;background:color-mix(in srgb,var(--jg) 22%,transparent)}'
        '.jg-dot{display:inline-block;width:9px;height:9px;border-radius:50%;'
        'margin-right:6px}</style>'
        f'<table class="jg-table" style="--jg:{accent()}"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table>')


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
            if "kind" in members.columns:   # the latent model names its own kinds
                names = kind_names(ss.get("kinds"))
                members = members.assign(kind=[names.get(k, "No life event found")
                                               for k in members.kind])
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


def max_citizens() -> int:
    """The largest population the app will generate. A hosted app keeps the
    whole log in memory before it is downloaded, on a machine shared with
    others, so it allows less than an app on the user's own computer."""
    if os.environ.get("MITOS_MAX_CITIZENS"):
        return int(os.environ["MITOS_MAX_CITIZENS"])
    return 20000 if runs_locally() else 5000


def refresh_from_mitos() -> None:
    if not runs_locally():
        return
    with st.expander("Refresh from MITOS now"):
        st.caption("Downloads every procedure again (about 5,000 small documents, a quarter "
                   "of an hour) and replaces the catalogue if its content differs. The "
                   "scheduled job does the same on the first of every month; use this to be ahead of it.")
        c1, c2, _ = st.columns([1, 1, 4])
        go = c1.button("Refresh", type="primary", width="stretch")
        stop = c2.button("Stop", key="stop_refresh", width="stretch",
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
    st.header(f"{ICONS['Catalogue']} Catalogue")
    cat = catalogue()
    linked = sum(1 for p in cat.procedures.values() if p["requires"])
    c1, c2, c3 = st.columns(3)
    c1.metric("Snapshot of MITOS", cat.meta["retrieved"], border=True)
    c2.metric("Procedures", f"{len(cat.procedures):,}", border=True)
    c3.metric("With evidence links", f"{linked:,}", border=True)
    st.caption("A scheduled job downloads MITOS again on the first of every month and replaces the catalogue "
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
                hide_index=True, width="stretch")
        else:
            st.write("No bundled scenario is affected.")
        for key, head in (("new", "New"), ("withdrawn", "Withdrawn"), ("changed", "Changed")):
            if r[key]:
                with st.expander(f"{head}: {len(r[key])}"):
                    st.dataframe(pd.DataFrame([{
                        "id": x["id"], "procedure": x["title"],
                        **({"what changed": "; ".join(x["what"])} if "what" in x else {})}
                        for x in r[key]]), hide_index=True, width="stretch")
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
