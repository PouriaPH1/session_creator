#!/usr/bin/env bash
# ==============================================================================
# Telegram Session Creator & Manager - Automated 1-Click Installer
# ==============================================================================
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}====================================================================${NC}"
echo -e "${CYAN}🚀 Telegram Session Creator & Manager - Auto Installer${NC}"
echo -e "${CYAN}====================================================================${NC}"

# Detect Linux Distribution
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
fi

echo -e "${BLUE}📦 [1/6] Installing system packages and dependencies...${NC}"
if [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-pip python3-venv git curl ufw
elif [ "$OS" = "centos" ] || [ "$OS" = "rhel" ] || [ "$OS" = "almalinux" ]; then
    sudo yum install -y -q python3 python3-pip git curl
else
    echo -e "${YELLOW}⚠️ Unknown OS, assuming python3 & venv are installed.${NC}"
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

echo -e "${BLUE}🐍 [2/6] Setting up Python virtual environment...${NC}"
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo -e "${BLUE}⚙️ [3/6] Configuring environment settings (.env)...${NC}"
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}فایل .env یافت نشد. لطفاً مشخصات تلگرام خود را وارد کنید:${NC}"
    read -p "🔹 Enter your Telegram API_ID [12345678]: " user_api_id
    user_api_id=${user_api_id:-12345678}

    read -p "🔹 Enter your Telegram API_HASH [your_api_hash]: " user_api_hash
    user_api_hash=${user_api_hash:-your_api_hash}

    read -p "🔹 Web Panel Port [8000]: " user_port
    user_port=${user_port:-8000}

    cat <<EOF > .env
API_ID=$user_api_id
API_HASH=$user_api_hash
SERVER_HOST=0.0.0.0
SERVER_PORT=$user_port
SERVER_URL=http://127.0.0.1:$user_port
EOF
    echo -e "${GREEN}✅ .env file created successfully.${NC}"
else
    echo -e "${GREEN}✅ Existing .env file found.${NC}"
fi

# Read port from .env
PORT=$(grep -E "^SERVER_PORT=" .env | cut -d '=' -f 2 || echo "8000")
PORT=${PORT:-8000}

echo -e "${BLUE}🛡️ [4/6] Configuring firewall (Opening port $PORT)...${NC}"
if command -v ufw >/dev/null 2>&1; then
    sudo ufw allow "$PORT"/tcp >/dev/null 2>&1 || true
fi

echo -e "${BLUE}🔄 [5/6] Creating automatic background service (systemd)...${NC}"
CURRENT_USER=$(whoami)
SERVICE_FILE="/etc/systemd/system/session-creator.service"

sudo bash -c "cat <<EOF > $SERVICE_FILE
[Unit]
Description=Telegram Session Creator and Admin Panel
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/run_server.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF"

sudo systemctl daemon-reload
sudo systemctl enable session-creator.service >/dev/null 2>&1
sudo systemctl restart session-creator.service

# Detect Public IP
SERVER_IP=$(curl -s -4 ifconfig.me || curl -s -4 api.ipify.org || echo "YOUR_SERVER_IP")

echo -e "${BLUE}✨ [6/6] Creating management shortcut (manage.sh)...${NC}"
chmod +x manage.sh 2>/dev/null || true

echo -e "\n${GREEN}====================================================================${NC}"
echo -e "${GREEN}🎉 نصب و راه‌اندازی با موفقیت انجام شد!${NC}"
echo -e "${GREEN}سرویس هم‌اکنون در پس‌زمینه در حال اجراست و با ریستارت سرور خودکار بالا می‌آید.${NC}"
echo -e "${GREEN}====================================================================${NC}"
echo -e "🌐 آدرس پنل ادمین شما: ${CYAN}http://$SERVER_IP:$PORT${NC}"
echo -e "📘 مستندات API سواگر: ${CYAN}http://$SERVER_IP:$PORT/docs${NC}"
echo -e ""
echo -e "دستورات مدیریتی با اسکریپت ./manage.sh:"
echo -e "  🔹 مشاهده لاگ‌های زنده:    ${YELLOW}./manage.sh logs${NC}"
echo -e "  🔹 ریستارت برنامه:         ${YELLOW}./manage.sh restart${NC}"
echo -e "  🔹 متوقف کردن برنامه:      ${YELLOW}./manage.sh stop${NC}"
echo -e "  🔹 وضعیت سرویس:            ${YELLOW}./manage.sh status${NC}"
echo -e "${GREEN}====================================================================${NC}"
