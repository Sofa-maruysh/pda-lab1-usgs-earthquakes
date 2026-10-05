BOT_NAME = "earthquake_project"

SPIDER_MODULES = ["earthquake_project.spiders"]
NEWSPIDER_MODULE = "earthquake_project.spiders"

ROBOTSTXT_OBEY = True
CONCURRENT_REQUESTS_PER_DOMAIN = 1
DOWNLOAD_DELAY = 0.5
DOWNLOAD_TIMEOUT = 60
RETRY_TIMES = 3
USER_AGENT = "pda-lab1-usgs-earthquakes/1.0 (educational project)"
LOG_LEVEL = "INFO"
