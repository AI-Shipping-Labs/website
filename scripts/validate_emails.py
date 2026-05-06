#!/usr/bin/env python
"""Sanity-check imported contact emails: syntax + DNS MX record.

Targets `/api/contacts/export` so it can run against local / staging / prod
without DB access. On failures, optionally tags the contact via
`/api/contacts/<email>/tags`. Read-only by default; pass --mark-bounced to
write the tag.

This is a free first-pass: it catches typos, dead domains, and malformed
addresses (~30-50% of garbage). It does NOT catch "domain exists but the
mailbox doesn't" — for that you need a paid validator (ZeroBounce,
NeverBounce, etc.). Per-domain MX results are cached for the run, so a
3000-row gmail.com batch hits DNS once.

Usage:
    uv run python scripts/validate_emails.py \
        --base-url https://prod.aishippinglabs.com \
        --token "$API_SHIPPING_LABS_API_TOKEN" \
        --tag event \
        --mark-bounced
"""

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

import dns.exception
import dns.resolver
import email_validator

BOUNCED_TAG = "bounced"
DEFAULT_DNS_TIMEOUT = 5.0


def _request(url, *, method, headers, body=None, timeout=120):
    req = urllib.request.Request(
        url, data=body, method=method, headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def fetch_contacts(base_url, token):
    headers = {"Authorization": f"Token {token}", "Accept": "application/json"}
    status, body = _request(
        f"{base_url}/api/contacts/export", method="GET", headers=headers,
    )
    if status != 200:
        raise SystemExit(f"export failed: HTTP {status} {body[:300]}")
    return json.loads(body)["contacts"]


def set_tags(base_url, token, email, tags):
    headers = {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    quoted = urllib.parse.quote(email, safe="@.+")
    body = json.dumps({"tags": tags}).encode("utf-8")
    status, resp_body = _request(
        f"{base_url}/api/contacts/{quoted}/tags",
        method="POST",
        headers=headers,
        body=body,
        timeout=30,
    )
    return status, resp_body


def check_syntax(addr):
    try:
        email_validator.validate_email(addr, check_deliverability=False)
        return True, None
    except email_validator.EmailNotValidError as exc:
        return False, str(exc)


def check_mx(domain, *, cache, timeout=DEFAULT_DNS_TIMEOUT):
    """Return one of "valid" / "no_mx_record" / "dns_timeout"."""
    if domain in cache:
        return cache[domain]
    resolver = dns.resolver.Resolver()
    resolver.lifetime = timeout
    resolver.timeout = timeout
    try:
        answers = resolver.resolve(domain, "MX")
        result = "valid" if list(answers) else "no_mx_record"
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
        result = "no_mx_record"
    except dns.exception.Timeout:
        result = "dns_timeout"
    except dns.exception.DNSException:
        # Catch-all for less common resolver errors. Treat as inconclusive
        # so we don't penalise a real address on a transient hiccup.
        result = "dns_timeout"
    cache[domain] = result
    return result


def classify(email, *, cache, timeout):
    ok, _err = check_syntax(email)
    if not ok:
        return "invalid_syntax"
    domain = email.split("@", 1)[1].lower()
    return check_mx(domain, cache=cache, timeout=timeout)


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--base-url", default=os.environ.get("API_BASE_URL", "http://localhost:8000"))
    p.add_argument("--token", default=os.environ.get("API_SHIPPING_LABS_API_TOKEN", ""))
    p.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Restrict to users carrying this tag. Repeatable; multiple OR.",
    )
    p.add_argument(
        "--mark-bounced",
        action="store_true",
        help=f"Add the `{BOUNCED_TAG}` tag to failing users (NOT applied on dns_timeout).",
    )
    p.add_argument("--limit", type=int, default=0, help="Stop after N contacts.")
    p.add_argument("--timeout", type=float, default=DEFAULT_DNS_TIMEOUT, help="Per-DNS-query timeout (seconds).")
    p.add_argument("--verbose", action="store_true", help="Print every failure.")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not args.token:
        raise SystemExit("missing --token (or API_SHIPPING_LABS_API_TOKEN env var)")

    print(f"fetching contacts from {args.base_url}...")
    contacts = fetch_contacts(args.base_url, args.token)
    if args.tag:
        wanted = {t for t in args.tag}
        contacts = [c for c in contacts if wanted & set(c.get("tags") or [])]
    if args.limit:
        contacts = contacts[: args.limit]
    print(f"checking {len(contacts)} contacts (tags={args.tag or 'any'})")

    cache: dict[str, str] = {}
    counts: Counter = Counter()
    failures = defaultdict(list)
    write_attempts = 0
    write_failures = 0

    t0 = time.time()
    for i, contact in enumerate(contacts, 1):
        email = contact["email"]
        result = classify(email, cache=cache, timeout=args.timeout)
        counts[result] += 1
        if result != "valid":
            failures[result].append(email)
            if args.verbose:
                print(f"  {result:18s} {email}")
        if args.mark_bounced and result in ("invalid_syntax", "no_mx_record"):
            existing = list(contact.get("tags") or [])
            if BOUNCED_TAG in existing:
                continue
            new_tags = existing + [BOUNCED_TAG]
            status, body = set_tags(args.base_url, args.token, email, new_tags)
            write_attempts += 1
            if status != 200:
                write_failures += 1
                print(f"  TAG-FAIL {email}: HTTP {status} {body[:150]}")
        if i % 200 == 0:
            print(f"  ...{i}/{len(contacts)} ({time.time()-t0:.1f}s elapsed)")

    print(f"\ndone in {time.time()-t0:.1f}s")
    print("=== results ===")
    print(f"  checked:       {len(contacts)}")
    print(f"  valid:         {counts['valid']}")
    print(f"  invalid_syntax:{counts['invalid_syntax']}")
    print(f"  no_mx_record:  {counts['no_mx_record']}")
    print(f"  dns_timeout:   {counts['dns_timeout']}")
    print(f"  unique domains:{len(cache)}")
    if args.mark_bounced:
        print(f"  tagged `{BOUNCED_TAG}`: {write_attempts - write_failures} (write_failures={write_failures})")

    if not args.verbose:
        for reason in ("invalid_syntax", "no_mx_record", "dns_timeout"):
            if not failures[reason]:
                continue
            print(f"\nfirst 20 {reason}:")
            for email in failures[reason][:20]:
                print(f"  {email}")
            if len(failures[reason]) > 20:
                print(f"  ...and {len(failures[reason]) - 20} more")


if __name__ == "__main__":
    main()
