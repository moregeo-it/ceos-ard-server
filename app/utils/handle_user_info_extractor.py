from typing import Dict, Any
import jwt
import logging

from app.oauth.handler import oauth

logger = logging.getLogger(__name__)

async def extract_github_user_info(token: Dict[str, Any]) -> Dict[str, str]:
    try:
        user_response = await oauth.github.get('user', token=token)
        if user_response.status_code != 200:
            raise Exception(f"GitHub API returned status {user_response.status_code}")
        
        profile = user_response.json()

        email = profile.get('email')
        if not email:
            email_response = await oauth.github.get('user/emails', token=token)
            if email_response.status_code == 200:
                emails = email_response.json()
                primary_email = next((e['email'] for e in emails if e['primary']), None)
                email = primary_email or (emails[0]['email'] if emails else None)

        return {
            'email': email,
            'provider': 'github',
            'username': profile.get('login'),
            'external_id': str(profile.get('id')),
            'full_name': profile.get('name') or profile.get('login'),
        }
        
    except Exception as e:
        logger.error(f"Error extracting GitHub user info: {e}")
        raise Exception(f"Failed to extract GitHub user information: {str(e)}")

async def extract_google_user_info(token: Dict[str, Any]) -> Dict[str, str]:
    try:
        # Try ID token first (OpenID Connect)
        if 'id_token' in token:
            id_token = jwt.decode(token['id_token'], options={"verify_signature": False})
            email = id_token.get('email')
            return {
                'email': email,
                'username': email.split('@')[0] if email else None,
                'external_id': str(id_token.get('sub')),
                'full_name': id_token.get('name'),
                'provider': 'google'
            }
        
        # Fallback to userinfo endpoint
        user_response = await oauth.google.get('userinfo', token=token)

        if user_response.status_code != 200:
            raise Exception(f"Google userinfo API returned status {user_response.status_code}")
        
        profile = user_response.json()
        print(profile)
        email = profile.get('email')
        return {
            'email': email,
            'username': email.split('@')[0] if email else None,
            'external_id': str(profile.get('sub')),
            'full_name': profile.get('name'),
            'provider': 'google'
        }
        
    except Exception as e:
        logger.error(f"Error extracting Google user info: {e}")
        raise Exception(f"Failed to extract Google user information: {str(e)}")