GMAIL_USER = "your-coach@gmail.com"
GMAIL_APP_PASSWORD = "16charapppassword"
MY_EMAIL = "you@gmail.com"
FROM_NAME = "Cornerman"
OPENROUTER_KEY = "sk-or-..."
OPENROUTER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
OPENROUTER_FALLBACKS = ["nvidia/nemotron-3-super-120b-a12b:free"]

# Zepp / Amazfit. No official personal-data API; this uses the app's own
# endpoints with a token from your browser session. Log in at
# https://watchface.zepp.com/, read the hm-user-login-info cookie in DevTools
# (Application > Cookies), URL-decode it, and take token_info.app_token and
# token_info.user_id. Close the tab -- do NOT click log out, that voids it.
# Region matters: a REDIRECTION response naming a region means wrong host.
ZEPP_TOKEN = ""
ZEPP_UID = ""
ZEPP_HOST = "api-mifit.huami.com"
