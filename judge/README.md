# Judge (Korean name probability)

Scripts for scoring person names by probability of being Korean (0.0–1.0).

## Commands
### Merge tmp CSVs into final results

`results/tmp/` holds chunk CSVs (`0.csv`, `1000.csv`, …). To merge them into one JSON + CSV and fill missing names (from `names.json`) with NaN:

```bash
cd judge
python results/tmp_compiler.py
```

**Output (in `judge/`):**

- `results.json` – full list, one entry per name in `names.json`
- `results.csv` – same as CSV (missing names get score=NaN)

### Run Grok batch (full)

```bash
cd judge
python judge_authors_grok.py
```

### Run Grok batch (test, 30 names)

```bash
python judge_authors_grok.py --test
```

### Options (Grok)

| Option | Description |
|--------|-------------|
| `--test` | Process only 30 names (+ 3 extra Korean names) |
| `--input PATH` | Input JSON (default: `names.json`) |
| `--output PATH` | Output JSON (default: `results/results_grok.json`) |
| `--max-per-batch N` | Max names per batch (default: 1000) |
| `--debug` | Save raw first result page to `.debug_first_page.json` |
