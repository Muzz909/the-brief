# Going live with v2 (about 35 minutes)

v1 (Apps Script + Google Sheet) stays as the **front door**: sign-up, settings, votes, clicks.
v2 (this repo, Python on GitHub Actions) does the **thinking**: events, ranking, grounded writing, sending.

You need: v1 deployed (DEPLOY.md), a GitHub account, GitHub Desktop, a Gmail app password, an Anthropic API key, and a fine-grained GitHub token.

---

## 1. Put the code on GitHub (5 min)

1. Unzip `the-brief-v2.zip` somewhere easy, e.g. `Documents\the-brief`.
2. GitHub Desktop → **File → Add local repository** → pick that folder → it offers to **create a repository** → yes.
3. **Publish repository** → name `the-brief`. Untick "Keep this code private" (public = free Actions minutes and a portfolio link).
   If you already created an empty `the-brief` repo on github.com, use **Repository → Repository settings → Remote** and paste its URL instead, then **Push**.

## 2. Turn on GitHub Pages, for click tracking (1 min)

Repo on github.com → **Settings → Pages** → *Source*: **Deploy from a branch** → Branch **main**, folder **/docs** → Save.
Your Pages URL is `https://<your-username>.github.io/the-brief`.

## 3. Add secrets and variables (5 min)

Repo → **Settings → Secrets and variables → Actions**.

**Secrets** tab → *New repository secret*, one by one:

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your Anthropic key |
| `APPS_SCRIPT_URL` | your v1 web app URL (ends in `/exec`) |
| `BRIEF_API_KEY` | make up a long random password (30+ characters). You'll paste the same one into Apps Script |
| `GMAIL_ADDRESS` | the Gmail you send from |
| `GMAIL_APP_PASSWORD` | the 16-character app password |

**Variables** tab → *New repository variable*:

| Name | Value |
|---|---|
| `PAGES_URL` | `https://<your-username>.github.io/the-brief` |
| `BRIEF_EXPERIMENT_START` | tomorrow's date, e.g. `2026-11-25` |
| `BRIEF_EXPERIMENT_END` | a week later, e.g. `2026-12-01` |

## 4. Update v1 (5 min)

1. Apps Script editor → replace `Code.gs` with `apps_script/Code.gs` and `Settings` with `apps_script/Settings.html` from this repo. Save.
2. **Project Settings → Script Properties** → add:

| Property | Value |
|---|---|
| `BRIEF_API_KEY` | the same random password as the GitHub secret |
| `GITHUB_REPO` | `<your-username>/the-brief` |
| `GITHUB_TOKEN` | your fine-grained token (Actions + Contents read/write on this repo only) |

3. **Deploy → Manage deployments → ✏️ → Version: New version → Deploy.** The URL stays the same.
4. Run **setup** once more from the editor (it adds the *Clicks* tab).

Don't set `V2_MODE` yet.

## 5. First live run (5–10 min)

1. Repo → **Actions** → if asked, **enable workflows**.
2. **The Brief** workflow → **Run workflow** → command **ingest** → Run. The first run downloads the embedding model (~1 min).
   Open the run: the log shows feeds fetched, articles, events.
3. Run workflow again → command **preview** → tokens: your reader token (the long code after `?t=` in your *Edit my interests* link) → Run.
   Within ~3 minutes your first v2 edition arrives. The run's **Summary** shows stories, claims kept and timing; the HTML is under **Artifacts**.

## 6. Hand over (1 min)

When the preview looks right: Apps Script → Script Properties → add `V2_MODE` = `1`.
From now on v1 wakes v2 the moment each reader's send time arrives; the hourly run is a safety net. Previews and welcome editions go through v2 too.

## 7. Turn on the eval gate (5 min)

1. Run workflow → command **snapshot**. Download the artifact, put `snapshot-YYYY-MM-DD.json.gz` in `evals/snapshots/`, commit, push.
2. On your laptop (in the repo folder): `pip install -r requirements-dev.txt` then
   `python -m evals.run_evals --label` → fill `evals/labels/pairs.csv` (same / different) and `relevance.csv` (0–3). Even 50 rows each is useful.
3. `python -m evals.run_evals --update-baseline` → commit `evals/baseline.json`. From then on, every change that makes quality worse fails the **Evals** check.

## Useful commands

| Where | What |
|---|---|
| Actions → Run workflow → `experiment` | Interleaving readout: v2 vs baseline wins, sign test |
| Actions → Run workflow → `due` | Send any edition that's due now |
| Laptop: `python -m pytest -q` | 18 offline tests |
| Laptop: `node apps_script/test/harness.js` | 49 checks on the Apps Script side |

## If something goes wrong

| Symptom | Fix |
|---|---|
| Run fails at "Run" with `APPS_SCRIPT_URL and BRIEF_API_KEY must be set` | A secret name is misspelled |
| `Apps Script API error: unauthorized` | `BRIEF_API_KEY` differs between GitHub and Apps Script |
| `SMTPAuthenticationError` | Wrong app password, or 2-Step Verification is off |
| A section says "temporarily unavailable" | That source failed this run; the rest still sent |
| No edition at send time | Check Apps Script → Executions for `dispatch failed`; the hourly run will still send within the hour |
