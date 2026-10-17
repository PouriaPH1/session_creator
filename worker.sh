#!/usr/bin/env bash
# ==============================================================================
# Telegram Session Creator - Smart 1-Click Worker for Target Server (Server 2)
# ==============================================================================
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}====================================================================${NC}"
echo -e "${CYAN}🚀 Telegram Session Worker (Target Server Migration)${NC}"
echo -e "${CYAN}====================================================================${NC}"

# 1. Normalize and Auto-Detect Master Server URL
RAW_INPUT="$1"

# If not provided in CLI, check if already saved in .env
if [ -z "$RAW_INPUT" ] && [ -f ".env" ]; then
    SAVED_URL=$(grep -E "^SERVER_URL=" .env | cut -d '=' -f 2- | tr -d '\r' | tr -d ' ' || true)
    if [ -n "$SAVED_URL" ] && [ "$SAVED_URL" != "http://127.0.0.1:8000" ]; then
        RAW_INPUT="$SAVED_URL"
        echo -e "${BLUE}💡 استفاده از آدرس ذخیره‌شده قبلی: ${YELLOW}$RAW_INPUT${NC}"
    fi
fi

# If still empty, prompt the user
if [ -z "$RAW_INPUT" ]; then
    echo -e "${YELLOW}آدرس سرور اول را وارد کنید (می‌توانید فقط آی‌پی بنویسید، مثلاً 194.5.210.12):${NC}"
    read -p "🔹 Master Server IP / URL: " RAW_INPUT
fi

if [ -z "$RAW_INPUT" ]; then
    echo -e "${RED}خطا: آدرس سرور اول مشخص نشده است!${NC}"
    echo -e "نحوه استفاده:"
    echo -e "  ${YELLOW}bash worker.sh 194.5.210.12${NC}"
    echo -e "  ${YELLOW}bash worker.sh http://194.5.210.12:8000${NC}"
    exit 1
fi

# Clean up input (remove spaces and trailing slashes)
RAW_INPUT=$(echo "$RAW_INPUT" | tr -d ' ' | sed 's:/*$::')

# Add http:// if scheme is missing
if [[ ! "$RAW_INPUT" =~ ^https?:// ]]; then
    CLEAN_HOST="$RAW_INPUT"
    PROTOCOL="http://"
else
    PROTOCOL=$(echo "$RAW_INPUT" | grep -oE '^https?://')
    CLEAN_HOST=$(echo "$RAW_INPUT" | sed -E 's|^https?://||')
fi

# Determine port and resolve Master URL
if [[ "$CLEAN_HOST" =~ :[0-9]+$ ]]; then
    MASTER_URL="${PROTOCOL}${CLEAN_HOST}"
else
    DEFAULT_PORT=8000
    CANDIDATE_URL="${PROTOCOL}${CLEAN_HOST}:${DEFAULT_PORT}"
    
    echo -e "${BLUE}🔍 در حال بررسی اتصال به پورت پیش‌فرض ($DEFAULT_PORT)...${NC}"
    if curl -s -f -m 3 "$CANDIDATE_URL/health" >/dev/null 2>&1; then
        MASTER_URL="$CANDIDATE_URL"
    else
        # Try fallback ports in case Server 1 auto-shifted to another port
        FOUND_PORT=""
        for PORT_TRY in 8001 8002 8080 80; do
            if curl -s -f -m 2 "${PROTOCOL}${CLEAN_HOST}:${PORT_TRY}/health" >/dev/null 2>&1; then
                FOUND_PORT="$PORT_TRY"
                break
            fi
        done
        
        if [ -n "$FOUND_PORT" ]; then
            echo -e "${GREEN}🔄 سرور اول روی پورت اختصاصی $FOUND_PORT پیدا شد!${NC}"
            MASTER_URL="${PROTOCOL}${CLEAN_HOST}:${FOUND_PORT}"
        else
            MASTER_URL="$CANDIDATE_URL"
        fi
    fi
fi

# 2. Pre-flight Connectivity Check
echo -e "${BLUE}📡 بررسی نهایی ارتباط با سرور اول: $MASTER_URL ...${NC}"
if curl -s -f -m 4 "$MASTER_URL/health" >/dev/null 2>&1; then
    echo -e "${GREEN}✅ ارتباط با سرور اول برقرار است.${NC}"
else
    echo -e "${YELLOW}⚠️ هشدار: سرور اول در آدرس $MASTER_URL پاسخ نداد.${NC}"
    echo -e "${YELLOW}موارد زیر را در سرور اول بررسی کنید:${NC}"
    echo -e "  1. سرویس روشن باشد (./manage.sh status)"
    echo -e "  2. پورت در فایروال سرور اول باز باشد (sudo ufw allow 8000/tcp)"
    read -p "آیا با این حال ادامه می‌دهید؟ (y/N): " continue_anyway
    if [[ ! "$continue_anyway" =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 3. Detect and display Server 2's own Public IP
TARGET_IP=$(curl -s -4 -m 3 ifconfig.me 2>/dev/null || curl -s -4 -m 3 api.ipify.org 2>/dev/null || echo "نامشخص")
echo -e "🌐 ${GREEN}آی‌پی این سرور (مقصد): ${CYAN}$TARGET_IP${NC}"
echo -e "ℹ️  ${CYAN}سشن‌های جدید تلگرام با آی‌پی $TARGET_IP ساخته و امضا می‌شوند.${NC}"

# 4. Install system packages if needed
if ! command -v python3 >/dev/null 2>&1; then
    echo -e "${BLUE}📦 Installing Python3 & venv...${NC}"
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        if [ "$ID" = "ubuntu" ] || [ "$ID" = "debian" ]; then
            sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-pip python3-venv curl
        elif [ "$ID" = "centos" ] || [ "$ID" = "rhel" ] || [ "$ID" = "almalinux" ]; then
            sudo yum install -y -q python3 python3-pip curl
        fi
    fi
fi

# 5. Virtual Environment
if [ ! -d "venv" ]; then
    echo -e "${BLUE}🐍 Creating Python virtual environment...${NC}"
    python3 -m venv venv
fi

source venv/bin/activate
pip install -r requirements.txt -q

# 6. Auto-Fetch Telegram API Credentials from Master Server (Zero-Prompt)
if [ ! -f ".env" ]; then
    echo -e "${BLUE}⚙️ دریافت خودکار تنظیمات API تلگرام از سرور اول...${NC}"
    REMOTE_CFG=$(curl -s -m 4 "$MASTER_URL/api/config/telegram" 2>/dev/null || true)
    REMOTE_API_ID=$(echo "$REMOTE_CFG" | grep -oE '"api_id":[0-9]+' | cut -d':' -f2 || true)
    REMOTE_API_HASH=$(echo "$REMOTE_CFG" | grep -oE '"api_hash":"[^"]+' | cut -d'"' -f4 || true)

    if [ -n "$REMOTE_API_ID" ] && [ "$REMOTE_API_ID" != "12345678" ] && [ -n "$REMOTE_API_HASH" ]; then
        echo -e "${GREEN}✅ مشخصات تلگرام (API_ID و API_HASH) خودکار از سرور اول کپی شد!${NC}"
        user_api_id="$REMOTE_API_ID"
        user_api_hash="$REMOTE_API_HASH"
    else
        echo -e "${YELLOW}مشخصات API تلگرام یافت نشد؛ لطفاً دستی وارد کنید:${NC}"
        read -p "🔹 Enter Telegram API_ID [12345678]: " user_api_id
        user_api_id=${user_api_id:-12345678}

        read -p "🔹 Enter Telegram API_HASH: " user_api_hash
        user_api_hash=${user_api_hash:-your_api_hash}
    fi

    cat <<EOF > .env
API_ID=$user_api_id
API_HASH=$user_api_hash
SERVER_URL=$MASTER_URL
EOF
else
    # Save/Update Master URL in existing .env
    if grep -q "^SERVER_URL=" .env; then
        sed -i "s|^SERVER_URL=.*|SERVER_URL=$MASTER_URL|" .env
    else
        echo "SERVER_URL=$MASTER_URL" >> .env
    fi
fi

# 7. Ask for 2FA password and batch delay
echo ""
read -p "🔹 اگر اکانت‌ها رمز دو مرحله‌ای (2FA) دارند وارد کنید (در غیر این صورت اینتر بزنید): " two_fa_pwd
read -p "🔹 تاخیر ایمن بین هر اکانت به ثانیه [پیش‌فرض 15]: " delay_sec
delay_sec=${delay_sec:-15}

echo -e "${GREEN}====================================================================${NC}"
echo -e "${GREEN}🚀 شروع فرآیند ساخت سشن‌ها با آی‌پی سرور دوم ($TARGET_IP)...${NC}"
echo -e "${GREEN}====================================================================${NC}"

if [ -n "$two_fa_pwd" ]; then
    python run_worker.py --server-url "$MASTER_URL" --all --password "$two_fa_pwd" --delay "$delay_sec"
else
    python run_worker.py --server-url "$MASTER_URL" --all --delay "$delay_sec"
fi

echo -e "\n${GREEN}====================================================================${NC}"
echo -e "${GREEN}🎉 تمام سشن‌های جدید با موفقیت با آی‌پی این سرور ($TARGET_IP) ساخته شدند!${NC}"
echo -e "📂 مسیر سشن‌های نهایی: ${CYAN}$(pwd)/storage/target_sessions/${NC}"
echo -e "${GREEN}====================================================================${NC}"
