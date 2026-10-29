"""Daily auto-poster: Instagram + LinkedIn (image + text).

Reads today's row from posts.csv and publishes it.
Also keeps the Instagram token alive (refreshes it every run) and warns you
when the LinkedIn token is about to expire.
"""
import csv
import datetime
import os
import sys
import time
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("Asia/Kolkata")  # change if you want a different "today"
TODAY = datetime.datetime.now(TZ).date().isoformat()

REPO = os.environ.get("GITHUB_REPOSITORY", "")
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
PLATFORMS = [p.strip() for p in os.environ.get("PLATFORMS", "instagram,linkedin").split(",")]

IG_USER_ID = os.environ.get("IG_USER_ID", "")
IG_TOKEN = os.environ.get("IG_TOKEN", "")
LI_TOKEN = os.environ.get("LI_TOKEN", "")
LI_PERSON_ID = os.environ.get("LI_PERSON_ID", "")

LI_WARN_DAYS = 7


def check(r: requests.Response) -> requests.Response:
    if not r.ok:
        raise RuntimeError(f"{r.request.method} {r.url.split('?')[0]} -> {r.status_code}: {r.text}")
    return r


# ---------------------------------------------------------------- content ---
def get_today_post():
    with open("posts.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["date"].strip() == TODAY:
                return row
    return None


def image_url(image: str) -> str:
    """Public URL Instagram can download from."""
    if image.startswith("http"):
        return image
    return f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/images/{image}"


def image_bytes(image: str) -> bytes:
    local = os.path.join("images", image)
    if not image.startswith("http") and os.path.exists(local):
        with open(local, "rb") as f:
            return f.read()
    return check(requests.get(image_url(image), timeout=60)).content


# -------------------------------------------------------------- instagram ---
def refresh_instagram_token() -> str:
    """Refresh the long-lived token (+60 days) and save it back to GitHub Secrets."""
    r = check(requests.get(
        "https://graph.instagram.com/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": IG_TOKEN},
        timeout=30,
    ))
    data = r.json()
    new_token = data["access_token"]
    print(f"Instagram token refreshed, valid for {data.get('expires_in', 0) // 86400} more days.")

    if os.environ.get("GH_PAT"):
        from gh_secrets import update_github_secret
        update_github_secret("IG_TOKEN", new_token)
    else:
        print("WARNING: GH_PAT not set, new token was not saved. It will expire in ~60 days.")
    return new_token


def post_instagram(token: str, caption: str, image: str) -> None:
    base = f"https://graph.instagram.com/v21.0/{IG_USER_ID}"
    r = check(requests.post(f"{base}/media", data={
        "image_url": image_url(image),
        "caption": caption,
        "access_token": token,
    }, timeout=60))
    container_id = r.json()["id"]

    # wait until Instagram has processed the image
    for _ in range(12):
        s = check(requests.get(
            f"https://graph.instagram.com/v21.0/{container_id}",
            params={"fields": "status_code", "access_token": token},
            timeout=30,
        )).json().get("status_code")
        if s == "FINISHED":
            break
        if s in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram media processing failed: {s}")
        time.sleep(5)

    r = check(requests.post(f"{base}/media_publish", data={
        "creation_id": container_id,
        "access_token": token,
    }, timeout=60))
    print("Instagram posted:", r.json())


# --------------------------------------------------------------- linkedin ---
def linkedin_days_left():
    """Days until the LinkedIn token expires (needs LI_CLIENT_ID/SECRET), else None."""
    cid, secret = os.environ.get("LI_CLIENT_ID"), os.environ.get("LI_CLIENT_SECRET")
    if not (cid and secret):
        return None
    r = requests.post(
        "https://www.linkedin.com/oauth/v2/introspectToken",
        data={"client_id": cid, "client_secret": secret, "token": LI_TOKEN},
        timeout=30,
    )
    if not r.ok:
        return None
    d = r.json()
    if not d.get("active"):
        return 0
    return (d["expires_at"] - time.time()) / 86400


def post_linkedin(text: str, image: str) -> None:
    headers = {"Authorization": f"Bearer {LI_TOKEN}", "X-Restli-Protocol-Version": "2.0.0"}
    owner = f"urn:li:person:{LI_PERSON_ID}"

    # 1) register the upload
    r = check(requests.post(
        "https://api.linkedin.com/v2/assets?action=registerUpload",
        headers=headers,
        json={"registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "owner": owner,
            "serviceRelationships": [{
                "relationshipType": "OWNER",
                "identifier": "urn:li:userGeneratedContent",
            }],
        }},
        timeout=60,
    ))
    value = r.json()["value"]
    upload_url = value["uploadMechanism"][
        "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"]["uploadUrl"]
    asset = value["asset"]

    # 2) upload the image bytes
    check(requests.put(
        upload_url,
        data=image_bytes(image),
        headers={"Authorization": f"Bearer {LI_TOKEN}"},
        timeout=120,
    ))

    # 3) create the post with the image
    body = {
        "author": owner,
        "lifecycleState": "PUBLISHED",
        "specificContent": {"com.linkedin.ugc.ShareContent": {
            "shareCommentary": {"text": text},
            "shareMediaCategory": "IMAGE",
            "media": [{"status": "READY", "media": asset}],
        }},
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    r = check(requests.post("https://api.linkedin.com/v2/ugcPosts",
                            headers=headers, json=body, timeout=60))
    print("LinkedIn posted:", r.status_code)


# ------------------------------------------------------------------- main ---
def main() -> int:
    problems = []
    token = IG_TOKEN

    # keep Instagram token alive (runs every day, even if nothing is scheduled)
    if "instagram" in PLATFORMS:
        try:
            token = refresh_instagram_token()
        except Exception as e:
            print("WARNING: Instagram token refresh failed:", e)

    # warn early about the LinkedIn token
    if "linkedin" in PLATFORMS:
        days = linkedin_days_left()
        if days is not None and days < LI_WARN_DAYS:
            msg = f"LinkedIn token expires in {max(days, 0):.0f} days. Run get_linkedin_token.py."
            print("ALERT:", msg)
            problems.append(msg)

    post = get_today_post()
    if not post:
        print(f"Nothing scheduled for {TODAY}.")
    else:
        if "instagram" in PLATFORMS:
            try:
                post_instagram(token, post["instagram_caption"], post["image"])
            except Exception as e:
                print("Instagram FAILED:", e)
                problems.append("Instagram post failed")
        if "linkedin" in PLATFORMS:
            try:
                post_linkedin(post["linkedin_text"], post["image"])
            except Exception as e:
                print("LinkedIn FAILED:", e)
                problems.append("LinkedIn post failed")

    # a non-zero exit makes GitHub email you about the failed run (free alert)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
