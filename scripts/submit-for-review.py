#!/usr/bin/env python3
"""
Submit apps for App Store review via ASC REST API.
Usage: python3 scripts/submit-for-review.py [slug] [--all] [--new-only]
Requires: ASC_KEY_ID, ASC_ISSUER_ID, ASC_PRIVATE_KEY env vars
"""
import os, sys, json, time, base64
import urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).parent.parent

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
except ImportError:
    print("pip install cryptography")
    sys.exit(1)

NEW_APPS = []  # kept for backwards compat — now unused; --all is the default

def make_jwt(key_id, issuer_id, private_key_pem):
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "ES256", "kid": key_id, "typ": "JWT"}).encode()
    ).rstrip(b'=').decode()
    now = int(time.time())
    payload = base64.urlsafe_b64encode(
        json.dumps({"iss": issuer_id, "iat": now, "exp": now + 1200,
                    "aud": "appstoreconnect-v1"}).encode()
    ).rstrip(b'=').decode()
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    sig_der = key.sign(f"{header}.{payload}".encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(sig_der)
    raw_sig = base64.urlsafe_b64encode(
        r.to_bytes(32, 'big') + s.to_bytes(32, 'big')
    ).rstrip(b'=').decode()
    return f"{header}.{payload}.{raw_sig}"

def asc_request(token, method, path, body=None, _retry=0):
    url = f"https://api.appstoreconnect.apple.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 429 and _retry < 3:
            wait = 30 * (2 ** _retry)
            print(f"  Rate limited — waiting {wait}s...")
            time.sleep(wait)
            return asc_request(token, method, path, body, _retry + 1)
        body_str = e.read().decode()[:800]
        print(f"  HTTP {e.code} {method} {path}: {body_str}")
        return None
    except Exception as e:
        if _retry < 2:
            wait = 30 * (_retry + 1)
            print(f"  Network error ({type(e).__name__}) — retrying in {wait}s...")
            time.sleep(wait)
            return asc_request(token, method, path, body, _retry + 1)
        print(f"  Network error after retries: {e}")
        return None

def get_editable_version(token, app_id):
    states = "PREPARE_FOR_SUBMISSION,DEVELOPER_REJECTED,REJECTED,METADATA_REJECTED,WAITING_FOR_REVIEW"
    resp = asc_request(token, "GET",
        f"/v1/apps/{app_id}/appStoreVersions?filter[appStoreState]={states}&filter[platform]=IOS&limit=1")
    if resp and resp.get("data"):
        return resp["data"][0]["id"], resp["data"][0]["attributes"]["appStoreState"]
    return None, None

def attach_build_to_version(token, app_id, version_id):
    """Find the latest valid build and link it to the App Store Version. Returns build_id or None."""
    builds = asc_request(token, "GET",
        f"/v1/builds?filter[app]={app_id}&filter[processingState]=VALID&filter[expired]=false&sort=-uploadedDate&limit=1")
    if not builds or not builds.get("data"):
        print(f"  No valid builds found")
        return None
    build_id = builds["data"][0]["id"]
    build_ver = builds["data"][0]["attributes"].get("version", "?")
    resp = asc_request(token, "PATCH", f"/v1/appStoreVersions/{version_id}", {
        "data": {
            "type": "appStoreVersions",
            "id": version_id,
            "relationships": {"build": {"data": {"type": "builds", "id": build_id}}}
        }
    })
    if resp is not None:
        print(f"  Attached build {build_ver} ({build_id})")
        return build_id
    return None

def set_export_compliance(token, build_id):
    """Mark build as not using non-exempt encryption — required before App Store review."""
    asc_request(token, "PATCH", f"/v1/builds/{build_id}", {
        "data": {
            "type": "builds",
            "id": build_id,
            "attributes": {"usesNonExemptEncryption": False}
        }
    })
    return True

def wait_for_screenshots_ready(token, version_id, max_wait=600):
    """Poll until all screenshots have assetDeliveryState.state=COMPLETE."""
    locs = asc_request(token, "GET",
        f"/v1/appStoreVersions/{version_id}/appStoreVersionLocalizations?filter[locale]=en-US")
    if not locs or not locs.get("data"):
        return True
    loc_id = locs["data"][0]["id"]
    sc_sets = asc_request(token, "GET",
        f"/v1/appStoreVersionLocalizations/{loc_id}/appScreenshotSets?limit=40")
    if not sc_sets or not sc_sets.get("data"):
        return True
    waited = 0
    while waited < max_wait:
        all_ready = True
        for sc_set in sc_sets["data"]:
            screenshots = asc_request(token, "GET",
                f"/v1/appScreenshotSets/{sc_set['id']}/appScreenshots?limit=20")
            if not screenshots or not screenshots.get("data"):
                continue
            for sc in screenshots["data"]:
                delivery = sc["attributes"].get("assetDeliveryState", {})
                state = delivery.get("state", "COMPLETE") if delivery else "COMPLETE"
                if state == "FAILED":
                    # Failed screenshots block submission — return False to signal re-upload needed
                    errors = delivery.get("errors", [])
                    print(f"  ⚠ Screenshot FAILED: {sc['id'][:8]}... errors={errors}")
                    return False
                if state not in ("COMPLETE",):
                    all_ready = False
                    break
            if not all_ready:
                break
        if all_ready:
            return True
        print(f"  Screenshots processing, waiting 30s... ({waited}s elapsed)")
        time.sleep(30)
        waited += 30
    print(f"  ⚠ Screenshots not ready after {max_wait}s, proceeding anyway")
    return False

def submit_for_review(token, app_id, version_id):
    """Use reviewSubmissions API. Reuses existing READY_FOR_REVIEW submission if one exists."""
    # Check for existing submissions first — reuse to avoid 5-slot concurrency limit
    existing = asc_request(token, "GET", f"/v1/reviewSubmissions?filter[app]={app_id}&filter[platform]=IOS")
    submission_id = None
    if existing and existing.get("data"):
        for sub in existing["data"]:
            state = sub["attributes"].get("state", "")
            if state in ("READY_FOR_REVIEW",):
                submission_id = sub["id"]
                print(f"  Reusing existing submission {submission_id[:8]}...")
                break
            elif state in ("WAITING_FOR_REVIEW", "IN_REVIEW", "COMPLETE"):
                return True, f"already-{state.lower()}"

    needs_item = True
    if not submission_id:
        # Create a new reviewSubmission
        resp = asc_request(token, "POST", "/v1/reviewSubmissions", {
            "data": {
                "type": "reviewSubmissions",
                "attributes": {"platform": "IOS"},
                "relationships": {"app": {"data": {"type": "apps", "id": app_id}}}
            }
        })
        if not resp or not resp.get("data"):
            return False, "create reviewSubmission failed"
        submission_id = resp["data"]["id"]
    else:
        # Check if the existing submission already has items
        items_resp = asc_request(token, "GET", f"/v1/reviewSubmissions/{submission_id}/items")
        if items_resp and items_resp.get("data") and len(items_resp["data"]) > 0:
            needs_item = False

    if needs_item:
        # Add reviewSubmissionItem for the version
        resp2 = asc_request(token, "POST", "/v1/reviewSubmissionItems", {
            "data": {
                "type": "reviewSubmissionItems",
                "relationships": {
                    "reviewSubmission": {"data": {"type": "reviewSubmissions", "id": submission_id}},
                    "appStoreVersion": {"data": {"type": "appStoreVersions", "id": version_id}}
                }
            }
        })
        if not resp2 or not resp2.get("data"):
            return False, "add reviewSubmissionItem failed"

    # Submit (set submitted=true)
    resp3 = asc_request(token, "PATCH", f"/v1/reviewSubmissions/{submission_id}", {
        "data": {
            "type": "reviewSubmissions",
            "id": submission_id,
            "attributes": {"submitted": True}
        }
    })
    if not resp3:
        return False, "patch submitted=true failed"
    return True, submission_id

def validate_app_ready(token, app_id, version_id, slug):
    """Check icon, description, screenshots. Returns (ready, issues[])."""
    issues = []

    # Check version localization for description + name
    locs = asc_request(token, "GET",
        f"/v1/appStoreVersions/{version_id}/appStoreVersionLocalizations?filter[locale]=en-US")
    if not locs or not locs.get("data"):
        issues.append("no en-US localization")
    else:
        attrs = locs["data"][0].get("attributes", {})
        desc = attrs.get("description") or ""
        if len(desc) < 50:
            issues.append(f"description too short ({len(desc)} chars)")

        # Check screenshots — screenshotCount is unreliable; check actual screenshot objects
        loc_id = locs["data"][0]["id"]
        sc_sets = asc_request(token, "GET",
            f"/v1/appStoreVersionLocalizations/{loc_id}/appScreenshotSets")
        has_screenshots = False
        if sc_sets and sc_sets.get("data"):
            for sc_set in sc_sets["data"]:
                screenshots = asc_request(token, "GET",
                    f"/v1/appScreenshotSets/{sc_set['id']}/appScreenshots?limit=1")
                if screenshots and screenshots.get("data") and len(screenshots["data"]) > 0:
                    has_screenshots = True
                    break
        if not has_screenshots:
            issues.append("no screenshots")

    # Check app info for name (icon comes from IPA, no separate API check)
    info = asc_request(token, "GET", f"/v1/apps/{app_id}/appInfos")
    if not info or not info.get("data"):
        issues.append("no appInfo")

    return len(issues) == 0, issues

def process_app(creds, slug, app_data):
    key_id, issuer_id, private_key = creds
    token = make_jwt(key_id, issuer_id, private_key)  # fresh token per app (avoids 20-min expiry)
    app_id = app_data.get("ascAppId")
    if not app_id:
        return

    print(f"\n[{slug}] app_id={app_id}")

    version_id, state = get_editable_version(token, app_id)
    if not version_id:
        print(f"  No editable version (already submitted or live)")
        return

    print(f"  State: {state}")

    if state in ("WAITING_FOR_REVIEW", "IN_REVIEW", "PENDING_DEVELOPER_RELEASE", "READY_FOR_SALE"):
        print(f"  Already in review or live, skipping")
        return

    # Validate before submitting
    ready, issues = validate_app_ready(token, app_id, version_id, slug)
    if not ready:
        print(f"  ⚠ Not ready: {', '.join(issues)} — skipping submit")
        return

    # Attach build to version (required before submit)
    build_id = attach_build_to_version(token, app_id, version_id)
    if not build_id:
        print(f"  ⚠ Could not attach build — skipping submit")
        return

    # Mark build as not using non-exempt encryption
    set_export_compliance(token, build_id)

    # Wait for screenshots to finish processing before submitting
    sc_ready = wait_for_screenshots_ready(token, version_id)
    if not sc_ready:
        print(f"  ⚠ FAILED screenshots detected — skipping submit, re-run upload-metadata first")
        return

    # Submit for review
    ok, info = submit_for_review(token, app_id, version_id)
    if ok:
        print(f"  ✅ Submitted for review (submission_id={info})")
    else:
        print(f"  ❌ Submit failed: {info}")

def main():
    key_id     = os.environ.get("ASC_KEY_ID", "")
    issuer_id  = os.environ.get("ASC_ISSUER_ID", "")
    private_key = os.environ.get("ASC_PRIVATE_KEY", "")

    if not all([key_id, issuer_id, private_key]):
        print("Missing ASC env vars")
        sys.exit(1)

    creds = (key_id, issuer_id, private_key)
    apps = json.loads((ROOT / "apps.json").read_text())

    slugs_arg = [s for s in sys.argv[1:] if s not in ("--all", "--new-only")]

    if "--all" in sys.argv or "--new-only" in sys.argv or not sys.argv[1:]:
        target_slugs = [k for k, v in apps.items() if v.get("ascAppId")]
        print(f"Targeting all {len(target_slugs)} registered apps")
    else:
        target_slugs = slugs_arg

    for slug in target_slugs:
        if slug not in apps:
            print(f"Unknown slug: {slug}")
            continue
        process_app(creds, slug, apps[slug])

if __name__ == "__main__":
    main()
