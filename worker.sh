#!/usr/bin/env bash
# ==============================================================================
# Telegram Session Creator - 1-Click Worker for Target Server (Server 2)
# ==============================================================================
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

MASTER_URL="$1"

if [ -z "$MASTER_URL" ]; then
    echo -e "${RED}خطا: آدرس سرور اول را وارد نکرده‌اید!${NC}"
    echo -e "نحوه استفاده:"
    echo -e "  ${YELLOW}bash worker.sh http://MASTER_SERVER_IP:8000${NC}"
    exit 1
fi

echo -e "${CYAN}====================================================================${NC}"
echo -e "${CYAN}🚀 Telegram Session Worker (Target Server Migration)${NC}"
echo -e "${CYAN}Connecting to Master Server: $MASTER_URL${NC}"
echo -e "${CYAN}====================================================================${NC}"

# 1. Install prerequisites if needed
if ! command -v python3 >/dev/null 2>&1; then
    echo -e "${BLUE}📦 Installing Python3 & venv...${NC}"
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        if [ "$ID" = "ubuntu" ] || [ "$ID" = "debian" ]; then
            sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-pip python3-venv
        elif [ "$ID" = "centos" ] || [ "$ID" = "rhel" ] || [ "$ID" = "almalinux" ]; then
            sudo yum install -y -q python3 python3-pip
        fi
    fi
fi

# 2. Virtual Environment
if [ ! -d "venv" ]; then
    echo -e "${BLUE}🐍 Creating virtual environment...${NC}"
    python3 -m venv venv
fi

source venv/bin/activate
pip install -r requirements.txt -q

# 3. Check .env
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}فایل .env یافت نشد. دریافت مقادیر API تلگرام:${NC}"
    read -p "🔹 Enter Telegram API_ID [12345678]: " user_api_id
    user_api_id=${user_api_id:-12345678}

    read -p "🔹 Enter Telegram API_HASH: " user_api_hash
    user_api_hash=${user_api_hash:-your_api_hash}

    cat <<EOF > .env
API_ID=$user_api_id
API_HASH=$user_api_hash
SERVER_URL=$MASTER_URL
EOF
fi

# Ask for 2FA password if any
echo ""
read -p "🔹 اگر اکانت‌ها رمز دو مرحله‌ای (2FA) دارند وارد کنید (در غیر این صورت اینتر بزنید): " two_fa_pwd
read -p "🔹 تاخیر ایمن بین هر اکانت به ثانیه [پیش‌فرض 15]: " delay_sec
delay_sec=${delay_sec:-15}

echo -e "${GREEN}====================================================================${NC}"
echo -e "${GREEN}🚀 شروع فرآیند ساخت سشن‌ها با آی‌پی این سرور...${NC}"
echo -e "${GREEN}====================================================================${NC}"

if [ -n "$two_fa_pwd" ]; then
    python run_worker.py --server-url "$MASTER_URL" --all --password "$two_fa_pwd" --delay "$delay_sec"
else
    python run_worker.py --server-url "$MASTER_URL" --all --delay "$delay_sec"
fi

echo -e "\n${GREEN}====================================================================${NC}"
echo -e "${GREEN}🎉 تمام سشن‌های جدید با موفقیت با آی‌پی این سرور ساخته شدند!${NC}"
echo -e "📂 مسیر سشن‌های نهایی: ${CYAN}$(pwd)/storage/target_sessions/${NC}"
echo -e "${GREEN}====================================================================${NC}"
