#!/usr/bin/env python3
"""
Upload app metadata (description, keywords, screenshots, icon) to App Store Connect.
Usage: python3 scripts/upload-metadata.py [slug] [--all]
Requires: ASC_KEY_ID, ASC_ISSUER_ID, ASC_PRIVATE_KEY env vars
"""
import os, sys, json, time, base64, mimetypes, re
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

def asc_request(token, method, path, body=None, content_type="application/json"):
    url = f"https://api.appstoreconnect.apple.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": content_type,
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body_str = e.read().decode()[:500]
        print(f"  HTTP {e.code} on {method} {path}: {body_str}")
        return None

def get_or_create_localization(token, app_id, locale="en-US"):
    resp = asc_request(token, "GET", f"/v1/apps/{app_id}/appInfos")
    if not resp or not resp.get("data"):
        return None, None
    info_id = resp["data"][0]["id"]

    locs = asc_request(token, "GET", f"/v1/appInfos/{info_id}/appInfoLocalizations?filter[locale]={locale}")
    if locs and locs.get("data"):
        return info_id, locs["data"][0]["id"]

    create = asc_request(token, "POST", "/v1/appInfoLocalizations", {
        "data": {"type": "appInfoLocalizations", "attributes": {"locale": locale},
                 "relationships": {"appInfo": {"data": {"type": "appInfos", "id": info_id}}}}
    })
    if create and create.get("data"):
        return info_id, create["data"]["id"]
    return info_id, None

def get_or_create_version_localization(token, app_id, locale="en-US"):
    # Get latest editable version
    resp = asc_request(token, "GET",
        f"/v1/apps/{app_id}/appStoreVersions?filter[appStoreState]=PREPARE_FOR_SUBMISSION,DEVELOPER_REJECTED,REJECTED,METADATA_REJECTED&limit=1")
    if not resp or not resp.get("data"):
        # Create new version
        resp2 = asc_request(token, "POST", "/v1/appStoreVersions", {
            "data": {"type": "appStoreVersions",
                     "attributes": {"platform": "IOS", "versionString": "1.0.0"},
                     "relationships": {"app": {"data": {"type": "apps", "id": app_id}}}}
        })
        if not resp2 or not resp2.get("data"):
            print("  Could not create version")
            return None, None
        version_id = resp2["data"]["id"]
    else:
        version_id = resp["data"][0]["id"]

    locs = asc_request(token, "GET",
        f"/v1/appStoreVersions/{version_id}/appStoreVersionLocalizations?filter[locale]={locale}")
    if locs and locs.get("data"):
        return version_id, locs["data"][0]["id"]

    create = asc_request(token, "POST", "/v1/appStoreVersionLocalizations", {
        "data": {"type": "appStoreVersionLocalizations",
                 "attributes": {"locale": locale},
                 "relationships": {"appStoreVersion": {"data": {"type": "appStoreVersions", "id": version_id}}}}
    })
    if create and create.get("data"):
        return version_id, create["data"]["id"]
    return version_id, None

def upload_screenshot(token, version_loc_id, img_path, display_type="APP_IPHONE_65"):
    img = Path(img_path)
    size = img.stat().st_size
    # Reserve slot
    reserve = asc_request(token, "POST", "/v1/appScreenshots", {
        "data": {"type": "appScreenshots",
                 "attributes": {"fileSize": size, "fileName": img.name,
                                "screenshotDisplayType": display_type},
                 "relationships": {"appStoreVersionLocalization":
                     {"data": {"type": "appStoreVersionLocalizations", "id": version_loc_id}}}}
    })
    if not reserve or not reserve.get("data"):
        print(f"  Could not reserve screenshot slot for {img.name}")
        return False

    sc = reserve["data"]
    sc_id = sc["id"]
    ops = sc.get("attributes", {}).get("uploadOperations", [])
    if not ops:
        print(f"  No upload operations for {img.name}")
        return False

    # Upload parts
    img_data = img.read_bytes()
    for op in ops:
        offset = op["offset"]
        length = op["length"]
        chunk = img_data[offset:offset+length]
        req_headers = {h["name"]: h["value"] for h in op.get("requestHeaders", [])}
        upload_req = urllib.request.Request(op["url"], data=chunk, method=op["method"],
                                           headers=req_headers)
        try:
            urllib.request.urlopen(upload_req, timeout=60)
        except Exception as ex:
            print(f"  Upload chunk failed: {ex}")
            return False

    # Commit
    md5 = __import__("hashlib").md5(img_data).hexdigest()
    asc_request(token, "PATCH", f"/v1/appScreenshots/{sc_id}", {
        "data": {"type": "appScreenshots", "id": sc_id,
                 "attributes": {"uploaded": True, "sourceFileChecksum": md5}}
    })
    return True

def set_app_info(token, app_id, name, subtitle, locale="en-US"):
    _, loc_id = get_or_create_localization(token, app_id, locale)
    if not loc_id:
        print("  Could not get app info localization")
        return
    asc_request(token, "PATCH", f"/v1/appInfoLocalizations/{loc_id}", {
        "data": {"type": "appInfoLocalizations", "id": loc_id,
                 "attributes": {"name": name[:30], "subtitle": subtitle[:30]}}
    })
    print(f"  App info updated: name={name[:30]}")

def set_version_metadata(token, version_loc_id, description, keywords, whats_new):
    asc_request(token, "PATCH", f"/v1/appStoreVersionLocalizations/{version_loc_id}", {
        "data": {"type": "appStoreVersionLocalizations", "id": version_loc_id,
                 "attributes": {
                     "description": description[:4000],
                     "keywords": keywords[:100],
                     "whatsNew": whats_new[:4000],
                     "supportUrl": "https://practicetestgeeks.com/contact-us",
                     "marketingUrl": "https://practicetestgeeks.com",
                 }}
    })
    print(f"  Version metadata set")

APP_META = {
    "reading":         {"subtitle": "Reading Skills Practice",    "keywords": "reading comprehension,practice test,reading skills,exam prep,passages"},
    "bluebook-sat":    {"subtitle": "Digital SAT Prep",           "keywords": "bluebook SAT,digital SAT,College Board,SAT prep,exam practice"},
    "mensa":           {"subtitle": "IQ Test Practice",           "keywords": "MENSA,IQ test,intelligence,aptitude,exam prep"},
    "cefr":            {"subtitle": "English Proficiency Prep",   "keywords": "CEFR,English proficiency,language test,B1 B2 C1,exam prep"},
    "nbt":             {"subtitle": "NBT Exam Prep",              "keywords": "NBT,National Benchmark Test,South Africa,university,exam prep"},
    "bartender":       {"subtitle": "Bartender License Prep",     "keywords": "bartender,liquor license,alcohol certification,TIPS,exam prep"},
    "ielts":           {"subtitle": "IELTS Band Score Prep",      "keywords": "IELTS,English test,British Council,band score,exam prep"},
    "police-officer":  {"subtitle": "Law Enforcement Prep",       "keywords": "police officer,law enforcement,civil service,aptitude,exam prep"},
    "ap-us-history":   {"subtitle": "APUSH Exam Prep",            "keywords": "AP US History,APUSH,Advanced Placement,College Board,exam prep"},
    "celpip":          {"subtitle": "Canadian English Test Prep", "keywords": "CELPIP,Canadian English,Paragon Testing,PR,exam prep"},
    "alcpt":           {"subtitle": "Military English Placement",  "keywords": "ALCPT,American Language Course,military English,placement,exam prep"},
    "truck-dispatcher":{"subtitle": "Dispatcher Certification",   "keywords": "truck dispatcher,freight,logistics,DOT,exam prep"},
    "amcat":           {"subtitle": "Aspiring Minds Test Prep",   "keywords": "AMCAT,Aspiring Minds,computer adaptive,placement,exam prep"},
    "ibew-aptitude":   {"subtitle": "Electrician Aptitude Prep",  "keywords": "IBEW,electrician,aptitude test,NJATC,exam prep"},
    "amc-mcq":         {"subtitle": "Australian Medical Prep",    "keywords": "AMC,Australian Medical Council,MCQ,medical,exam prep"},
    "bar-exam":        {"subtitle": "Bar Exam MBE Prep",          "keywords": "bar exam,MBE,law,attorney,Multistate,exam prep"},
    "ucat":            {"subtitle": "Medical School Aptitude",    "keywords": "UCAT,university clinical aptitude,medical school,UK,exam prep"},
    "millwright":      {"subtitle": "Industrial Mechanic Prep",   "keywords": "millwright,industrial mechanic,Red Seal,trades,exam prep"},
    "bls":             {"subtitle": "BLS Certification Prep",     "keywords": "BLS,basic life support,CPR,AHA,certification,exam prep"},
    "smart-serve":     {"subtitle": "Ontario Alcohol Cert Prep",  "keywords": "Smart Serve,Ontario,alcohol service,responsible,exam prep"},
    "air-brake":       {"subtitle": "Air Brake Endorsement Prep", "keywords": "air brake,commercial vehicle,CDL,endorsement,exam prep"},
}

DESCRIPTION_TEMPLATE = """{name} — Free Practice Tests & Exam Prep

Pass your {name} on the first try with hundreds of real practice questions, detailed answer explanations, and instant scoring.

FEATURES:
• Hundreds of practice questions with detailed explanations
• Instant scoring and performance tracking
• Study mode and timed test mode
• Covers all exam topics and domains
• Free — no account required

Whether you're a first-time test-taker or brushing up before your exam, our {name} app gives you everything you need to succeed.

Download now and start practicing for free!"""

def process_app(token, slug, app_data):
    app_id = app_data.get("ascAppId")
    if not app_id:
        print(f"  [{slug}] No ascAppId, skipping")
        return

    print(f"\n[{slug}] Processing app ID {app_id}")
    meta = APP_META.get(slug, {"subtitle": "Practice Test Prep", "keywords": "practice test,exam prep"})
    display_name = app_data.get("ascName") or app_data.get("name", slug)
    description = DESCRIPTION_TEMPLATE.format(name=display_name)

    # 1. App info (name + subtitle)
    set_app_info(token, app_id, display_name, meta["subtitle"])

    # 2. Version metadata (description + keywords)
    _, version_loc_id = get_or_create_version_localization(token, app_id)
    if version_loc_id:
        set_version_metadata(token, version_loc_id, description, meta["keywords"],
                             "Initial release.")

        # 3. Screenshots
        sc_dir = ROOT / "screenshots" / slug
        if sc_dir.exists():
            existing = asc_request(token, "GET",
                f"/v1/appStoreVersionLocalizations/{version_loc_id}/appScreenshots")
            if existing and existing.get("data"):
                print(f"  Screenshots already exist ({len(existing['data'])}), skipping")
            else:
                sc_files = sorted(sc_dir.glob("*.png"))[:3]
                for sc_path in sc_files:
                    ok = upload_screenshot(token, version_loc_id, sc_path, "APP_IPHONE_65")
                    print(f"  Screenshot {sc_path.name}: {'✓' if ok else '✗'}")

    print(f"  [{slug}] Done")

def main():
    key_id     = os.environ.get("ASC_KEY_ID", "")
    issuer_id  = os.environ.get("ASC_ISSUER_ID", "")
    private_key = os.environ.get("ASC_PRIVATE_KEY", "")

    if not all([key_id, issuer_id, private_key]):
        print("Missing ASC env vars")
        sys.exit(1)

    token = make_jwt(key_id, issuer_id, private_key)
    apps = json.loads((ROOT / "apps.json").read_text())

    slugs_arg = sys.argv[1:] if sys.argv[1:] else []
    if "--all" in slugs_arg or not slugs_arg:
        target_slugs = list(apps.keys())
    else:
        target_slugs = [s for s in slugs_arg if s != "--all"]

    for slug in target_slugs:
        if slug not in apps:
            print(f"Unknown slug: {slug}")
            continue
        process_app(token, slug, apps[slug])

if __name__ == "__main__":
    main()
