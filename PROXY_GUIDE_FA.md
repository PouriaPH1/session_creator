# راهنمای ساخت پروکسی SOCKS5 روی سرور دوم

با اسکریپت `setup_proxy_server.sh` هر سرور لینوکسی (Ubuntu/Debian/CentOS/AlmaLinux) در یک مرحله به یک پروکسی SOCKS5 اختصاصی تبدیل می‌شود تا سشن‌ها از طریق آی‌پی آن سرور ساخته شوند.

---

## ۱. انتقال اسکریپت به سرور دوم

**روش الف — کلون ریپو روی سرور دوم:**
```bash
git clone https://github.com/PouriaPH1/session_creator.git
cd session_creator
```

**روش ب — فقط کپی همین فایل از سیستم خودتان (بدون نیاز به دسترسی گیت روی سرور):**
```bash
scp setup_proxy_server.sh root@SERVER2_IP:/root/
```

## ۲. اجرای اسکریپت (روی سرور دوم)

ساده‌ترین حالت (پورت `1080`، کاربر `teleuser`، رمز تصادفی):
```bash
sudo bash setup_proxy_server.sh
```

با پورت، نام کاربری و رمز دلخواه:
```bash
sudo bash setup_proxy_server.sh 1080 teleuser MyStrongPass123
```

| آرگومان | ترتیب | پیش‌فرض |
|---|---|---|
| پورت | اول | `1080` |
| نام کاربری | دوم | `teleuser` |
| رمز عبور | سوم | ۱۲ کاراکتر تصادفی |

> اگر بدون root اجرا شود، اسکریپت خودش با `sudo` دوباره اجرا می‌شود.

## ۳. خروجی

در پایان، آدرس کامل پروکسی چاپ می‌شود:
```
socks5://teleuser:PASSWORD@SERVER2_IP:1080
```
این آدرس را کپی کنید (رمز فقط همین‌جا نمایش داده می‌شود).

## ۴. اضافه کردن در پنل وب سرور اول

1. وارد پنل وب سرور اول شوید.
2. روی **«مدیریت پروکسی‌ها»** بزنید.
3. یک نام (مثلاً `سرور دوم`) و آدرس بالا را وارد و ذخیره کنید.
4. دکمه **تست** را بزنید تا اتصال بررسی شود.
5. در صورت تمایل آن را **پیش‌فرض** کنید تا همه‌ی سشن‌های جدید از این پروکسی ساخته شوند.

استفاده‌ی مستقیم از CLI هم ممکن است:
```bash
python run_worker.py --phone +1234567890 --proxy socks5://teleuser:PASSWORD@SERVER2_IP:1080
```

---

## اسکریپت دقیقاً چه کار می‌کند؟

1. نصب ابزارهای بیلد و کامپایل [microsocks](https://github.com/rofl0r/microsocks) در `/usr/local/bin/microsocks`
2. ساخت سرویس systemd به نام `socks5-proxy` (با ریبوت سرور خودکار روشن می‌شود و در صورت کرش ری‌استارت می‌شود)
3. باز کردن پورت در `ufw` و `iptables`
4. تشخیص آی‌پی عمومی سرور و چاپ آدرس پروکسی

## دستورات مدیریتی (روی سرور دوم)

```bash
systemctl status socks5-proxy      # وضعیت سرویس
systemctl restart socks5-proxy     # ری‌استارت
systemctl stop socks5-proxy        # توقف
journalctl -u socks5-proxy -f      # مشاهده لاگ زنده
```

**دیدن یا تغییر رمز فعلی:**
```bash
grep ExecStart /etc/systemd/system/socks5-proxy.service
```
برای تغییر رمز یا پورت کافی است اسکریپت را دوباره با مقادیر جدید اجرا کنید؛ فایل سرویس بازنویسی و سرویس ری‌استارت می‌شود. (بعد از آن آدرس را در پنل سرور اول هم به‌روز کنید.)

**تست از هر سیستم دیگر:**
```bash
curl -x socks5h://teleuser:PASSWORD@SERVER2_IP:1080 https://api.ipify.org
```
خروجی باید آی‌پی سرور دوم باشد.

## عیب‌یابی

- **تست در پنل ناموفق است:** فایروال پنل ارائه‌دهنده‌ی VPS (Security Group) را هم برای پورت باز کنید؛ اسکریپت فقط فایروال داخلی سرور را باز می‌کند.
- **آی‌پی به‌جای مقدار واقعی `IP_SERVER` چاپ شد:** سرور به اینترنت برای تشخیص آی‌پی دسترسی نداشته؛ آی‌پی را دستی از پنل VPS بردارید.
- **حذف کامل پروکسی:**
  ```bash
  systemctl disable --now socks5-proxy
  rm /etc/systemd/system/socks5-proxy.service /usr/local/bin/microsocks
  systemctl daemon-reload
  ```
