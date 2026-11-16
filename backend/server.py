import dns.resolver
import json
import os
import re
import smtplib
import socket
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


def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    return []


def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)



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



def _get_mx_host(domain):
    """Return the highest-priority MX hostname for a domain."""
    try:
        records = _resolver.resolve(domain, "MX")
        best = sorted(records, key=lambda r: r.preference)[0]
        return str(best.exchange).rstrip(".")
    except Exception:
        return None


def _verify_domain_smtp(domain, emails):
    """
    Verify a list of email addresses against a domain's mail server in one
    SMTP session. Returns a dict mapping email -> status.

    Statuses: 'valid', 'invalid', 'catch_all', 'unknown'
    """
    mx_host = _get_mx_host(domain)
    if not mx_host:
        return {e: "unknown" for e in emails}

    try:
        with smtplib.SMTP(timeout=10) as smtp:
            smtp.connect(mx_host, 25)
            smtp.ehlo("emailfynda.com")
            smtp.mail("verify@emailfynda.com")

            # Catch-all detection: if a nonsense address is accepted, every
            # address on this domain will return 250 — flag them all.
            probe = f"zzz_no_exist_probe_xyz@{domain}"
            code, _ = smtp.rcpt(probe)
            if code == 250:
                return {e: "catch_all" for e in emails}

            # Real checks
            smtp.rset()
            smtp.mail("verify@emailfynda.com")
            results = {}
            for email in emails:
                try:
                    code, _ = smtp.rcpt(email)
                    if code == 250:
                        results[email] = "valid"
                    elif code in (550, 551, 552, 553, 554):
                        results[email] = "invalid"
                    else:
                        results[email] = "unknown"
                except smtplib.SMTPServerDisconnected:
                    # Some servers close after a few RCPTs; mark rest unknown
                    for e in emails:
                        results.setdefault(e, "unknown")
                    break
            return results
    except Exception:
        return {e: "unknown" for e in emails}


def _verify_domain(domain, first_name, last_name):
    """Generate and verify all patterns for one domain via SMTP."""
    patterns = generate_email_patterns(first_name, last_name, domain)
    statuses = _verify_domain_smtp(domain, patterns)
    emails = [{"email": p, "status": statuses.get(p, "unknown")} for p in patterns]
    return {"domain": domain, "emails": emails}


@app.route("/")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/search", methods=["POST"])
def search():
    history = load_history()
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

    # If the company input looks like a domain, use it directly after verifying MX.
    _DOMAIN_RE = re.compile(r"^[a-zA-Z0-9-]+\.[a-zA-Z]{2,}$")
    if _DOMAIN_RE.match(company):
        verified = _check_domain(company)
        domains = [verified] if verified else []
    else:
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
    })


@app.route("/api/search-manual", methods=["POST"])
def search_manual():
    """Search with a manually provided domain."""
    history = load_history()
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
    })


@app.route("/api/history")
def get_history():
    history = load_history()
    return jsonify(history)


@app.route("/api/history/clear", methods=["POST"])
def clear_history():
    save_history([])
    return jsonify({"message": "History cleared."})


@app.route("/api/test-smtp")
def test_smtp():
    """Check whether outbound port 25 is reachable from this host."""
    targets = [
        ("gmail-smtp-in.l.google.com", 25),
        ("aspmx.l.google.com", 25),
        ("alt1.aspmx.l.google.com", 25),
    ]
    results = []
    for host, port in targets:
        try:
            with socket.create_connection((host, port), timeout=5):
                results.append({"host": host, "port": port, "reachable": True})
        except Exception as e:
            results.append({"host": host, "port": port, "reachable": False, "error": str(e)})

    any_ok = any(r["reachable"] for r in results)
    return jsonify({
        "port_25_reachable": any_ok,
        "verdict": "SMTP verification should work" if any_ok else "Port 25 is blocked on this host — SMTP verification won't work",
        "targets": results,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port)
