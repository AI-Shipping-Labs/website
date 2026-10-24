"""Read the exact deployment job receipt without an external GitHub CLI."""

from urllib.parse import urljoin, urlsplit

import requests

from .errors import ReleaseError


class GitHubLogSession(requests.Session):
    def __init__(self):
        super().__init__()
        self.trust_env = False
        self.max_redirects = 3

    def get_redirect_target(self, response):
        target = super().get_redirect_target(response)
        if target is None:
            return None
        parsed = urlsplit(urljoin(response.url, target))
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ReleaseError("release-evidence-dev-job-logs-failed category=unsafe-redirect")
        return target


def read_job_logs(github, job_id):
    if github.base_url != "https://api.github.com" or not isinstance(job_id, int) or isinstance(job_id, bool) or job_id <= 0:
        raise ReleaseError("release-evidence-dev-job-logs-failed category=invalid-identity")
    url = f"{github.base_url}/repos/AI-Shipping-Labs/website/actions/jobs/{job_id}/logs"
    headers = {"Authorization": f"{github.auth_scheme} {github.token}"}
    try:
        with GitHubLogSession() as session:
            response = session.get(url, headers=headers, timeout=github.timeout)
    except requests.Timeout:
        raise ReleaseError("release-evidence-dev-job-logs-failed category=timeout") from None
    except requests.RequestException:
        raise ReleaseError("release-evidence-dev-job-logs-failed category=transport") from None
    if response.status_code != 200:
        raise ReleaseError(f"release-evidence-dev-job-logs-failed category=http status={response.status_code}")
    return response.text
