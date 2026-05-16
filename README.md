# JD-Search

**Your personal Singapore job-search assistant** — wakes up every morning, scans hundreds of new job postings across multiple Singapore career sites, throws away the ones you can't apply to (citizens-only, Mandarin-required, wrong seniority), scores the rest against your CV, and emails you a one-page ranked digest.

No more checking 5 different career sites every day. No more applying to roles that say "Singapore citizens & PR only" at the bottom. No more wondering whether you're a good fit.

---

## What it does, in plain English

Every morning at 7am Singapore time, JD-Search:

1. **Fetches** all brand/marketing job postings from:
   - **MyCareersFuture.sg** (the Singapore government job board — ~200 new listings/day)
   - **LVMH careers** (~58 Singapore roles in beauty/fragrance/luxury)
   - **Unilever** (Workday)
   - **Honeywell** (Oracle HCM)
   - More companies you can plug in later

2. **Throws away** the ones with deal-breakers — by actually reading the job description, not just the title:
   - "Open to Singapore citizens & PR only" → rejected
   - "Must be bilingual in English and Mandarin" → rejected
   - "Brand Ambassador (face-to-face)" → rejected
   - "Requires 15+ years experience" → rejected (you only have 7)
   - Too junior (1-2 yrs) → rejected

3. **Scores the survivors** against your actual CV using AI (OpenAI's GPT-5-mini). Each job gets:
   - An **overall score 0-100**
   - A list of **why your background fits** (strengths)
   - A list of **what you're missing** (gaps you'll need to address in your cover letter)

4. **Emails you a digest** of the top ~20 roles, grouped into:
   - 🟢 **Top matches** (score ≥ 75) — apply today
   - 🟡 **Worth a look** (score 60-74) — worth reading

A typical morning digest looks like this:

```
JD-Search Daily Digest — 2026-05-16
Week 2026-W20 · fetched 223 · new 201 · kept 168 · rejected 24

## Top matches (score ≥ 75)
### [83] Senior Marketing Executive — MYFIRST TECH ASIA
Singapore · https://www.mycareersfuture.gov.sg/job/...
- Anchor similarity: 85 · Resume fit: 80
- Strengths: Consumer electronics brand experience at Samsung;
  end-to-end NPD, retail activation, P&L management;
  integrated marketing across TVC, digital, social...
- Gaps: No explicit child-focused product experience;
  limited evidence of advanced CRM/analytics tools...

### [82] Marketing Manager — HEGEN PTE. LTD.
...
```

---

## What you'll need before you start

You'll be setting this up once on your Mac. It takes about 20 minutes.

| Thing | Why | Where to get it |
|---|---|---|
| **Python 3.11+** | The tool runs on Python | macOS already has it; `python3 --version` to check |
| **`uv`** (Python package manager) | Installs the tool's dependencies | Run `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **OpenAI API key** | The AI that scores jobs against your CV | https://platform.openai.com/api-keys — costs ~$0.50/month at default settings |
| **Gmail account + app password** | To email you the daily digest | See "Setting up Gmail" below |
| **Your CV in LaTeX awesome-cv format** | The tool reads this to know your background | If you don't have one, copy from https://github.com/posquit0/Awesome-CV |

> **Don't have a LaTeX CV?** You can skip the LaTeX parsing entirely and write your candidate profile by hand in `target_profile/profile.yaml`. See "Option B" below.

---

## Setup — step by step

### 1. Get the code

Open Terminal and run:

```bash
git clone git@github.com:NayMyatMin/JD-Search.git
cd JD-Search
```

### 2. Install the tool

```bash
uv sync
```

This downloads all the libraries the tool needs (about 2 minutes the first time).

### 3. Add your API keys

Create a file called `.env` in the project folder. You can do this in any text editor (TextEdit, VS Code, etc.). Paste this in, then fill in your real values:

```
OPENAI_API_KEY=sk-...your-openai-key-here...
GMAIL_USER=you@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
EMAIL_TO=you@gmail.com
```

#### Setting up Gmail

You **cannot** use your normal Gmail password here. You need an "app password":

1. Go to https://myaccount.google.com/security
2. Make sure **2-Step Verification** is on
3. Search for "App passwords" → create one called "JD-Search"
4. Google gives you a 16-character password like `abcd efgh ijkl mnop` — paste that into `GMAIL_APP_PASSWORD`

### 4. Tell the tool about yourself

Open `target_profile/profile.yaml` and edit it to match your background:

```yaml
candidate_name: Your Name
current_role: Brand Manager
current_company: Acme Co.
years_experience: 7
location_now: Bangkok, Thailand

hard_requirements:
  english_only: true            # set false if you speak Mandarin/Malay
  requires_sponsorship: true    # set false if you're SG citizen/PR
  reject_citizen_pr_only: true  # auto-reject SG-citizen-only roles

target_seniority:
  min_years: 4
  max_years: 10

role_families:
  - Regional Brand Marketing
  - Product Marketing
  - Brand Strategy
  # add what you want

sectors_of_interest:
  - FMCG
  - Beauty & Fragrance
  - Consumer Electronics
  # add what you want

avoid:
  - Brand Ambassador / face-to-face roles
  - Mandarin-required roles
```

### 5. Connect your CV

**Option A — you have a LaTeX awesome-cv resume:**

Point the tool at your CV folder:

```bash
export JD_SEARCH_RESUME_DIR=/path/to/your-resume/awesomecv
uv run jd build-profile
```

This reads your LaTeX source files and converts them to a plain-text profile the AI uses to score jobs.

**Option B — you don't have a LaTeX CV:**

Just create the file by hand:

```bash
mkdir -p data
nano data/resume_profile.txt
```

Paste in 1-2 paragraphs about your career (job titles, achievements, skills) — the same kind of text that would go in a LinkedIn "About" section. The AI uses this to compare against job postings.

### 6. Try a dry run (no email, no AI cost)

```bash
uv run jd run --no-email --dry-run
```

This fetches all postings and writes a digest, but skips the AI scoring step. You should see something like:

```
2026-05-16 INFO  MCF fetched 206 postings across 7 keywords
2026-05-16 INFO  adapter mycareersfuture → 206 postings
2026-05-16 INFO  adapter smartrecruiters → 17 postings
2026-05-16 INFO  223 fetched, 201 new
2026-05-16 INFO  digest written → digests/2026-W20/2026-05-16.md
```

Open `digests/2026-W20/2026-05-16.md` — you'll see the list of fetched jobs (no scores yet, since you skipped the AI).

### 7. Do a real run

```bash
uv run jd run --no-email
```

This time the AI scores everything (~15-25 minutes the first time, much faster on later runs since it only scores new jobs). When done, your digest at `digests/2026-W20/YYYY-MM-DD.md` has full scores, strengths, and gaps.

### 8. Test the email

```bash
uv run jd email-test
```

Sends a 2-line test email to confirm Gmail works. If you don't get it, check:
- Did you use an app password (not your normal Gmail password)?
- Is `notification.email.enabled: true` in `config/config.yaml`? (It's `false` by default — flip to `true` when ready)

### 9. Set up the daily 7am email

```bash
bash scripts/install_cron.sh
```

This installs a daily schedule so the tool runs every morning at 7am Singapore time without you doing anything.

---

## Daily use

Once it's running, you don't have to touch anything. Just check your inbox at 7am.

Useful commands if you want to run things manually:

```bash
# Run the pipeline right now (will only score genuinely new jobs)
uv run jd run

# Run without sending email (useful for testing)
uv run jd run --no-email

# Just fetch — don't call the AI at all
uv run jd run --dry-run

# Cap how many new jobs to score (good for limiting cost)
uv run jd run --limit 20

# Re-generate today's digest from already-scored data (free, instant)
uv run jd digest

# Render today's digest as a PDF
uv run jd pdf

# Test the Gmail connection
uv run jd email-test
```

---

## Customizing what the tool looks for

### Change which job titles to scan

Edit `config/config.yaml` → `filters.relevant_title_keywords`:

```yaml
filters:
  relevant_title_keywords:
    - brand
    - marketing
    - campaign
    - growth
    - product manager  # add your own
```

Only postings whose **title** contains one of these words will be scored.

### Change which languages get rejected

Edit `config/config.yaml` → `gate.reject_non_english_languages`:

```yaml
gate:
  reject_non_english_languages:
    - Mandarin
    - Chinese
    - Malay
    - Tamil
    - Japanese
    - Korean
```

If a job description **requires** any of these, it gets auto-rejected.

### Change the score thresholds

```yaml
scoring:
  top_match_threshold: 75      # green section
  worth_a_look_threshold: 60   # yellow section
  max_jobs_per_run: 400        # cap AI cost per day
```

### Add a new company to scan

The tool already supports Workday, SmartRecruiters, and Oracle HCM career sites. To add a new Workday company:

```bash
uv run jd verify-workday https://COMPANY.wdN.myworkdayjobs.com/SITE
```

It will probe the careers page and print a config block you can paste into `config/companies.yaml`.

---

## Output: where things live

```
digests/                    Your daily digests (markdown + PDF)
  2026-W20/
    2026-05-16.md          ← today's digest
    2026-05-16.pdf
data/
  jobs.db                  ← every job ever seen + its score
  resume_profile.txt       ← your CV in plain text
config/
  config.yaml              ← title filters, language filters, thresholds
  companies.yaml           ← which companies to scrape
target_profile/
  profile.yaml             ← who you are
  reference_jds/           ← 4 sample JDs you'd love (the AI uses these as anchors)
```

The `data/jobs.db` file is a small database that tracks every job you've ever seen, so:
- A job will only appear in **one** digest ever (no spam)
- You can later query "show me everything Shiseido has posted in the last month"
- Re-runs are fast because already-scored jobs aren't re-scored

---

## Troubleshooting

**"OPENAI_API_KEY missing"**
Your `.env` file is missing or doesn't have the key. Check it exists in the project root and has `OPENAI_API_KEY=sk-...` on one line.

**"email failed — check .env and app password"**
You used your normal Gmail password instead of an app password. Generate one at https://myaccount.google.com/apppasswords.

**The digest is empty / shows "kept 0"**
Probably one of:
- You ran `--dry-run` (which skips scoring) — try without it
- All matching jobs were already shown in a previous digest. Try `uv run jd digest` to see today's report from existing data
- Your title keywords in `config/config.yaml` are too narrow

**Same job showing up over and over**
Shouldn't happen — once a job is in a digest, `seen_in_digest_on` is set and it won't reappear. If it does, delete the database with `rm data/jobs.db` and re-run.

**AI costs are too high**
Lower `scoring.max_jobs_per_run` in `config/config.yaml` (e.g. 50 instead of 400). The tool uses GPT-5-mini which is cheap (~$0.50/month at 400 jobs/day), but you can also pass `--limit 20` to cap any single run.

---

## How the AI scoring works (for the curious)

Every kept job goes through two AI calls:

1. **Extraction** — pulls structured data out of the JD: seniority, required years, languages, citizenship requirements, sectors. This is what powers the deal-breaker filter.

2. **Scoring** — compares the extracted JD against your `profile.yaml`, your `resume_profile.txt`, AND four "anchor" job descriptions you'd love (in `target_profile/reference_jds/`). The score is a blend of:
   - **Anchor similarity** — how similar is this JD to your dream JDs?
   - **Resume fit** — how well does your background actually fit this specific role?

The final score is 0-100. Anything ≥75 is a strong match.

You can edit the 4 reference JDs in `target_profile/reference_jds/` — put 4 job postings you'd say yes to immediately, and the AI will use those as the "north star" when ranking.

---

## What it doesn't do

- **Doesn't apply for you** — read-only digest. The application is still on you.
- **Doesn't scan LinkedIn** — LinkedIn blocks scrapers. There's an experimental adapter (`pip install 'jd-search[linkedin]'`) but you'd need to enable it manually.
- **Doesn't know about hidden constraints** — if a job is posted by a recruiter (e.g. "Recruit Express") for an unnamed employer who actually requires Mandarin, the digest can't catch that.
- **Doesn't handle non-Singapore jobs** — it's pre-configured for Singapore. You can adapt the adapters but it'll take work.

---

## Quick command reference

| Command | What it does |
|---|---|
| `uv run jd run` | Full pipeline: fetch → filter → score → digest → email |
| `uv run jd run --no-email` | Same, but don't send email |
| `uv run jd run --dry-run` | Just fetch, skip the AI |
| `uv run jd run --limit 20` | Cap AI calls to 20 jobs |
| `uv run jd digest` | Re-write today's digest from saved data (no AI calls) |
| `uv run jd pdf` | Render today's digest as a PDF |
| `uv run jd email-test` | Send a tiny test email |
| `uv run jd build-profile` | Re-parse your LaTeX CV into plain text |
| `uv run jd fetch-only` | Test one adapter without any AI calls |
| `uv run jd verify-workday URL` | Verify a Workday company before adding to config |
