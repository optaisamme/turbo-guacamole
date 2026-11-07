# Job Search Automation

Each time you start a search, this tool looks through public job listings, keeps the jobs that
fit **your** rules (job titles, state, salary, and more), checks that each application link
works, and shows you the results. You set it up once on the GitHub website. No coding needed.
Searches run only when you start one, unless you turn on automatic weekly searches.

## Why I built this

The search filters on sites like LinkedIn, Indeed and HiringCafe either did not let me narrow
my search the way I wanted, or did not search a wide enough variety of job boards. I built
this tool to meet the needs of the students I work with, and I am sharing it for anyone else
it can help.

---

## Your 4 steps

| Step | What you do | Where | Time |
|---|---|---|---|
| **1. Make your own copy** | Copy this project into your own GitHub account ([picture of where to click](SETUP.md#step-1-make-your-own-copy)) | [SETUP.md](SETUP.md), Step 1 | 2 minutes |
| **2. Answer the setup questionnaire** | In **your copy**: [open the questionnaire list](../../issues/new/choose) (**Issues** tab, then **New issue**), click **Job search setup**, answer the questions, then click **Create**. Your settings are saved and your first search starts. Stay on the page that opens: a reply links to the running search | [SETUP.md](SETUP.md), Step 2 | 10 minutes |
| **3. Watch or start a search** | To watch a search: in **your copy**, click the **Actions** tab, then **Run job search** (yellow dot = running, green check = finished). To start another: **Run workflow** (twice). Automatic weekly searches start **off** (see below) | [HOW_TO_RUN_A_SEARCH_NOW.md](HOW_TO_RUN_A_SEARCH_NOW.md) | 1 minute to start, 10 to 20 minutes to finish |
| **4. Read your results** | Open the results folder | **[JOB_SEARCH_RESULTS](JOB_SEARCH_RESULTS)** folder | Any time |

Do the steps **in order**. A search cannot run until you have your own copy (step 1) and at
least one job title in your settings (step 2). First time? Start with **[SETUP.md](SETUP.md)**;
it walks you through steps 1 to 4.

**Already set up?** Open your copy, then click
**[START A SEARCH NOW](../../actions/workflows/run_job_search.yml)**, then click the
grey **Run workflow** button and the green **Run workflow** button.
[Picture of where to click](HOW_TO_RUN_A_SEARCH_NOW.md#the-picture-where-to-click)

---

## Your privacy

1. Your questionnaire answers and your job search results are saved in your copy. In a **public** copy,
   anyone can read them, so make your copy **private** (SETUP.md, Step 1).
2. Do not enter passwords, keys, contact details, a résumé or anything confidential in the questionnaire.
3. Changing an answer does not erase the old one: GitHub keeps the history of the questionnaire issue and of
   your settings files. Deleting the issue removes it; removing old settings from the files' history takes
   extra steps (GitHub's guide: "Removing sensitive data from a repository").
4. What leaves your copy: your job titles (and skills-based matching keywords, if on) are sent to job sites as
   search words. With the free web search key, your job titles, your state (or "remote") and those keywords are
   also sent to Tavily. Nothing else from your answers is sent anywhere.

---

## Where your results are

Open the **[JOB_SEARCH_RESULTS](JOB_SEARCH_RESULTS)** folder.

1. **The folder's main page** opens automatically and shows your **latest results**:
   a. **Jobs that match all your rules**, with an **Apply** link for each.
   b. **Near misses:** jobs that did not meet one of your rules, and which rule. Other rules
      passed or could not be checked.
   c. **Jobs whose link could not be checked**, with a link to find the employer's own posting.
   d. **Jobs that need checking:** jobs that failed none of your rules but did not say enough to be
      sure (for example, no salary listed), with what to check.
   e. **Popular job boards to search yourself:** one-click searches for your job titles on 32
      boards, grouped by type: large job sites (LinkedIn, Indeed, Google Jobs and more);
      students, hourly and government (Handshake, CareerOneStop, USAJOBS, Snagajob, Upwork,
      AmeriCorps);
      nonprofit and international (Idealist, ReliefWeb, Chronicle of Philanthropy, Devex and
      more);
      diversity (DiversityJobs, PowerToFly and more); and tech and startups.
2. **One report per search**, numbered and dated, for example
   `REPORT_001_2026-11-06_13-47_UTC.md`. The newest is listed first. Each report lists the jobs
   found in that search: matches, jobs that need checking, and near misses.

Also:

1. **Actions tab:** shows whether a search worked (green check) or needs a fix (red X).
   Click a finished search to see its results right on that page.

Times are in UTC. Mountain Time is UTC minus 7 hours in winter and minus 6 in summer.

---

## Changing your settings

All settings are made with the **[setup questionnaire](../../issues/new/choose)** (Issues tab, New issue,
Job search setup). You never need to edit a file.

To change something: open the **[JOB_SEARCH_RESULTS](JOB_SEARCH_RESULTS)** folder and click **click here to
change these settings** (under "Your settings"). The questionnaire opens with your current text answers
filled in. Change only what you want, then click **Create**.

1. A text box left empty turns that setting off.
2. A dropdown left on "None" keeps its current choice.

It covers: job titles, remote only or not, your state, salary, title words to skip, words that rule
out a job, employers to skip, certifications, your skills, companies to watch, skills-based matching,
how many jobs to look through, and automatic weekly searches (they start **off**).

**Also optional:** a free web search key. It is added in **Settings**, not in the questionnaire. See
[Other optional edits: a free web search key](#other-optional-edits-a-free-web-search-key).

### What happens after you change a setting

1. Your change is used in the **next search** (an automatic weekly search, if you turned it on, or one you start yourself).
2. Each search first checks your settings. If something is wrong, the search stops and says
   what to fix.
3. Jobs found earlier are checked again with your new settings:
   a. A job that now fits is shown as **Newly verified (seen before)**.
   b. A job that now fails one rule is listed under Near Misses in the next report. Jobs that
      fail 2 or more rules are only counted in the totals.
   c. A job already shown to you is never shown again as new.
4. To start completely fresh, delete the file `data/jobs.sqlite` (the only file you would ever
   delete). The next search starts over.

---

## Other optional edits: a free web search key

Without a key, every search already checks 7 job sites and company careers pages. Adding
one free key turns on a **web search**: for each of your job titles, the tool also searches
the whole web, including company careers pages, and finds jobs that are on no job site. It
also finds new companies to check. It is optional and stays off until you add the key.

| Service | What it adds | Where to get the free key | Secret name to add |
|---|---|---|---|
| **Tavily** (web search) | Searches the whole web for each of your job titles, including company careers pages | https://app.tavily.com: on the log-in page that opens, click **Sign up** (or continue with a Google, GitHub, LinkedIn or Microsoft account). After signing in, copy your API key from your Tavily account page (it starts with `tvly-`) | `TAVILY_API_KEY` |

**Free plan:** 1,000 web searches a month. This tool uses 2 web searches for each of your job
titles, plus 2 for skills-based matching when it is on. For example, 5 job titles use 10 web
searches per search, about 45 a month with weekly searches. If the free searches run out, the rest
of each search still runs.

### How to add the key

1. Sign up at https://app.tavily.com and copy your API key (see the table).
2. Open **your copy** of this project on github.com.
3. Click **Settings** (top row of tabs, far right).
4. On the left, click **Secrets and variables**, then **Actions**.
5. Click the green **New repository secret** button.
6. In **Name**, type `TAVILY_API_KEY` exactly (capital letters and underscores included).
   In **Secret**, paste the key. Click **Add secret**.
7. Start a search. In the report, under "Where this search looked", the Web search (Tavily)
   row shows **Yes** in "Searched this time?" when it worked. **Off: no key** means the secret
   is missing or misspelled. **No: error** means the key was not accepted.

The key is private: only you can see it, and it is never shown in results. To remove it,
open the same page and click the trash icon next to it.

**Boards that do not offer keys:** LinkedIn, Indeed, Handshake, Idealist and the other popular
boards on your results page offer no key for automated searching. Use the one-click search
links on the results page for those.

---

## Folders you can ignore

| Folder or file | What it is |
|---|---|
| `data/` | Jobs the tool has already seen. Updated automatically |
| `jobsearch/`, `scripts/`, `tests/` | The program itself |
| `SETUP.md`, `HOW_TO_RUN_A_SEARCH_NOW.md` | Guides (read them; no need to change them) |

---

## How a search works

1. **Checks your settings** and lists what is ON and OFF.
2. **Collects public job listings** from these job sites:
   a. **No setup needed:** Himalayas, The Muse, Remote OK, Jobicy, We Work Remotely, Remotive
      and Hacker News "Who is hiring?" (mostly tech jobs). Several send their newest jobs
      whatever your job titles are (for example The Muse's newest 1,000 and all of Jobicy's US
      remote jobs; see "How many jobs to look through" in the questionnaire), so each search looks
      at a few thousand listings.
   b. **Web search (free key, recommended, see SETUP.md Step 6):** searches the whole web for
      each of your job titles, finding jobs that are on no job site above. For each title it
      runs 2 searches: one across company careers pages (Greenhouse, Lever, Ashby,
      SmartRecruiters, Workday) and one across the rest of the web. Every result is checked
      like any other job.
   c. **Company careers pages** on Greenhouse, Lever, Ashby or SmartRecruiters. A company is
      added when a job matching your job titles or skills is found on its own careers page;
      every open job there is then read each search. It is dropped after more than 5 searches
      in a row with no matching job. Companies you add in the questionnaire are always read.
3. **Keeps jobs with your job titles.** With skills-based matching on (questionnaire), it
   also keeps jobs with other titles whose listing has your required keywords and enough of
   your skills (from the questionnaire). Those are marked "(skills match)".
4. **Applies your rules**: remote only (on unless you turn it off), your state, salary,
   certifications, your own rules. When a
   posting is unclear, the job is listed under **Jobs that need checking** instead of guessed.
5. **Checks the application link** to make sure the job is still open.
   Himalayas blocks automated link checks. A Himalayas job counts as open when Himalayas'
   own job feed listed it during the search and its closing date has not passed. These
   jobs are marked **"third-party site, link not checked."**
6. **Compares each job with your experience.**
7. **Saves the results** in [JOB_SEARCH_RESULTS](JOB_SEARCH_RESULTS).

The tool never applies to jobs or contacts employers. It does not search popular boards
such as LinkedIn, Indeed, Handshake or Idealist: they have no public data feed, require
signing in, block automated requests, or their terms do not allow automated searching.
Instead, the results page has **one-click search links** for 32 of these boards, built from
your job titles, so you can open those searches yourself.
It does not get around sites that block automated checks.

---

## For programmers

```bash
python3 -m pytest -q
```

```bash
python3 -m jobsearch check
```

```bash
python3 -m jobsearch run --dry-run
```
