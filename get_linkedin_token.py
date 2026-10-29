"""Renew your LinkedIn token (takes ~2 minutes, needed about every 60 days).

Run on your own computer:
    pip install requests pynacl
    export LI_CLIENT_ID=...  LI_CLIENT_SECRET=...
    export GH_PAT=...  GITHUB_REPOSITORY=yourname/yourrepo     (optional: auto-saves to GitHub)
    python get_linkedin_token.py

(On Windows PowerShell use:  $env:LI_CLIENT_ID="..."  instead of export.)
"""
import os
from urllib.parse import parse_qs, urlencode, urlparse

import requests

CLIENT_ID = os.environ["LI_CLIENT_ID"]
CLIENT_SECRET = os.environ["LI_CLIENT_SECRET"]
REDIRECT = os.environ.get("LI_REDIRECT_URI", "https://localhost/callback")

auth_url = "https://www.linkedin.com/oauth/v2/authorization?" + urlencode({
    "response_type": "code",
    "client_id": CLIENT_ID,
    "redirect_uri": REDIRECT,
    "scope": "openid profile w_member_social",
    "state": "renew",
})
print("\n1) Open this link in your browser and click Allow:\n")
print(auth_url)
print("\n2) Your browser will land on a page that fails to load. That's fine.")
print("   Copy the FULL address from the address bar and paste it here.\n")
pasted = input("Pasted URL: ").strip()
code = parse_qs(urlparse(pasted).query)["code"][0]

r = requests.post("https://www.linkedin.com/oauth/v2/accessToken", data={
    "grant_type": "authorization_code",
    "code": code,
    "redirect_uri": REDIRECT,
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
}, timeout=30)
r.raise_for_status()
token = r.json()["access_token"]
print(f"\nToken received (valid ~{r.json().get('expires_in', 0) // 86400} days).")

info = requests.get("https://api.linkedin.com/v2/userinfo",
                    headers={"Authorization": f"Bearer {token}"}, timeout=30)
info.raise_for_status()
person_id = info.json()["sub"]

if os.environ.get("GH_PAT") and os.environ.get("GITHUB_REPOSITORY"):
    from gh_secrets import update_github_secret
    update_github_secret("LI_TOKEN", token)
    update_github_secret("LI_PERSON_ID", person_id)
    print("Saved to GitHub Secrets. Done!")
else:
    print("\nAdd these manually in GitHub -> Settings -> Secrets -> Actions:")
    print("LI_TOKEN     =", token)
    print("LI_PERSON_ID =", person_id)
