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

def detect_display_type(img_path):
    """Map PNG dimensions to the correct ASC screenshotDisplayType."""
    import struct
    data = open(img_path, 'rb').read(24)
    w = struct.unpack('>I', data[16:20])[0]
    h = struct.unpack('>I', data[20:24])[0]
    landscape = w > h
    if landscape: w, h = h, w  # normalize to portrait
    mapping = {
        (1290, 2796): "APP_IPHONE_67",  # iPhone 14/15/16 Pro Max
        (1284, 2778): "APP_IPHONE_65",  # iPhone 12/13 Pro Max
        (1242, 2688): "APP_IPHONE_65",  # iPhone XS Max
        (1125, 2436): "APP_IPHONE_58",  # iPhone X/XS
        (828,  1792): "APP_IPHONE_61",  # iPhone XR/11
        (1242, 2208): "APP_IPHONE_55",  # iPhone 6/7/8 Plus
        (750,  1334): "APP_IPHONE_47",  # iPhone 6/7/8
    }
    dtype = mapping.get((w, h))
    if not dtype:
        # Default: use 6.7" for anything large-ish
        dtype = "APP_IPHONE_67" if h >= 2700 else "APP_IPHONE_65"
    return dtype if not landscape else dtype  # TODO landscape support

def delete_all_screenshot_sets(token, version_loc_id):
    """Delete all existing screenshot sets so we can re-upload with correct type."""
    sets = asc_request(token, "GET",
        f"/v1/appStoreVersionLocalizations/{version_loc_id}/appScreenshotSets")
    if not sets or not sets.get("data"):
        return
    for s in sets["data"]:
        set_id = s["id"]
        # Delete individual screenshots first
        sc_list = asc_request(token, "GET", f"/v1/appScreenshotSets/{set_id}/appScreenshots")
        if sc_list and sc_list.get("data"):
            for sc in sc_list["data"]:
                asc_request(token, "DELETE", f"/v1/appScreenshots/{sc['id']}")
        # Then delete the set
        asc_request(token, "DELETE", f"/v1/appScreenshotSets/{set_id}")
    print(f"  Cleared {len(sets['data'])} screenshot set(s)")

def get_or_create_screenshot_set(token, version_loc_id, display_type):
    sets = asc_request(token, "GET",
        f"/v1/appStoreVersionLocalizations/{version_loc_id}/appScreenshotSets?filter[screenshotDisplayType]={display_type}")
    if sets and sets.get("data"):
        return sets["data"][0]["id"]
    resp = asc_request(token, "POST", "/v1/appScreenshotSets", {
        "data": {"type": "appScreenshotSets",
                 "attributes": {"screenshotDisplayType": display_type},
                 "relationships": {"appStoreVersionLocalization":
                     {"data": {"type": "appStoreVersionLocalizations", "id": version_loc_id}}}}
    })
    if resp and resp.get("data"):
        return resp["data"]["id"]
    return None

def upload_screenshot(token, version_loc_id, img_path, display_type=None):
    img = Path(img_path)
    size = img.stat().st_size

    # Auto-detect display type from image dimensions
    if not display_type:
        display_type = detect_display_type(str(img_path))

    # Get/create screenshot set for this display type
    set_id = get_or_create_screenshot_set(token, version_loc_id, display_type)
    if not set_id:
        print(f"  Could not get screenshot set for {display_type}")
        return False

    # Reserve slot
    reserve = asc_request(token, "POST", "/v1/appScreenshots", {
        "data": {"type": "appScreenshots",
                 "attributes": {"fileSize": size, "fileName": img.name},
                 "relationships": {"appScreenshotSet":
                     {"data": {"type": "appScreenshotSets", "id": set_id}}}}
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
    info_id, loc_id = get_or_create_localization(token, app_id, locale)
    if not loc_id:
        print("  Could not get app info localization")
        return
    asc_request(token, "PATCH", f"/v1/appInfoLocalizations/{loc_id}", {
        "data": {"type": "appInfoLocalizations", "id": loc_id,
                 "attributes": {"name": name[:30], "subtitle": subtitle[:30],
                                "privacyPolicyUrl": "https://practicetestgeeks.com/privacy-policy/"}}
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
    # Batch 2 — 41 apps added 2026-05-08
    "473-postal-exam": {"subtitle": "USPS 473 Exam Prep",         "keywords": "473 postal exam,USPS,postal worker,civil service,exam prep"},
    "act":             {"subtitle": "ACT College Test Prep",      "keywords": "ACT,American College Testing,college admission,test prep,exam"},
    "ase":             {"subtitle": "Auto Mechanic Cert Prep",    "keywords": "ASE,Automotive Service,auto mechanic,certification,exam prep"},
    "boating":         {"subtitle": "Boater Safety License Prep", "keywords": "boating license,boater safety,marine,boating exam,test prep"},
    "canada-citizenship": {"subtitle": "Canadian Citizenship Test","keywords": "Canadian citizenship,Canada test,immigration,civic exam,prep"},
    "ccht":            {"subtitle": "Hemodialysis Tech Prep",     "keywords": "CCHT,hemodialysis,nephrology technician,certification,exam prep"},
    "cda":             {"subtitle": "Child Development Cert",     "keywords": "CDA,child development associate,early childhood,daycare,exam prep"},
    "chauffeur":       {"subtitle": "Chauffeur License Prep",     "keywords": "chauffeur license,driver license,commercial driving,DMV,exam prep"},
    "clb":             {"subtitle": "Canadian Language Test",     "keywords": "CLB,Canadian Language Benchmarks,English,immigration,exam prep"},
    "cpr":             {"subtitle": "CPR Certification Prep",     "keywords": "CPR,cardiopulmonary resuscitation,first aid,life support,certification"},
    "dmv":             {"subtitle": "Driver License Test Prep",   "keywords": "DMV,driver license,permit test,driving,exam prep"},
    "ekg":             {"subtitle": "EKG Tech Cert Prep",         "keywords": "EKG,electrocardiogram,cardiology technician,certification,exam prep"},
    "epa":             {"subtitle": "EPA 608 HVAC Prep",          "keywords": "EPA 608,refrigerant,HVAC,environmental,certification,exam prep"},
    "esl":             {"subtitle": "ESL Practice Test",          "keywords": "ESL,English second language,ELL,language learning,test prep"},
    "f-02":            {"subtitle": "NYC Fireguard F-02 Prep",    "keywords": "F-02,fireguard,NYC fire safety,fire warden,certification"},
    "forklift":        {"subtitle": "Forklift License Prep",      "keywords": "forklift license,OSHA,warehouse,safety,certification,exam prep"},
    "fsc":             {"subtitle": "CA Firearm Safety Cert",     "keywords": "FSC,firearm safety certificate,California gun,handgun,exam prep"},
    "g1":              {"subtitle": "Ontario G1 License Prep",    "keywords": "G1 Ontario,driver license,Canada driving,permit,exam prep"},
    "hha":             {"subtitle": "Home Health Aide Prep",      "keywords": "HHA,home health aide,nursing,caregiver,certification,exam prep"},
    "hiset":           {"subtitle": "HiSET Diploma Test Prep",    "keywords": "HiSET,high school equivalency,GED alternative,diploma,exam prep"},
    "language-proficiency": {"subtitle": "Language Proficiency Test","keywords": "language proficiency,English fluency,language test,immigration,exam prep"},
    "lpn":             {"subtitle": "LPN Nursing Cert Prep",      "keywords": "LPN,licensed practical nurse,nursing,NCLEX-PN,exam prep"},
    "mace":            {"subtitle": "Medication Aide Cert",       "keywords": "MACE,medication aide,nursing,certification,med tech,exam prep"},
    "moca":            {"subtitle": "Cognitive Assessment Test",  "keywords": "MoCA,Montreal Cognitive Assessment,dementia screening,cognitive test,exam"},
    "nccco":           {"subtitle": "Crane Operator Cert Prep",   "keywords": "NCCCO,crane operator,certification,construction,exam prep"},
    "notary-public":   {"subtitle": "Notary Public Test Prep",    "keywords": "notary public,notary exam,signature certification,test prep"},
    "nremt":           {"subtitle": "EMT Paramedic Cert Prep",    "keywords": "NREMT,EMT,paramedic,emergency medical,certification,exam prep"},
    "parapro":         {"subtitle": "ParaPro Teaching Test",      "keywords": "ParaPro,paraprofessional,teaching assistant,ETS,exam prep"},
    "pca":             {"subtitle": "Care Assistant Cert Prep",   "keywords": "PCA,personal care assistant,caregiver,nursing aide,certification"},
    "pert":            {"subtitle": "FL College Placement Test",  "keywords": "PERT,Florida college placement,postsecondary readiness,exam prep"},
    "ptcb":            {"subtitle": "Pharmacy Tech Cert Prep",    "keywords": "PTCB,pharmacy technician,certification,CPhT,exam prep"},
    "pte":             {"subtitle": "Pearson English Test",       "keywords": "PTE,Pearson English,language test,immigration,exam prep"},
    "ramsay":          {"subtitle": "Mechanical Aptitude Prep",   "keywords": "Ramsay,mechanical aptitude,maintenance,industrial,exam prep"},
    "rbt":             {"subtitle": "Behavior Tech Cert Prep",    "keywords": "RBT,registered behavior technician,ABA,autism therapy,exam prep"},
    "rma":             {"subtitle": "Medical Assistant Cert",     "keywords": "RMA,registered medical assistant,AMT,clinical,exam prep"},
    "snhd":            {"subtitle": "NV Food Handler Cert",       "keywords": "SNHD,Southern Nevada Health,food handler,Las Vegas,exam prep"},
    "tabe":            {"subtitle": "Adult Basic Education Test", "keywords": "TABE,adult basic education,GED prep,literacy,exam prep"},
    "toefl":           {"subtitle": "TOEFL English Test Prep",    "keywords": "TOEFL,English language,iBT,university admission,exam prep"},
    "tsi":             {"subtitle": "Texas College Readiness",    "keywords": "TSI,Texas Success Initiative,college readiness,Texas,exam prep"},
    "wonderlic":       {"subtitle": "Wonderlic Cognitive Test",   "keywords": "Wonderlic,personnel test,cognitive aptitude,employment,exam prep"},
    "workkeys":        {"subtitle": "WorkKeys Skills Cert",       "keywords": "WorkKeys,ACT NCRC,workplace skills,job certification,exam prep"},
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

def get_meta(slug, app_data):
    """Return metadata for any app — use APP_META if available, else derive from name."""
    if slug in APP_META:
        return APP_META[slug]
    name = app_data.get("ascName") or app_data.get("name", slug.replace("-", " ").title())
    # Extract keywords from slug
    words = slug.replace("-", " ")
    return {
        "subtitle": "Practice Test & Exam Prep",
        "keywords": f"{words},practice test,exam prep,study guide,test preparation",
    }

def process_app(token, slug, app_data):
    app_id = app_data.get("ascAppId")
    if not app_id:
        print(f"  [{slug}] No ascAppId, skipping")
        return

    print(f"\n[{slug}] Processing app ID {app_id}")
    meta = get_meta(slug, app_data)
    display_name = app_data.get("ascName") or app_data.get("name", slug)
    description = DESCRIPTION_TEMPLATE.format(name=display_name)

    # 1. App info (name + subtitle)
    set_app_info(token, app_id, display_name, meta["subtitle"])

    # 2. Version metadata (description + keywords)
    _, version_loc_id = get_or_create_version_localization(token, app_id)
    if version_loc_id:
        set_version_metadata(token, version_loc_id, description, meta["keywords"],
                             "Initial release.")

        # 3. Screenshots — always clear and re-upload to fix any wrong-dimension sets
        sc_dir = ROOT / "screenshots" / slug
        if sc_dir.exists():
            sc_files = sorted(sc_dir.glob("*.png"))[:3]
            if sc_files:
                delete_all_screenshot_sets(token, version_loc_id)
                for sc_path in sc_files:
                    ok = upload_screenshot(token, version_loc_id, str(sc_path))
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
        target_slugs = [k for k, v in apps.items() if v.get("ascAppId")]
        print(f"Uploading metadata for {len(target_slugs)} registered apps")
    else:
        target_slugs = [s for s in slugs_arg if s != "--all"]

    for slug in target_slugs:
        if slug not in apps:
            print(f"Unknown slug: {slug}")
            continue
        process_app(token, slug, apps[slug])

if __name__ == "__main__":
    main()
