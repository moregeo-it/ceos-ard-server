import logging
from typing import Any

import httpx
from fastapi import HTTPException, status

from app.config import settings

logger = logging.getLogger(__name__)


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

    async def _make_github_request(
        self, method: str, url: str, token: str, auth_type: str = "Bearer", params: dict = None, json_data: dict = None, timeout: float = 30.0
    ) -> dict[str, Any]:
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
            JSON response data

        Raises:
            HTTPException: For various error conditions with appropriate status codes
        """
        headers = self._get_auth_headers(token, auth_type)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if method.upper() == "GET":
                    response = await client.get(url, headers=headers, params=params)
                elif method.upper() == "POST":
                    response = await client.post(url, headers=headers, params=params, json=json_data)
                elif method.upper() == "PATCH":
                    response = await client.patch(url, headers=headers, params=params, json=json_data)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                # Handle common GitHub API status codes
                if response.status_code == 200 or response.status_code == 201 or response.status_code == 202:
                    return response.json()
                elif response.status_code == 404:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="GitHub resource not found")
                elif response.status_code == 403:
                    # Check if it's a rate limit issue
                    if "rate limit" in response.text.lower():
                        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="GitHub API rate limit exceeded")
                    else:
                        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to GitHub repository")
                elif response.status_code == 422:
                    error_data = response.json()
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"GitHub API validation error: {error_data.get('message', 'Unknown error')}",
                    )
                else:
                    logger.error(f"GitHub API error: {response.status_code} - {response.text}")
                    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"GitHub API returned status {response.status_code}")

        except httpx.TimeoutException as e:
            logger.error(f"Timeout requesting GitHub API: {url}")
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="GitHub API request timed out") from e
        except httpx.RequestError as e:
            logger.error(f"Network error requesting GitHub API: {e}")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to connect to GitHub API") from e

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

    async def check_user_fork(self, access_token: str, username: str, upstream_owner: str, upstream_repo: str) -> dict[str, Any] | None:
        url = f"{self.base_url}/repos/{username}/{upstream_repo}"

        try:
            fork_repo = await self._make_github_request("GET", url, access_token)

            if fork_repo["fork"] and fork_repo["owner"]["login"] == upstream_owner:
                logger.info(f"User {username} has forked {upstream_owner}/{upstream_repo}")
                return fork_repo

            return None

        except HTTPException as e:
            if e.status_code == 404:
                logger.info(f"User {username} has not forked {upstream_owner}/{upstream_repo}")
                return None
            raise

    async def create_fork(self, access_token: str, upstream_owner: str, upstream_repo: str) -> dict[str, Any]:
        url = f"{self.base_url}/repos/{upstream_owner}/{upstream_repo}/forks"

        try:
            fork_repo = await self._make_github_request("POST", url, access_token, timeout=60.0)
            logger.info(f"Successfully forked {upstream_owner}/{upstream_repo} to {fork_repo['owner']['login']}/{fork_repo['name']}")
            return fork_repo
        except HTTPException as e:
            # Add more specific context for this endpoint
            if e.status_code == 404:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Repository {upstream_owner}/{upstream_repo} not found") from e
            raise

    async def get_or_create_fork(self, username: str, access_token: str, upstream_owner: str, upstream_repo: str) -> tuple[dict[str, Any], bool]:
        fork_repo = await self.check_user_fork(access_token, username, upstream_owner, upstream_repo)

        if fork_repo:
            return fork_repo, False

        new_fork = await self.create_fork(access_token, upstream_owner, upstream_repo)

        return new_fork, True

    async def create_pull_request(self, access_token: str, owner: str, repo: str, pr_data: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls"

        try:
            return await self._make_github_request("POST", url, access_token, json_data=pr_data, timeout=60.0)
        except HTTPException as e:
            # Add more specific context for this endpoint
            if e.status_code == 404:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Repository {owner}/{repo} not found") from e
            raise

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

    async def get_pull_request_commits(self, owner: str, repo: str, number: int, access_token: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls/{number}/commits"
        try:
            return await self._make_github_request("GET", url, access_token, timeout=60.0)
        except HTTPException as e:
            if e.status_code == 404:
                logger.info(f"Pull request {number} not found for {owner}/{repo}")
                return []
            raise
