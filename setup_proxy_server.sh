#!/usr/bin/env bash
# ==============================================================================
# Telegram Session Creator - 1-Click SOCKS5 Proxy Server Installer
# Turn any Linux VPS into a fast, dedicated SOCKS5 proxy for session creation
# ==============================================================================
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}====================================================================${NC}"
echo -e "${CYAN}🚀 SOCKS5 Proxy Server 1-Click Setup (for Target Server / VPS)${NC}"
echo -e "${CYAN}====================================================================${NC}"

# Detect root / sudo
if [ "$EUID" -ne 0 ]; then
    echo -e "${YELLOW}⚠️ این اسکریپت نیاز به دسترسی root دارد. اجرای خودکار با sudo...${NC}"
    exec sudo bash "$0" "$@"
fi

PORT="${1:-1080}"
PROXY_USER="${2:-teleuser}"
PROXY_PASS="${3:-}"

# Generate random password if not passed
if [ -z "$PROXY_PASS" ]; then
    PROXY_PASS=$(tr -dc A-Za-z0-9 </dev/urandom | head -c 12 || echo "telepass123")
fi

echo -e "${BLUE}📦 [1/4] Installing high-performance SOCKS5 proxy engine (microsocks)...${NC}"
if [ -f /etc/os-release ]; then
    . /etc/os-release
    if [ "$ID" = "ubuntu" ] || [ "$ID" = "debian" ]; then
        apt-get update -qq >/dev/null 2>&1
        apt-get install -y -qq build-essential curl git ufw >/dev/null 2>&1
    elif [ "$ID" = "centos" ] || [ "$ID" = "rhel" ] || [ "$ID" = "almalinux" ]; then
        yum install -y -q gcc make git curl >/dev/null 2>&1
    fi
fi

# Build microsocks if not installed
if ! command -v microsocks >/dev/null 2>&1; then
    TMP_DIR=$(mktemp -d)
    git clone --depth 1 https://github.com/rofl0r/microsocks.git "$TMP_DIR" >/dev/null 2>&1
    make -C "$TMP_DIR" >/dev/null 2>&1
    cp "$TMP_DIR/microsocks" /usr/local/bin/microsocks
    chmod +x /usr/local/bin/microsocks
    rm -rf "$TMP_DIR"
fi

echo -e "${BLUE}⚙️ [2/4] Creating background systemd service...${NC}"
SERVICE_FILE="/etc/systemd/system/socks5-proxy.service"

cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=Lightweight SOCKS5 Proxy Server for Telegram Sessions
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/microsocks -p $PORT -u $PROXY_USER -P $PROXY_PASS -b 0.0.0.0
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable socks5-proxy.service >/dev/null 2>&1
systemctl restart socks5-proxy.service

echo -e "${BLUE}🛡️ [3/4] Configuring firewall for port $PORT ...${NC}"
if command -v ufw >/dev/null 2>&1; then
    ufw allow "$PORT"/tcp >/dev/null 2>&1 || true
fi
if command -v iptables >/dev/null 2>&1; then
    iptables -I INPUT -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null || true
fi

echo -e "${BLUE}🌐 [4/4] Detecting Public IP...${NC}"
SERVER_IP=$(curl -s -4 -m 3 ifconfig.me 2>/dev/null || curl -s -4 -m 3 api.ipify.org 2>/dev/null || echo "IP_SERVER")

PROXY_URL="socks5://${PROXY_USER}:${PROXY_PASS}@${SERVER_IP}:${PORT}"

echo -e "\n${GREEN}====================================================================${NC}"
echo -e "${GREEN}🎉 پروکسی SOCKS5 با موفقیت روی این سرور نصب و فعال شد!${NC}"
echo -e "${GREEN}این سرویس همیشه در پس‌زمینه فعال است و با ریستارت سرور خودکار روشن می‌شود.${NC}"
echo -e "${GREEN}====================================================================${NC}"
echo -e "🔹 آدرس پروکسی جهت کپی در پنل وب سرور اول:"
echo -e "👉 ${CYAN}${PROXY_URL}${NC}"
echo -e "\nاطلاعات مجزا:"
echo -e "  🔹 آی‌پی:      ${YELLOW}$SERVER_IP${NC}"
echo -e "  🔹 پورت:       ${YELLOW}$PORT${NC}"
echo -e "  🔹 نام‌کاربری:  ${YELLOW}$PROXY_USER${NC}"
echo -e "  🔹 رمز عبور:   ${YELLOW}$PROXY_PASS${NC}"
echo -e "${GREEN}====================================================================${NC}"
echo -e "💡 حالا وارد پنل وب سرور اول شوید، روی «مدیریت پروکسی‌ها» بزنید و این آدرس را اضافه کنید!"
