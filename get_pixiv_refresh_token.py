"""One-shot helper for obtaining a Pixiv OAuth refresh_token.

Run this script on a trusted local machine:

    python get_pixiv_refresh_token.py

Open the printed URL, sign in to Pixiv, copy the final redirected URL, and
paste it back into the prompt. The script prints the refresh_token only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse
import urllib.request

CLIENT_ID = "MOBrBDS8blbauoSck0ZfDbtuzpyT"
CLIENT_SECRET = "lsACyCD94FhDUtGTXi3QzcFE2uU1hqtDaKeqrdwj"
TOKEN_URL = "https://oauth.secure.pixiv.net/auth/token"
LOGIN_URL = "https://app-api.pixiv.net/web/v1/login"
REDIRECT_URI = "https://app-api.pixiv.net/web/v1/users/auth/pixiv/callback"


def code_verifier() -> str:
    return secrets.token_urlsafe(32)


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def parse_code(callback_url: str) -> str:
    parsed = urllib.parse.urlparse(callback_url.strip())
    query = urllib.parse.parse_qs(parsed.query)
    code = query.get("code", [""])[0]
    if not code:
        raise SystemExit("No code= parameter found in the pasted URL.")
    return code


def exchange_code(code: str, verifier: str) -> dict:
    payload = urllib.parse.urlencode(
        {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "include_policy": "true",
            "redirect_uri": REDIRECT_URI,
        },
    ).encode("utf-8")
    request = urllib.request.Request(
        TOKEN_URL,
        data=payload,
        headers={
            "User-Agent": "PixivAndroidApp/5.0.234 (Android 11; Pixel 5)",
            "App-OS": "android",
            "App-OS-Version": "11",
            "App-Version": "5.0.234",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    verifier = code_verifier()
    params = urllib.parse.urlencode(
        {
            "code_challenge": code_challenge(verifier),
            "code_challenge_method": "S256",
            "client": "pixiv-android",
        },
    )
    print("Open this URL in your browser and complete Pixiv login:")
    print(f"{LOGIN_URL}?{params}")
    callback_url = input("\nPaste the final redirected URL here:\n> ")
    token = exchange_code(parse_code(callback_url), verifier)
    refresh_token = (token.get("response") or token).get("refresh_token")
    if not refresh_token:
        raise SystemExit(f"Token response did not contain refresh_token: {token}")
    print("\nPIXIV_REFRESH_TOKEN:")
    print(refresh_token)


if __name__ == "__main__":
    main()
