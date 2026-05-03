from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
import time

# Set up global driver so it persists across functions
driver = None

def start_youtube():
    global driver
    options = Options()
    options.add_argument("--start-maximized")
    driver = webdriver.Chrome(options=options)
    driver.get("https://www.youtube.com")
    time.sleep(5)

def search_youtube(query):
    global driver
    search_box = driver.find_element(By.NAME, "search_query")
    search_box.clear()
    search_box.send_keys(query)
    search_box.send_keys(Keys.RETURN)
    time.sleep(5)

def play_video_by_index(index=1):
    global driver
    try:
        videos = driver.find_elements(By.ID, "video-title")
        if 0 < index <= len(videos):
            videos[index - 1].click()
            return f"Playing video number {index}"
        else:
            return "Video number out of range."
    except Exception as e:
        return f"Error playing video: {e}"

def scroll_down():
    global driver
    driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.PAGE_DOWN)

def scroll_up():
    global driver
    driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.PAGE_UP)

def open_liked_videos():
    global driver
    driver.get("https://www.youtube.com/playlist?list=LL")  # YouTube "Liked videos" playlist
    time.sleep(5)

def toggle_play_pause():
    global driver
    body = driver.find_element(By.TAG_NAME, 'body')
    body.send_keys("k")  # 'k' toggles play/pause on YouTube

def stop_youtube():
    global driver
    if driver:
        driver.quit()
        driver = None
