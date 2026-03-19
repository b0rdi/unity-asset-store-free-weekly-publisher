import os
import sys
import json
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo

# Discord Configuration - Get from GitHub Secrets
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# Unity Asset Store Configuration and Selectors
ASSET_STORE_URL = "https://assetstore.unity.com"
ASSET_PARENT_SELECTOR = "[data-type='CalloutSlim']"
ASSET_NAME_SELECTOR = "h2"
ASSET_IMAGE_SELECTOR = "img"
ASSET_BUTTON_SELECTOR = "a"
ASSET_DESCRIPTION_SELECTOR = "body"
ASSET_PRICE_SELECTOR = "._3Yjml"

# Savings File Configuration
SAVINGS_FILE = "savings.json"


logging.basicConfig(
	level=logging.INFO,
	format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)


# region: Unity Asset Store


def scrape_asset_info() -> tuple[str, str, str, str]:
	"""Scrapes the Unity Asset Store for the free asset of the week using new selectors."""
	try:
		url = f"{ASSET_STORE_URL}/publisher-sale"
		response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
		response.raise_for_status()
		soup = BeautifulSoup(response.content, "html.parser")

		free_asset_section = soup.select_one(ASSET_PARENT_SELECTOR)

		if free_asset_section:
			asset_name_element = free_asset_section.find(ASSET_NAME_SELECTOR)
			asset_name = asset_name_element.get_text(strip=True) if asset_name_element else "Asset Name Not Found"

			asset_image_element = free_asset_section.find(ASSET_IMAGE_SELECTOR)
			asset_image = asset_image_element.get("src") if asset_image_element else ""

			asset_button_element = free_asset_section.find(ASSET_BUTTON_SELECTOR)
			asset_url = asset_button_element.get("href") if asset_button_element else ""

			asset_description_element = free_asset_section.find(class_=ASSET_DESCRIPTION_SELECTOR)
			asset_description = asset_description_element.get_text(strip=True) if asset_description_element else "Asset Description Not Found"
			
			return asset_name, asset_image, asset_description, asset_url
		else:
			log.warning("Could not find the free asset section using the specified selector.")
			return None, None, None, None
			
	except requests.exceptions.RequestException as e:
		log.error(f"Error fetching the URL: {e}")
		return None, None, None, None


def next_weekday_at_time(weekday: int, target_time: time, tz=timezone.utc) -> datetime:
	now = datetime.now(tz)
	days_ahead = (weekday - now.weekday() + 7) % 7
	if days_ahead == 0 and now.time() >= target_time:
		days_ahead = 7

	next_day = (now + timedelta(days=days_ahead)).date()
	return datetime.combine(next_day, target_time, tzinfo=tz)


def get_expiry_date() -> str:
	# Get next Thursday at 8:00 AM PT
	pt_tz = ZoneInfo("America/Los_Angeles")
	next_thursday_8am_pt = next_weekday_at_time(weekday=3, target_time=time(8, 0), tz=pt_tz)
	# Convert to UTC
	next_thursday_utc = next_thursday_8am_pt.astimezone(timezone.utc)
	# Format it like: October 2, 2025 at 3:00PM UTC
	day = next_thursday_utc.day
	hour = next_thursday_utc.strftime("%I:%M%p").lstrip("0")
	return next_thursday_utc.strftime(f"%B {day}, %Y at {hour} UTC")


# endregion: Unity Asset Store

# region: Discord


def send_discord_notification(asset: str, image: str, description: str, url: str, price: float) -> None:
	"""Sends a Discord webhook message about the newly detected free asset."""
	if not url.startswith("https://"):
		url = ASSET_STORE_URL + url

	expiry_date = get_expiry_date()
	price_text = f"${price:.2f}" if price > 0 else "N/A"
	payload = {
		"embeds": [
			{
				"title": asset,
				"description": description,
				"url": url,
				"color": 5763719,
				"thumbnail": {"url": image} if image else None,
				"fields": [
					{"name": "Price", "value": price_text, "inline": True},
					{"name": "Free Until", "value": expiry_date, "inline": True},
				]
			}
		]
	}

	# Remove optional keys that are None to keep payload clean.
	payload["embeds"][0] = {k: v for k, v in payload["embeds"][0].items() if v is not None}

	response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=30)
	response.raise_for_status()
	log.info("Discord notification sent successfully.")


# endregion: Discord

# region: Price Scraping


def scrape_asset_price(asset_url: str) -> float:
	"""Visits the asset's page and scrapes its regular price."""
	if not asset_url.startswith("https://"):
		asset_url = ASSET_STORE_URL + asset_url

	try:
		log.info(f"Scraping price from: {asset_url}")
		headers = { 'User-Agent': 'Mozilla/5.0' }
		cookies = { 'AC_CURR': 'USD' }
		response = requests.get(asset_url, headers=headers, cookies=cookies)
		response.raise_for_status()
		soup = BeautifulSoup(response.content, "html.parser")
		
		price_element = soup.select_one(ASSET_PRICE_SELECTOR)

		if price_element:
			price_text = price_element.contents[-1].get_text(strip=True)
			log.info(f"Found price: {price_text}")
			number = float(price_text.replace("$", ""))
			return number
		else:
			log.warning("Could not find the price element on the page.")
			return 0.0
	except requests.exceptions.RequestException as e:
		log.error(f"Error fetching the asset price URL: {e}")
		return 0.0


def read_total_savings() -> tuple[float, int, float, int, str | None]:
	"""Reads the total savings, number of assets, and cumulative savings from the JSON file."""
	try:
		with open(SAVINGS_FILE, 'r') as f:
			data = json.load(f)
			current_savings = float(data.get("total_savings", 0.0))
			current_assets = int(data.get("total_assets", 0))
			current_cumulative_savings = float(data.get("total_cumulative_savings", 0.0))
			current_emails_sent = int(data.get("total_emails_sent", 0))
			last_asset_url = data.get("last_asset_url")
			return current_savings, current_assets, current_cumulative_savings, current_emails_sent, last_asset_url
	except FileNotFoundError:
		log.warning(f"'{SAVINGS_FILE}' not found. Starting savings from 0.")
		return 0.0, 0, 0.0, 0, None
	except (json.JSONDecodeError, TypeError):
		log.error(f"Could not read or parse '{SAVINGS_FILE}'. Treating savings as 0.")
		return 0.0, 0, 0.0, 0, None


def save_total_savings(new_total: float, new_assets: int, new_cumulative_savings: float, new_emails_sent: int, last_asset_url: str | None = None) -> None:
	"""Saves the new total savings, number of assets, cumulative savings, and number of emails sent to the JSON file."""
	data = {
		"total_savings": round(new_total, 2),
		"total_assets": new_assets,
		"total_cumulative_savings": round(new_cumulative_savings, 2),
		"total_emails_sent": new_emails_sent,
		"last_run_date": datetime.now(tz=ZoneInfo("America/Los_Angeles")).date().isoformat(),
		"last_asset_url": last_asset_url,
	}
	with open(SAVINGS_FILE, 'w') as f:
		json.dump(data, f, indent=2)
	log.info(f"Successfully saved new total savings: {data['total_savings']:.2f}, number of assets: {data['total_assets']}, cumulative savings: {data['total_cumulative_savings']:.2f}, emails sent: {data['total_emails_sent']}, last run date: {data['last_run_date']}, last asset URL: {data['last_asset_url']}")


# endregion: Price Scraping

# region: Main


def should_run_now(now_pt: datetime) -> bool:
	"""
	Determines if the script should proceed based on time and previous runs.
	Returns True if we should send the email, False otherwise.
	"""
	# 1. Setup current time in iso format
	today_str = now_pt.date().isoformat()
	
	log.info(f"Current Time (PT): {now_pt.strftime('%Y-%m-%d %H:%M:%S')}")

	# 2. Check that it is Thursday
	if now_pt.weekday() != 3: 
		log.info("Today is not Thursday. Exiting.")
		return False

	# 3. Check that it is after 8:30 AM PT
	# We use >= 8:30 to prevent the Winter 7:30 AM run from triggering,
	# but allow delayed jobs (e.g. 9:00 AM) to still work.
	if now_pt.time() < time(8, 30):
		log.info("It is too early (before 8:30 AM PT). Exiting.")
		return False

	# 4. Check that we have not already run today
	try:
		with open(SAVINGS_FILE, 'r') as f:
			data = json.load(f)
			last_run = data.get("last_run_date")
	except (FileNotFoundError, json.JSONDecodeError):
		log.warning(f"Could not read '{SAVINGS_FILE}'. Returning empty data.")
		last_run = None
	
	if last_run and last_run == today_str:
		log.info(f"Script already ran successfully today ({today_str}). Exiting.")
		return False

	return True


def main():
	# Check if the script was run manually or by the schedule
	RUN_CONTEXT = os.getenv("RUN_CONTEXT")

	if RUN_CONTEXT == "schedule":
		target_tz = ZoneInfo("America/Los_Angeles")
		current_pt_time = datetime.now(target_tz)
		if not should_run_now(current_pt_time):
			log.info(f"Not the right time or already ran today. Current PT: {current_pt_time.strftime('%A %H:%M')}. Exiting.")
			sys.exit(0) # Exit with success, but do nothing
			
		log.info(f"Correct time ({current_pt_time.strftime('%H:%M PT')}) detected. Running script...")
	elif RUN_CONTEXT == "workflow_dispatch":
		log.info("Run triggered by 'workflow_dispatch'. Bypassing time check.")
	else:
		log.info(f"Run context is '{RUN_CONTEXT}'. Bypassing time check.")
	
	log.info("Starting the Unity Asset Notifier script...")
	
	missing_vars = []
	if not DISCORD_WEBHOOK_URL:
		missing_vars.append("DISCORD_WEBHOOK_URL")
	
	if missing_vars:
		log.error(f"Error: Missing required environment variables: {', '.join(missing_vars)}")
		sys.exit(2)
	
	asset, image, description, url = scrape_asset_info()
	if not all ([asset, description, image, url]):
		log.warning("Could not find asset information. No fields will be updated.")
		sys.exit(3)
	
	log.info(f"Found asset: {asset}")
	
	try:
		current_savings, current_assets, current_cumulative_savings, current_emails_sent, last_asset_url = read_total_savings()
		normalized_url = url if url.startswith("https://") else ASSET_STORE_URL + url
		if last_asset_url == normalized_url:
			log.info("Asset has not changed since the last successful notification. Skipping Discord send.")
			sys.exit(0)

		asset_price = scrape_asset_price(url)
		send_discord_notification(asset, image, description, url, asset_price)

		if asset_price > 0.0:
			new_savings = current_savings + asset_price
			new_assets = current_assets + 1
			new_cumulative_savings = current_cumulative_savings + asset_price
			new_emails_sent = current_emails_sent + 1
			save_total_savings(new_savings, new_assets, new_cumulative_savings, new_emails_sent, normalized_url)
		else:
			log.warning("Asset price is 0 or could not be found. Savings will not be updated.")
			save_total_savings(current_savings, current_assets, current_cumulative_savings, current_emails_sent, normalized_url)
		
		sys.exit(0)
	except Exception as e:
		log.error(f"Unexpected error while updating fields: {e}")
		sys.exit(1)


if __name__ == "__main__":
	main()


# endregion: Main

# Exit code defintions:
# 0 = success
# 1 = generic error
# 2 = missing config
# 3 = data issue (e.g. asset not found)
