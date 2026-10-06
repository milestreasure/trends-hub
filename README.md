# Magazine Trends Hub

Free, automated trends hub. A daily GitHub Action pulls UK news per magazine segment (Google News RSS),
clusters headlines into trends, scores them (last 7 days vs the 7 before), and writes `docs/data/trends.json`.
`docs/index.html` is the static front end.

## Set up
1. Create a GitHub repo and push this folder.
2. Settings > Pages > Deploy from branch > `main` / `/docs`.
3. Actions > "Update trends" > Run workflow (it then runs daily at 05:00 UTC).
4. Optional: add repo secret `ANTHROPIC_API_KEY` to get a written summary and editorial angle per trend.

## Local run
`pip install -r requirements.txt && python pipeline/run.py`, then `python -m http.server -d docs`.

## Edit coverage
Add or change queries in `taxonomy.yaml` (segment > channel: search query).

## Notes
- Google News RSS has no formal SLA and its terms limit commercial use. Check before client-facing use.
- Not yet built: cross-segment themes, local-language segments, editor approval step.
