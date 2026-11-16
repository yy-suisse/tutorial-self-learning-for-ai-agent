#!/usr/bin/env python3
"""H4: quoted clinical terms -> SNOMED CT check -> MeSH choice -> PubMed query and counts.

Usage
  python h4_query.py "I want RCTs on \"kidney transplant\" [70536003] AND \"abnormal monocytes\"."
  python h4_query.py --file request.txt
  python h4_query.py --file request.txt --no-count      (skip the PubMed counts)

Setup
  UMLS_API_KEY   required for the route by ID (set it in your environment, never in code)
  NCBI_API_KEY   optional, faster PubMed counts
  The folder agentlib/ (extract_snomed.py, logic.py, llm.py, models.yaml) sits one level up
  from this script. The local model is read from models.yaml (profile local).

Steps (same as the plan)
  1. extract terms and IDs            agentlib.extract_snomed   [CODE]
  2. extract the logic                agentlib.logic            [LLM, checked by code]
  3. check each ID                    SNOMED CT FHIR $lookup    [TOOL]  stops or asks you
  4. choose MeSH by ID and by label   UMLS crosswalk, MeSH lookup [TOOL]  you tick the boxes
  5. build the query string           this file                 [CODE]
  6. check: keys, restatement, counts  this file + PubMed esearch [CODE, YOU, TOOL]
"""
import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

from agentlib.extract_snomed import extract_terms            # step 1
from agentlib.logic import extract_logic, tree_to_text        # step 2
from agentlib.llm import get_client

CACHE_DIR = os.path.join(HERE, "cache")
RESULTS_DIR = os.path.join(HERE, "results")
FHIR = "https://r4.ontoserver.csiro.au/fhir"
UTS = "https://uts-ws.nlm.nih.gov/rest"
MESH_LOOKUP = "https://id.nlm.nih.gov/mesh/lookup"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
FSN_USE = "900000000000003001"
UMLS_KEY = os.environ.get("UMLS_API_KEY", "")
NCBI_KEY = os.environ.get("NCBI_API_KEY", "")
FILTERS = {"RCT": "randomized controlled trial[pt]"}     # add study types here


class StopRun(Exception):
    """A tool could not be reached or a precondition failed. The run stops."""


# ---------- HTTP with disk cache ----------

def cached_get(url, cache_key=None):
    """GET + JSON, cached on disk. The cache key never contains the API key."""
    key = hashlib.sha256((cache_key or url).encode()).hexdigest()
    path = os.path.join(CACHE_DIR, key + ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    with urllib.request.urlopen(url, timeout=30) as r:
        body = json.load(r)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False)
    return body


# ---------- step 3: SNOMED CT ----------

def get_concept(code):
    """One SNOMED CT ID -> dict with fsn, tag, display, active. None if the ID does not exist."""
    q = urllib.parse.urlencode({"system": "http://snomed.info/sct", "code": code})
    url = f"{FHIR}/CodeSystem/$lookup?{q}"
    try:
        body = cached_get(url)
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            return None
        raise StopRun(f"SNOMED server error {e.code}; nothing was checked") from e
    except urllib.error.URLError as e:
        raise StopRun(f"SNOMED server not reachable ({e.reason}); nothing was checked") from e
    if body.get("resourceType") == "OperationOutcome":
        return None
    c = {"id": code, "active": True, "display": None, "fsn": None, "tag": None}
    for p in body.get("parameter", []):
        name = p.get("name")
        if name == "display":
            c["display"] = p.get("valueString")
        elif name == "designation":
            parts = {x["name"]: x for x in p.get("part", [])}
            if parts.get("use", {}).get("valueCoding", {}).get("code") == FSN_USE:
                c["fsn"] = parts["value"].get("valueString")
        elif name == "property":
            parts = {x["name"]: x for x in p.get("part", [])}
            if parts.get("code", {}).get("valueCode") == "inactive":
                c["active"] = not parts["value"].get("valueBoolean", False)
    m = re.search(r"\(([^()]+)\)\s*$", c["fsn"] or "")
    c["tag"] = m.group(1) if m else None
    return c


def check_id(term):
    """Step 3 with a pause: a bad ID asks you for a new ID, 'd' to dismiss it, or 'q' to stop."""
    while term["id"]:
        c = get_concept(term["id"])
        if c and c["active"]:
            print(f'  "{term["term"]}"  <-  {c["fsn"]}')
            return c
        reason = "not found" if c is None else "inactive"
        print(f'\n  ID {term["id"]} for "{term["term"]}" is {reason} in SNOMED CT.')
        print("  Find the right ID in a SNOMED CT browser, then:")
        answer = input("  enter a new ID | d = dismiss the ID | q = stop > ").strip().lower()
        if answer == "q":
            raise SystemExit("stopped by user")
        if answer == "d":
            term["id"] = None
            print(f'  warning: "{term["term"]}" continues as a free-text term only')
            return None
        term["id"] = answer          # checked again at the top of the loop
    return None


# ---------- step 4: MeSH by ID (UMLS crosswalk) and by label (MeSH lookup) ----------

def mesh_by_id(snomed_id):
    """Crosswalk SNOMEDCT_US -> MeSH descriptors (ui starts with D). No climbing."""
    if not UMLS_KEY:
        raise StopRun("UMLS_API_KEY is not set; the route by ID is skipped")
    found, page = [], 1
    while True:
        base = f"{UTS}/crosswalk/current/source/SNOMEDCT_US/{snomed_id}"
        q = urllib.parse.urlencode({"targetSource": "MSH", "pageNumber": page})
        try:
            body = cached_get(f"{base}?{q}&apiKey={UMLS_KEY}", cache_key=f"{base}?{q}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return found
            raise StopRun(f"UMLS error {e.code} for {snomed_id}") from e
        for item in body.get("result", []):
            if item.get("rootSource") == "MSH" and item["ui"].startswith("D"):
                found.append({"ui": item["ui"], "name": item.get("name", item["ui"])})
        if page >= body.get("pageCount", 1):
            return found
        page += 1


def mesh_by_label(text, limit=15):
    """MeSH lookup on the quoted word: labels and entry terms that contain it."""
    q = urllib.parse.urlencode({"label": text, "match": "contains", "limit": limit})
    items = cached_get(f"{MESH_LOOKUP}/descriptor?{q}")
    return [{"ui": i["resource"].rsplit("/", 1)[-1], "name": i["label"]} for i in items]


def candidates(term):
    """Route by ID first, then the label route. Each candidate keeps its source."""
    items = []
    if term["id"]:
        try:
            items += [{"src": "ID", **d} for d in mesh_by_id(term["id"])]
        except StopRun as e:
            print(f"  {e}")
    seen = {i["ui"] for i in items}
    try:
        by_label = mesh_by_label(term["term"])
    except urllib.error.URLError as e:
        raise StopRun(f"MeSH lookup not reachable ({e.reason})") from e
    ranked = sorted(by_label, key=lambda c: difflib.SequenceMatcher(
        None, term["term"].lower(), c["name"].lower()).ratio(), reverse=True)
    for c in ranked:
        if c["ui"] not in seen:
            items.append({"src": "label", **c})
            seen.add(c["ui"])
    return items


def choose(term):
    """Show the candidates for one term; you tick the numbers. Nothing ticked = free text only."""
    items = candidates(term)
    print(f'\n"{term["term"]}"   ID: {term["id"] or "none"}')
    if not items:
        print("  no MeSH found by ID or label; the term stays free text only")
        return []
    for n, i in enumerate(items, 1):
        print(f"  [{n}] {i['src']:<5} {i['ui']:<9} {i['name']}")
    while True:
        raw = input("  tick numbers, e.g. 1 3 (Enter for none) > ").split()
        bad = [x for x in raw if not (x.isdigit() and 1 <= int(x) <= len(items))]
        if not bad:
            return [items[int(x) - 1] for x in raw]
        print(f"  not in the list: {bad}; try again")


# ---------- step 5: query string ----------

def block(term, picked):
    """(MeSH OR ... OR "your word"[tiab]). Without a tick: ("your word"[tiab])."""
    word = f'"{term["term"]}"[tiab]'
    if not picked:
        return f"({word})"
    return "(" + " OR ".join([f'"{p["name"]}"[Mesh]' for p in picked] + [word]) + ")"


def render(node, blocks):
    """Logic tree (keys only) -> query string with AND, OR, NOT and brackets."""
    if isinstance(node, str):
        return blocks[node]
    if "and" in node:
        return "(" + " AND ".join(render(n, blocks) for n in node["and"]) + ")"
    if "or" in node:
        return "(" + " OR ".join(render(n, blocks) for n in node["or"]) + ")"
    if "not" in node:
        return "NOT " + render(node["not"], blocks)
    raise ValueError(f"unknown node {node!r}")


def leaves(node):
    if isinstance(node, str):
        return [node]
    if "not" in node:
        return leaves(node["not"])
    children = node.get("and", node.get("or", []))
    return [leaf for c in children for leaf in leaves(c)]


# ---------- step 6: checks ----------

def check_keys(tree, keys):
    """6a: every key exactly once, no unknown key."""
    count = Counter(leaves(tree))
    return [k for k in keys if count[k] != 1] + [k for k in count if k not in keys]


def pubmed_count(query):
    """6c: hit count from PubMed esearch. Not cached, because counts change."""
    params = {"db": "pubmed", "term": query, "retmode": "json", "rettype": "count"}
    if NCBI_KEY:
        params["api_key"] = NCBI_KEY
    with urllib.request.urlopen(f"{EUTILS}/esearch.fcgi?{urllib.parse.urlencode(params)}", timeout=30) as r:
        body = json.load(r)
    time.sleep(0.12 if NCBI_KEY else 0.4)      # NCBI limits: 10 per second with key, 3 without
    return int(body["esearchresult"]["count"])


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description="H4 PubMed query builder")
    ap.add_argument("text", nargs="?", help="the request, with quoted terms and optional [ID]")
    ap.add_argument("--file", help="read the request from a text file")
    ap.add_argument("--no-count", action="store_true", help="skip the PubMed hit counts")
    args = ap.parse_args()
    text = open(args.file, encoding="utf-8").read() if args.file else args.text
    if not text:
        ap.error("give the request text or --file")

    # step 1
    try:
        terms = extract_terms(text)
    except ValueError as e:
        raise SystemExit(f"step 1 error:\n{e}")
    print(f"\nstep 1: {len(terms)} terms found")

    # step 2 (LLM). Use the same client line as in your notebook.
    client, model = get_client(require_local=True)
    logic = extract_logic(client, model, text)
    print("step 2: logic extracted and checked")

    # step 3: each ID is checked; a bad one pauses for you
    print("\nstep 3: SNOMED CT check")
    for t in terms:
        if t["id"]:
            check_id(t)

    # step 4: you tick the MeSH descriptors per term
    print("\nstep 4: choose MeSH descriptors (tick per term)")
    chosen = {t["key"]: choose(t) for t in terms}

    # step 5: build the query string
    blocks = {t["key"]: block(t, chosen[t["key"]]) for t in terms}
    filters = []
    for f in logic.get("filters", []):
        if f in FILTERS:
            filters.append(FILTERS[f])
        else:
            print(f"warning: filter {f!r} is not in FILTERS and is skipped")
    query = " AND ".join([render(logic["tree"], blocks)] + filters)

    # step 6a: keys
    problems = check_keys(logic["tree"], [t["key"] for t in terms])
    if problems:
        raise SystemExit(f"step 6a: key problems {problems}; the query was not built")

    # step 6b: restatement, you confirm
    labels = {t["key"]: t["term"] for t in terms}
    print("\nstep 6b: your request as a sentence")
    print("  " + tree_to_text(logic["tree"], labels))
    if input("  is this what you meant? [y/n] > ").strip().lower() != "y":
        raise SystemExit("stopped: correct the request and run again")

    print("\nQuery:")
    print(query)

    # step 6c: counts per block and total
    counts = {}
    if not args.no_count:
        print("\nstep 6c: hit counts")
        for t in terms:
            counts[t["term"]] = pubmed_count(blocks[t["key"]])
            print(f'  {t["term"]:<28} {counts[t["term"]]:>9}')
        total = pubmed_count(query)
        print(f'  {"TOTAL query":<28} {total:>9}')
        counts["TOTAL"] = total

    # save the run (no API keys in the file)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(RESULTS_DIR, f"run_{stamp}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "request": text,
            "terms": [{"key": t["key"], "term": t["term"], "id": t["id"]} for t in terms],
            "tree": logic["tree"],
            "filters": logic.get("filters", []),
            "chosen": {k: [c["ui"] for c in v] for k, v in chosen.items()},
            "blocks": blocks,
            "query": query,
            "counts": counts,
        }, f, ensure_ascii=False, indent=1)
    print(f"\nsaved: {out}")


if __name__ == "__main__":
    main()
