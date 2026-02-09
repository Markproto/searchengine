#!/bin/bash
# Deploy latest changes and clean up old sources
cd /opt/profoundd
sudo -u profoundd git pull origin claude/custom-news-search-engine-YWN94
sudo -u profoundd /opt/profoundd/venv/bin/python3 /opt/profoundd/scripts/cleanup_sources.py
sudo systemctl restart profoundd
echo "Done. Now go to Admin > Seed Sources > Run Crawl"
