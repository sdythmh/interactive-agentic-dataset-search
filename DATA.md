# Data

## Portals

| Code | Portal | Address | Access used |
|---|---|---|---|
| `au` | data.gov.au | <https://data.gov.au> | CKAN API (`ingest/fetch_ckan.py`) |
| `uk` | data.gov.uk | <https://www.data.gov.uk> | CKAN API (`ingest/fetch_ckan.py`) |
| `us` | catalog.data.gov | <https://catalog.data.gov> | Catalog API via api.data.gov, needs your own key (`ingest/fetch_catalog_api.py`) |

For each portal the system indexes a sample of 8,000 records (dataset
metadata: title, description, publisher, tags, formats, spatial footprint,
dates, URL).

## Why only identifiers and links are published

The records belong to their publishers and are distributed by the portals
under the portals' and publishers' own terms, which differ from record to
record. Apart from the screenshot and the few dataset titles described below,
this repository therefore contains no record metadata taken from the portals:
`benchmark/` holds record identifiers, portal URLs, and text we wrote ourselves
(the topics, sub-questions, narratives and vague questions). A few questions in
the topics quote the titles of datasets on the portals; those titles belong to
the portals and their publishers. `figures/teaser.png` is a screenshot of the
interface in which the titles and short parts of the descriptions of a few
data.gov.au records are visible; that content belongs to the portal and its
publishers.

To run the system, fetch the metadata from the portals (see the README). What
you fetch is the portal's current version of each record, which may differ
from the version we evaluated on.

## `benchmark/`

150 search topics (50 per portal), each with four sub-questions (600 in all),
relevance judgements for every topic, and 18 vague questions (12 au, 6 uk).

### `topics_{au,uk,us}.json`

```
{"corpus": "au", "portal": "data.gov.au",
 "topics": [{"topic": "topic-01",
             "narrative": "Relevant records report ...",
             "sub_questions": {"L1": "...", "L2": "...", "L3": "...", "L4": "..."}},
            ...]}
```

- `topic`: topic id, `<kind>-<number>`; the kind (`topic`, `publisher`,
  `spatial`, `region`, `graph`) says what the topic is mainly about.
- `narrative`: what counts as relevant; relevance was judged against it.
- `sub_questions`: four phrasings of the same need. L1 is a full noun phrase,
  L2 a short keyword query, L3 a natural-language question that implies the
  need without naming it, L4 a distorted phrasing (misspellings or another
  language).

### `gold_{au,uk,us}.json`

```
{"corpus": "au",
 "topics": [{"topic": "topic-01",
             "relevant": ["<record id>", ...],
             "groups": [["<record id>", ...], ...]}, ...]}
```

- `relevant`: ids of every record judged relevant to the topic.
- `groups`: the relevant records grouped for scoring; records with the same
  title form one group, so several copies of one record count once. Every id
  in `relevant` is in exactly one group. Scores are computed against the
  groups (`eval/score.py`).

### `vague_{au,uk}.json`

```
{"corpus": "au",
 "questions": [{"id": "vague-topic-03", "question": "flood studies",
                "topic": "topic-03",
                "withheld": "along the Murray River near Albury",
                "withheld_dim": "place"}, ...]}
```

- `question`: the vague question, written from the topic by leaving one
  detail out.
- `topic`: the topic it comes from; it is scored against that topic's gold.
- `withheld`: the detail that was left out (the simulated user's answer, and
  the text added to the question in the "detail given" condition).
- `withheld_dim`: what kind of detail it is: `place`, `org` or `facility`.

### `samples_{au,uk,us}.csv`

Columns `id,url`, one row per indexed record, 8,000 rows per portal.

- au and uk: `id` is the CKAN package id.
- us: `id` is `uuid5(NAMESPACE_URL, "https://catalog.data.gov/dataset/" + slug)`
  (`ingest/fetch_catalog_api.py`); the Catalog API has no UUID field.
- `url`: the record's page on the portal.

### License

The files in `benchmark/` are released under the Creative Commons Attribution
4.0 International license (CC BY 4.0,
<https://creativecommons.org/licenses/by/4.0/>). The URLs point to records
whose own terms are set by their publishers and portals.
