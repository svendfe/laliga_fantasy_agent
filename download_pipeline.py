import os
from dotenv import load_dotenv
import requests
import json
from datetime import datetime
import logging
from typing import Dict
from pathlib import Path

# Configure logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Constants
URLS = {
    "current_market": "https://api-fantasy.llt-services.com/api/v3/league/016644922/market?x-lang=en",
    "all_players": "https://api-fantasy.llt-services.com/api/v3/players?x-lang=en",
    "current_week": "https://api-fantasy.llt-services.com/api/v3/week/current?x-lang=en",
    "jefes": "https://api-fantasy.llt-services.com/api/v5/leagues/016644922/ranking?x-lang=en",
    "calendar": "https://api-fantasy.llt-services.com/api/v3/calendar?weekNumber="
}
DATA_DIR = Path("data")

# Helper Functions
def make_request(method: str, url: str, headers: Dict[str, str], **kwargs) -> Dict:
    """Make an HTTP request and handle errors."""
    try:
        response = requests.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Request to {url} failed: {e}")
        raise

def ensure_directory_exists(directory: str):
    """Ensure a directory exists inside the data folder."""
    data_directory = DATA_DIR / directory
    os.makedirs(data_directory, exist_ok=True)

# Authorization
def get_autorization_headers() -> Dict[str, str]:
    """Get authorization headers using environment variables."""
    logger.info("Loading environment variables...")
    load_dotenv()

    TOKEN_URL = os.getenv("TOKEN_URL")
    CLIENT_ID = os.getenv("CLIENT_ID")
    REFRESH_TOKEN = os.getenv("REFRESH_TOKEN")

    # Validate environment variables
    if not TOKEN_URL or not CLIENT_ID or not REFRESH_TOKEN:
        logger.error("Missing required environment variables. Check your .env file.")
        raise ValueError("Missing required environment variables. Check your .env file.")

    logger.info("Refreshing the token...")
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
        "client_id": CLIENT_ID
    }

    auth_data = make_request("POST", TOKEN_URL, headers={}, data=payload)
    access_token = auth_data["access_token"]
    logger.info("Token refreshed successfully.")

    return {"Authorization": f"Bearer {access_token}"}

def download_current_market(headers: Dict[str, str]):
    """Download current market data."""
    logger.info("Downloading current market data...")
    data = make_request("GET", URLS["current_market"], headers=headers)
    current_date = datetime.now().strftime("%Y%m%d")
    ensure_directory_exists("market")
    file_path = DATA_DIR / f"market/market_{current_date}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    logger.info(f"Current market data saved to {file_path}")

def download_all_players(headers: Dict[str, str]):
    """Download all players data."""
    logger.info("Downloading all players data...")
    data = make_request("GET", URLS["all_players"], headers=headers)
    current_date = datetime.now().strftime("%Y%m%d")
    ensure_directory_exists("players")
    file_path = DATA_DIR / f"players/players_{current_date}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    logger.info(f"All players data saved to {file_path}")

def download_team(id_team: int, nombre_jefe: str, headers: Dict[str, str]):
    """Download data for a specific team."""
    logger.info(f"Downloading data for team {nombre_jefe} (ID: {id_team})...")
    url_team = f"https://api-fantasy.llt-services.com/api/v4/leagues/016644922/teams/{id_team}?x-lang=en"
    data = make_request("GET", url_team, headers=headers)
    current_date = datetime.now().strftime("%Y%m%d")
    ensure_directory_exists("equipos")
    file_path = DATA_DIR / f"equipos/{nombre_jefe}_{current_date}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    logger.info(f"Team data for {nombre_jefe} saved to {file_path}")

def download_team_formation(id_team: int, nombre_jefe: str, headers: Dict[str, str]):
    """Download formation data for a specific team."""
    logger.info(f"Downloading formation for team {nombre_jefe} (ID: {id_team})...")
    week_file_path = DATA_DIR / "current_week.json"
    with open(week_file_path, "r", encoding="utf-8") as f:
        current_week_data = json.load(f)
    previous_week = current_week_data.get("previousWeek")

    url_team_formation = f"https://api-fantasy.llt-services.com/api/v4/teams/{id_team}/lineup/week/{previous_week}?x-lang=en"
    data = make_request("GET", url_team_formation, headers=headers)
    ensure_directory_exists("formaciones")
    file_path = DATA_DIR / f"formaciones/{nombre_jefe}_{previous_week}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    logger.info(f"Formation for {nombre_jefe} saved to {file_path}")

def download_all_teams(headers: Dict[str, str]):
    """Download all teams data."""
    logger.info("Downloading all teams data...")
    data = make_request("GET", URLS["jefes"], headers=headers)
    current_date = datetime.now().strftime("%Y%m%d")
    ensure_directory_exists("jefes")
    file_path = DATA_DIR / f"jefes/jefes_{current_date}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    logger.info(f"Team rankings saved to {file_path}")

    for jefe in data:
        id_jefe = jefe['team']['id']
        nombre_jefe = jefe['team']['manager']['managerName']
        download_team(id_jefe, nombre_jefe, headers)
        download_team_formation(id_jefe, nombre_jefe, headers)

def download_current_week(headers: Dict[str, str]):
    """Download current week data."""
    logger.info("Downloading current week data...")
    data = make_request("GET", URLS["current_week"], headers=headers)
    current_week = data.get("weekNumber")
    file_path = DATA_DIR / "current_week.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    logger.info(f"Current week data saved to {file_path}")
    download_current_calendar(headers, current_week)

def download_current_calendar(headers: Dict[str, str], download_week_num):
    """Download current calendar data."""
    logger.info("Downloading current calendar data...")
    call_path = f"{URLS['calendar']}{download_week_num}&x-lang=en"
    data = make_request("GET", call_path, headers=headers)
    ensure_directory_exists("calendar")
    file_path = DATA_DIR / f"calendar/week_{download_week_num}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

    logger.info(f"Data for week {download_week_num} saved to {file_path}")

def download_check():
    #TODO check market time
    """Check if we have already downloaded the data today"""
    #check random directory latest date
    directory = DATA_DIR / "market"
    if not directory.exists():return True

    files = sorted(directory.glob("*.json"), reverse=True)
    if not files: return True        
    latest = files[0].name
    date_str = latest.split("_")[1].split(".")[0]

    current_date = datetime.now().strftime("%Y%m%d")
    if current_date == date_str: return False
    return True

# Main Function
def main():
    load_data_check = download_check()
    if load_data_check:
        logger.info("Starting the download pipeline...")
        try:
            headers = get_autorization_headers()
            download_current_week(headers)
            download_current_market(headers)
            download_all_players(headers)
            download_all_teams(headers)
            logger.info("Download pipeline completed successfully.")
        except Exception as e:
            logger.error(f"An error occurred: {e}")
    else: 
        logger.info("We already have todays data downlaoded...")

if __name__ == "__main__":
    #print(download_check())
    main()