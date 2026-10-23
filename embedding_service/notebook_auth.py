"""Exchange notebook credentials for App-scoped OAuth; never persist tokens."""

import threading
import time
from urllib.parse import urlparse

import requests


class NotebookAppAuth:
    def __init__(
        self, client, app_name, notebook_token, session=None, clock=time.monotonic
    ):
        self.client = client
        self.app_name = app_name
        self.notebook_token = notebook_token
        self.session = session or requests.Session()
        self.clock = clock
        self._token = None
        self._expires = 0
        self._audience = None
        self._lock = threading.Lock()
        self.host = client.config.host.rstrip("/")
        parsed = urlparse(self.host)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Expected an HTTPS Databricks workspace origin")

    def authenticate(self):
        with self._lock:
            if not self._token or self.clock() >= self._expires:
                if self._audience is None:
                    self._audience = self.client.apps.get(
                        self.app_name
                    ).oauth2_app_client_id
                if not self._audience:
                    raise RuntimeError("The App has no OAuth client ID")
                try:
                    response = self.session.post(
                        self.host + "/oidc/v1/token",
                        data={
                            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                            "subject_token": self.notebook_token(),
                            "subject_token_type": "urn:databricks:params:oauth:token-type:personal-access-token",
                            "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
                            "scope": "all-apis",
                            "audience": self._audience,
                        },
                        timeout=30,
                        allow_redirects=False,
                    )
                    if response.status_code != 200:
                        raise RuntimeError(
                            f"Notebook OAuth exchange returned HTTP {response.status_code}"
                        )
                    payload = response.json()
                    token = payload.get("access_token")
                    lifetime = float(payload.get("expires_in", 60))
                    if (
                        not isinstance(token, str)
                        or not token
                        or not 0 < lifetime <= 86400
                    ):
                        raise ValueError("Invalid OAuth response")
                except RuntimeError:
                    raise
                except Exception:
                    # Exceptions and OAuth responses can contain credential material.
                    raise RuntimeError(
                        "Notebook OAuth exchange failed; check notebook access and App permissions"
                    ) from None
                self._token = token
                self._expires = self.clock() + max(0, lifetime - 60)
            return {"Authorization": "Bearer " + self._token}
