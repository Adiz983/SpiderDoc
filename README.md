# Spider Doc

A terminal tool that crawls a financial filing (10-K, 8-K, etc.) with an animated spider, highlights key figures and risk language, and prints a report. Python 3.8+, standard library only, no API keys.

## Run it

```
python3 spider.py --demo                          # fictional filing, works offline
python3 spider.py --file filing.htm               # any saved filing (.htm, .html, .txt)
python3 spider.py MSFT --email you@example.com    # latest 10-K from SEC EDGAR
python3 spider.py HOOD --form 8-K --insiders --email you@example.com
```

Options: `--speed 2` (faster), `--budget 30` (target seconds), `--no-anim` (no animation, just the report).

The SEC requires contact info on every request, so `--email` is needed when using a ticker (or set `SEC_EMAIL`).

## What it does

- Highlights headings and flagged sentences in green and numbers in purple.
- Pulls about 40 key figures per filing (revenue, income, cash, etc.) and shows the change versus the prior period.
- Flags language such as material weakness, restatement, related party, non-GAAP, and guidance.
- With `--insiders`, reads recent Form 4 filings and flags sales shortly before earnings and clusters of insider sales.
- Saves a Markdown report when it finishes.

## Limits

- Output is for review only. Flagged language and insider timing patterns are not evidence of wrongdoing or insider trading.
- Page numbers are estimates, and figures are shown as printed (units are not converted).
- Uses simple rules, not AI, so it can miss things or flag false positives.
