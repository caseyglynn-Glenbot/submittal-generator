# Deploy Guide — Submittal Generator on Render + n8n Cloud

Total setup time: ~45 minutes the first time.

## Architecture

```
[n8n Cloud Form]  -->  [Render Python Service]
       |                       |
       v                       v
   [Email user]           [Generates PDF]
```

The Python pipeline runs on Render (a managed cloud platform).
n8n Cloud handles the form intake and emails the result.

---

## Part 1: Deploy the Python Service to Render (20 min)

### 1.1 Get the code into GitHub

Create a new GitHub repo (e.g. `submittal-generator`). Push everything in
this folder to it:

```
submittal-generator/
├── Dockerfile
├── render.yaml
├── requirements.txt
├── service.py
├── orchestrator.py
├── quote_parser.py
├── annotation_engine.py
├── table_detector.py
├── mapping_table.py
├── datasheet_filler.py
├── templates/         <-- drop your blank template PDFs here
└── defender_drawings/ <-- drop your Defender3 drawings folder here
```

**Add your templates and Defender3 drawings to the repo** before pushing.
Yes, they go in the repo — they're configuration as far as Render is
concerned. If templates are sensitive, make the repo private.

### 1.2 Connect Render to GitHub

1. Sign up at https://render.com (free)
2. Dashboard → "New" → "Blueprint"
3. Connect your GitHub account, pick the `submittal-generator` repo
4. Render detects `render.yaml` and proposes the service
5. Click "Apply"

Render builds the Docker image (~5 min on first deploy), starts the service,
and gives you a URL like `https://submittal-generator-xyz.onrender.com`.

### 1.3 Get your API key

In Render dashboard → your service → "Environment" tab. Find `API_KEY` (Render
auto-generated it). Copy the value — you'll paste it into n8n in the next step.

### 1.4 Test the service

```bash
curl https://YOUR-SERVICE.onrender.com/health
# Should return {"status":"ok"}

curl -X POST https://YOUR-SERVICE.onrender.com/generate \
  -H "X-API-Key: YOUR_API_KEY_HERE" \
  -F "quote_pdf=@/path/to/test_quote.pdf" \
  -F "job_number=12345" \
  -F "engineer_initials=ABC" \
  --output test_submittal.pdf
```

If `test_submittal.pdf` opens with the expected pages, the service works.

---

## Part 2: Configure n8n Cloud (15 min)

### 2.1 Add SMTP credentials in n8n

You need an email account to send the result from. Options:

- **Gmail**: Settings → enable App Passwords, use `smtp.gmail.com:587` with
  your Gmail address + the App Password
- **Postmark / SendGrid / SES**: better for production, ~free for low volume
- **Your company SMTP**: if available

In n8n Cloud → Credentials → Create New → "SMTP". Fill in host/port/user/password.
Note the credential name.

### 2.2 Add the Render API key as an n8n credential

In n8n Cloud → Credentials → Create New → "Header Auth" credential.
- Name: "Submittal API"
- Header name: `X-API-Key`
- Header value: paste the API key from Render

### 2.3 Import the workflow

1. n8n Cloud → Workflows → "Import from File"
2. Upload `n8n_workflow.json` from this folder
3. Open the "Generate Submittal" node, replace
   `https://YOUR-SERVICE.onrender.com/generate` with your actual Render URL
4. In both email nodes, replace `submittals@yourdomain.com` with your
   sending address
5. Open both email nodes → Credentials → pick your SMTP credential
6. Open the "Generate Submittal" node → Credentials → pick the
   "Submittal API" header-auth credential

### 2.4 Activate the workflow

Click "Active" toggle in the top right. n8n gives you a public form URL
like `https://your-instance.app.n8n.cloud/form/submittal-generator`.

Share that URL with your team. Anyone who has it can upload quotes.

---

## Part 3: Use It

1. Open the form URL
2. Enter your email, upload the quote PDF, enter job number + initials
3. Submit
4. Wait ~30 seconds (first request of the day is slower if you used the
   free tier, since Render sleeps)
5. Check your inbox — the submittal arrives as an email attachment

---

## Updating Templates

When you add or fix a template:

1. Drop the new PDF into the `templates/` folder in your local clone
2. `git add . && git commit -m "Update X template" && git push`
3. Render auto-rebuilds (~3 min)
4. Done — the workflow uses the new template on the next form submission

This is the unsung benefit of this setup: template updates are version-
controlled and reversible.

---

## Costs

- **Render Starter plan**: $7/month (always-on, instant response)
- **Render Free plan**: $0/month (15-min idle timeout, ~30 sec cold start)
- **n8n Cloud**: whatever plan you're already on; this workflow doesn't
  push you over limits unless you generate hundreds of submittals per day

Free tier is fine if your team generates a few submittals per week and
you don't mind a 30-second wait on the first request of the day.

---

## Troubleshooting

**"Generate Submittal" node returns 401**
→ X-API-Key mismatch. Check the n8n credential matches Render's `API_KEY` env var.

**"Generate Submittal" times out**
→ Cold start. First request after 15 min idle takes ~30 sec on free tier.
   The default 120 sec timeout in the workflow should cover it.

**Email never arrives**
→ Check the "Email Error" node didn't fire (visible in n8n Executions tab).
   If it did, the error message tells you what went wrong on the Python side.

**Submittal looks wrong (missing pages, wrong variants)**
→ Open the Render service logs, look for "MISSING:" lines that show which
   template files weren't found. Add them to the `templates/` folder in
   git and push.

**Service crashes**
→ Render auto-restarts. If it keeps crashing, check the logs for Python
   tracebacks. Most likely cause: a malformed template PDF.
