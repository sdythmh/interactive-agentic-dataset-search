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
`benchmark/` holds record identifiers, portal URLs, relevance labels and text
we wrote ourselves (the query intents, queries, narratives and vague queries),
and `results/` holds only identifiers. A few queries quote the titles of
datasets on the portals; those titles belong to the portals and their
publishers. `figures/teaser.png` is a screenshot of the
interface in which the titles and short parts of the descriptions of a few
data.gov.au records are visible; that content belongs to the portal and its
publishers.

To run the system, fetch the metadata from the portals (see the README). What
you fetch is the portal's current version of each record, which may differ
from the version we evaluated on.

## `benchmark/`

150 query intents (50 per portal), each with four queries (600 in all), one
of each type: catalog wording, colloquial, indirect and misspelled; relevance
judgements for every query intent; and 18 vague queries (12 au, 6 uk).

The data files keep their original key names:

| Key in the files | Term in the paper |
|---|---|
| `topic` | query intent |
| `sub_questions` | the four queries of a query intent |
| `L1` | catalog wording |
| `L2` | colloquial |
| `L3` | indirect |
| `L4` | misspelled |
| `need` (in `results/`) | query intent (or vague query, in C1 and C2) |
| `level` (in `results/`) | query type (`L1`–`L4`) |

### `topics_{au,uk,us}.json`

```
{"corpus": "au", "portal": "data.gov.au",
 "topics": [{"topic": "topic-01",
             "narrative": "Relevant records report ...",
             "sub_questions": {"L1": "...", "L2": "...", "L3": "...", "L4": "..."}},
            ...]}
```

- `topic`: query intent id, `<kind>-<number>`; the kind (`topic`,
  `publisher`, `spatial`, `region`, `graph`) says what the query intent is
  mainly about. The 144 publisher queries are the queries of the 36
  `publisher-*` query intents.
- `narrative`: what counts as relevant; relevance was judged against it.
- `sub_questions`: the four queries of the query intent: L1 catalog wording,
  L2 colloquial, L3 indirect, L4 misspelled. Six L4 queries (two per portal,
  all of `spatial-*` query intents) are written mainly in Chinese instead
  (another language).

### `gold_{au,uk,us}.json`

```
{"corpus": "au",
 "topics": [{"topic": "topic-01",
             "relevant": ["<record id>", ...],
             "groups": [["<record id>", ...], ...]}, ...]}
```

- `relevant`: ids of every record judged relevant to the query intent.
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

- `question`: the vague query, written from the query intent by leaving one
  detail out.
- `topic`: the query intent it comes from; it is scored against that query
  intent's gold.
- `withheld`: the detail that was left out (the simulated user's reply in C2).
- `withheld_dim`: what kind of detail it is: `place`, `org` or `facility`.

### `qrels_{au,uk,us}.txt`

Every relevance judgement behind `gold_{au,uk,us}.json`, one per line:

```
<topic> 0 <record id> <0|1>
```

1 means judged relevant, 0 means judged and not relevant; a (query intent,
record) pair that is not in the file was not judged. For each query intent the
records labelled 1 are exactly its `relevant` list in `gold_*.json`. The
judgements were made by Claude Opus 5.5; the paper describes how they were
checked.

### `samples_{au,uk,us}.csv`

Columns `id,url`, one row per indexed record, 8,000 rows per portal.

- au and uk: `id` is the CKAN package id.
- us: `id` is `uuid5(NAMESPACE_URL, "https://catalog.data.gov/dataset/" + slug)`
  (`ingest/fetch_catalog_api.py`); the Catalog API has no UUID field.
- `url`: the record's page on the portal.

## `results/`

The runs behind Table 3 and the Discussion of the paper, one file per portal
and method, `results/<portal>/<method>.json`:

- `A1`–`A5`: the five methods of Table 3 (au, uk, us).
- `B2`: InterADS without publisher linking (au, uk, us).
- `C1`, `C2`: the vague queries without and with the clarifying question (au,
  uk).

The files contain only identifiers and configuration names. Each row of A1–A5
and B2 gives the query intent (`need`), the query type (`level`), the
configuration (`config`) and the ids of the top ten records (`retrieved`).
Each row of C1 and C2 gives the vague query id (`need`), the query intent it
is scored against (`resolves_to`), the configuration and the top ten ids.
`eval/runs_paper.json` names these files for `eval/score.py`.

## License

The files in `benchmark/` and `results/` are released under the Creative
Commons Attribution 4.0 International license (CC BY 4.0,
<https://creativecommons.org/licenses/by/4.0/>). The URLs point to records
whose own terms are set by their publishers and portals.
