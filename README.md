# IADS: Interactive Agentic Dataset Search

IADS searches three open government data portals (data.gov.au, data.gov.uk and
catalog.data.gov) from plain-language requests, with a local LLM that plans
the search, asks back when the request is ambiguous, links the publisher a
request names, and shows how each result was found.

![IADS](figures/teaser.png)

Demo video: to be added.

## What the system does

- **Asks.** When a request leaves out which organisation or area is meant and
  the first results come from several publishers, IADS asks one clarifying
  question and offers those publishers as answer options.
- **Links.** When a request names a publisher in its own words ("Britain's
  national weather service"), a rule collects up to 20 candidate publishers
  from the portal's publisher list and the LLM picks one or none; the chosen
  publisher's records are searched as a fifth retrieval path.
- **Explains.** Every answer cites the records it uses. The comparison view
  puts the final ten next to a keyword search and a single search round, the
  Advanced view shows each stage (plan, retrieval paths, decisions,
  re-ranking), and each record page has a map and a "Why it was found" panel.

The pipeline has five stages: plan (one LLM call writes the search plan;
publisher linking), retrieve (keyword BM25, dense `bge-small-en-v1.5`,
spatial and graph paths, fused by RRF with k = 60, pool of 50, deduplicated by
title), decide (clarification rule; LLM decision loop of up to four rounds),
rank and answer (`bge-reranker-base` re-ranking with a graph vote; an answer
with citations and a citation check), and present (the web interface).

## Test environment

The reported results were produced on one machine:

- Intel Core i7-12700H, 32 GB RAM, NVIDIA RTX 3060 (laptop)
- Qwen2.5-7B-Instruct (`qwen2.5:7b-instruct`) served by Ollama
- OpenSearch 2.19, Neo4j 5
- Python 3.11

## Install

These commands have not been run from this repository as released.

```bash
conda create -n iads python=3.11
conda activate iads
pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements.txt
```

On a machine without CUDA, install `torch==2.6.0` from PyPI instead. The
embedder (`BAAI/bge-small-en-v1.5`) and the re-ranker
(`BAAI/bge-reranker-base`) run on CPU and are downloaded from Hugging Face on
first use.

Use Python 3.11 for the evaluation scripts: from Python 3.12 on, `sum()` over
floats rounds differently, which can move the reported p-values in the last
digits.

## Start the backends

These commands have not been run from this repository as released.

```bash
export NEO4J_PASSWORD=<a password of at least 8 characters>
docker compose up -d
ollama pull qwen2.5:7b-instruct
```

`docker-compose.yml` starts one OpenSearch node (port 9200) shared by the three
portals and one Neo4j server per portal (bolt ports 7687 au, 7688 uk, 7689 us).
`config.py` reads `NEO4J_PASSWORD` from the environment; Ollama is expected at
`http://localhost:11434`.

## Fetch the metadata and build the indexes

These commands have not been run from this repository as released.

`benchmark/samples_{au,uk,us}.csv` give the id and portal URL of each sampled
record (8,000 per portal). A script that fetches these records by id is not
provided yet and will be added later. The metadata are not in this repository
(see [DATA.md](DATA.md)), and the scores in the paper were computed on our
copy of the records.

**Fetching.** The existing harvest and sampling entry points below harvest a
whole portal and then draw a sample; on today's portals they do not give the
same 8,000 records. They write `data/elements.jsonl` (au),
`data/uk/elements.jsonl` and `data/us/elements.jsonl`:

```bash
python ingest/fetch_ckan.py                       # au (CKAN)
CORPUS=uk python ingest/fetch_ckan.py             # uk (CKAN)
export DATAGOV_API_KEY=<your api.data.gov key>    # us: needs your own key
CORPUS=us python ingest/fetch_catalog_api.py
CORPUS=us python ingest/fetch_catalog_api.py --merge
CORPUS=<portal> python ingest/sample_elements.py --geo-frac -1
```

**Building.** For each portal (`au`, `uk`, `us`), once its `elements.jsonl`
exists and the backends are up:

```bash
bash eval/run_clear.sh au python ingest/build_indexes.py --english-subfields --embed-fields full
bash eval/run_clear.sh au python ingest/build_vocabulary.py
bash eval/run_clear.sh au python ingest/build_token_df.py
python ingest/build_publisher_lexicon.py --corpus au
```

`eval/run_clear.sh <portal>` loads the portal's settings (`eval/v4.env`,
`eval/uk/v4.env`, `eval/us/v4.env`), which also name the index. For `us`,
`build_publisher_lexicon.py` reads the publisher names from
`data/us/records_raw.jsonl.gz`, which `fetch_catalog_api.py --merge` writes.

## Start the interface

```bash
bash ui/run_ui.sh --demo
```

This starts one worker per portal and the gateway, waits until every worker
is ready, sends one warm-up question per portal, and prints "ready for demo".
Open <http://127.0.0.1:8000>. `--demo` is the configuration described in the
paper: the comparison view, the grounded clarification rule and publisher
linking. Set `UI_PYTHON` if `python` on the `PATH` is not the environment's
interpreter. Stop everything with `bash ui/stop_ui.sh`.

Three example requests:

| Portal | Request | What it shows |
|---|---|---|
| Australia | garbage collection days | a clarifying question with publishers as options (for example, answer "City of Greater Dandenong") |
| United Kingdom | weather records from Britain's national weather service | publisher linking to the Met Office |
| Australia | earthquake data from the national geology agency | a second search round that links Geoscience Australia |

## Run the baselines and ablations

These commands have not been run from this repository as released.

The evaluation runs every sub-question of the benchmark through the system.
They are long: the full system takes about 30 seconds per sub-question on the
test machine, 200 sub-questions per portal. Run from a fresh shell, so no
switch from an earlier command is still set. For each portal (`au` shown):

```bash
python eval/benchmark_input.py --corpus au --out runs/au

# A1 keyword only (BM25)
bash eval/run_clear.sh au env RERANK_MODE=off python eval/run_eval.py \
    --gold runs/au/topics.json --configs a_keyword --out runs/au/A1.json
# A2 dense only, A3 dense + LLM re-ranking
bash eval/run_clear.sh au env RERANK_MODE=off LLM_RERANK_LOG=runs/au/A2A3.llm.jsonl \
    python eval/run_methods.py --gold runs/au/topics.json \
    --configs e_dense,f_dense_llm --out runs/au/A2A3.json
# A4 keyword + dense + cross-encoder
bash eval/run_clear.sh au python eval/run_eval.py \
    --gold runs/au/topics.json --configs b_fused --out runs/au/A4.json
# A5 IADS
bash eval/run_clear.sh au env ENTITY_RESOLVE=link_path python eval/run_eval.py \
    --gold runs/au/topics.json --configs d_agent --out runs/au/A5.json
# B1 without re-ranking
bash eval/run_clear.sh au env ENTITY_RESOLVE=link_path RERANK_MODE=off python eval/run_eval.py \
    --gold runs/au/topics.json --configs d_agent --out runs/au/B1.json
# B2 without publisher linking
bash eval/run_clear.sh au python eval/run_eval.py \
    --gold runs/au/topics.json --configs d_agent --out runs/au/B2.json
# B3 without the decision loop (one round)
bash eval/run_clear.sh au env ENTITY_RESOLVE=link_path python eval/run_eval.py \
    --gold runs/au/topics.json --configs c_routed --out runs/au/B3.json
```

Clarification (18 vague questions, au and uk only; `au` shown). C1 sends the
vague question as is, C2 lets the system ask and a simulated user answer with
the left-out detail, C3 adds the left-out detail to the question:

```bash
bash eval/run_clear.sh au python eval/run_vague.py --vague runs/au/vague.json \
    --gold runs/au/topics.json --condition noask --configs d_agent --out runs/au/C1.json
bash eval/run_clear.sh au env ASK_STYLE=grounded ASK_MAX=1 USER_SIM=template \
    python eval/run_vague.py --vague runs/au/vague.json \
    --gold runs/au/topics.json --condition ask --configs d_agent --out runs/au/C2.json
bash eval/run_clear.sh au python eval/run_vague.py --vague runs/au/vague.json \
    --gold runs/au/topics.json --condition oracle --configs d_agent --out runs/au/C3.json
```

## Recompute the tables from `benchmark/`

This command has been run only with `eval/runs_template.json` changed to point
at our original run files, which are not in this repository.

```bash
python eval/score.py --runs eval/runs_template.json --out runs/scores.json
```

`eval/runs_template.json` names the run files written above. The script
recomputes Recall@10, nDCG@10 and MRR@10 from each row's top ten against the
relevance groups in `benchmark/gold_<portal>.json` (records with the same
title form one group), averages over each topic's four sub-questions and then
over topics, and prints the method table, the ablation table, the paired
differences to IADS with topic-level confidence intervals and Holm-corrected
p-values, and the clarification table. LLM output can vary between machines,
so new runs give close but not identical numbers.

## Tests

```bash
for t in tests/test_*.py; do python "$t" || echo "FAILED: $t"; done
```

The tests run offline (no backend, no LLM). Some parts of
`tests/test_us_fetch.py` and `tests/test_us_places.py` need files that are not
in this repository and are skipped with a message.

## Third-party data and software

- Map tiles: © OpenStreetMap contributors (ODbL), loaded from
  tile.openstreetmap.org.
- State and region boxes for the uk and us portals: derived from Natural Earth
  (public domain).
- Frontend libraries in `ui/static/vendor/`: Vue (MIT), Leaflet (BSD-2-Clause),
  Cytoscape.js (MIT) and the Inter typeface (SIL Open Font License 1.1); their
  licence files are next to them.

## License

The code is released under the MIT License (see [LICENSE](LICENSE)). The
files in `benchmark/` are released under CC BY 4.0 (see [DATA.md](DATA.md)).

## Citation

The paper is under review at the WWW 2027 Demo Track.

```bibtex
@inproceedings{han2027iads,
  title     = {Interactive Agentic Dataset Search (IADS)},
  author    = {Han, Minghao and Qi, Jianzhong},
  booktitle = {Companion Proceedings of the ACM Web Conference 2027 (WWW '27), Demo Track},
  year      = {2027},
  note      = {Under review}
}
```
