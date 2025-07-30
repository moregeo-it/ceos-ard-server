import httpx
import logging
from typing import List, Dict, Any
from fastapi import HTTPException, status

from app.config import settings

logger = logging.getLogger(__name__)

class GitHubService:
    def __init__(self):
        self.base_url = settings.GITHUB_API_BASE_URL
        self.default_headers = {
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'CEOS-ARD-Editor'
        }
    
    async def get_repository_contents(
        self, 
        owner: str, 
        repo: str, 
        token: str,
        path: str = "", 
        branch: str = "main"
    ) -> List[Dict[str, Any]]:

        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing GitHub access token"
            )
        
        self.default_headers['Authorization'] = f"token {token}"

        url = f"{self.base_url}/repos/{owner}/{repo}/contents"

        if path:
            url += f"/{path}"
        
        params = {'ref': branch}
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    url, 
                    headers=self.default_headers,
                    params=params
                )
                
                if response.status_code == 404:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Repository {owner}/{repo} or path '{path}' not found"
                    )
                elif response.status_code == 403:
                    # Check if it's a rate limit issue
                    if 'rate limit' in response.text.lower():
                        raise HTTPException(
                            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="GitHub API rate limit exceeded"
                        )
                    else:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="Access denied to GitHub repository"
                        )
                elif response.status_code != 200:
                    logger.error(f"GitHub API error: {response.status_code} - {response.text}")
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"GitHub API returned status {response.status_code}"
                    )
                
                return response.json()
                
        except httpx.TimeoutException:
            logger.error(f"Timeout requesting GitHub API for {owner}/{repo}/{path}")
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="GitHub API request timed out"
            )
        except httpx.RequestError as e:
            logger.error(f"Network error requesting GitHub API: {e}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to connect to GitHub API"
            )
    
    async def get_pfs_folders(
        self, 
        owner: str, 
        repo: str, 
        token: str,
        branch: str = "main"
    ) -> List[str]:
        try:
            if not token:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing GitHub access token"
                )
            print(f"owner: {owner}, repo: {repo}, token: {token}, branch: {branch}")
            contents = await self.get_repository_contents(owner, repo, token, "pfs", branch)
            
            pfs_folders = [
                item['name'] 
                for item in contents 
                if item['type'] == 'dir' and not item['name'].startswith('.')
            ]
            
            logger.info(f"Found {len(pfs_folders)} PFS folders in {owner}/{repo}")
            
            return sorted(pfs_folders)
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error getting PFS folders: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve PFS folders"
            )

github_service = GitHubService()