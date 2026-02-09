#!/bin/bash
# Install Let's Encrypt SSL for profoundd.com
# Run after DNS is pointed to server

set -e

echo "Installing Certbot..."
apt install -y certbot python3-certbot-nginx

echo "Requesting SSL certificate..."
certbot --nginx -d profoundd.com -d www.profoundd.com --non-interactive --agree-tos --email admin@profoundd.com

echo "Setting up auto-renewal..."
systemctl enable certbot.timer
systemctl start certbot.timer

echo "SSL installed! Site available at https://profoundd.com"
