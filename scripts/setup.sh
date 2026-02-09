#!/bin/bash
# =============================================
# Profoundd Search Engine - Server Setup Script
# Run on a fresh Ubuntu 22.04+ Droplet
# Usage: sudo bash scripts/setup.sh
# =============================================

set -e

echo "=== Profoundd Server Setup ==="
echo "Setting up your search engine on profoundd.com"
echo ""

# --- System Updates ---
echo "[1/8] Updating system..."
apt update && apt upgrade -y
apt install -y python3-pip python3-venv git nginx curl wget ufw software-properties-common

# --- Firewall ---
echo "[2/8] Configuring firewall..."
ufw allow OpenSSH
ufw allow 80
ufw allow 443
ufw --force enable

# --- Create app user ---
echo "[3/8] Creating app user..."
if ! id "profoundd" &>/dev/null; then
    useradd -m -s /bin/bash profoundd
fi

# --- App Directory ---
echo "[4/8] Setting up application..."
APP_DIR="/opt/profoundd"
mkdir -p $APP_DIR
cp -r . $APP_DIR/
chown -R profoundd:profoundd $APP_DIR

# --- Python Environment ---
echo "[5/8] Setting up Python environment..."
cd $APP_DIR
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# --- Elasticsearch ---
echo "[6/8] Installing Elasticsearch..."
if ! command -v elasticsearch &>/dev/null; then
    wget -qO - https://artifacts.elastic.co/GPG-KEY-elasticsearch | gpg --dearmor -o /usr/share/keyrings/elasticsearch-keyring.gpg
    echo "deb [signed-by=/usr/share/keyrings/elasticsearch-keyring.gpg] https://artifacts.elastic.co/packages/8.x/apt stable main" | tee /etc/apt/sources.list.d/elastic-8.x.list
    apt update
    apt install -y elasticsearch

    # Configure for low memory (1GB Droplet)
    cat > /etc/elasticsearch/jvm.options.d/memory.options << 'JVMEOF'
-Xms256m
-Xmx256m
JVMEOF

    # Disable security for local-only use
    cat >> /etc/elasticsearch/elasticsearch.yml << 'ESEOF'
xpack.security.enabled: false
xpack.security.enrollment.enabled: false
xpack.security.http.ssl.enabled: false
ESEOF

    systemctl daemon-reload
    systemctl enable elasticsearch
    systemctl start elasticsearch
fi

# --- Environment File ---
echo "[7/8] Creating environment config..."
if [ ! -f $APP_DIR/.env ]; then
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    cat > $APP_DIR/.env << ENVEOF
FLASK_APP=profoundd.app
FLASK_ENV=production
SECRET_KEY=$SECRET
DOMAIN=profoundd.com
ELASTICSEARCH_URL=http://localhost:9200
DATABASE_URL=sqlite:///data/profoundd.db
ADMIN_USERNAME=admin
ADMIN_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")
CRAWL_INTERVAL_MINUTES=60
ENVEOF
    chown profoundd:profoundd $APP_DIR/.env
    chmod 600 $APP_DIR/.env
    echo "  Generated .env file. Check /opt/profoundd/.env for admin credentials!"
fi

# --- Systemd Service ---
echo "[8/8] Setting up services..."
cat > /etc/systemd/system/profoundd.service << 'SVCEOF'
[Unit]
Description=Profoundd Search Engine
After=network.target elasticsearch.service

[Service]
User=profoundd
Group=profoundd
WorkingDirectory=/opt/profoundd
Environment="PATH=/opt/profoundd/venv/bin:/usr/bin"
ExecStart=/opt/profoundd/venv/bin/gunicorn -w 2 -b 127.0.0.1:8000 --timeout 120 profoundd.app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF

# --- Crawler Timer ---
cat > /etc/systemd/system/profoundd-crawler.service << 'CRAWLEOF'
[Unit]
Description=Profoundd Crawler
After=network.target elasticsearch.service

[Service]
User=profoundd
Group=profoundd
WorkingDirectory=/opt/profoundd
Environment="PATH=/opt/profoundd/venv/bin:/usr/bin"
ExecStart=/opt/profoundd/venv/bin/python -m profoundd.crawler.feed_crawler
Type=oneshot

[Install]
WantedBy=multi-user.target
CRAWLEOF

cat > /etc/systemd/system/profoundd-crawler.timer << 'TIMEREOF'
[Unit]
Description=Run Profoundd Crawler every hour

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
TIMEREOF

# --- Nginx ---
cat > /etc/nginx/sites-available/profoundd << 'NGXEOF'
server {
    listen 80;
    server_name profoundd.com www.profoundd.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }

    location /static/ {
        alias /opt/profoundd/profoundd/frontend/static/;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }
}
NGXEOF

ln -sf /etc/nginx/sites-available/profoundd /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

# --- Enable & Start ---
systemctl daemon-reload
systemctl enable profoundd profoundd-crawler.timer
systemctl start profoundd profoundd-crawler.timer

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Services:"
echo "  - App:           http://profoundd.com (port 80)"
echo "  - Elasticsearch: http://localhost:9200"
echo "  - Crawler:       runs hourly via systemd timer"
echo ""
echo "Next steps:"
echo "  1. Point profoundd.com DNS A record to this server's IP"
echo "  2. Install SSL: sudo certbot --nginx -d profoundd.com -d www.profoundd.com"
echo "     (install certbot: sudo apt install certbot python3-certbot-nginx)"
echo "  3. Check admin credentials: cat /opt/profoundd/.env"
echo "  4. Log into admin: https://profoundd.com/admin"
echo "  5. Seed sources and trigger first crawl from admin panel"
echo ""
echo "Useful commands:"
echo "  systemctl status profoundd          # Check app status"
echo "  journalctl -u profoundd -f          # View app logs"
echo "  systemctl status profoundd-crawler  # Check crawler"
echo "  systemctl restart profoundd         # Restart app"
echo ""
