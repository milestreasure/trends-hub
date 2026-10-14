# Trends Hub

A free, automated trends site for magazine publishers. A daily GitHub Action collects UK and US news per sub-category
(Google News RSS), adds Wikipedia surges, YouTube UK chart entries and other signals, groups them into trends, scores
them (last 7 days against the 7 before) and writes `docs/data/trends.json`. `docs/index.html` is the site.

## Files
- `taxonomy.yaml`: segments and sub-categories from Categories_and_Sub_Cats.xlsx, each with a search query.
- `topics.yaml`: keyword lists for the cross-section themes.
- `publishers.yaml`: which publishers count as UK or US.
- `pipeline/run.py`, `pipeline/signals.py`, `pipeline/platforms.py`: the daily job.
- `.github/workflows/daily.yml`: schedule (05:00 UTC daily, or run it by hand from the Actions tab).
- `docs/index.html`: the site, served by GitHub Pages from the `main` branch and `/docs` folder.

## Secrets (Settings > Secrets and variables > Actions)
- `YOUTUBE_API_KEY`: free, enables YouTube.
- `ANTHROPIC_API_KEY`: optional, adds written summaries and editor angles. Without it the site still shows
  data-based guidance on every trend.

## Notes
- The run makes about 300 requests and takes roughly 8 to 10 minutes.
- Foreign Language Titles is not covered yet (needs local-language sources).
- Google News RSS is unofficial and its terms limit commercial use. Check before client-facing use.
