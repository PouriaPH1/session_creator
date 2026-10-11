# 🚀 Telegram Session Creator & Migration System

سیستم خودکار مدیریت و مهاجرت سشن‌های تلگرام بر پایه کتابخانه **Telethon** و فریم‌ورک **FastAPI** با معماری تمیز (Clean Architecture) و دیزاین پترن‌های استاندارد.

این پروژه برای حل مشکل منقضی یا بن شدن سشن‌ها هنگام جابجایی بین سرورها/آی‌پی‌های مختلف طراحی شده است.

---

## 🏗 معماری سیستم (System Architecture) & Design Patterns

این پروژه بر اساس تفکیک وظایف (Separation of Concerns) و الگوهای طراحی زیر پیاده‌سازی شده است:

1. **معماری Master-Worker (Client-Server):**
   * **Master (OTP Server):** سشن‌های خریداری شده و موجود در دایرکتوری `storage/source_sessions` را لود کرده و به پیام‌های چت رسمی تلگرام (`777000`) دسترسی دارد. از طریق REST API کد تایید را در اختیار کلاینت‌های دیگر قرار می‌دهد.
   * **Worker (Session Creator):** روی سرور یا سیستم جدید اجرا شده و با آی‌پی جدید فرآیند درخواست کد (`send_code_request`) را به تلگرام ارسال کرده و سپس کد را از طریق API سرور Master دریافت و ورود را نهایی می‌کند. سشن‌های جدید در `storage/target_sessions` ذخیره می‌شوند.

2. **الگوهای طراحی استفاده‌شده (Design Patterns):**
   * **Factory Pattern (`core/device_profiler.py`):** ایجاد مشخصات دستگاهی استاندارد و واقع‌گرایانه (Device Model, OS, App Version) به صورت Deterministic بر اساس شماره تا از تغییر نامتعارف فینگرپرینت جلوگیری شود.
   * **Object Pool / Repository Pattern (`server/session_pool.py`):** مدیریت چرخه حیات کلاینت‌های تلگرام، اتصال، نگه‌داری و بستن امن سشن‌ها.
   * **Strategy / Polling Client (`worker/otp_client.py`):** استراتژی بازخوانی خودکار کد با قابلیت Retry و Polling و هندلینگ Timeout.
   * **Service Layer (`server/otp_service.py` & `worker/session_creator.py`):** جداسازی کامل بیزینس لاجیک از لایه وب و رابط خط فرمان (CLI).

---

## 📁 ساختار پروژه (Directory Structure)

```
session_creator/
├── config/                  # تنظیمات و متغیرهای محیطی با pydantic-settings
│   ├── settings.py
│   └── __init__.py
├── core/                    # هسته مشترک، اینترفیس‌ها، خطاها و کارخانه پروفایل دستگاه
│   ├── constants.py
│   ├── device_profiler.py   # Factory تولید مشخصات دستگاهی
│   ├── exceptions.py        # خطاهای سفارشی پروژه
│   ├── interfaces.py        # Abstract Base Classes
│   └── __init__.py
├── server/                  # سرویس Master (FastAPI + Web Admin Panel)
│   ├── main.py              # اندپوینت‌های REST API و پنل وب
│   ├── session_pool.py      # مدیریت پول سشن‌های مبدأ Telethon
│   ├── otp_service.py       # استخراج کد پیام رسمی 777000
│   ├── account_service.py   # مدیریت پروفایل، آواتار، ۲FA و اسپم‌بات
│   ├── migration_manager.py # مدیریت جاب‌های مهاجرت در پس‌زمینه
│   ├── templates/
│   │   └── index.html       # داشبورد مدیریت وب مدرن و ریسپانسیو
│   └── __init__.py
├── worker/                  # سرویس ساخت سشن در سرور مقصد
│   ├── otp_client.py        # پولینگ API سرور Master
│   ├── session_creator.py   # لاگین Telethon و ساخت سشن جدید
│   └── __init__.py
├── cli/                     # اسکریپت‌های اجرایی با پارامترهای خط فرمان
│   ├── run_server.py
│   └── run_worker.py
├── storage/
│   ├── source_sessions/     # سشن‌های اولیه خریداری شده (.session)
│   └── target_sessions/     # سشن‌های جدید تولید شده (.session)
├── tests/                   # تست‌های واحد (Unit Tests)
├── requirements.txt
├── .env.example
├── run_server.py            # میانبر اجرای وب سرور Master
└── run_worker.py            # میانبر اجرای ساخت سشن Worker
```

---

## ⚡ راه‌اندازی سریع و آسان (Zero-Config / 1-Click Setup)

برای اینکه نیازی به تنظیم دستی پیش‌نیازها، پایتون، فایروال و ساخت سرویس سیستم نداشته باشید، از یکی از ۳ روش زیر استفاده کنید:

### 🌟 روش اول: نصب خودکار روی لینوکس (توصیه شده - فقط ۱ دستور)
کافیست در ترمینال سرور خود دستور زیر را اجرا کنید:
```bash
git clone https://github.com/PouriaPH1/session_creator.git && cd session_creator && bash install.sh
```
* **چه اتفاقی می‌افتد؟**
  1. پایتون، venv و تمام پکیج‌ها را خودکار نصب می‌کند.
  2. فایل `.env` را تعاملی ایجاد می‌کند.
  3. پورت فایروال سرور را باز می‌کند.
  4. **سرویس پس‌زمینه خودکار (Systemd Service)** می‌سازد تا حتی اگر سرور ریبوت شد، برنامه همیشه روشن بماند!
  5. لینک پنل را به همراه IP عمومی سرور به شما نمایش می‌دهد.

مدیریت سرویس با یک اسکریپت ساده:
```bash
./manage.sh logs       # مشاهده لاگ‌های زنده
./manage.sh restart    # ریستارت سرویس
./manage.sh stop       # توقف سرویس
./manage.sh status     # بررسی وضعیت اجرا
```

---

### 🐳 روش دوم: راه‌اندازی با داکر (Docker Compose)
اگر داکر دارید، اجرای کل پروژه فقط با یک دستور انجام می‌شود:
```bash
docker compose up -d
```
پنل روی پورت ۸۰۰۰ بالا می‌آید و اطلاعات سشن‌ها به صورت دائمی در پوشه `storage/` حفظ می‌شوند.

---

### 🪟 روش سوم: ویندوز (دابل‌کلیک)
روی فایل `start_windows.bat` دابل‌کلیک کنید! خودش نیازمندی‌ها را نصب کرده، سرور را روشن می‌کند و مرورگر را روی آدرس پنل باز می‌کند.

---

## 🛠 روش راه‌اندازی دستی (Manual Setup)

اگر مایل بودید به صورت دستی اجرا کنید:
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run_server.py
```

---

## 🚀 راهنمای اجرا

### گام اول: اجرای سرور مبدأ (Master OTP Server)

1. سشن‌های تلگرام اولیه‌ای که خریداری کرده‌اید را درون پوشه `storage/source_sessions/` کپی کنید (فایل‌های با پسوند `.session`).
2. سرور را اجرا کنید:
```bash
python run_server.py
```
* سرور وب روی آدرس `http://localhost:8000` بالا می‌آید.
* **داشبورد گرافیکی پنل ادمین (Web Dashboard):** با مراجعه به آدرس `http://localhost:8000` در مرورگر، پنل مدیریت گرافیکی کامل در دسترس است با امکانات:
  - 📊 مشاهده آمار سشن‌های مبدأ و مقصد
  - 📋 جدول سشن‌ها همراه با نام، یوزرنیم، وضعیت لاگین، مدل گوشی و شماره
  - 👤 **ویرایش پروفایل:** تغییر نام (First Name)، نام خانوادگی (Last Name) و بیوگرافی (About/Bio)
  - 🖼 **آپلود عکس پروفایل:** تغییر آواتار و عکس پروفایل اکانت‌ها از طریق مرورگر
  - 🔒 **تنظیم ۲FA تکی و همگانی:** امکان اختصاص رمز تصادفی و قدرتمند برای تک‌تک یا تمام حساب‌ها با یک کلیک
  - 📑 **خروجی اکسل و CSV پسوردها:** ذخیره مطمئن و دانلود لیست شماره‌ها و پسوردهای ۲FA ست‌شده
  - 🏷 **تغییر نام تصادفی گروهی:** اختصاص نام‌های طبیعی ایرانی یا انگلیسی به کلیه اکانت‌ها
  - 🛡 **خروج از سایر نشست‌ها (Terminate Other Sessions):** لاگ‌اوت کردن تمام دستگاه‌ها و سرورهای قبلی
  - 🤖 **بررسی سلامت اکانت با @SpamBot:** استعلام وضعیت لیمیت و ریپورت بودن شماره‌ها
  - 📤 درگ اند دراپ و آپلود فایل‌های `.session`
  - 🔑 دریافت کد زنده تلگرام با دکمه کپی
  - ⚡ شروع ساخت سشن (تکی یا گروهی) مستقیم از پنل وب
  - 💾 دانلود فایل‌های سشن مقصد (تکی یا فایل ZIP تجمیعی)
  - 📜 مانیتورینگ زنده لاگ‌ها و نوار پیشرفت عملیات
* **مستندات تعاملی Swagger:** در آدرس `http://localhost:8000/docs`

---

### گام دوم: اجرای ساخت سشن در سرور مقصد (Worker / سرور ۲)

روی سرور دوم که می‌خواهید سشن‌ها با آی‌پی آن ساخته شوند:

#### ⚡ روش سریع (فقط ۱ دستور با اسکریپت worker.sh):
```bash
git clone https://github.com/PouriaPH1/session_creator.git && cd session_creator && bash worker.sh http://MASTER_SERVER_IP:8000
```
این اسکریپت خودش محیط را آماده کرده، به سرور اول متصل می‌شود و تمام سشن‌ها را با آی‌پی سرور دوم ساخته و در `storage/target_sessions` تحویل می‌دهد!

---

#### یا اجرای دستی با پایتون:
```bash
python run_worker.py --server-url http://MASTER_IP:8000 --all --delay 15
```

#### حالت ۳: خواندن شماره‌ها از فایل متنی
یک فایل مثلاً `phones.txt` درست کنید که در هر خط یک شماره (یا `phone:password`) باشد:
```bash
python run_worker.py --server-url http://MASTER_IP:8000 --file phones.txt --delay 20
```

#### حالت ۴: استفاده از پروکسی اختصاصی
```bash
python run_worker.py --phone +1234567890 --proxy socks5://user:pass@proxy_host:1080
```

---

## 🛡 تمهیدات ضد بن (Anti-Ban Measures)

1. **پروفایل سخت‌افزاری پایدار (Consistent Device Profile):** از طریق `DeviceProfileFactory`، هر شماره به صورت قطعی (Deterministic) با یک مدل معتبر گوشی (Samsung S23، Xiaomi 13، Pixel 8 و...) ست می‌شود.
2. **فیلتر زمانی کد لاگین (Timestamp Filtering):** برای جلوگیری از بازگرداندن کدهای قدیمی، کدها بر اساس زمان بعد از درخواست لاگین فیلتر می‌شوند.
3. **فاصله زمانی ایمن (Pacing Delay):** در مهاجرت گروهی، بین ساخت هر سشن به صورت پیش‌فرض ۱۵ ثانیه تاخیر اعمال می‌شود تا از خطای `FLOOD_WAIT` سرورهای تلگرام جلوگیری گردد.

---

## 🧪 اجرای تست‌ها
```bash
python -m pytest
```
