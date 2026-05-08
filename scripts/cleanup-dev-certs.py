#!/usr/bin/env python3
"""
Deletes stale Apple Development certificates from App Store Connect.
Fixes "private key not installed in keychain" on reused GitHub Actions runners.
Requires: ASC_KEY_ID, ASC_ISSUER_ID, ASC_PRIVATE_KEY environment variables.
Exits 0 even on failure so the build step isn't blocked.
"""
import os, json, time, base64, sys
import urllib.request, urllib.error

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
except ImportError:
    print("cryptography not installed, skipping cert cleanup")
    sys.exit(0)

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


def main():
    key_id     = os.environ.get('ASC_KEY_ID', '')
    issuer_id  = os.environ.get('ASC_ISSUER_ID', '')
    private_key = os.environ.get('ASC_PRIVATE_KEY', '')

    if not all([key_id, issuer_id, private_key]):
        print("Missing ASC env vars, skipping cert cleanup")
        return

    try:
        token = make_jwt(key_id, issuer_id, private_key)
    except Exception as e:
        print(f"JWT generation failed: {e}")
        return

    headers = {"Authorization": f"Bearer {token}"}

    # List Development certs
    try:
        req = urllib.request.Request(
            "https://api.appstoreconnect.apple.com/v1/certificates"
            "?filter[certificateType]=IOS_DEVELOPMENT&limit=200",
            headers=headers
        )
        data = json.loads(urllib.request.urlopen(req, timeout=30).read())
        certs = data.get('data', [])
        print(f"Found {len(certs)} IOS_DEVELOPMENT certificates")
    except Exception as e:
        print(f"Failed to list certs: {e}")
        return

    deleted = 0
    for cert in certs:
        cert_id = cert['id']
        name = cert.get('attributes', {}).get('displayName', cert_id)
        try:
            del_req = urllib.request.Request(
                f"https://api.appstoreconnect.apple.com/v1/certificates/{cert_id}",
                method="DELETE",
                headers=headers
            )
            urllib.request.urlopen(del_req, timeout=30)
            print(f"  ✓ Deleted: {name} ({cert_id})")
            deleted += 1
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300]
            print(f"  ✗ Could not delete {cert_id}: HTTP {e.code} - {body}")
        except Exception as e:
            print(f"  ✗ Error deleting {cert_id}: {e}")

    print(f"Done. Deleted {deleted}/{len(certs)} certificates.")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"Unexpected error in cert cleanup (non-fatal): {e}")
