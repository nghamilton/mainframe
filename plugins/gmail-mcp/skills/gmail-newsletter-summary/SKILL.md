---
name: gmail-newsletter-summary
description: >
  Summarise unread Gmail newsletters and blog post emails into skimmable bullet points.
  Trigger this skill whenever the user asks to: "summarise my emails", "what's in my inbox",
  "catch me up on newsletters", "digest my unread emails", "what newsletters do I have",
  or any similar phrasing that implies reading and summarising email content.
  Only process emails that are newsletters or blog post updates -- ignore transactional,
  personal, work, or notification emails entirely.
compatibility: >
  Requires the gmail-mcp server running (localhost:3000 or via Cloudflare tunnel).
  Configure in .mcp.json with type "http" and url "http://localhost:3000/mcp".
  This server provides all tools: gmail_search_messages, gmail_read_message,
  gmail_mark_messages_read, gmail_archive_messages.
---

# Gmail Newsletter Summary Skill

## Purpose

Fetch unread Gmail messages, filter to newsletters and blog posts only, and produce a
skimmable bullet-point digest. Points are deduplicated across emails -- if two newsletters
cover the same topic, merge their details into one point. Each point links to its source
email(s) for fact-checking.

**This skill uses sub-agents for extraction to avoid exhausting the main context window.**

---

## Workflow

### Step 1 -- Fetch unread emails

Use the `gmail_search_messages` tool from the gmail-mcp server to search for unread messages:

```
gmail_search_messages(query: "is:unread in:inbox", maxResults: 150)
```

This returns messageId, from, subject, and date for each result. You do NOT need to
fetch full message bodies yet -- classification in Step 2 uses only from/subject.

Fetch up to 150 results to ensure at least 20 newsletters survive the Step 2 filter.
Most inbox emails are non-newsletters, so a large initial fetch is necessary.

**Do NOT record any messageIds yet.** IDs are only added to the approved list during
Step 2, after classification. Recording them here and passing them to Step 7 would mark
non-newsletter emails as read.

### Step 2 -- Filter to newsletters and blog posts only

Classify each email. **Include** only emails that match these patterns.

**As you classify each email, maintain two explicit lists:**
- `newsletter_ids` -- messageIds of emails that PASS the filter. Only these will be marked read in Step 7.
- (implicitly) everything else -- never added to `newsletter_ids`, never marked read.

**If you are uncertain whether an email is a newsletter, do NOT add it to `newsletter_ids`.**
When in doubt, exclude.

**Always include -- known newsletters (no further classification needed):**
- Peter Attia / peterattiamd.com -- health, longevity, medical science
- The Neuron / theneurondaily.com -- AI news digest
- Seeking Alpha Wall Street Breakfast (Sunday edition only; exclude SA Breaking News,
  SA Morning Briefing, SA Cryptocurrency Digest, SA Pre-market summaries)
- The Economist newsletters (Economist Today, Well Informed, etc.)
- Import AI (Jack Clark / importai.substack.com)
- Exponential View (Azeem Azhar / exponentialview.co)
- NLP Newsletter (nlpnews@substack.com)
- DL News newsletters
- EdWeek newsletters
- Vox Future Perfect
- Homo Sapiens / Duncan Sabien (substack)

**Include signals (for unlisted senders):**
- Sender domain is a known publishing platform (substack.com, mailchimp.com, beehiiv.com,
  convertkit.com, ghost.io, buttondown.email, medium.com, etc.)
- Subject or body contains newsletter-style language: "this week", "edition", "issue #",
  "roundup", "digest", "in today's post", "read online", "view in browser"
- Email is clearly a curated content update, article summary, or blog post

**Hard excludes -- always skip, never add to `newsletter_ids`:**
- Transactional: order confirmations, receipts, trading statements, account alerts
- Personal or direct correspondence between people
- Promotional / sales / discount emails
- Social media notifications (Facebook, Instagram, etc.)
- Single-stock analysis or investor deep dives (e.g. Seeking Alpha individual stock posts)
- Event invitations, webinar promos, podcast episode announcements with no news content
- If uncertain, **exclude**

If zero emails pass the filter, tell the user clearly: no newsletter/blog emails found in
their unread inbox, and stop.

### Step 3 -- Extract points via sub-agents (parallel batches)

Processing all newsletters in a single context window will exhaust context. Instead,
dispatch sub-agents to extract points from batches of emails in parallel.

**3a -- Prepare batches**

Split the classified newsletters into batches of **up to 8 emails each**. For each email
in a batch, include the messageId, sender/publication name, and subject.

Each sub-agent will use `gmail_read_message` to fetch the full body for each email
in its batch. Do NOT fetch bodies in the main context -- let the sub-agents do it.

**3b -- Dispatch one Agent per batch**

For each batch, launch a sub-agent using the **Agent tool** with these parameters:
- `subagent_type`: `"general-purpose"`
- `description`: `"extract newsletter points batch N"`
- Launch all batch agents **in a single message** so they run in parallel

Each sub-agent prompt must contain:

1. **The email list** for its batch: messageId, sender/publication, subject for each.
   Tell the sub-agent to call `gmail_read_message(messageId: "...")` for each email
   to fetch the full body text.
2. **The extraction rules** (see below -- copy them verbatim into each prompt).
3. **An instruction to return structured JSON** -- an array of objects, one per email:
   ```json
   [
     {
       "messageId": "19d62cd3809a013b",
       "publication": "The Neuron",
       "subject": "AI News Roundup",
       "points": [
         "OpenAI API load: 6B to 15B tokens/min in 5 months",
         "Anthropic releases Claude 4.5 with 1M context window"
       ]
     }
   ]
   ```

**Extraction rules to include in each sub-agent prompt:**

```
For each email, extract up to 5 key informational points.

CRITICAL QUALITY GATE -- apply to every point before including it:

1. Does it contain a specific fact (number, name, date, outcome, decision)?
   NO -> discard it.
2. Could it be guessed from the subject line alone?
   YES -> discard it.
3. Does it describe that a thing EXISTS rather than what the thing SAYS?
   YES -> discard it. Read the thing and extract what it says.
4. Is it a framework, methodology, or opinion with no concrete data?
   YES -> discard it.

Examples of points that FAIL this gate:
- "The Neuron's framework for learning AI in 2026: a five-level proficiency stack"
  FAILS #3 and #4: describes a framework exists, says nothing concrete.
- "Code.org's new CEO on computer science education in the age of AI"
  FAILS #2 and #3: restates subject, describes an interview exists.
- "Finding a diet that works is enough of a win; think in trade-offs"
  FAILS #4: opinion with no data.

If an email contains ONLY framework/opinion/meta content with no extractable facts,
return an empty points array for that email. Do not pad with low-quality summaries.

LOCATE THE RICHEST CONTENT REGION FIRST. Many newsletters concentrate news in a
structural section:

| Newsletter | Where the news lives |
|---|---|
| The Neuron | "Around the Horn" numbered list near the bottom |
| Exponential View | Bullet list after the main essay intro |
| Import AI | Numbered sections with *** separators; each section = one story |
| Wall Street Breakfast | "Top News" and "What SA Analysts Are Watching" sections |
| The Economist Today | "Editor's picks" + "More of our coverage" blurbs |
| Peter Attia | Episode/article summary bullets below the main description |
| DL News | Numbered or bulleted story list in body |

For unlisted newsletters: scan for a numbered or bulleted list of 3-8 items.

ENRICH WITH WEB FETCH WHERE CONTENT IS THIN. If an email has only teaser text and
links, use web_fetch on 1-2 significant article URLs. Prioritise when: the body gives
only a headline, the story seems significant, and the link is first-party. Skip sponsor
links, social media, and paywalled sites.

WHAT COUNTS AS A POINT: A point must contain at least one SPECIFIC FACT -- a number,
a name, a date, an outcome, a measurement, a decision, or a concrete event. If you
cannot state a specific fact from the email, DO NOT include it as a point. Topic
descriptions are NOT points. "War disrupts energy supply" is a topic. "Iran strikes
knock out 40% of Gulf refining capacity; Brent crude hits $127/bbl" is a point.

BAD (topic, no facts, or meta-description):
- "How the Iran war affects energy and food prices"
- "AI automation is advancing across industries"
- "New research on longevity biomarkers"
- "Deep dive on OpenAI demand signals post-$122B raise"
- "Alexander Berger published annual Letter from the CEO"
- "Sam Altman got testy with investor Brad Gerstner over share sales"

The last three fail because: "deep dive on X" describes an article, not a fact.
"Someone published a letter" tells you an email was sent -- that is not information.
The letter CONTAINS information: extract THAT. "Got testy" is gossip with no
material consequence.

THE CORE TEST: does the point tell the reader something they did not know before?
If it just tells them an email/article/letter EXISTS, it fails. Read the actual
content and extract what is IN it.

BAD: "Alexander Berger published annual Letter from the CEO"
GOOD: "Open Philanthropy deployed $620M in 2025; 38% to AI safety, up from 22%"

BAD: "Code.org's new CEO on computer science education in the age of AI"
GOOD: "Code.org pivoting curriculum to pair-programming with AI copilots; 54% of
US high schools now offer CS, up from 35% in 2020"

The bad versions tell you a document exists. The good versions tell you what is IN
the document. Always extract what is IN the document.

If the email body is too thin to extract facts (just a teaser), use web_fetch on
the linked article. If you still find no specific facts, return an empty points
array for that email rather than producing a content-free summary.

GOOD (specific facts):
- "Iranian strikes cut 3.2M bbl/day Gulf output; diesel up 44% in 48 hours"
- "OpenAI API load: 6B to 15B tokens/min in 5 months"
- "Blood pressure, glucose, lipids outperform aging clocks as mortality predictors (n=12,400)"

If the email body is too thin to extract specific facts (just teasers/headlines with
no detail), use web_fetch on the linked article to get the facts. If you still cannot
find specific facts after fetching, skip that email entirely rather than producing a
vague topic summary.

WRITING STYLE: Each point is a single sentence for fast skimming -- short, active,
no filler. Omit greetings, sign-offs, sponsors, CTAs. Write like a wire headline: state
the fact, drop obvious subjects. Use colons to compress. Numbers earn their space.
Never write "researchers found", "according to", "this signals".

Return ONLY a JSON array as described. No commentary, no markdown fences, just the raw JSON.
```

**3c -- Collect results**

After all sub-agents return, parse their JSON outputs and merge into a single flat list of
all emails and their extracted points.

### Step 4 -- Deduplicate across emails

Scan all extracted points (from all sub-agent results combined) for overlaps. Two points
overlap if they describe the **same real-world topic, event, or entity**.

When overlap is found:
- Merge into a single point
- Retain all unique detail from each source (one email may have extra context)
- Tag the merged point with **all** contributing email IDs
- The merged point may be slightly longer than a standard point to accommodate the
  extra detail, but keep it to 1-2 sentences max

### Step 5 -- Assign categories

Before rendering, assign each point a category based on its content. Use this system:

| Category | Tag | Examples |
|---|---|---|
| AI & Tech | `[AI]` | AI models, compute, ML research, developer tools, tech policy |
| Markets & Finance | `[FIN]` | stocks, macro, earnings, Fed, oil, crypto markets |
| World & Politics | `[WORLD]` | geopolitics, elections, government policy, international affairs |
| Health & Science | `[HEALTH]` | medicine, longevity, biology, research findings |
| Ideas & Society | `[IDEAS]` | sociology, education, culture, economics, philosophy |

Assign the single most relevant category per point. Do not force a category if content is
genuinely multi-domain -- prefer the primary thrust of the point.

### Step 6 -- Render the digest

Use an **HTML artifact** so color can be applied. The output should render as a clean,
dark-mode-friendly digest.

**Category color system (use subtly -- background chip/badge only, never whole bullet):**
- AI & Tech -- `#1e40af` (dark blue) badge with white text
- Markets & Finance -- `#166534` (dark green) badge with white text
- World & Politics -- `#92400e` (amber-brown) badge with white text
- Health & Science -- `#0e7490` (teal) badge with white text
- Ideas & Society -- `#5b21b6` (purple) badge with white text

**Visual structure:**
- Each newsletter gets a section header: publication name only (bold). No subject line.
- Each bullet: `[CATEGORY BADGE]  Point text  [link]` link
- Category badge is a small inline pill: `<span style="...">AI</span>` style
- Bullet text is plain -- no additional color on the main text
- Merged points section at end, visually separated

**Typography/style rules:**
- No em dashes (`--` or `:` instead)
- No "importantly", "notably", "crucially"
- Max 1 sentence per bullet (2 only for merged points)
- Font: system sans-serif, readable at a glance

### Step 7 -- Mark as read and archive

After rendering the digest, call both `gmail_mark_messages_read` and
`gmail_archive_messages` from the gmail-mcp server with the `newsletter_ids` list
built during Step 2.

**This list contains only emails that explicitly passed the Step 2 classification.**
It must never include IDs of excluded emails, regardless of how many emails were
fetched in Step 1. If you are unsure whether a given ID belongs in this list, it
does not belong in this list.

**YOU MUST call both tools. This step is NOT optional. Do not skip it.**

First, mark as read:
```
gmail_mark_messages_read(messageIds: ["19d62cd3809a013b", ...])
```

Then, archive (remove from inbox):
```
gmail_archive_messages(messageIds: ["19d62cd3809a013b", ...])
```

Use the exact same `newsletter_ids` list for both calls. Both calls are required.
Do not end the skill without completing both. If one fails, retry it once before
giving up.

Show the result briefly below the digest:

> *Marked N newsletters as read and archived.*

If both retries fail, show:

> *Could not mark as read/archive -- server may be restarting. Ask me to retry.*

### Step 8 -- Offer follow-up actions

After the digest and confirmation, offer one line:
> *Reply with a topic to go deeper.*


---

## Language Style Guide

Write like a wire headline, not a summary. State the fact -- omit who said it, omit that
it was found/reported/argued. The core rule: **drop the subject entirely when it is obvious.**

| Bad -- too long | Good -- tight |
|---|---|
| "Researchers at MIT found that AI automation advances gradually" | "AI automation: broad rising tide across tasks, not sudden disruption" |
| "According to the newsletter, OpenAI's API load grew from 6B to 15B tokens/min" | "OpenAI API load: 6B to 15B tokens/min in 5 months" |
| "The study showed startups trained in AI discovered 44% more use cases" | "AI-trained startups: 44% more use cases, 1.9x revenue (INSEAD/HBS, n=515)" |
| "Peter Attia discusses how traditional health metrics outperform aging clocks" | "Blood pressure, glucose, lipids, fitness outperform aging clocks as clinical predictors" |
| "Japan has set a target to capture 30% of the global physical AI market by 2040" | "Japan targets 30% of global physical AI market by 2040 -- driven by severe labour shortage" |
| "This is an important development because it signals..." | Drop entirely -- state only the development |

**Additional rules:**
- Never write "researchers found", "the author argues", "according to", "the newsletter reports"
- Never write "this signals", "this suggests", "this means that"
- Numbers and specifics earn their space -- keep them; cut the words around them
- Colons compress definitions: "Core PCE: due Thursday, expected 2.5% annual"
- Hedge only if the source itself hedges -- don't add your own uncertainty
- Passive voice only when the actor genuinely doesn't matter

Target reading time: scan the full digest in under 90 seconds.

---

## Edge Cases

- **Paywalled content**: if only a teaser is visible, summarise what's available -- do not
  add any paywalled indicator or marker. Treat teaser content the same as full content.
- **Very short emails** (< 3 extractable points): include what's there; don't pad
- **Foreign language emails**: summarise in English unless the user's language preference
  is known to be different
- **Duplicate newsletters** (same publication, multiple issues): treat as separate emails;
  do not merge across issues
