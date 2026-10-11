#!/usr/bin/env bash
# ==============================================================================
# Telegram Session Creator - Service Manager
# ==============================================================================
SERVICE_NAME="session-creator.service"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

case "$1" in
    start)
        echo -e "${GREEN}Starting $SERVICE_NAME...${NC}"
        sudo systemctl start $SERVICE_NAME
        sudo systemctl status $SERVICE_NAME --no-pager
        ;;
    stop)
        echo -e "${YELLOW}Stopping $SERVICE_NAME...${NC}"
        sudo systemctl stop $SERVICE_NAME
        ;;
    restart)
        echo -e "${BLUE}Restarting $SERVICE_NAME...${NC}"
        sudo systemctl restart $SERVICE_NAME
        sudo systemctl status $SERVICE_NAME --no-pager
        ;;
    status)
        sudo systemctl status $SERVICE_NAME
        ;;
    logs)
        echo -e "${CYAN}Streaming live logs (Ctrl+C to exit)...${NC}"
        sudo journalctl -u $SERVICE_NAME -f -n 50
        ;;
    uninstall)
        read -p "Are you sure you want to disable and remove the service? [y/N]: " confirm
        if [[ "$confirm" =~ ^[yY]$ ]]; then
            sudo systemctl stop $SERVICE_NAME 2>/dev/null || true
            sudo systemctl disable $SERVICE_NAME 2>/dev/null || true
            sudo rm -f /etc/systemd/system/$SERVICE_NAME
            sudo systemctl daemon-reload
            echo -e "${GREEN}Service removed successfully.${NC}"
        fi
        ;;
    *)
        echo -e "${CYAN}Telegram Session Creator Management Menu${NC}"
        echo "Usage: ./manage.sh {start|stop|restart|status|logs|uninstall}"
        echo ""
        echo "Commands:"
        echo "  start     - Start background service"
        echo "  stop      - Stop background service"
        echo "  restart   - Restart background service"
        echo "  status    - Check current running status"
        echo "  logs      - View real-time application logs"
        echo "  uninstall - Remove systemd background service"
        exit 1
        ;;
esac
