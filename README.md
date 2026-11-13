# Email Finder

Find work emails for founders. Enter a name and company, and the tool guesses the domain, generates common email patterns, and verifies them against the mail server.

## Architecture

- **Backend** (Render, free tier): Flask API with SMTP verification
- **Frontend** (Netlify): Static HTML styled after Vercel's design system

## Deploy

### 1. Backend → Render

1. Push this repo to GitHub
2. Go to [render.com](https://render.com), connect your GitHub repo
3. Render auto-detects `render.yaml` and creates the service
4. Copy your backend URL (e.g. `https://email-finder-api.onrender.com`)

### 2. Frontend → Netlify

1. Open `frontend/index.html` and replace `YOUR_RENDER_URL_HERE` with your Render backend URL
2. Go to [netlify.com](https://netlify.com), connect the same GitHub repo
3. Set **Base directory** to `frontend` and **Publish directory** to `frontend`
4. Deploy

## Local Development

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python server.py
```

Open `frontend/index.html` in your browser (update `API_URL` to `http://localhost:5050`).

## How it works

1. You enter a founder's name and company (and optionally their X handle)
2. The tool guesses the company domain from common patterns (joined words, hyphenated, abbreviations, common prefixes/suffixes)
3. If no domain is found, you can type one in manually
4. It generates 8 email pattern variations (first@, first.last@, flast@, etc.)
5. It verifies each pattern against the mail server using SMTP RCPT TO
6. Results show which emails are likely valid, invalid, catch-all, or unknown

## Limits

- 10 lookups per day
- SMTP verification accuracy varies by mail server
- Render free tier spins down after 15 min of inactivity (~30s cold start)
