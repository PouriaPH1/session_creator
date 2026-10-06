import re

# Telegram Official Service Notifications sender ID
TELEGRAM_SERVICE_NOTIFICATIONS_ID = 777000

# Regex pattern to match 5-digit Telegram login codes
# Matches patterns like "Login code: 12345" or standalone 5 digits
OTP_REGEX = re.compile(r"(?<!\d)(\d{5})(?!\d)")

# Common regex to find codes inside text
CONTEXTUAL_OTP_REGEX = re.compile(
    r"(?:Login\s*code|کد\s*ورود|code)[\s:]*(\d{5})",
    re.IGNORECASE
)
