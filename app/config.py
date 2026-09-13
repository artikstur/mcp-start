from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Settings:
    institute_base_url: str = "https://kpfu.ru/biology-medicine"
    schedule_page_url: str = (
        "https://kpfu.ru/biology-medicine/uchebnyj-process/"
        "raspisanie-zachjotov-i-ekzamenov"
    )
    request_timeout: float = float(os.getenv("REQUEST_TIMEOUT", "20"))
    max_crawl_pages: int = int(os.getenv("MAX_CRAWL_PAGES", "40"))

settings = Settings()
