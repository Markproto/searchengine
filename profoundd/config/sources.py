"""
Feed sources organized by domain/category.
Each source has: name, url, category, default_credibility (1-10), feed_type (rss/atom/api).
Expand this list as needed via the admin panel.
"""

NEWS_SOURCES = [
    # --- Major Wire Services ---
    {"name": "Reuters", "url": "https://feeds.reuters.com/reuters/topNews", "category": "news", "credibility": 9, "feed_type": "rss"},
    {"name": "Associated Press", "url": "https://rsshub.app/apnews/topics/apf-topnews", "category": "news", "credibility": 9, "feed_type": "rss"},
    {"name": "AFP", "url": "https://www.afp.com/en/feed", "category": "news", "credibility": 9, "feed_type": "rss"},

    # --- US News ---
    {"name": "NPR News", "url": "https://feeds.npr.org/1001/rss.xml", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "PBS NewsHour", "url": "https://www.pbs.org/newshour/feeds/rss/headlines", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "ABC News", "url": "https://abcnews.go.com/abcnews/topstories", "category": "news", "credibility": 7, "feed_type": "rss"},
    {"name": "CBS News", "url": "https://www.cbsnews.com/latest/rss/main", "category": "news", "credibility": 7, "feed_type": "rss"},

    # --- International ---
    {"name": "BBC News", "url": "http://feeds.bbci.co.uk/news/rss.xml", "category": "news", "credibility": 9, "feed_type": "rss"},
    {"name": "BBC World", "url": "http://feeds.bbci.co.uk/news/world/rss.xml", "category": "news", "credibility": 9, "feed_type": "rss"},
    {"name": "The Guardian", "url": "https://www.theguardian.com/world/rss", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml", "category": "news", "credibility": 7, "feed_type": "rss"},
    {"name": "DW News", "url": "https://rss.dw.com/rdf/rss-en-all", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "France24", "url": "https://www.france24.com/en/rss", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "Japan Times", "url": "https://www.japantimes.co.jp/feed/", "category": "news", "credibility": 7, "feed_type": "rss"},

    # --- Business News ---
    {"name": "CNBC", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", "category": "news", "credibility": 7, "feed_type": "rss"},
    {"name": "Bloomberg", "url": "https://feeds.bloomberg.com/markets/news.rss", "category": "news", "credibility": 8, "feed_type": "rss"},
]

MEDICAL_SOURCES = [
    # --- PubMed / NIH ---
    {"name": "PubMed Trending", "url": "https://pubmed.ncbi.nlm.nih.gov/trending/rss/", "category": "medical", "credibility": 10, "feed_type": "rss"},
    {"name": "NIH News", "url": "https://www.nih.gov/news-events/news-releases/feed", "category": "medical", "credibility": 10, "feed_type": "rss"},
    {"name": "NIH Research", "url": "https://www.nih.gov/news-events/nih-research-matters/feed", "category": "medical", "credibility": 10, "feed_type": "rss"},
    {"name": "WHO News", "url": "https://www.who.int/rss-feeds/news-english.xml", "category": "medical", "credibility": 10, "feed_type": "rss"},
    {"name": "WHO Disease Outbreaks", "url": "https://www.who.int/rss-feeds/disease-outbreak-news.xml", "category": "medical", "credibility": 10, "feed_type": "rss"},
    {"name": "CDC Newsroom", "url": "https://tools.cdc.gov/api/v2/resources/media/rss/132608.rss", "category": "medical", "credibility": 10, "feed_type": "rss"},
    {"name": "Medical News Today", "url": "https://www.medicalnewstoday.com/newsfeeds/rss", "category": "medical", "credibility": 7, "feed_type": "rss"},
    {"name": "The Lancet", "url": "https://www.thelancet.com/rssfeed/lancet_current.xml", "category": "medical", "credibility": 10, "feed_type": "rss"},
    {"name": "NEJM", "url": "https://www.nejm.org/action/showFeed?jc=nejm&type=etoc&feed=rss", "category": "medical", "credibility": 10, "feed_type": "rss"},
    {"name": "BMJ", "url": "https://www.bmj.com/rss/recent.xml", "category": "medical", "credibility": 10, "feed_type": "rss"},
    {"name": "JAMA Network", "url": "https://jamanetwork.com/rss/site_3/67.xml", "category": "medical", "credibility": 10, "feed_type": "rss"},
    {"name": "WebMD Health", "url": "https://rssfeeds.webmd.com/rss/rss.aspx?RSSSource=RSS_PUBLIC", "category": "medical", "credibility": 6, "feed_type": "rss"},
    {"name": "Stat News", "url": "https://www.statnews.com/feed/", "category": "medical", "credibility": 8, "feed_type": "rss"},
    {"name": "FDA News", "url": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/fda-newsroom/rss.xml", "category": "medical", "credibility": 10, "feed_type": "rss"},
]

LEGAL_SOURCES = [
    # --- Legal News & Case Law ---
    {"name": "SCOTUSblog", "url": "https://www.scotusblog.com/feed/", "category": "legal", "credibility": 9, "feed_type": "rss"},
    {"name": "Law.com", "url": "https://feeds.law.com/law/headlines", "category": "legal", "credibility": 7, "feed_type": "rss"},
    {"name": "Jurist", "url": "https://www.jurist.org/news/feed/", "category": "legal", "credibility": 8, "feed_type": "rss"},
    {"name": "Cornell LII", "url": "https://www.law.cornell.edu/lii/rss.xml", "category": "legal", "credibility": 9, "feed_type": "rss"},
    {"name": "Courthouse News", "url": "https://www.courthousenews.com/feed/", "category": "legal", "credibility": 7, "feed_type": "rss"},
    {"name": "Reuters Legal", "url": "https://www.reuters.com/legal/rss", "category": "legal", "credibility": 8, "feed_type": "rss"},
    {"name": "Lawfare", "url": "https://www.lawfaremedia.org/feed", "category": "legal", "credibility": 8, "feed_type": "rss"},
    {"name": "Above the Law", "url": "https://abovethelaw.com/feed/", "category": "legal", "credibility": 6, "feed_type": "rss"},
    {"name": "ABA Journal", "url": "https://www.abajournal.com/feed", "category": "legal", "credibility": 8, "feed_type": "rss"},
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
    {"name": "The Register", "url": "https://www.theregister.com/headlines.atom", "category": "tech", "credibility": 7, "feed_type": "atom"},
    {"name": "Engadget", "url": "https://www.engadget.com/rss.xml", "category": "tech", "credibility": 7, "feed_type": "rss"},
    {"name": "IEEE Spectrum", "url": "https://spectrum.ieee.org/feeds/feed.rss", "category": "tech", "credibility": 9, "feed_type": "rss"},
]

FINANCE_SOURCES = [
    # --- Finance ---
    {"name": "Financial Times", "url": "https://www.ft.com/rss/home", "category": "finance", "credibility": 9, "feed_type": "rss"},
    {"name": "MarketWatch", "url": "https://feeds.marketwatch.com/marketwatch/topstories/", "category": "finance", "credibility": 7, "feed_type": "rss"},
    {"name": "Yahoo Finance", "url": "https://finance.yahoo.com/news/rssindex", "category": "finance", "credibility": 6, "feed_type": "rss"},
    {"name": "Investopedia", "url": "https://www.investopedia.com/feedbuilder/feed/getfeed?feedName=rss_headline", "category": "finance", "credibility": 7, "feed_type": "rss"},
    {"name": "The Economist", "url": "https://www.economist.com/finance-and-economics/rss.xml", "category": "finance", "credibility": 9, "feed_type": "rss"},
    {"name": "Wall Street Journal", "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml", "category": "finance", "credibility": 9, "feed_type": "rss"},
    {"name": "Seeking Alpha", "url": "https://seekingalpha.com/feed.xml", "category": "finance", "credibility": 6, "feed_type": "rss"},
    {"name": "Barrons", "url": "https://www.barrons.com/feed", "category": "finance", "credibility": 8, "feed_type": "rss"},
]

SCIENCE_SOURCES = [
    # --- Science ---
    {"name": "Nature", "url": "https://www.nature.com/nature.rss", "category": "science", "credibility": 10, "feed_type": "rss"},
    {"name": "Science Magazine", "url": "https://www.science.org/rss/news_current.xml", "category": "science", "credibility": 10, "feed_type": "rss"},
    {"name": "Scientific American", "url": "http://rss.sciam.com/ScientificAmerican-Global", "category": "science", "credibility": 9, "feed_type": "rss"},
    {"name": "NASA", "url": "https://www.nasa.gov/rss/dyn/breaking_news.rss", "category": "science", "credibility": 10, "feed_type": "rss"},
    {"name": "NASA Spaceflight", "url": "https://www.nasaspaceflight.com/feed/", "category": "science", "credibility": 7, "feed_type": "rss"},
    {"name": "New Scientist", "url": "https://www.newscientist.com/feed/home/", "category": "science", "credibility": 8, "feed_type": "rss"},
    {"name": "Phys.org", "url": "https://phys.org/rss-feed/", "category": "science", "credibility": 7, "feed_type": "rss"},
    {"name": "Space.com", "url": "https://www.space.com/feeds/all", "category": "science", "credibility": 7, "feed_type": "rss"},
    {"name": "ScienceDaily", "url": "https://www.sciencedaily.com/rss/all.xml", "category": "science", "credibility": 7, "feed_type": "rss"},
]

EDUCATION_SOURCES = [
    # --- Education ---
    {"name": "Inside Higher Ed", "url": "https://www.insidehighered.com/rss/feed", "category": "education", "credibility": 8, "feed_type": "rss"},
    {"name": "Chronicle of Higher Ed", "url": "https://www.chronicle.com/feed", "category": "education", "credibility": 8, "feed_type": "rss"},
    {"name": "Education Week", "url": "https://www.edweek.org/feed", "category": "education", "credibility": 8, "feed_type": "rss"},
    {"name": "Times Higher Education", "url": "https://www.timeshighereducation.com/rss", "category": "education", "credibility": 8, "feed_type": "rss"},
    {"name": "EdSurge", "url": "https://www.edsurge.com/articles_rss", "category": "education", "credibility": 7, "feed_type": "rss"},
]

ENVIRONMENT_SOURCES = [
    # --- Environment & Climate ---
    {"name": "Climate.gov", "url": "https://www.climate.gov/feeds/all", "category": "environment", "credibility": 10, "feed_type": "rss"},
    {"name": "Carbon Brief", "url": "https://www.carbonbrief.org/feed/", "category": "environment", "credibility": 9, "feed_type": "rss"},
    {"name": "Mongabay", "url": "https://news.mongabay.com/feed/", "category": "environment", "credibility": 8, "feed_type": "rss"},
    {"name": "The Guardian - Environment", "url": "https://www.theguardian.com/environment/rss", "category": "environment", "credibility": 8, "feed_type": "rss"},
    {"name": "Yale Environment 360", "url": "https://e360.yale.edu/feed.xml", "category": "environment", "credibility": 9, "feed_type": "rss"},
    {"name": "EPA Newsroom", "url": "https://www.epa.gov/rss/epa-news-releases", "category": "environment", "credibility": 9, "feed_type": "rss"},
]

POLITICS_SOURCES = [
    # --- Politics & Policy ---
    {"name": "Politico", "url": "https://www.politico.com/rss/politicopicks.xml", "category": "politics", "credibility": 7, "feed_type": "rss"},
    {"name": "The Hill", "url": "https://thehill.com/feed/", "category": "politics", "credibility": 7, "feed_type": "rss"},
    {"name": "FiveThirtyEight", "url": "https://fivethirtyeight.com/feed/", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "ProPublica", "url": "https://www.propublica.org/feeds/propublica/main", "category": "politics", "credibility": 9, "feed_type": "rss"},
    {"name": "Brookings", "url": "https://www.brookings.edu/feed/", "category": "politics", "credibility": 9, "feed_type": "rss"},
    {"name": "RAND", "url": "https://www.rand.org/content/rand/blog.rss", "category": "politics", "credibility": 9, "feed_type": "rss"},
    {"name": "CRS Reports", "url": "https://crsreports.congress.gov/rss/reports", "category": "politics", "credibility": 10, "feed_type": "rss"},
]

ALL_SOURCES = (
    NEWS_SOURCES + MEDICAL_SOURCES + LEGAL_SOURCES + TECH_SOURCES +
    FINANCE_SOURCES + SCIENCE_SOURCES + EDUCATION_SOURCES +
    ENVIRONMENT_SOURCES + POLITICS_SOURCES
)

CATEGORIES = {
    "news": {"label": "News", "description": "General & World News", "icon": "newspaper", "color": "#3b82f6"},
    "medical": {"label": "Medical", "description": "Health, Medicine & Research", "icon": "heartbeat", "color": "#ef4444"},
    "legal": {"label": "Legal", "description": "Law, Courts & Regulations", "icon": "gavel", "color": "#a855f7"},
    "tech": {"label": "Technology", "description": "Tech, AI & Digital", "icon": "microchip", "color": "#06b6d4"},
    "finance": {"label": "Finance", "description": "Markets, Economy & Business", "icon": "chart-line", "color": "#22c55e"},
    "science": {"label": "Science", "description": "Research, Space & Discovery", "icon": "flask", "color": "#f59e0b"},
    "education": {"label": "Education", "description": "Higher Ed, Research & Learning", "icon": "graduation-cap", "color": "#ec4899"},
    "environment": {"label": "Environment", "description": "Climate, Energy & Conservation", "icon": "leaf", "color": "#10b981"},
    "politics": {"label": "Politics", "description": "Policy, Government & Analysis", "icon": "landmark", "color": "#8b5cf6"},
}
