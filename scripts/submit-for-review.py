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

NEW_APPS = [
    "reading","bluebook-sat","mensa","cefr","nbt","bartender","ielts",
    "police-officer","ap-us-history","celpip","alcpt","truck-dispatcher",
    "amcat","ibew-aptitude","amc-mcq","bar-exam","ucat","millwright",
    "bls","smart-serve","air-brake"
]

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

def asc_request(token, method, path, body=None):
    url = f"https://api.appstoreconnect.apple.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body_str = e.read().decode()[:800]
        print(f"  HTTP {e.code} {method} {path}: {body_str}")
        return None

def get_editable_version(token, app_id):
    states = "PREPARE_FOR_SUBMISSION,DEVELOPER_REJECTED,REJECTED,METADATA_REJECTED,WAITING_FOR_REVIEW"
    resp = asc_request(token, "GET",
        f"/v1/apps/{app_id}/appStoreVersions?filter[appStoreState]={states}&filter[platform]=IOS&limit=1")
    if resp and resp.get("data"):
        return resp["data"][0]["id"], resp["data"][0]["attributes"]["appStoreState"]
    return None, None

def set_export_compliance(token, version_id):
    # Mark as not using non-exempt encryption (standard for WebView apps)
    resp = asc_request(token, "PATCH", f"/v1/appStoreVersions/{version_id}", {
        "data": {
            "type": "appStoreVersions",
            "id": version_id,
            "attributes": {
                "usesNonExemptEncryption": False
            }
        }
    })
    return resp is not None

def get_or_set_age_rating(token, app_id):
    # Check if age rating questionnaire exists
    resp = asc_request(token, "GET", f"/v1/apps/{app_id}/ageRatingDeclaration")
    if not resp or not resp.get("data"):
        return False
    decl_id = resp["data"]["id"]
    # Set all ratings to NONE / false (educational content, no mature content)
    asc_request(token, "PATCH", f"/v1/ageRatingDeclarations/{decl_id}", {
        "data": {
            "type": "ageRatingDeclarations",
            "id": decl_id,
            "attributes": {
                "alcoholTobaccoOrDrugUseOrReferences": "NONE",
                "contests": "NONE",
                "gambling": False,
                "gamblingSimulated": "NONE",
                "kidsAgeBand": None,
                "lootBox": False,
                "medicalOrTreatmentInformation": "NONE",
                "profanityOrCrudeHumor": "NONE",
                "seventeenPlus": False,
                "sexualContentGraphicAndNudity": "NONE",
                "sexualContentOrNudity": "NONE",
                "unrestrictedWebAccess": True,
                "violenceCartoonOrFantasy": "NONE",
                "violenceRealisticProlongedGraphicOrSadistic": "NONE",
                "violenceRealistic": "NONE",
                "horrorOrFearThemes": "NONE",
                "matureOrSuggestiveThemes": "NONE"
            }
        }
    })
    return True

def submit_for_review(token, version_id):
    resp = asc_request(token, "POST", "/v1/appStoreVersionSubmissions", {
        "data": {
            "type": "appStoreVersionSubmissions",
            "relationships": {
                "appStoreVersion": {
                    "data": {"type": "appStoreVersions", "id": version_id}
                }
            }
        }
    })
    return resp is not None

def process_app(token, slug, app_data):
    app_id = app_data.get("ascAppId")
    if not app_id:
        print(f"  [{slug}] No ascAppId, skipping")
        return

    print(f"\n[{slug}] app_id={app_id}")

    version_id, state = get_editable_version(token, app_id)
    if not version_id:
        print(f"  [{slug}] No editable version found (may already be submitted or live)")
        return

    print(f"  State: {state}")

    if state == "WAITING_FOR_REVIEW":
        print(f"  Already submitted, skipping")
        return

    # Set export compliance
    ok = set_export_compliance(token, version_id)
    print(f"  Export compliance: {'✓' if ok else '✗'}")

    # Set age rating
    ok = get_or_set_age_rating(token, app_id)
    print(f"  Age rating: {'✓' if ok else 'skipped'}")

    # Submit for review
    ok = submit_for_review(token, version_id)
    print(f"  Submit for review: {'✓' if ok else '✗'}")

def main():
    key_id     = os.environ.get("ASC_KEY_ID", "")
    issuer_id  = os.environ.get("ASC_ISSUER_ID", "")
    private_key = os.environ.get("ASC_PRIVATE_KEY", "")

    if not all([key_id, issuer_id, private_key]):
        print("Missing ASC env vars")
        sys.exit(1)

    token = make_jwt(key_id, issuer_id, private_key)
    apps = json.loads((ROOT / "apps.json").read_text())

    slugs_arg = [s for s in sys.argv[1:] if s not in ("--all", "--new-only")]
    new_only = "--new-only" in sys.argv

    if "--all" in sys.argv or not sys.argv[1:]:
        target_slugs = list(apps.keys())
    elif new_only:
        target_slugs = NEW_APPS
    else:
        target_slugs = slugs_arg

    for slug in target_slugs:
        if slug not in apps:
            print(f"Unknown slug: {slug}")
            continue
        process_app(token, slug, apps[slug])

if __name__ == "__main__":
    main()
