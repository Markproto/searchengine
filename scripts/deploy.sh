#!/bin/bash
# Deploy latest changes, clean up old sources, seed & sync ratings
cd /opt/profoundd
sudo -u profoundd git pull origin claude/custom-news-search-engine-YWN94
sudo -u profoundd /opt/profoundd/venv/bin/python3 /opt/profoundd/scripts/cleanup_sources.py
sudo -u profoundd /opt/profoundd/venv/bin/python3 /opt/profoundd/scripts/seed_sources.py
sudo systemctl restart profoundd
echo "Done. Sources seeded and credibility ratings synced. Run Crawl from Admin if needed."
