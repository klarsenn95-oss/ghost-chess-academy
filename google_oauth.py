"""Google OAuth (Authorization Code flow), implemented directly against
Google's endpoints with `requests` rather than pulling in Authlib — the
whole flow is three HTTP calls (authorize redirect, token exchange,
userinfo), not worth a new heavy dependency for.

Email comes back verified by Google itself, which is the whole point:
the coach's own registration-code gate stays as the paid-access control,
but the account's email is now something real password-reset mail can
reach, instead of whatever string a Ghost happened to type at signup.
"""
from __future__ import annotations

import os
import urllib.parse

import requests

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def configured() -> bool:
    return bool(os.environ.get("GOOGLE_OAUTH_CLIENT_ID") and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"))


def redirect_uri(public_base_url: str) -> str:
    return public_base_url.rstrip("/") + "/auth/google/callback"


def build_authorize_url(public_base_url: str, state: str) -> str:
    params = {
        "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        "redirect_uri": redirect_uri(public_base_url),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def exchange_code_for_userinfo(public_base_url: str, code: str) -> dict | None:
    """Returns {"email": ..., "name": ..., "email_verified": bool} or None
    on any failure — callers treat a None the same as "Google login failed,
    try again", no need to distinguish network vs. token vs. userinfo errors
    at the call site."""
    try:
        token_res = requests.post(TOKEN_URL, data={
            "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri(public_base_url),
        }, timeout=10)
        token_res.raise_for_status()
        access_token = token_res.json().get("access_token")
        if not access_token:
            return None
        info_res = requests.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
        info_res.raise_for_status()
        info = info_res.json()
        email = (info.get("email") or "").strip().lower()
        if not email:
            return None
        return {
            "email": email,
            "name": info.get("name") or info.get("given_name") or "",
            "email_verified": bool(info.get("email_verified")),
        }
    except Exception as exc:
        print(f"[GHOST/GoogleOAuth] Échec de l'échange OAuth : {exc}")
        return None
