# Surya Lekka

Video: (link) · Blog: (link)

Surya Lekka checks a household's rooftop solar quote before they sign. It reads the quote, shows the line each number came from, checks the sums and the central subsidy against the government rule, and lists what to ask the vendor.

Site: https://main.d2sqhcgne0nq26.amplifyapp.com/ (AWS Amplify Hosting). Mirror: https://vabhishekprakash.github.io/surya-lekka/ (GitHub Pages).

## Press release

This is a Working Backwards press release, written before launch to describe the product we are building.

### Surya Lekka helps households check a rooftop solar quote before they sign

Read the quote, check the numbers and the subsidy rule, and know what to ask the vendor.

A rooftop solar quote packs a lot into a page or two: how many panels and of what wattage, the system size, the price with GST and extra charges, and the subsidy the vendor expects the household to get. Under PM Surya Ghar, the central subsidy depends on the DC capacity of the panels, the household's state, and when they applied on the National Portal, and it needs DCR panels made from Indian cells. Checking all of that by hand means finding each figure on the quote, knowing which rule applies, and redoing the sums.

With Surya Lekka, a household uploads the quote as a PDF or as photos of its pages. Amazon Textract reads each page, answering a fixed set of questions and reading its tables, and the app keeps the line each value came from, or says "not found". The reading can be wrong: on our 8 development quotes it read 36 of the 82 printed values correctly, got 9 wrong and filled in 1 that the quote doesn't state, and the app marked all 10 of those "Check this". On 9 held-out quotes, read once, it read 27 of 92 correctly, got 4 wrong and filled in 3, and marked 4 of those 7. The household confirms or corrects the values and answers a few questions, such as their state and when they applied. Plain, tested Python code then runs the checks, and a finding only appears after the household confirms the numbers it uses. Results show the quote line each value came from (or that you typed it) and the working; subsidy findings that use a rule also name the rule and its date. The details the quote leaves out become a short, polite message to copy or send to the vendor on WhatsApp. Anyone who would rather not upload anything can type the numbers in instead.

> "I had the quote on my phone and the vendor wanted an answer. It showed me the panel count didn't add up to the system size and gave me the questions to send back."
>
> An illustrative household quote, written for this press release. It is not from a real user.

## FAQ

### What does it check?

Four things. Whether the number of panels times their wattage matches the system size the quote states (C1). Whether the central subsidy on the quote matches the rule for that panel capacity (C2). Whether the price, GST, extra charges, discount, subsidy and net cost add up (C3). And which details the quote leaves out, such as the exact panel and inverter models, the DCR declaration and the vendor's registration number (C4).

### What does it never do?

It never recommends a system size, predicts savings or payback, ranks vendors, accuses a vendor of anything or certifies that a household is eligible for the subsidy. It never says a quote is "safe". State subsidies are marked "not checked". Every check ends in one of five plain results: matches, doesn't match, missing, needs checking, or not checked.

### What happens to my quote?

The page turns your quote into page images on your own device and only those images are uploaded. On the public site the notice reads: "Your pages are read by Amazon Textract in AWS's Mumbai region (India) and deleted from our storage after reading. This AWS account has opted out of AWS using them to improve its services. If reading fails, they're removed automatically, usually within two days." Logs hold job ids, timings and reason codes, never the text of your quote. See [Privacy and safety](#privacy-and-safety) for the details, including the notice shown when Amazon Textract reads the pages and what changes when Nova is reached through a cross-Region inference profile.

### Why not just use a subsidy calculator?

A calculator needs you to find each number, know which figure is the DC panel capacity, and know which rule and date apply to you. Surya Lekka starts from the quote itself. It shows where each number came from, checks the quote's own arithmetic as well as the subsidy, and turns what's missing into questions for the vendor.

## How it works

![Surya Lekka architecture on AWS](docs/surya-lekka-architecture.svg)

1. In the browser, pdf.js turns each page of a PDF into a JPEG (at most 20 pages, each under 3,750,000 bytes and 8,000 pixels a side). Photos are scaled down to fit the same limits. Nothing leaves the device at this step. Every page keeps its number in the original quote; a page over the 20-page limit, too large to send or unreadable is listed as left out, and the server marks that reading incomplete, so the checks that need the whole quote ask first.
2. The page asks the API for a job. It gets back a secret job token and presigned POSTs, and uploads the page images straight to a private S3 bucket, then a small manifest.
3. The manifest's S3 event starts the worker Lambda. It claims the job with a conditional write, so a duplicate event does nothing, and reads the pages with the stack's reading engine:
   - Amazon Textract (`ReadingEngine=textract`): one AnalyzeDocument call per page with the page bytes, asking for tables and 15 fixed questions (`src/extract/textract_queries.py`). Each answer keeps its own text, the full lines it sits on and its page. An answer below the confidence threshold (50 out of 100, chosen on our development quotes), one that doesn't parse as its field's type, or one on no line of the page is dropped. Every remaining answer is kept page by page, so two pages that disagree reach the household as a conflict to resolve. A table gives several options only when two or more of its rows each have a different system size and a price. The same reply is also read for lines that name their own value ("Grand total Rs. 1,65,850/-", "No. of modules: 5") and for bill-of-materials rows that tie a panel count to its wattage and make. A line that names no role, or two, gives nothing, a percentage is never an amount, and one printed amount never fills two fields. Values the sources disagree on stay a conflict for the household. A valid GSTIN's state code shows where the vendor is registered for GST, as information only; it never fills in the household's state.
   - Amazon Nova (`ReadingEngine=nova`): batches of at most five images, answered through a fixed JSON schema in which each field gets a value and the exact text it came from, or "not found".

   The worker saves each page or batch as it arrives, so a retry only reads the rest.
4. The worker merges the batches, runs the checks, stores the result in DynamoDB and deletes the uploaded pages.
5. The review screen shows every value next to its source text and a thumbnail of the user's own page, with the line or table cells behind it outlined on the page (only page numbers and box coordinates are stored beside the evidence). The user corrects anything wrong, can add a charge the reading missed, says whether every charge on the quote is listed, and answers the questions the rules need. The checks run again on the corrected values, and each correction is stored as the user's.
   Some values are marked "Check this": two parts of the quote disagree on them, they couldn't be read cleanly, the reading was less than 80% sure, or they come from a kind of reading that got a value wrong on our development quotes (`src/rules/check_this.json`). Every check that uses such a value waits at "needs confirmation" until the household ticks that one value as right, or types the right one. There is no button that accepts them all.
   A number the household types is asked about when it looks unusual for a home system: panels outside 100 to 800 W, a system outside 0.5 to 20 kW, more than 100 panels, an amount outside a household range, or a figure about 10, 100 or 1,000 times what the other numbers imply (`src/rules/entry_guards.json`). Nothing is changed. Until the household confirms the number, the checks that use it say "Please check the number you entered".
   A finding only appears after you confirm the numbers it uses. No check says "matches" or "doesn't match" before then. The results screen lists them, each with the quote line it came from or "you typed this", and asks "Are these the numbers on your quote?" with Yes or Fix a number. The confirmation holds only for those numbers in that option: a correction, a switch to another option, or the same number typed in a different format asks again. The server enforces this, not only the page. Each confirmation is a token the server issued, signed with HMAC-SHA256 under a per-stack secret that CloudFormation generates in AWS Secrets Manager. It covers the job, the review revision, the option, the fields and their values. Every change of corrections or answers, an option switch included, moves the job's review revision on, so an edit that is later undone, or a switch from option A to B and back, needs a fresh confirmation; a token from another job, an older revision or one computed outside the server is refused. "Type the numbers instead" stores no numbers. Its first answer carries a challenge with a random id, and only that challenge's tokens confirm the typed values. To make an edit that is later undone ask again, it keeps a short-lived session record holding only that random id, a counter and a keyed hash of the values (no numbers), which expires after 15 minutes.
6. The results screen groups the findings by outcome and offers the vendor questions with Copy and Send on WhatsApp buttons.

There are two other ways in. The three samples are made-up quotes with saved readings, so trying one never calls the model. While user tests run, a fourth sample, S4, opens only from the link `#sample=S4`. It is a test sample with a deliberately wrong reading: it reads the panel wattage as 550 W where its page prints 500 W, to see whether testers catch it at the confirm step. "Type the numbers instead" sends the figures the user types straight to the checks. Only the short-lived session record described above is stored: a random id, a counter and a keyed hash of the values, no numbers, expiring after 15 minutes. Every result carries a label saying where its values came from: "Read by Amazon Textract", "Read by Amazon Nova", "Saved reading of a made-up quote. Not read live." (every sample, S4 included) or "Entered by you".

The stack is defined in `template.yaml` (AWS SAM): an HTTP API on API Gateway, seven Lambda functions on Python 3.12, a private S3 bucket for uploads, DynamoDB for jobs, an SQS queue for worker events that failed, CloudWatch logs kept for 7 days, X-Ray tracing, and the web app in a second private bucket behind CloudFront.

The public site is on AWS Amplify Hosting in ap-south-1, at https://main.d2sqhcgne0nq26.amplifyapp.com/, published by manual deployments (a zip of the built web app, no Git connection). GitHub Pages serves the same files as a mirror, at https://vabhishekprakash.github.io/surya-lekka/. Both talk to the same API, which accepts both addresses. The template's own CloudFront hosting is switched off (`HostingEnabled=false`) while our account is blocked from creating CloudFront distributions. The template still supports CloudFront: `HostingEnabled` is only switched off until the account is verified.

## What it checks

The checks are plain Python in `src/checks/` and use `Decimal` for money. Each finding carries its status, the quote lines it used, any value it worked out (labelled as computed), and the rule id and date where a rule applies. Every finding, vendor question and vendor-message line also carries a stable message key and its parameters (`src/checks/messages.py`), so the text can be translated without touching the checks.

### C1: system size

Panel count times panel wattage, compared with the system size on the quote, only when the quote says that size is the DC capacity of the panels (kWp). If the size is given in kVA, as AC or inverter capacity, or without saying what it measures, the two aren't compared and the household is asked what the figure means. The check allows 0.01 kWp for rounding. That allowance is ours, not an official one.

### C2: central subsidy

The subsidy on the quote compared with the central rule for the panels' DC capacity. Gates run first, in this order, and the first one that applies decides the result:

| Order | Gate | Passes when | Otherwise |
|---|---|---|---|
| 1 | `processing` | every page was processed | needs checking |
| 2 | `options` | the quote has one option, or the user picked one | needs checking |
| 3 | `consumer_type` | the user confirmed an individual household | not checked for an RWA or group housing, else needs checking |
| 4 | `state` | a recognised state or UT | needs checking |
| 5 | `portal_date` | the user applied on the National Portal on or after 13 Feb 2024 | not checked if earlier, else needs checking |
| 6 | `prior_subsidy` | the user confirmed a first system with no earlier central subsidy | not checked if not, else needs checking |
| 7 | `give_it_up` | no Give It Up opt-out | not checked if the user confirmed it, needs checking if only the quote mentions it |
| 8 | `subsidy_stated` | the quote states a separate central subsidy amount | needs checking |
| 9 | `dc_capacity` | the DC panel capacity is known | needs checking |
| 10 | `dc_range` | for a range of capacities, both ends give the same capped amount | needs checking |

Once every gate passes, the stated amount either matches the rule (within Rs 1, our rounding allowance) or doesn't. DCR eligibility is never inferred from a quote, and a match always says that eligibility (DCR panels, registration, inspection) was not verified.

### C3: price arithmetic

Base price plus GST (when the quote adds it on top) plus the extra charges inside the total, minus any discount, compared with the stated total. Then the total minus the subsidies the net cost takes off, compared with the stated net cost. A missing amount is never treated as zero. If the quote doesn't say whether GST is included, or whether a charge is inside the total, the household is asked.

### C4: missing details

Panel wattage and count, panel and inverter make and model, inverter rating, the DCR declaration, the vendor's registration number, whether GST is included, charges outside the total, and net-meter charges. Anything absent is reported as "not found on the quote", never as a fact about the vendor. Details that are present are not verified.

### Rules and sources

The rule values live in `src/rules/cfa_rules.json` with their sources, sections and dates. Every value was checked against the sources on 8 Oct 2026.

| Rule | Value | Source |
|---|---|---|
| Effective date | Applications received on the National Portal on or after 13 Feb 2024. The quote date and the claim date don't count. | MNRE guidelines, section 2(c) |
| `CFA-RES-GENERAL` | General-category state or UT: Rs 30,000 per kWp up to 2 kWp, then Rs 18,000 per kWp from 2 to 3 kWp, capped at Rs 78,000 | MNRE guidelines, sections 5(h) and 5(k); PIB release 2042617 |
| `CFA-RES-SPECIAL` | Special-category state or UT: Rs 33,000 per kWp up to 2 kWp, then Rs 19,800 per kWp from 2 to 3 kWp, capped at Rs 85,800 | MNRE guidelines, sections 5(h) and 5(k); PIB release 2042617 |
| `CFA-DCR-REQUIRED` | The subsidy needs domestically manufactured modules made from domestically manufactured cells | MNRE guidelines, section 5(m) |
| Special-category list | Arunachal Pradesh, Assam, Manipur, Meghalaya, Mizoram, Nagaland, Sikkim, Tripura, Himachal Pradesh, Uttarakhand, Jammu and Kashmir, Ladakh, Andaman and Nicobar Islands, Lakshadweep | MNRE guidelines, section 5(g) |

The subsidy is prorated by the DC capacity of the panels, not the inverter rating, and fractions count: 2.825 kWp in a general-category state gives Rs 74,850.

Sources:

- MNRE, Operational Guidelines for Implementation of PM Surya Ghar: Muft Bijli Yojana for the component "CFA to Residential Consumers", 7 Jun 2024.
- MNRE, Amendment in Guidelines for Implementation of PM-Surya Ghar: Muft Bijli Yojana for the component of "CFA to residential consumers", 7 Jul 2025. It doesn't change the rates, caps or effective date. Its Phase-II transitional provisions are not modelled.
- PIB release ID 2042617, Lok Sabha written reply, Annexure II CFA table, 7 Aug 2024 (corroborating).

## Privacy and safety

- The browser renders the quote into page images on the device. Only those images are uploaded, through presigned POSTs that accept one JPEG of limited size per page.
- The privacy notice in the web app comes from the Region the stack is deployed in and the reading engine. For Mumbai it reads: "Your pages are processed in AWS's Mumbai region (India) and deleted after reading. If reading fails, they're removed automatically, usually within two days." For Sydney it names "AWS's Sydney region (Australia)". When Nova is reached through a cross-Region inference profile (`global.` or `apac.`), pages may be read in other AWS Regions, and the notice says so instead.
- With Amazon Textract, AWS may store and use the pages to improve its AI services, and may store some of that content in another Region, unless the account opts out through an AWS Organizations AI services opt-out policy. `deploy.ps1` reads the account's effective policy with `aws organizations describe-effective-policy --policy-type AISERVICES_OPT_OUT_POLICY`. Only when that policy opts Textract out, by name or through `default`, does the page say: "Your pages are read by Amazon Textract in AWS's Mumbai region (India) and deleted from our storage after reading. This AWS account has opted out of AWS using them to improve its services. If reading fails, they're removed automatically, usually within two days." If the policy doesn't confirm it, or the call is denied, the page says: "Your pages are read by Amazon Textract in AWS's Mumbai region (India) and deleted from our storage after reading. AWS may keep and use them to improve its AI services and may store some of that content in another AWS region. If reading fails, they're removed automatically, usually within two days."
- The worker deletes a job's pages after a successful reading, or after a failure that can't be retried. It retries objects S3 reports as not deleted and logs only counts. A lifecycle rule removes anything left under `uploads/` after a day, and job records stop being readable after 24 hours: the API refuses an expired job, and DynamoDB deletes expired items in the background, usually within a few days.
- Logs hold job ids, timings, statuses, token counts or pages read, a cost estimate and reason codes. The logging helper refuses any other field. Tests check that error paths never log document text, evidence or job tokens. The worker never logs or stores Textract's raw reply, only the facts mapped from it.
- Each job has a random secret token. Only its SHA-256 hash is stored, and every read or change needs the token.
- Abuse and cost limits: a kill switch that pauses uploads, saved samples and typed checks alike; daily caps on new checks and on pages (counted from each check's declared page count when it is created, live samples included); separate daily caps for saved samples and for typed checks; a per-address daily cap for each kind (the address is stored only as a keyed hash); and API throttling of 5 requests a second with bursts of 10. Every request is checked in full before it takes a slot, so a refused one (HTTP 400, or 413 for an oversized saved sample) spends no quota and leaves a typed session as it was. A request's address slot and day slot are taken in one transaction, so a refused day slot uses no address slot. Budget alerts are set on the AWS account by hand, outside the template: a monthly cost budget of $15 that alerts at $3, $8 and 100% of actual cost, and a zero-spend budget that alerts above $0.01. The CloudWatch alarms below come with the template.
- The upload bucket blocks public access and refuses plain HTTP. The public site runs with CloudFront hosting off, and CORS lets browsers on two origins call the API and post to the upload bucket: the GitHub Pages address (`SiteOrigin`) and the Amplify Hosting address (`SecondSiteOrigin`). CORS is a browser access policy, not a security boundary: it doesn't authenticate clients or stop direct requests. Per-job secret tokens, presigned uploads, quotas and throttling do that work. `deploy.ps1` refuses `-Pages` or `-Amplify` with hosting on, because the template would then not let that site call the API.
- Only with CloudFront hosting on (`HostingEnabled=true`, not the public site's setting): the web app sits in a second private bucket that also blocks public access and refuses plain HTTP, only the CloudFront distribution can read it, through origin access control, and CORS allows the CloudFront domain alone.
- The secret that signs confirmation tokens is created by CloudFormation in AWS Secrets Manager. Only the four functions that sign tokens may read it, and only that one secret; they read it once per cold start. It is not in the repo, `config.js`, any response or the logs. A test checks that no secret value reaches an X-Ray trace.
- Each Lambda function has its own IAM role with only the actions it needs and no AWS managed policy. It may write logs only to its own log group (create streams and put events, but not create groups) and send X-Ray traces. The worker's Bedrock permission exists only while Nova reads, and names the exact model or inference profile and the Regions that profile lists. Its `textract:AnalyzeDocument` permission exists only while Textract reads. That action has no resource-level permissions, so the statement uses `"*"`. The worker sends page bytes, so Textract needs no access to the bucket.
- The web app places every piece of quote text with `textContent`, never as HTML. pdf.js is pinned to one version on cdnjs and checked against its SRI hash before it runs.
- X-Ray traces each Lambda invocation and, through the AWS X-Ray SDK's botocore patch, each AWS call it makes (Textract, S3, DynamoDB, Secrets Manager), so the service map shows them. Our own recorder replaces the SDK's: a traced call records its operation, HTTP status and timing only, with no parameters, bodies, exception messages or stacks. Tests put marker text into page bytes, a token hash, an object key, the confirmation secret and an AWS error message, and check that none of it reaches a trace.
- A review is checked in full before anything is written, and its corrections, result and review revision are written together, only if the revision is still the one the request read. A rejected or stale request changes nothing.

## Alarms

`deploy.ps1 -AlarmEmail <address>` subscribes that address to the stack's SNS topic (the address confirms the subscription from its inbox; the address is never stored in this repo). Each alarm fires on one occurrence in five minutes:

| Alarm | What it means | What to do |
|---|---|---|
| FailedJobsAlarm | The worker logged `job_failed`: a quote couldn't be read. | Look up the job's reason code in the worker's log group. Throttling or a quota means waiting or lowering the caps; a bad page means nothing is wrong on our side. |
| FailureQueueAlarm | A worker event failed twice and is waiting in the failure queue. | Read the queue message for the job id, check the worker's log for that job, then delete the message. The household can retry from the page. |
| LambdaErrorsAlarm | One of the stack's functions ended in an error. | Find the function and time in CloudWatch metrics, then read its log group around that time. |
| LambdaThrottlesAlarm | A function was throttled (account concurrency is 10). | Usually a burst of use. If it repeats, lower the daily caps or pause with the kill switch. |

## Run it locally

You need Python 3.12. From the repo root:

```
python -m venv .venv
.venv\Scripts\activate           # on macOS or Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

To try the web app on your own machine with no AWS account:

```
python -m src.api.local_server
```

Then open http://127.0.0.1:8000/. The server keeps everything in memory, and a stub stands in for the model, so every uploaded quote comes back with the same made-up reading, labelled "Read by the local test stub, not a model". The page loads pdf.js from cdnjs, so turning a PDF into page images needs an internet connection. Photos don't.

Options:

- `--stub slow` waits 75 seconds per call, long enough to show the slow-reading message.
- `--stub fail-once` fails the first reading of each upload, so you can try the retry button.
- `--daily-cap 0` shows the daily limit message.
- `--reading-off` behaves like a stack deployed with `ReadingEngine=none`: uploads get the "type the numbers instead" message, while samples and typed numbers keep working.
- `--port` changes the port.

## Deploy

You need the AWS CLI, the AWS SAM CLI and PowerShell, plus the Python packages for rendering the sample pages:

```
pip install -r requirements-dev.txt -r requirements-spike.txt
```

Then, from the repo root:

```
.\scripts\deploy.ps1 -Profile default -Region ap-south-1 -ExpectedAccount <your-account-id>
```

The script prints the AWS account and Region first and stops before changing anything if the account isn't the one you expected. It then renders the sample quotes, lints the template, runs `sam build` and `sam deploy` without prompts (SAM creates its own artifacts bucket and saves the settings to `samconfig.toml`), uploads the sample pages and saved readings, uploads `web/` with a `config.js` that points at the new API, and clears the CloudFront cache. It prints the API URL and the site URL at the end.

The stack deploys in `ap-south-1` (Mumbai) or `ap-southeast-2` (Sydney). The template refuses any other Region. Options:

- `-HostingEnabled false` leaves out CloudFront and the site bucket (see below).
- `-ReadingEngine textract` turns reading on with Amazon Textract in the stack's Region. Each page costs about $0.020 per page (tables and queries, Mumbai pricing). `DailyPageCap` (default 300 pages per UTC day, across all checks, live samples included) limits first reads to about $6.00 a day. A page that was read and saved is never read again. If saving its reading keeps failing, later runs read it again: the worker counts every read it starts in the job's record and stops the job, with reason `read_limit`, once the job has started four reads per batch (the first run, Lambda's one retry and the two retries the page offers). The AWS SDK can retry a single read after throttling or a network error, so this budget counts reads started, not AWS charges. The daily page cap still limits how many pages enter in a day.
- `-ReadingEngine nova -ModelId <id>` turns reading on with Amazon Nova. The default is `none`. The model ID can be an inference profile such as `global.amazon.nova-2-lite-v1:0` or `apac.amazon.nova-pro-v1:0`, or a model in the stack's own Region such as `amazon.nova-pro-v1:0`, for accounts that can't use cross-Region inference. For a profile, the script reads the Regions it routes to with `aws bedrock get-inference-profile` and grants the worker those and no others.
- `-DailyJobCap`, `-IpDailyJobCap` and `-DailyPageCap` (defaults 200, 10 and 300) are passed on every deploy and printed at the end, so a cap changed for one deploy never lingers.
- `-SampleDailyCap` and `-IpSampleDailyCap` (defaults 1000 and 50) cap saved samples, and `-TypedDailyCap` and `-IpTypedDailyCap` (defaults 5000 and 200) cap typed-in checks, overall and per address a day. They are passed and printed the same way. 0 refuses every request of that kind.
- `-Amplify -AmplifyAppId <id>` also publishes the built `web/` folder to an existing Amplify Hosting app as a manual deployment (zip upload, no Git connection), waits for it, and lets the API accept that address as a second origin. A deploy without `-Amplify` removes that origin again.
- `-StackName` sets the stack name, which also starts the upload bucket's name.

### Without CloudFront

New AWS accounts are sometimes blocked from creating CloudFront distributions until the account is verified. Ours is, so our public site is on GitHub Pages for now. The template still creates CloudFront and the site bucket when `HostingEnabled` is true.

To publish the web app on GitHub Pages instead, set the repository's Pages source to GitHub Actions, then run:

```
.\scripts\deploy.ps1 -Profile default -Region ap-south-1 -ExpectedAccount <your-account-id> -HostingEnabled false -ReadingEngine textract -SiteOrigin https://<your-user>.github.io -Pages
```

After the stack is deployed, the script checks the AI services opt-out policy, sets the repo variables the site needs with `gh variable set` (API URL, Region, reading engine, opt-out status, cross-Region), then starts `.github/workflows/pages.yml` with `gh workflow run`. That workflow runs only when started by hand, never on push, and it publishes with the repository variables the latest deployment set. Nothing checks them against the deployed stack, so deploy with `-Pages` whenever they change. It builds `web/` with `scripts/build_site.py` from those variables and needs no AWS credentials. A missing or unknown opt-out value gives the notice that AWS may keep the pages. CORS allows browser calls only from the `SiteOrigin` you pass.

To serve the web app from your own machine instead, deploy everything else with:

```
.\scripts\deploy.ps1 -Profile default -Region ap-south-1 -ExpectedAccount <your-account-id> -HostingEnabled false
```

CORS then allows browser calls only from `http://127.0.0.1:8000` (the `SiteOrigin` parameter). The script prints the API URL and the command that serves `web/` on your machine against it:

```
python -m src.api.local_server --port 8000 --api <ApiUrl> --region ap-south-1
```

Open http://127.0.0.1:8000/. In this mode the local server only serves the web app, with a `config.js` that points at the deployed API, so uploads and checks go to the stack in AWS.

### Smoke test

```
.\scripts\smoke_test.ps1 -Profile default -Region ap-south-1 -ExpectedAccount <your-account-id>
```

It makes the same account check, then runs a sample (saved reading), the checks on S2's numbers typed in, and either the reading-off refusal or, with reading on, one synthetic page through the whole job flow (one model call). With hosting on, or with `-SiteUrl` for a site served elsewhere such as GitHub Pages, it also loads the page and every file it uses from under the site's path, checks that the API accepts the site's origin, and sends one page through a real presigned upload from that origin (no manifest follows, so nothing is read). It prints PASS or FAIL for each step and never prints document text.

## Tests

```
pytest
python scripts/lint_template.py template.yaml
```

The lint runs cfn-lint on the SAM template, then on the plain CloudFormation the SAM translator produces from it, all offline. The template tests evaluate the conditions for each setting, with hosting on and off and with reading off, by Textract, or by Nova through a regional profile, a global profile or an in-region model, and check the exact permissions and that nothing refers to a resource that isn't created. The deploy and smoke test scripts are tested end to end on Windows with `aws` and `sam` replaced by stand-ins that only record their arguments.

GitHub Actions runs the tests and the lint on every push (`.github/workflows/tests.yml`). One redaction server test fails now and then. If it is the only failure, CI runs it once more instead of skipping it.

## Limits

- Lambda concurrency on our account is 10.
- Amazon Nova reading is built but switched off while Bedrock access isn't granted. Amazon Textract reads the quotes in the meantime. With `ReadingEngine=none`, the samples and typed-in numbers work, and uploads get a message asking the household to type the numbers in.
- Amazon Textract's limits: at most 15 queries per page, and queries in English only. Text smaller than about 15 pixels tall on the page image may be missed, and each page image must be under 10 MB and 10,000 pixels a side. Textract has no question for the vendor's state or for charges outside the total, so those always come from the household.
- Panel count and wattage answers are paired in the order Textract returns them on a page. When a page has several, the household confirms the pairing.
- Only the central subsidy for an individual household is checked. State top-ups are not checked, and applications received before 13 Feb 2024 follow earlier rules that aren't covered.
- Eligibility is never verified: not DCR panels, not the vendor's registration, not the inspection.
- The reading can be wrong. On our 8 development quotes Amazon Textract, with our mapping, read 36 of 82 printed values correctly, 9 wrong and 1 that isn't on the quote; all 10 were marked "Check this", but that list of risky readings was built from those same quotes. On the 9 held-out quotes it read 27 of 92 correctly, 4 wrong and 3 that aren't on the quote, and marked 4 of those 7 (see Results). Every value is shown with its source text, highlighted on its page, for the household to confirm or correct.
- The results screen and the vendor message can be shown in Telugu. Amazon Translate drafted it; Claude Opus 5.5 reviewed it, and a second independent Claude check back-translated it. No professional translator has reviewed it. Other screens are in English. Hindi isn't offered. The language doesn't change what is read: Amazon Textract's questions work on English quotes only.
- At most 20 pages per quote are read.
- With hosting off, the web app has to be served from the `SiteOrigin` address (`http://127.0.0.1:8000` by default) or the `SecondSiteOrigin` address to reach the API.

## Results

The method: 17 real quotes, hand-labelled by us, 8 for development and 9 held out. The labels were written and frozen before any reading of the held-out quotes, and the scoring, the reader and the safety harness were frozen at the tag `reader-safe-2` (3fa34d6). The held-out set was read once, all nine quotes together, from that tag, on 10 Oct 2026: 61 pages, about $1.22 of Amazon Textract. A second run is refused. The quotes and labels stay outside this repo, and only aggregate numbers are published here. Reader performance is reported separately for development and held-out sets; any combined missing-field count describes only this 17-quote collection.

Reading, before the household confirms or corrects anything (printed fields only):

| | Development (8 quotes) | Held out (9 quotes) |
|---|---|---|
| Printed values read correctly (recall) | 36 of 82 (44%) | 27 of 92 (29%) |
| Filled values that were correct (precision) | 36 of 46 (78%) | 27 of 34 (79%) |
| Wrong values | 9 | 4 |
| Values filled in that the quote doesn't state | 1 | 3 |
| Judgement fields filled in wrongly | 0 of 40 | 0 of 45 |
| Wrong or filled-in values marked "Check this" | 10 of 10 | 4 of 7 |

We evaluated the frozen `reader-safe-2` reader once on nine held-out quotes. It correctly recovered 27 of 92 printed values (29% recall); 27 of 34 filled values were correct (79% precision). The corresponding development results were 36 of 82 (44%) and 36 of 46 (78%). Held-out errors comprised four wrong values and three unsupported fills, against nine and one on development. There were no judgement-field false fills in 45 held-out or 40 development opportunities; these are observations, not a zero-risk guarantee. "Check this" flagged 4 of the 7 held-out errors. Its 10 of 10 development result was measured on the errors used to build that rule. The other 3 held-out errors rely on the household confirming the numbers before any finding appears.

Findings on the raw reading: across scenarios S-A to S-E, neither the untouched run nor the separate accept-all-without-corrections run produced a definitive finding on any of the 17 quotes. Both use scenario inputs and the label-derived answers about the quote where the labels have them. Zero definitive findings means no definitive-finding error rate or bound can be estimated. It also means this evaluation demonstrated no completed checks from the uncorrected readings: the reader misses too many of the values the checks need (panel wattage, the base price, which subsidy a figure is).

Findings on the labelled values (the hand-made labels as the quote's values, every number confirmed), scenario S-A:

| | Development (8) | Held out (9) |
|---|---|---|
| System size: matches / doesn't match | 0 / 1 | 1 / 0 |
| Central subsidy: matches / doesn't match | 1 / 0 | 3 / 0 |
| Total: matches / doesn't match | 0 / 0 | 0 / 0 |
| Net cost: matches / doesn't match | 1 / 0 | 4 / 0 |
| Charges outside the total, or not clearly inside it | 6 | 8 |
| Number of panels not found | 3 | 5 |
| Panel wattage not found, or given as a range | 5 | 3 |
| Panel make and model not found | 1 | 2 |
| Inverter make and model not found | 1 | 1 |
| Inverter rating not found, or unclear | 4 | 4 |

Using our hand-labelled values and confirming every operand under scenario S-A, central subsidy amounts matched the implemented rule in one development and three held-out cases. This is not reader accuracy or verified eligibility. One development capacity calculation disagreed with the stated capacity; that alone does not establish vendor misconduct.

We publish no count of quotes missing a DCR declaration, a vendor registration number or net-meter charges. An earlier count of 16 of 17 for each measured our labels, not the quotes: the labels record DCR wording for 6 quotes in a field the evaluation script didn't read, and they have no field for a registration number or net-meter charges. A keyword search of the quotes' own text (`eval/search_details.py`) finds DCR wording in 6 of the 17, registration or portal wording in 7 and net-meter wording in 14. A keyword hit is not a declaration, and those pages are being checked by eye. This small collection does not establish market prevalence.

No total check completed: none of the 17 labels states a base price that the total can be checked against. Each definitive finding on labelled values is listed, with its numbers and rule, in a file outside this repo. An independent script that shares no code with the app (`eval/recheck_findings.py`) recomputed all 11 from their operands with the rules as written, and agreed with 11 of 11. Positive controls (made-up quotes with hand-worked findings, `eval/positive_controls.txt`) still give every expected "matches" and "doesn't match" at the tag, so the absence of findings on the raw reading is not a harness that can't see them.

Evaluation notes:

- Vendor name: when a label lists several names printed on the quote, separated by semicolons, the reading counts as correct if it matches any one of them. A note in square brackets at the end of a label is ours, not part of a name. This rule was set before the held-out run.
- Page limit: the held-out run keeps the product's 20-page limit. One held-out quote has 43 pages, so only pages 1 to 20 are read and its result is marked as processing incomplete, as it would be for a user.

## How this was built

We used these AI tools while building Surya Lekka:

| Tool | Role |
|---|---|
| Claude Code, running Claude Opus 5.5 | Wrote the code from our block-by-block instructions. We checked each block’s results, ran it and tested it. |
| Claude Opus 5.5, in the Claude app  | Planning; reviewed the Telugu text, with a second independent back-translation check. |
| ChatGPT, model Luna 5.6 | Finding public example quotes. |

Inside the product, Amazon Textract reads the pages, and Amazon Translate drafted the Telugu once (never at runtime).

## Credits and licences

Surya Lekka's own code is under the MIT License (`LICENSE`). It uses:

| Dependency | Where | Licence |
|---|---|---|
| [pdf.js](https://github.com/mozilla/pdf.js) 4.10.38, from cdnjs | web app, renders PDF pages in the browser | Apache-2.0 |
| [boto3](https://github.com/boto/boto3) and [botocore](https://github.com/boto/botocore) 1.43.109 | Lambda functions | Apache-2.0 |
| [AWS X-Ray SDK for Python](https://github.com/aws/aws-xray-sdk-python) 2.15.0 | Lambda functions | Apache-2.0 |
| [wrapt](https://github.com/GrahamDumpleton/wrapt), used by the X-Ray SDK | Lambda functions | BSD-2-Clause |
| [pytest](https://docs.pytest.org/) | tests | MIT |
| [jsonschema](https://github.com/python-jsonschema/jsonschema) | tests | MIT |
| [moto](https://github.com/getmoto/moto) 5.2.3 | tests | Apache-2.0 |
| [cfn-lint](https://github.com/aws-cloudformation/cfn-lint) 1.57.2 | template lint | MIT-0 |
| [AWS SAM translator](https://github.com/awslabs/serverless-application-model) 1.113.0 | template lint | Apache-2.0 |
| [PyMuPDF](https://github.com/pymupdf/pymupdf) 1.28.2 | only local tools (redaction, the evaluation scripts) and, at build time, rendering the sample pages; it is not installed in the deployed functions and the site does not use it | AGPL-3.0 (or an Artifex commercial licence) |
| [RapidOCR](https://github.com/RapidAI/RapidOCR) (rapidocr-onnxruntime) 1.4.4 | redaction tools | Apache-2.0 |
| [ONNX Runtime](https://onnxruntime.ai) 1.30.0 | redaction tools | MIT |
| [OpenCV](https://github.com/opencv/opencv-python) (opencv-python) 5.0.0.93 | redaction tools | Apache-2.0 |
| [NumPy](https://numpy.org) 2.5.3 | redaction tools | BSD-3-Clause, with parts under 0BSD, MIT, Zlib and CC0-1.0 |
| [jsdom](https://github.com/jsdom/jsdom) 30.1.2 (`tests/package.json`) | tests: runs the web app in a simulated browser | MIT |
| GitHub Actions: [checkout](https://github.com/actions/checkout), [setup-python](https://github.com/actions/setup-python), [setup-node](https://github.com/actions/setup-node), [configure-pages](https://github.com/actions/configure-pages), [upload-pages-artifact](https://github.com/actions/upload-pages-artifact), [deploy-pages](https://github.com/actions/deploy-pages) | CI and the GitHub Pages mirror | MIT |

The architecture diagram (`docs/surya-lekka-architecture.svg`) and the made-up sample quotes are our own. Each licence is taken from the package's own metadata (for pdf.js, its cdnjs entry; for the GitHub Actions, each repository's licence file); the versions are the ones pinned in the requirements files.

## Team

- **Abhishek: data, evaluation, product decisions and AWS.** Collected 17 real rooftop-solar quotes, redacted them on his own machine with our local redaction tool, and hand-labelled every value before any reading of the held-out set. Ran the evaluation under rules fixed in advance: labels frozen first, nine quotes held out and read exactly once, development and held-out numbers never pooled. Made the product calls: no finding until the household confirms its numbers, every value shown with the line it came from, and no tuning of the reader to hit a target. Owns the AWS account and the deployment in ap-south-1, including the AI services opt-out, cost caps and budgets. Directed the AI coding agent block by block, reviewed its reports and test results, and ran outside reviews whose findings became fixes and regression tests. Wrote some of the code himself.

- **Arya: team lead, vendor outreach, testing and demo.** Arranged the vendor's consent to use a real quote in the demo. Responsible for end-to-end testing of the household journey with people who hadn't seen the app, and for preparing the demo video.
