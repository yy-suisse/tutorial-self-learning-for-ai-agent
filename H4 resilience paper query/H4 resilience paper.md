# H4. PubMed query builder from quoted clinical terms (resilience paper)

## 1. Goal

- **Input:** a text where you describe the literature you want. Every clinical term for the query is in double quotes.
- **Output:** one PubMed query string, with the hit counts as feedback.
- **Principle:** you choose the words, because you know how papers name things, and you give the SNOMED CT ID whenever possible. The agent finds the MeSH terms for each of your terms, by ID and by name, and builds a correct query around them.

## 2. Input format

| Type | You write | What it is used for |
| --- | --- | --- |
| A. Coded term (the default) | `"kidney transplant" [70536003]` | Your word is searched as free text. MeSH is searched by the ID and by your word. |
| B. Raw term (the exception) | `"abnormal monocytes"` | Your word is searched as free text. MeSH is searched by your word. |

**Rule: the ID has priority.**

- Give the SNOMED CT ID whenever a suitable concept exists.
- Use a raw term only for something extra: an idea that has no good SNOMED CT concept, or that SNOMED CT describes in a different way than papers do.
- In step 4, a coded term is searched in MeSH by two routes, the ID and your word. A raw term has the word route only.

Why the ID comes first:

| Reason | With the ID | With the name only |
| --- | --- | --- |
| Meaning | One ID is one meaning | One word can have several MeSH candidates, for example "ventilation" |
| No exact MeSH match | Code climbs to the SNOMED parent and tries again | Nothing to climb; the search fails |
| Same result on every run | Yes, whatever word is in the quotes | Depends on your spelling |

- The quoted text is your own word, the one you want searched in titles and abstracts.
- The bracket holds a SNOMED CT ID only, digits only, right after the closing quote.
- Use double quotes for clinical terms only, nowhere else in the text.
- Write the logic in plain words: `AND`, `OR`, with brackets.

**Example request**

```python
text_body = (
    'I want RCTs on "kidney transplant" [70536003] '
    'AND ("lymphopenia" [48813009] OR "neutropenia" [165517008]) '
    'AND "abnormal monocytes".'
)
```

## 3. Strengths of this method

| Strength | What it means in practice |
| --- | --- |
| Your word and a coded concept together | Your word is the one papers use. The ID fixes one exact meaning. Neither is enough alone. |
| Two routes to MeSH cover two aspects | The ID and your word can name two aspects of the same clinical thing, for example the measurement and the substance for albumin. Both descriptors go into the query, so papers indexed under either one are found. |
| A fallback when MeSH has no exact match | With an ID, code climbs to the SNOMED parent and tries again. A word alone has no parent. |
| Raw terms cover what SNOMED CT cannot say well | SNOMED CT often describes a test where papers describe a result. A raw term such as "abnormal monocytes" fills this gap. |
| The model has one small job | It only reads the logic of your sentence. It never writes an ID, a MeSH descriptor or the query string, so it cannot invent one. |
| Every decision is visible | The FSN and semantic tag, the MeSH found by each route, and the hit count of each block are printed. You can refuse or correct at each point. |
| Same input, same query | IDs, cached tool results and a query built by code give the same result on every run. This can be described exactly in a methods section. |

**Limits**

- It is not systematic: the result depends on the words and IDs you choose.
- Each term has one free-text word. A paper that uses another word and is not yet indexed with MeSH is missed.
- MeSH coverage is partial. Some terms keep their free text only.

## 4. Workflow schema

Legend: `[CODE]` pure Python, `[LLM]` local model, `[TOOL]` external service called by code, `[YOU]` human decision.

```
 Your request text ("term" [ID]  and  "term")
        |
        v
 1. Extract terms and IDs ....................... [CODE]
        |   regex, curly quotes fixed, check digit
        v
 2. Extract the logic ........................... [LLM]  prompt 1
        |   output: JSON tree (and / or / not) + filters
        v
 3. Check each ID (type A only) ................. [TOOL] SNOMED CT
        |   exists and active; FSN and semantic tag
        |   printed next to your term
        |   FSN does not fit your term ---------> [YOU] fix the ID or go on
        v
 4. Find the MeSH descriptors ................... [TOOL] UMLS, MeSH, PubMed
        |   route by ID   (type A): UMLS crosswalk
        |   route by name (A and B): MeSH entry terms,
        |                            then PubMed's own mapping
        |   type A: both routes; two different
        |           descriptors are joined by OR
        |   MeSH found for each term -----------> [YOU] shown; you can refuse one
        |   no MeSH found ----------------------> [YOU] warning; free text only
        v
 5. Build the query string ...................... [CODE]
        |   one block per term, tree gives AND / OR
        v
 6. Check
        |   6a. every term used exactly once .... [CODE]
        |   6b. plain-sentence restatement -----> [YOU] yes or no
        |   6c. hit count per block and total ... [TOOL] PubMed count
        v
 Final query string + hit counts
```

## 5. Who does what

| Actor | Steps | Why this actor |
| --- | --- | --- |
| Code | 1, 5, 6a | Exact work: digits, brackets, quotes, counting. A model makes silent errors here. |
| LLM | 2 | Language work: reading the logic of your sentence. |
| Tool | 3, 4, 6c | Facts that must come from the source: names, MeSH, hit counts. |
| You | The words in the quotes, 3, 4, 6b | Only you know your intention and the words of the literature. |

The LLM is used in 1 place. It never writes an ID, a MeSH descriptor or the final query string.

## 6. Implementation, step by step

### Step 1. Extract terms and IDs [CODE]

- Status: written and tested. File `extract.py`, function `extract_terms(text)`.
- Curly quotes copied from Word or a PDF are converted to straight quotes first.
- A regex finds every `"term"` and the optional `[digits]` after it.
- The last digit of each ID is checked with the Verhoeff algorithm. A typo is reported before any request is sent.
- All errors are reported in one pass: wrong check digit, a bracket that does not follow a quoted term, a quote that is not closed, no term at all.
- Output: a list of items, with keys in text order.

```json
[
  {"key": "c1", "term": "kidney transplant",  "id": "70536003"},
  {"key": "c2", "term": "lymphopenia",        "id": "48813009"},
  {"key": "c3", "term": "neutropenia",        "id": "165517008"},
  {"key": "c4", "term": "abnormal monocytes", "id": null}
]
```

### Step 2. Extract the logic [LLM, prompt 1]

- Input: your request text and the list of keys with their terms.
- Output: a JSON tree, obtained with `ask_json` and validated against a schema.

```json
{
  "and": ["c1", {"or": ["c2", "c3"]}, "c4"],
  "filters": ["RCT"]
}
```

- Leaves are keys only. Operators are `and`, `or`, `not`.
- `filters` holds everything that is not a quoted term: study type, years, language.
- The model does not write the query string. Code builds it in step 5, so brackets and quotes cannot be wrong.

### Step 3. Check each ID [TOOL: SNOMED CT]

- For type A terms only.
- One function: `get_concept(id)` returns the FSN, the synonyms and the active status.
- Source: the SNOMED CT API, International Edition, English descriptions only.
- Answers are cached on disk. The release version is written in the trace.
- An inactive or unknown ID stops the run with a clear message.
- The FSN, with its semantic tag, is printed next to your term, for example `"albumin"  <-  Albumin measurement (procedure)`. You see that the ID is the right one, and which aspect it covers: here the measurement, while your word names the substance. The FSN is not searched.

### Step 4. Find the MeSH descriptors [TOOL + CODE]

Two routes. A coded term (type A) always uses both. A raw term (type B) uses the route by name only.

| Route | Used for | How |
| --- | --- | --- |
| By ID | Type A | UMLS crosswalk from SNOMEDCT\_US to MSH, by concept ID. No comparison of words. If no exact match exists, it is repeated on the SNOMED parents, one level at a time; a broader descriptor is accepted and flagged with its distance, for example "broader by 1 level". |
| By name | Types A and B | Your quoted term is sent to the MeSH lookup; a match with a MeSH name or entry term gives the descriptor. If nothing is found, your term is sent to PubMed, and the returned query translation shows the MeSH term it was mapped to. |

**Combining the two routes (type A)**

- Same descriptor from both routes: it is kept once.
- Two different descriptors: both are kept and joined by OR in step 5.
- Why they can differ: the ID and your word can name two aspects of the same clinical thing. Example: your word "albumin" is a substance, and the ID 26758005 is "Albumin measurement (procedure)". Each route can lead to a different MeSH descriptor, and papers can be indexed under either.

**What you see and decide**

- Code prints one line per term: `term  |  FSN (semantic tag)  |  MeSH by ID  |  MeSH by name`.
- You can refuse a descriptor. Two different descriptors can mean two aspects of the same thing, or a wrong ID for your word. Code cannot tell these apart, so you decide.
- No descriptor from either route: the term keeps its free text only, and you get a warning.
- Code checks that every descriptor exists in MeSH. The model is not involved in this step.
- Output per term: `{"mesh_by_id": "...", "distance": 0, "mesh_by_name": "...", "name_method": "entry term"}`. A route that found nothing gives `null`.

### Step 5. Build the query string [CODE]

- One block per term:

```
("<MeSH by ID>"[Mesh] OR "<MeSH by name>"[Mesh] OR "your term"[tiab])    two different descriptors
("<MeSH>"[Mesh] OR "your term"[tiab])                                    one descriptor
("your term"[tiab])                                                      no descriptor
```

- `[Mesh]` includes all the narrower MeSH terms automatically.
- `[tiab]` finds recent papers that are not indexed with MeSH yet.
- The tree from step 2 joins the blocks with AND, OR and NOT.
- Filters are added at the end from a fixed table, for example RCT becomes `randomized controlled trial[pt]`.

Result for the example request:

```
(block c1) AND ((block c2) OR (block c3)) AND (block c4)
AND randomized controlled trial[pt]
```

### Step 6. Check

| Check | Actor | What |
| --- | --- | --- |
| 6a | Code | Every key from step 1 is in the tree exactly once. No unknown key. |
| 6b | You | Code prints the tree as a sentence with your terms: "kidney transplant AND (lymphopenia OR neutropenia) AND abnormal monocytes, limited to RCT". You answer yes or no. |
| 6c | Tool | Hit count for each block and for the full query. A total of 0, or one block far smaller than the others, shows which term is the bottleneck. |

- On "no" at 6b, you correct the tree by hand or rerun step 2 with your remark.
- When the total is too small, you change the request yourself: a broader word in the quotes, a broader ID, or one term less.

## 7. Building blocks

**Prompt**

| Prompt | Step | Input | Output |
| --- | --- | --- | --- |
| 1. Logic | 2 | Request text, list of keys with terms | JSON tree and filters |

- It uses `ask_json` with a schema and 2 retries. File: `h4_logic.v1.txt`.

**Tools**

| File | Functions | Service |
| --- | --- | --- |
| `herotools/snomed.py` | `get_concept(id)`, `get_parents(id)` | SNOMED CT API |
| `herotools/mesh.py` | `umls_crosswalk(id)`, `mesh_lookup(term)`, `mesh_exists(descriptor)` | UMLS (licence and API key), NLM MeSH lookup |
| `herotools/pubmed.py` | `count(query)`, `translation(term)` | PubMed E-utilities |

- PubMed limit: 3 requests per second, 10 with a free NCBI API key. Code throttles the calls.
- All tool results are cached on disk, so a rerun is free and identical.

**Library modules**

- `llm.py`, `trace.py`, `prompts.py`: client, log, versioned prompt.
- `structured.py`: `ask_json` for prompt 1.
- `tools.py`: registry for the `herotools` functions.
- `verify.py`: key check, MeSH existence check.

**Files**

```
extract.py      step 1 (written)
logic.py        step 2: prompt 1 and the tree schema
build.py        step 5
checks.py       step 6a and the restatement for 6b
run.py          the 6 steps in order
requests/       your test requests, one text file each
results/        final query, tree, MeSH table, hit counts per run
```

## 8. Next steps

1. Step 1: done.
2. Step 2: prompt 1 and the tree schema.
3. Steps 3 and 4: `get_concept`, then the MeSH search. The route by name first; the route by ID when the UMLS key is ready.
4. Steps 5 and 6: query builder, checks, hit counts.

## 9. Done when

- 3 requests tested, at least 1 with a raw term and 1 with a term that has no MeSH.
- For the coded terms, you have counted how often the two routes gave different descriptors, and how often the ID found one where the name did not. These numbers justify the ID in the input.
- For each request: the restatement was confirmed by you, and the query returns a non-zero count.
- One comparison against a query you wrote by hand for the same request.

## 10. Privacy and open points

- Local model only.
- What leaves your machine: concept IDs, terms and queries, sent to SNOMED CT, UMLS, MeSH and PubMed. No patient data is involved.
- The UMLS licence and API key are needed for the route by ID. Without them, type A terms use the route by name only, like type B.
- The terms of use of the public SNOMED CT API are still to check.
- A raw term of 4 letters or fewer, such as an abbreviation, can match many unrelated papers. Check its block count at 6c.

## 11. Later additions (not part of this plan)

Ideas discussed earlier and set aside. None of them is needed for the plan above.

| Addition | What it would do |
| --- | --- |
| Titles and abstracts | Fetch the first papers, so that you can read them next to the hit count. |
| Literature term proposals | The model proposes more words per term. Code keeps a word only if it adds new papers: `gain(t) = N(block OR t) - N(block)`. |
| Broadening ladder | Code broadens the smallest block step by step while the total is below a minimum. |
| Post-coordinated expressions | Accept a SNOMED CT expression in the bracket and split it into focus and values. |
| Embedding proposals for MeSH | Propose the nearest MeSH labels when both routes find nothing. |
| Blind judge | A second prompt reads only the tree and says what it thinks the request was. |
| Recall check | You list known relevant PMIDs; code counts how many the query finds. |
| Local SNOMED CT copy | Replace the API inside `get_concept`, for speed and a fixed release. |
| MCP server | Serve the `herotools` functions through MCP for other clients. |
