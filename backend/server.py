import dns.resolver
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

HISTORY_FILE = os.path.join(os.path.dirname(__file__), "history.json")
DAILY_LIMIT = 10


def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    return []


def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def count_today(history):
    today = date.today().isoformat()
    return sum(1 for entry in history if entry.get("date") == today)


_resolver = dns.resolver.Resolver()
_resolver.timeout = 1.5    # seconds per individual query attempt
_resolver.lifetime = 3.0   # total seconds before giving up on a name


def _check_domain(domain):
    """Return domain only if it has MX records (can actually receive email)."""
    try:
        _resolver.resolve(domain, "MX")
        return domain
    except Exception:
        return None


def find_company_domain(company_name):
    """Try to find company domain by searching common patterns in parallel."""
    clean = re.sub(r"[^a-zA-Z0-9 ]", "", company_name).strip().lower()
    words = clean.split()

    fillers = {"inc", "llc", "ltd", "co", "corp", "the", "and", "of"}
    core_words = [w for w in words if w not in fillers]
    if not core_words:
        core_words = words

    candidates = set()
    tlds = [".com", ".io", ".co", ".org", ".dev", ".ai", ".xyz", ".tech"]

    joined = "".join(core_words)
    for tld in tlds:
        candidates.add(f"{joined}{tld}")

    if len(core_words) > 1:
        hyphenated = "-".join(core_words)
        for tld in tlds:
            candidates.add(f"{hyphenated}{tld}")

    for tld in tlds:
        candidates.add(f"{core_words[0]}{tld}")

    if len(core_words) > 1:
        for tld in tlds:
            candidates.add(f"{core_words[-1]}{tld}")

    for prefix in ["get", "use", "try", "go", "hey"]:
        candidates.add(f"{prefix}{joined}.com")
    for suffix in ["app", "hq", "io", "labs", "tech", "ai"]:
        candidates.add(f"{joined}{suffix}.com")

    if len(core_words) > 1:
        initials = "".join(w[0] for w in core_words)
        for tld in tlds:
            candidates.add(f"{initials}{tld}")

    # Check all candidates in parallel; collect the first 3 that resolve.
    verified = []
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(_check_domain, d): d for d in candidates}
        for future in as_completed(futures):
            result = future.result()
            if result:
                with lock:
                    if len(verified) < 3:
                        verified.append(result)

    return verified[:3]


def generate_email_patterns(first_name, last_name, domain):
    """Generate common work email patterns."""
    f = first_name.lower().strip()
    l = last_name.lower().strip()
    fi = f[0] if f else ""

    patterns = [
        f"{f}@{domain}",
        f"{f}.{l}@{domain}",
        f"{f}{l}@{domain}",
        f"{fi}{l}@{domain}",
        f"{fi}.{l}@{domain}",
        f"{l}@{domain}",
        f"{f}_{l}@{domain}",
        f"{f}-{l}@{domain}",
    ]
    return patterns



def verify_emails(patterns, domain):
    """
    Return patterns with their verification status.
    SMTP verification via port 25 is blocked on most cloud hosts (including
    Render's free tier), so we skip it and mark all patterns as 'unverified'.
    The domain is MX-confirmed, so the patterns are valid formats to try.
    """
    return [{"email": p, "status": "unverified"} for p in patterns]


def _verify_domain(domain, first_name, last_name):
    """Generate and verify all patterns for one domain."""
    patterns = generate_email_patterns(first_name, last_name, domain)
    emails = verify_emails(patterns, domain)
    return {"domain": domain, "emails": emails}


@app.route("/")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/search", methods=["POST"])
def search():
    history = load_history()
    today_count = count_today(history)

    if today_count >= DAILY_LIMIT:
        return jsonify({"error": f"Daily limit reached ({DAILY_LIMIT}/day). Try again tomorrow."}), 429

    data = request.json
    full_name = data.get("name", "").strip()
    company = data.get("company", "").strip()
    x_username = data.get("x_username", "").strip()

    if not full_name or not company:
        return jsonify({"error": "Name and company are required."}), 400

    parts = full_name.split()
    if len(parts) < 2:
        return jsonify({"error": "Please enter a full name (first and last)."}), 400

    first_name = parts[0]
    last_name = parts[-1]

    domains = find_company_domain(company)
    if not domains:
        entry = {
            "date": date.today().isoformat(),
            "timestamp": datetime.now().isoformat(),
            "name": full_name,
            "company": company,
            "x_username": x_username,
            "status": "no_domain",
            "domains_found": [],
            "results": [],
        }
        history.append(entry)
        save_history(history)
        return jsonify({
            "name": full_name,
            "company": company,
            "status": "no_domain",
            "message": f"Could not find a valid domain for '{company}'. Try entering the domain manually.",
            "domains_found": [],
            "results": [],
        })

    # Verify all found domains in parallel
    all_results = []
    with ThreadPoolExecutor(max_workers=len(domains)) as executor:
        futures = [
            executor.submit(_verify_domain, domain, first_name, last_name)
            for domain in domains
        ]
        for future in as_completed(futures):
            all_results.append(future.result())

    entry = {
        "date": date.today().isoformat(),
        "timestamp": datetime.now().isoformat(),
        "name": full_name,
        "company": company,
        "x_username": x_username,
        "status": "completed",
        "domains_found": domains,
        "results": all_results,
    }
    history.append(entry)
    save_history(history)

    return jsonify({
        "name": full_name,
        "company": company,
        "x_username": x_username,
        "status": "completed",
        "domains_found": domains,
        "results": all_results,
        "remaining_today": DAILY_LIMIT - today_count - 1,
    })


@app.route("/api/search-manual", methods=["POST"])
def search_manual():
    """Search with a manually provided domain."""
    history = load_history()
    today_count = count_today(history)

    if today_count >= DAILY_LIMIT:
        return jsonify({"error": f"Daily limit reached ({DAILY_LIMIT}/day). Try again tomorrow."}), 429

    data = request.json
    full_name = data.get("name", "").strip()
    company = data.get("company", "").strip()
    domain = data.get("domain", "").strip()
    x_username = data.get("x_username", "").strip()

    if not full_name or not domain:
        return jsonify({"error": "Name and domain are required."}), 400

    parts = full_name.split()
    if len(parts) < 2:
        return jsonify({"error": "Please enter a full name (first and last)."}), 400

    first_name = parts[0]
    last_name = parts[-1]

    result = _verify_domain(domain, first_name, last_name)
    all_results = [result]

    entry = {
        "date": date.today().isoformat(),
        "timestamp": datetime.now().isoformat(),
        "name": full_name,
        "company": company or domain,
        "x_username": x_username,
        "domain_manual": domain,
        "status": "completed",
        "domains_found": [domain],
        "results": all_results,
    }
    history.append(entry)
    save_history(history)

    return jsonify({
        "name": full_name,
        "company": company or domain,
        "x_username": x_username,
        "status": "completed",
        "domains_found": [domain],
        "results": all_results,
        "remaining_today": DAILY_LIMIT - today_count - 1,
    })


@app.route("/api/history")
def get_history():
    history = load_history()
    return jsonify(history)


@app.route("/api/history/clear", methods=["POST"])
def clear_history():
    save_history([])
    return jsonify({"message": "History cleared."})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port)
