"""Update a GitHub Actions secret from Python (used to save refreshed tokens).

Needs env vars:
  GH_PAT             fine-grained personal access token with "Secrets: Read and write"
  GITHUB_REPOSITORY  e.g. "yourname/social-poster" (set automatically in Actions)
"""
import base64
import os

import requests
from nacl import encoding, public


def update_github_secret(name: str, value: str) -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    headers = {
        "Authorization": f"Bearer {os.environ['GH_PAT']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    base = f"https://api.github.com/repos/{repo}/actions/secrets"

    r = requests.get(f"{base}/public-key", headers=headers, timeout=30)
    r.raise_for_status()
    key = r.json()

    pk = public.PublicKey(key["key"].encode(), encoding.Base64Encoder())
    encrypted = public.SealedBox(pk).encrypt(value.encode())

    r = requests.put(
        f"{base}/{name}",
        headers=headers,
        json={
            "encrypted_value": base64.b64encode(encrypted).decode(),
            "key_id": key["key_id"],
        },
        timeout=30,
    )
    r.raise_for_status()
    print(f"GitHub secret {name} updated.")
