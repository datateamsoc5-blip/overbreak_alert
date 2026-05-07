"""
Configuration module for environment variables and secrets
"""
import os
from typing import List, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class Config:
    """Application configuration class"""
    
    # Google Sheets
    GOOGLE_SHEET_ID: str = os.getenv("GOOGLE_SHEET_ID", "")
    
    # SeaTalk Bot
    SEATALK_APP_ID: str = os.getenv("SEATALK_APP_ID", "")
    SEATALK_APP_SECRET: str = os.getenv("SEATALK_APP_SECRET", "")
    SEATALK_SIGNING_SECRET: str = os.getenv("SEATALK_SIGNING_SECRET", "")
    
    # CC User IDs (for mentions)
    @property
    def CC_USER_IDS(self) -> List[str]:
        """Get list of CC user IDs from comma-separated string"""
        ids = os.getenv("CC_USER_IDS", "")
        return [uid.strip() for uid in ids.split(",") if uid.strip()]
    
    # Server (no default - must be set via environment)
    @property
    def PORT(self) -> Optional[int]:
        """Get port from environment variable (required for cloud platforms)"""
        port = os.getenv("PORT")
        return int(port) if port else None
    
    # Environment
    FLASK_ENV: str = os.getenv("FLASK_ENV", "development")
    DEBUG: bool = FLASK_ENV == "development"
    
    @classmethod
    def validate(cls) -> List[str]:
        """Validate that all required configuration is present
        
        Returns:
            List of missing configuration keys
        """
        required = [
            "GOOGLE_SHEET_ID",
            "SEATALK_APP_ID",
            "SEATALK_APP_SECRET",
            "SEATALK_SIGNING_SECRET",
        ]
        
        missing = []
        for key in required:
            if not os.getenv(key):
                missing.append(key)
        
        return missing
    
    @classmethod
    def is_configured(cls) -> bool:
        """Check if all required configuration is present"""
        return len(cls.validate()) == 0


# Global config instance
config = Config()
