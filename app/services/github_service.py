import logging
from typing import Any

import httpx
from fastapi import HTTPException, status

from app.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)

SUPPORTED_METHODS = ("GET", "POST", "PATCH", "PUT", "DELETE")


def head_repo_missing(pull_request: dict[str, Any]) -> bool:
    """
    Whether the fork this pull request was opened from has been deleted.

    GitHub closes such a pull request and nulls `head.repo`, keeping the pull request itself
    and every other `head` field, so this is the only signal. It can never be reopened, even
    after re-forking: the head is bound by repository id, and `PATCH /pulls/{n}` has no `head`.
    """
    if not pull_request:
        return False
    return (pull_request.get("head") or {}).get("repo") is None


def pull_request_is_merged(pull_request: dict[str, Any]) -> bool:
    """
    Whether the pull request was merged.

    `state` is only ever "open" or "closed", so comparing it against "merged" never matches;
    `merged_at` is the only signal.
    """
    if not pull_request:
        return False
    return pull_request.get("merged_at") is not None


class GitHubAPIError(HTTPException):
    """
    HTTPException that keeps GitHub's structured `errors` payload.

    The actionable reason lives in `errors[].message` — the top-level `message` is often just
    "Validation Failed" — and callers branch on `errors[].field`/`code` rather than on prose.
    """

    def __init__(self, status_code: int, detail: str, errors: list[dict[str, Any]] | None = None):
        self.github_errors = errors or []
        super().__init__(status_code=status_code, detail=detail)


class GitHubService:
    def __init__(self):
        self.base_url = settings.GITHUB_API_BASE_URL
        self.default_headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "CEOS-ARD-Editor"}

    def _get_auth_headers(self, token: str, auth_type: str = "Bearer") -> dict[str, str]:
        """Create headers with authorization token.

        Args:
            token: The access token
            auth_type: Either 'Bearer' or 'token' depending on the GitHub API endpoint
        """
        headers = self.default_headers.copy()
        headers["Authorization"] = f"{auth_type} {token}"
        return headers

    @staticmethod
    def _parse_json(response: httpx.Response) -> dict[str, Any] | list[dict[str, Any]] | None:
        """Response body as JSON, or None when the body is empty or not JSON."""
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    @staticmethod
    def _is_rate_limited(response: httpx.Response) -> bool:
        """
        Whether a 403/429 is a rate limit rather than a permission problem.

        Headers first: primary limits zero out `x-ratelimit-remaining`, secondary ones send
        `retry-after`. The body is a fallback because its wording differs between the two.
        """
        if response.headers.get("x-ratelimit-remaining") == "0":
            return True
        if response.headers.get("retry-after"):
            return True
        body = response.text.lower()
        return "rate limit" in body or "abuse detection" in body

    @staticmethod
    def _describe_validation_error(error_data: dict[str, Any] | None) -> tuple[str, list[dict[str, Any]]]:
        """
        Readable detail plus the raw `errors` list from a 422 body.

        Folds in `errors[].message`, which carries the actual reason (e.g. "state cannot be
        changed. The repository that submitted this pull request has been deleted.").
        """
        error_data = error_data or {}
        errors = [error for error in (error_data.get("errors") or []) if isinstance(error, dict)]

        reasons = []
        for error in errors:
            reason = error.get("message") or f"{error.get('field', 'field')} is {error.get('code', 'invalid')}"
            reasons.append(reason)

        summary = error_data.get("message") or "Unknown error"
        detail = f"{summary}: {'; '.join(reasons)}" if reasons else summary

        return detail, errors

    async def _make_github_request(
        self, method: str, url: str, token: str, auth_type: str = "Bearer", params: dict = None, json_data: dict = None, timeout: float = 30.0
    ) -> dict[str, Any] | list[dict[str, Any]] | None:
        """Make a GitHub API request with comprehensive error handling.

        Args:
            method: HTTP method ('GET', 'POST', etc.)
            url: Full URL for the request
            token: GitHub access token
            auth_type: Either 'Bearer' or 'token'
            params: Query parameters
            json_data: JSON body for POST requests
            timeout: Request timeout in seconds

        Returns:
            JSON response data (dict for single resources, list for collections, None for 204)

        Raises:
            GitHubAPIError: For various error conditions with appropriate status codes
        """
        method = method.upper()
        if method not in SUPPORTED_METHODS:
            raise ValueError(f"Unsupported HTTP method: {method}")

        headers = self._get_auth_headers(token, auth_type)

        try:
            # A renamed or transferred repository answers 301. GET follows it transparently;
            # writes must not, because httpx replays a redirected POST as a GET.
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=(method == "GET")) as client:
                response = await client.request(method, url, headers=headers, params=params, json=json_data)
        except httpx.TimeoutException as e:
            logger.error(f"Timeout requesting GitHub API: {url}")
            raise GitHubAPIError(status.HTTP_504_GATEWAY_TIMEOUT, "GitHub API request timed out") from e
        except httpx.RequestError as e:
            logger.error(f"Network error requesting GitHub API: {e}")
            raise GitHubAPIError(status.HTTP_502_BAD_GATEWAY, "Failed to connect to GitHub API") from e

        if response.status_code in (200, 201, 202):
            return self._parse_json(response)

        if response.status_code == 204:
            return None

        logger.error(f"GitHub API error: {method} {url} -> {response.status_code} - {response.text}")

        if response.status_code == 401:
            # A revoked or expired token. Must stay a 401 so the client re-authenticates
            # instead of reading it as a GitHub outage.
            raise GitHubAPIError(status.HTTP_401_UNAUTHORIZED, "GitHub access token is invalid or has been revoked. Please log in again.")

        if response.status_code == 404:
            raise GitHubAPIError(status.HTTP_404_NOT_FOUND, "GitHub resource not found")

        if response.status_code in (403, 429):
            if self._is_rate_limited(response):
                raise GitHubAPIError(status.HTTP_429_TOO_MANY_REQUESTS, "GitHub API rate limit exceeded. Please try again shortly.")
            raise GitHubAPIError(status.HTTP_403_FORBIDDEN, "Access denied to GitHub repository")

        if response.status_code == 422:
            detail, errors = self._describe_validation_error(self._parse_json(response))
            raise GitHubAPIError(status.HTTP_422_UNPROCESSABLE_ENTITY, f"GitHub API validation error: {detail}", errors=errors)

        if response.status_code in (301, 302, 307, 308):
            # A redirected write: the repository was renamed or transferred.
            raise GitHubAPIError(status.HTTP_502_BAD_GATEWAY, "GitHub repository has been moved or renamed. Please update the repository reference.")

        raise GitHubAPIError(status.HTTP_502_BAD_GATEWAY, f"GitHub API returned status {response.status_code}")

    async def get_repository_contents(self, owner: str, repo: str, token: str, path: str = "", branch: str = "main") -> list[dict[str, Any]]:
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing GitHub access token")

        url = f"{self.base_url}/repos/{owner}/{repo}/contents"
        if path:
            url += f"/{path}"

        params = {"ref": branch}

        try:
            return await self._make_github_request("GET", url, token, "token", params=params)
        except HTTPException as e:
            # Add more specific context for this endpoint
            if e.status_code == 404:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Repository {owner}/{repo} or path '{path}' not found") from e
            raise

    async def get_pfs_types(self, owner: str, repo: str, token: str, branch: str) -> list[str]:
        try:
            if not token:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing GitHub access token")

            contents = await self.get_repository_contents(owner, repo, token, "pfs", branch)

            pfs_folders = [item["name"] for item in contents if item["type"] == "dir" and not item["name"].startswith(".")]

            logger.info(f"Found {len(pfs_folders)} PFS folders in {owner}/{repo}")

            return sorted(pfs_folders)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to retrieve PFS information: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve PFS information") from e

    async def fork(self, user: User, upstream_owner: str, upstream_repo: str) -> dict[str, Any]:
        url = f"{self.base_url}/repos/{upstream_owner}/{upstream_repo}/forks"

        try:
            fork_repo = await self._make_github_request("POST", url, user.access_token, timeout=60.0)
            logger.info(f"Fork of {upstream_owner}/{upstream_repo} is at {fork_repo['owner']['login']}/{fork_repo['name']}")
            return fork_repo
        except HTTPException as e:
            # Add more specific context for this endpoint
            if e.status_code == 404:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Repository {upstream_owner}/{upstream_repo} not found") from e
            raise

    async def create_pull_request(self, access_token: str, owner: str, repo: str, pr_data: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls"

        try:
            return await self._make_github_request("POST", url, access_token, json_data=pr_data, timeout=60.0)
        except HTTPException as e:
            # Add more specific context for this endpoint
            if e.status_code == 404:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Repository {owner}/{repo} not found") from e
            raise

    async def get_repository(self, owner: str, repo: str, token: str) -> dict[str, Any] | None:
        """
        Repository metadata, or None when it does not exist.

        Tells a deleted fork apart from other push failures. GitHub answers 404 for both "gone"
        and "invisible to this token", so validate the token before reading None as deletion.
        """
        url = f"{self.base_url}/repos/{owner}/{repo}"
        try:
            return await self._make_github_request("GET", url, token)
        except HTTPException as e:
            if e.status_code == 404:
                logger.info(f"Repository {owner}/{repo} not found")
                return None
            raise

    async def get_authenticated_user(self, token: str) -> dict[str, Any]:
        """The token's own user. Cheapest way to tell a revoked token from a missing resource."""
        return await self._make_github_request("GET", f"{self.base_url}/user", token)

    async def get_pull_request(self, owner: str, repo: str, number: int, access_token: str) -> dict[str, Any]:
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls/{number}"
        try:
            return await self._make_github_request("GET", url, access_token, timeout=60.0)
        except HTTPException as e:
            if e.status_code == 404:
                logger.info(f"Pull request {number} not found for {owner}/{repo}")
                return None
            raise

    async def update_pull_request(self, owner: str, repo: str, number: int, access_token: str, pr_data: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls/{number}"
        try:
            return await self._make_github_request("PATCH", url, access_token, json_data=pr_data, timeout=60.0)
        except HTTPException as e:
            if e.status_code == 404:
                logger.info(f"Pull request {number} not found for {owner}/{repo}")
                return None
            raise

    async def get_repository_pull_requests(self, owner: str, repo: str, token: str, state: str = "all", per_page: int = 100) -> list[dict[str, Any]]:
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls"
        params = {"state": state, "per_page": per_page}

        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing GitHub token")

        all_prs = []
        page = 1

        while True:
            params["page"] = page

            try:
                prs = await self._make_github_request("GET", url, token, "token", params=params)

                if not prs:
                    break

                # Type guard: ensure we got a list, not a dict
                if not isinstance(prs, list):
                    logger.error(f"Unexpected response type from GitHub API: {type(prs)}, expected list")
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY, detail=f"GitHub API returned unexpected response type: {type(prs).__name__}"
                    )

                all_prs.extend(prs)

                # If we got fewer results than per_page, we've reached the last page
                if len(prs) < per_page:
                    break

                page += 1

            except HTTPException as e:
                if e.status_code == 404:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Repository {owner}/{repo} not found") from e
                raise

        logger.info(f"Fetched {len(all_prs)} pull requests from {owner}/{repo} (state={state})")
        return all_prs
