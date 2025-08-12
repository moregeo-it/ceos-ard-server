import base64
import logging
from typing import Any

import httpx
from fastapi import HTTPException, status
from ruamel.yaml import YAML

from app.config import settings

logger = logging.getLogger(__name__)


class GitHubService:
    def __init__(self):
        self.base_url = settings.GITHUB_API_BASE_URL
        self.default_headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "CEOS-ARD-Editor"}

    async def get_repository_contents(self, owner: str, repo: str, token: str, path: str = "", branch: str = "main") -> list[dict[str, Any]]:
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing GitHub access token")

        headers = self.default_headers.copy()
        headers["Authorization"] = f"token {token}"

        url = f"{self.base_url}/repos/{owner}/{repo}/contents"

        if path:
            url += f"/{path}"

        params = {"ref": branch}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=headers, params=params)

                if response.status_code == 404:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Repository {owner}/{repo} or path '{path}' not found")
                elif response.status_code == 403:
                    # Check if it's a rate limit issue
                    if "rate limit" in response.text.lower():
                        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="GitHub API rate limit exceeded")
                    else:
                        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to GitHub repository")
                elif response.status_code != 200:
                    logger.error(f"GitHub API error: {response.status_code} - {response.text}")
                    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"GitHub API returned status {response.status_code}")

                return response.json()

        except httpx.TimeoutException as e:
            logger.error(f"Timeout requesting GitHub API for {owner}/{repo}/{path}")
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="GitHub API request timed out") from e
        except httpx.RequestError as e:
            logger.error(f"Network error requesting GitHub API: {e}")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to connect to GitHub API") from e

    async def get_pfs_types(self, owner: str, repo: str, token: str, branch: str) -> list[str]:
        try:
            if not token:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing GitHub access token")
            contents = await self.get_repository_contents(owner, repo, token, "pfs", branch)

            pfs_folders = [item["name"] for item in contents if item["type"] == "dir" and not item["name"].startswith(".")]

            pfs_data = []

            for pfs_folder in pfs_folders:
                try:
                    document_path = f"pfs/{pfs_folder}/document.yaml"
                    document_response = await self.get_repository_contents(owner, repo, token, document_path, branch)
                    if isinstance(document_response, dict) and "content" in document_response:
                        # GitHub API returns base64 encoded content
                        encoded_content = document_response["content"]
                        # Remove any whitespace/newlines from base64 string
                        encoded_content = encoded_content.replace("\n", "").replace(" ", "")
                        decoded_content = base64.b64decode(encoded_content).decode("utf-8")
                    else:
                        # If it's already a string, use it directly
                        decoded_content = document_response
                    yaml_parser = YAML(typ="safe")
                    yaml_content = yaml_parser.load(decoded_content)
                    pfs_info = {
                        "id": yaml_content["id"],
                        "title": yaml_content["title"],
                    }
                    pfs_data.append(pfs_info)

                except Exception as e:
                    logger.error(f"Failed to retrieve PFS information for {pfs_folder}: {e}")
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to retrieve PFS information for {pfs_folder}"
                    ) from e

            return pfs_data
        except Exception as e:
            logger.error(f"Failed to retrieve PFS information: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve PFS information") from e

    async def check_user_fork(self, access_token: str, username: str, upstream_owner: str, upstream_repo: str) -> dict[str, Any] | None:
        try:
            url = f"{self.base_url}/repos/{username}/{upstream_repo}"
            headers = self.default_headers.copy()
            headers["Authorization"] = f"Bearer {access_token}"

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=headers)

            if response.status_code == 200:
                forked_repo = response.json()
                if forked_repo["fork"] and forked_repo["owner"]["login"] == upstream_owner:
                    logger.info(f"User {username} has forked {upstream_owner}/{upstream_repo}")
                    return forked_repo
            elif response.status_code == 404:
                logger.info(f"User {username} has not forked {upstream_owner}/{upstream_repo}")
                return None
            else:
                logger.error(f"GitHub API error: {response.status_code} - {response.text}")
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"GitHub API returned status {response.status_code}")

        except httpx.TimeoutException as e:
            logger.error(f"Timeout requesting GitHub API for {username}/{upstream_repo}")
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="GitHub API request timed out") from e
        except httpx.RequestError as e:
            logger.error(f"Network error requesting GitHub API: {e}")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to connect to GitHub API") from e

    async def create_fork(self, access_token: str, upstream_owner: str, upstream_repo: str) -> dict[str, Any]:
        url = f"{self.base_url}/repos/{upstream_owner}/{upstream_repo}/forks"
        headers = self.default_headers.copy()
        headers["Authorization"] = f"Bearer {access_token}"

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, headers=headers)

            if response.status_code == status.HTTP_202_ACCEPTED:
                forked_repo = response.json()
                logger.info(f"Successfully forked {upstream_owner}/{upstream_repo} to {forked_repo['owner']['login']}/{forked_repo['name']}")
                return forked_repo
            elif response.status_code == status.HTTP_403_FORBIDDEN:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to GitHub repository")
            elif response.status_code == status.HTTP_404_NOT_FOUND:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Repository {upstream_owner}/{upstream_repo} not found")
            else:
                logger.error(f"GitHub API error: {response.status_code} - {response.text}")
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"GitHub API returned status {response.status_code}")

        except httpx.TimeoutException as e:
            logger.error(f"Timeout requesting GitHub API for {upstream_owner}/{upstream_repo}")
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="GitHub API request timed out") from e
        except httpx.RequestError as e:
            logger.error(f"Network error requesting GitHub API: {e}")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to connect to GitHub API") from e

    async def get_or_create_fork(self, username: str, access_token: str, upstream_owner: str, upstream_repo: str) -> tuple[dict[str, Any], bool]:
        forked_repo = await self.check_user_fork(access_token, username, upstream_owner, upstream_repo)

        if forked_repo:
            return forked_repo, False

        new_fork = await self.create_fork(access_token, upstream_owner, upstream_repo)

        return new_fork, True

    async def create_pull_request(self, access_token: str, upstream_owner: str, upstream_repo: str, pr_data: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/repos/{upstream_owner}/{upstream_repo}/pulls"
        headers = self.default_headers.copy()
        headers["Authorization"] = f"Bearer {access_token}"

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, headers=headers, json=pr_data)

            if response.status_code == status.HTTP_201_CREATED:
                pr_response = response.json()
                logger.info(f"Successfully created pull request for {upstream_owner}/{upstream_repo}")

                return pr_response
            elif response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY:
                error_data = response.json()
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Failed to create pull request: {error_data['message', 'Unknown error']}",
                )
            elif response.status_code == status.HTTP_403_FORBIDDEN:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to GitHub repository")
            elif response.status_code == status.HTTP_404_NOT_FOUND:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Repository {upstream_owner}/{upstream_repo} not found")
            else:
                logger.error(f"GitHub API error: {response.status_code} - {response.text}")
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"GitHub API returned status {response.status_code}")

        except httpx.TimeoutException:
            logger.error(f"Timeout requesting GitHub API for {upstream_owner}/{upstream_repo}")
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="GitHub API request timed out") from None
        except httpx.RequestError as e:
            logger.error(f"Network error requesting GitHub API: {e}")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to connect to GitHub API") from e

    async def get_pull_request(self, owner: str, repo: str, number: int, access_token: str) -> dict[str, Any]:
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls/{number}"
        headers = self.default_headers.copy()
        headers["Authorization"] = f"Bearer {access_token}"

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(url, headers=headers)

            if response.status_code == status.HTTP_200_OK:
                return response.json()
            else:
                logger.error(f"GitHub API error: {response.status_code} - {response.text}")
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"GitHub API returned status {response.status_code}")

        except httpx.TimeoutException as e:
            logger.error(f"Timeout requesting GitHub API for {owner}/{repo}")
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="GitHub API request timed out") from e
        except httpx.RequestError as e:
            logger.error(f"Network error requesting GitHub API: {e}")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to connect to GitHub API") from e


github_service = GitHubService()
