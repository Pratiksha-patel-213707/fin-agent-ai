# fin-agent

Small Python project for validating manual journal entries against a chart of accounts and a set of accounting checks.

## What it does

- Loads a chart of accounts and a batch of manual adjustments from `input/`
- Runs deterministic validation checks
- Uses Gemini to turn validation issues into plain-English explanations
- Writes JSON and text reports to `output/`

## Project layout

```text
fin-agent/
├── agents/
├── core/
├── input/
├── output/
├── tests/
├── main.py
└── README.md
```

## Setup

Requirements:

- Python 3.11+
- A `GEMINI_API_KEY`

Install dependencies:

```bash
pip install google-genai pytest
```

Set the API key in your shell, then run:

```bash
python main.py
```

Optional flags:

```bash
python main.py --input-dir input/ --output-dir output/ --quiet
```

Run tests:

```bash
python -m pytest tests -v
```
