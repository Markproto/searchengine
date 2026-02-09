"""
Feed sources organized by domain/category.
Each source has: name, url, category, default_credibility (1-10), feed_type (rss/atom/api).
Expand this list as needed via the admin panel.
"""

NEWS_SOURCES = [
    # --- Major Wire Services ---
    {"name": "Reuters", "url": "https://feeds.reuters.com/reuters/topNews", "category": "news", "credibility": 9, "feed_type": "rss"},
    {"name": "Associated Press", "url": "https://rsshub.app/apnews/topics/apf-topnews", "category": "news", "credibility": 9, "feed_type": "rss"},

    # --- US News ---
    {"name": "NPR News", "url": "https://feeds.npr.org/1001/rss.xml", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "PBS NewsHour", "url": "https://www.pbs.org/newshour/feeds/rss/headlines", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "BBC News", "url": "http://feeds.bbci.co.uk/news/rss.xml", "category": "news", "credibility": 9, "feed_type": "rss"},
    {"name": "The Guardian", "url": "https://www.theguardian.com/world/rss", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml", "category": "news", "credibility": 7, "feed_type": "rss"},

    # --- Business News ---
    {"name": "CNBC", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", "category": "news", "credibility": 7, "feed_type": "rss"},
    {"name": "Bloomberg", "url": "https://feeds.bloomberg.com/markets/news.rss", "category": "news", "credibility": 8, "feed_type": "rss"},
]

MEDICAL_SOURCES = [
    # --- PubMed / NIH ---
    {"name": "PubMed Trending", "url": "https://pubmed.ncbi.nlm.nih.gov/trending/rss/", "category": "medical", "credibility": 10, "feed_type": "rss"},
    {"name": "NIH News", "url": "https://www.nih.gov/news-events/news-releases/feed", "category": "medical", "credibility": 10, "feed_type": "rss"},
    {"name": "WHO News", "url": "https://www.who.int/rss-feeds/news-english.xml", "category": "medical", "credibility": 10, "feed_type": "rss"},
    {"name": "CDC Newsroom", "url": "https://tools.cdc.gov/api/v2/resources/media/rss/132608.rss", "category": "medical", "credibility": 10, "feed_type": "rss"},
    {"name": "Medical News Today", "url": "https://www.medicalnewstoday.com/newsfeeds/rss", "category": "medical", "credibility": 7, "feed_type": "rss"},
    {"name": "The Lancet", "url": "https://www.thelancet.com/rssfeed/lancet_current.xml", "category": "medical", "credibility": 10, "feed_type": "rss"},
    {"name": "NEJM", "url": "https://www.nejm.org/action/showFeed?jc=nejm&type=etoc&feed=rss", "category": "medical", "credibility": 10, "feed_type": "rss"},
    {"name": "WebMD Health", "url": "https://rssfeeds.webmd.com/rss/rss.aspx?RSSSource=RSS_PUBLIC", "category": "medical", "credibility": 6, "feed_type": "rss"},
]

LEGAL_SOURCES = [
    # --- Legal News & Case Law ---
    {"name": "SCOTUSblog", "url": "https://www.scotusblog.com/feed/", "category": "legal", "credibility": 9, "feed_type": "rss"},
    {"name": "Law.com", "url": "https://feeds.law.com/law/headlines", "category": "legal", "credibility": 7, "feed_type": "rss"},
    {"name": "Jurist", "url": "https://www.jurist.org/news/feed/", "category": "legal", "credibility": 8, "feed_type": "rss"},
    {"name": "Cornell LII", "url": "https://www.law.cornell.edu/lii/rss.xml", "category": "legal", "credibility": 9, "feed_type": "rss"},
    {"name": "Courthouse News", "url": "https://www.courthousenews.com/feed/", "category": "legal", "credibility": 7, "feed_type": "rss"},
    {"name": "Reuters Legal", "url": "https://www.reuters.com/legal/rss", "category": "legal", "credibility": 8, "feed_type": "rss"},
]

TECH_SOURCES = [
    # --- Technology ---
    {"name": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/index", "category": "tech", "credibility": 8, "feed_type": "rss"},
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/", "category": "tech", "credibility": 7, "feed_type": "rss"},
    {"name": "Hacker News", "url": "https://hnrss.org/frontpage", "category": "tech", "credibility": 7, "feed_type": "rss"},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml", "category": "tech", "credibility": 7, "feed_type": "rss"},
    {"name": "Wired", "url": "https://www.wired.com/feed/rss", "category": "tech", "credibility": 7, "feed_type": "rss"},
    {"name": "MIT Technology Review", "url": "https://www.technologyreview.com/feed/", "category": "tech", "credibility": 9, "feed_type": "rss"},
    {"name": "ZDNet", "url": "https://www.zdnet.com/news/rss.xml", "category": "tech", "credibility": 7, "feed_type": "rss"},
]

FINANCE_SOURCES = [
    # --- Finance ---
    {"name": "Financial Times", "url": "https://www.ft.com/rss/home", "category": "finance", "credibility": 9, "feed_type": "rss"},
    {"name": "MarketWatch", "url": "https://feeds.marketwatch.com/marketwatch/topstories/", "category": "finance", "credibility": 7, "feed_type": "rss"},
    {"name": "Yahoo Finance", "url": "https://finance.yahoo.com/news/rssindex", "category": "finance", "credibility": 6, "feed_type": "rss"},
    {"name": "Investopedia", "url": "https://www.investopedia.com/feedbuilder/feed/getfeed?feedName=rss_headline", "category": "finance", "credibility": 7, "feed_type": "rss"},
    {"name": "The Economist", "url": "https://www.economist.com/finance-and-economics/rss.xml", "category": "finance", "credibility": 9, "feed_type": "rss"},
]

SCIENCE_SOURCES = [
    # --- Science ---
    {"name": "Nature", "url": "https://www.nature.com/nature.rss", "category": "science", "credibility": 10, "feed_type": "rss"},
    {"name": "Science Magazine", "url": "https://www.science.org/rss/news_current.xml", "category": "science", "credibility": 10, "feed_type": "rss"},
    {"name": "Scientific American", "url": "http://rss.sciam.com/ScientificAmerican-Global", "category": "science", "credibility": 9, "feed_type": "rss"},
    {"name": "NASA", "url": "https://www.nasa.gov/rss/dyn/breaking_news.rss", "category": "science", "credibility": 10, "feed_type": "rss"},
    {"name": "New Scientist", "url": "https://www.newscientist.com/feed/home/", "category": "science", "credibility": 8, "feed_type": "rss"},
    {"name": "Phys.org", "url": "https://phys.org/rss-feed/", "category": "science", "credibility": 7, "feed_type": "rss"},
]

ALL_SOURCES = NEWS_SOURCES + MEDICAL_SOURCES + LEGAL_SOURCES + TECH_SOURCES + FINANCE_SOURCES + SCIENCE_SOURCES

CATEGORIES = {
    "news": {"label": "News", "description": "General & World News", "icon": "newspaper"},
    "medical": {"label": "Medical", "description": "Health, Medicine & Research", "icon": "heartbeat"},
    "legal": {"label": "Legal", "description": "Law, Courts & Regulations", "icon": "gavel"},
    "tech": {"label": "Technology", "description": "Tech, AI & Digital", "icon": "microchip"},
    "finance": {"label": "Finance", "description": "Markets, Economy & Business", "icon": "chart-line"},
    "science": {"label": "Science", "description": "Research, Space & Discovery", "icon": "flask"},
}
