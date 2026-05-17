import webbrowser
from voxkage.config_loader import load_config

CONFIG = load_config()
WEBSITE_COMMANDS = CONFIG.get("website_commands", {})


def open_website(site_name):
    site_name = site_name.lower()

    if site_name in WEBSITE_COMMANDS:
        url = WEBSITE_COMMANDS[site_name]
        webbrowser.open(url)
        return f"Opening {site_name}"

    if site_name.startswith("http"):
        webbrowser.open(site_name)
        return f"Opening link {site_name}"

    # Fallback to Google search
    search_url = f"https://www.google.com/search?q={site_name}"
    webbrowser.open(search_url)
    return f"Searched Google for '{site_name}'"

def search_youtube(query):
    url = f"https://www.youtube.com/results?search_query={query}"
    webbrowser.open(url)

def search_google(query):
    url = f"https://www.google.com/search?q={query}"
    webbrowser.open(url)
