from server.otp_service import TelethonOTPService


def test_extract_code_standard_english():
    text = "Login code: 73821. Do not give this code to anyone, even if they say they're from Telegram!"
    code = TelethonOTPService._extract_code(text)
    assert code == "73821"


def test_extract_code_multiline_english():
    text = "Dear user,\nHere is your code: 49201\nPlease keep it secret."
    code = TelethonOTPService._extract_code(text)
    assert code == "49201"


def test_extract_code_persian():
    text = "کد ورود: 84729 - این کد را در اختیار هیچ‌کس قرار ندهید."
    code = TelethonOTPService._extract_code(text)
    assert code == "84729"


def test_extract_code_digits_fallback():
    text = "Your verification token is 10928."
    code = TelethonOTPService._extract_code(text)
    assert code == "10928"


def test_extract_code_none_found():
    text = "Hello, your account settings have been updated."
    code = TelethonOTPService._extract_code(text)
    assert code is None
