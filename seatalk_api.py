"""
SeaTalk API Integration Module
Handles app access token, message sending, and signature verification
"""
import hashlib
import json
import time
import requests
from typing import Optional, Dict, Any
from datetime import datetime, timezone

class SeaTalkAPI:
    BASE_URL = "https://openapi.seatalk.io"
    
    def __init__(self, app_id: str, app_secret: str, signing_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.signing_secret = signing_secret.encode('utf-8')
        self._access_token: Optional[str] = None
        self._token_expire: Optional[int] = None
    
    def _get_app_access_token(self) -> str:
        """Get or refresh app access token"""
        if self._access_token and self._token_expire and time.time() < self._token_expire - 300:
            return self._access_token
        
        url = f"{self.BASE_URL}/auth/app_access_token"
        payload = {
            "app_id": self.app_id,
            "app_secret": self.app_secret
        }
        headers = {"Content-Type": "application/json"}
        
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        if data.get("code") != 0:
            raise Exception(f"Failed to get access token: {data}")
        
        self._access_token = data["app_access_token"]
        self._token_expire = data["expire"]
        return self._access_token
    
    def verify_signature(self, body: bytes, signature: Optional[str]) -> bool:
        """Verify SeaTalk request signature using SHA-256"""
        if not signature:
            return False
        expected = hashlib.sha256(body + self.signing_secret).hexdigest()
        return expected == signature
    
    def send_group_message(self, group_id: str, message_content: str, format_type: int = 1) -> Dict[str, Any]:
        """Send a text message to a group chat"""
        url = f"{self.BASE_URL}/messaging/v2/group_chat"
        token = self._get_app_access_token()
        
        payload = {
            "group_id": group_id,
            "message": {
                "tag": "text",
                "text": {
                    "format": format_type,
                    "content": message_content
                }
            }
        }
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    
    def get_employee_profile(self, employee_codes: list) -> Dict[str, Any]:
        """Get employee profile information"""
        url = f"{self.BASE_URL}/contacts/v2/profile"
        token = self._get_app_access_token()
        
        params = [("employee_code", code) for code in employee_codes[:500]]
        
        headers = {"Authorization": f"Bearer {token}"}
        
        response = requests.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    
    def get_group_info(self, group_id: str) -> Dict[str, Any]:
        """Get group chat information"""
        url = f"{self.BASE_URL}/messaging/v2/group_chat/info"
        token = self._get_app_access_token()
        
        params = {"group_id": group_id}
        headers = {"Authorization": f"Bearer {token}"}
        
        response = requests.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
