"""
Feed sources organized by domain/category.
Each source has: name, url, category, default_credibility (1-10), feed_type (rss/atom/api).
Expand this list as needed via the admin panel.
"""

NEWS_SOURCES = [
    # --- US News ---
    {"name": "Washington Post", "url": "https://feeds.washingtonpost.com/rss/national", "category": "news", "credibility": 7, "feed_type": "rss"},
    {"name": "ABC News", "url": "https://abcnews.go.com/abcnews/topstories", "category": "news", "credibility": 7, "feed_type": "rss"},

    # --- International ---
    {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml", "category": "news", "credibility": 7, "feed_type": "rss"},
    {"name": "France24", "url": "https://www.france24.com/en/rss", "category": "news", "credibility": 7, "feed_type": "rss"},
    {"name": "Japan Times", "url": "https://www.japantimes.co.jp/feed/", "category": "news", "credibility": 7, "feed_type": "rss"},

    # --- Alternative / Independent ---
    {"name": "Breitbart", "url": "https://feeds.feedburner.com/breitbart", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "The Blaze", "url": "https://www.theblaze.com/rss", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "Daily Caller", "url": "https://dailycaller.com/feed/", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "Epoch Times", "url": "https://www.theepochtimes.com/feed", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "Zero Hedge", "url": "https://www.zerohedge.com/feed", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "Gateway Pundit", "url": "https://www.thegatewaypundit.com/feed/", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "Sputnik News", "url": "https://sputnikglobe.com/export/rss2/archive/index.xml", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "InfoWars", "url": "https://www.infowars.com/feed/custom_feed_rss", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "NewsPunch", "url": "https://newspunch.com/feed/", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "Vigilant Citizen", "url": "https://vigilantcitizen.com/feed/", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "True Pundit", "url": "https://www.truepundit.com/feed/", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "Before Its News", "url": "https://beforeitsnews.com/feed.xml", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "Neon Nettle", "url": "https://neonnettle.com/rss.xml", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "Hagmann Report", "url": "https://www.hagmannreport.com/feed/", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "Zuby", "url": "https://realtalkwithzuby.substack.com/feed", "category": "news", "credibility": 8, "feed_type": "rss"},

    # --- Business News ---
    {"name": "CNBC", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", "category": "news", "credibility": 7, "feed_type": "rss"},
    {"name": "Bloomberg", "url": "https://feeds.bloomberg.com/markets/news.rss", "category": "news", "credibility": 7, "feed_type": "rss"},
]

MEDICAL_SOURCES = [
    {"name": "WHO News", "url": "https://www.who.int/rss-feeds/news-english.xml", "category": "medical", "credibility": 7, "feed_type": "rss"},
    {"name": "Medical News Today", "url": "https://www.medicalnewstoday.com/newsfeeds/rss", "category": "medical", "credibility": 7, "feed_type": "rss"},
    {"name": "The Lancet", "url": "https://www.thelancet.com/rssfeed/lancet_current.xml", "category": "medical", "credibility": 7, "feed_type": "rss"},
    {"name": "NEJM", "url": "https://www.nejm.org/action/showFeed?jc=nejm&type=etoc&feed=rss", "category": "medical", "credibility": 7, "feed_type": "rss"},
    {"name": "WebMD Health", "url": "https://rssfeeds.webmd.com/rss/rss.aspx?RSSSource=RSS_PUBLIC", "category": "medical", "credibility": 7, "feed_type": "rss"},
    {"name": "FDA News", "url": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/fda-newsroom/rss.xml", "category": "medical", "credibility": 7, "feed_type": "rss"},

    # --- Independent Health / Substack ---
    {"name": "Dr. Robert Malone", "url": "https://rwmalonemd.substack.com/feed", "category": "medical", "credibility": 8, "feed_type": "rss"},
    {"name": "David Avocado Wolfe (Substack)", "url": "https://davidavocadowolfe.substack.com/feed", "category": "medical", "credibility": 8, "feed_type": "rss"},
    {"name": "David Avocado Wolfe (Blog)", "url": "https://shop.davidwolfe.com/blogs/learn.atom", "category": "medical", "credibility": 8, "feed_type": "atom"},
    {"name": "Dr. Judy Mikovits", "url": "https://therealdr.substack.com/feed", "category": "medical", "credibility": 8, "feed_type": "rss"},
]

LEGAL_SOURCES = [
    # --- Legal News & Case Law ---
    {"name": "SCOTUSblog", "url": "https://www.scotusblog.com/feed/", "category": "legal", "credibility": 7, "feed_type": "rss"},
    {"name": "Law.com", "url": "https://feeds.law.com/law/headlines", "category": "legal", "credibility": 7, "feed_type": "rss"},
    {"name": "Jurist", "url": "https://www.jurist.org/news/feed/", "category": "legal", "credibility": 7, "feed_type": "rss"},
    {"name": "Cornell LII", "url": "https://www.law.cornell.edu/lii/rss.xml", "category": "legal", "credibility": 7, "feed_type": "rss"},
    {"name": "Courthouse News", "url": "https://www.courthousenews.com/feed/", "category": "legal", "credibility": 7, "feed_type": "rss"},
    {"name": "Reuters Legal", "url": "https://www.reuters.com/legal/rss", "category": "legal", "credibility": 7, "feed_type": "rss"},
    {"name": "Lawfare", "url": "https://www.lawfaremedia.org/feed", "category": "legal", "credibility": 7, "feed_type": "rss"},
    {"name": "Above the Law", "url": "https://abovethelaw.com/feed/", "category": "legal", "credibility": 7, "feed_type": "rss"},
    {"name": "ABA Journal", "url": "https://www.abajournal.com/feed", "category": "legal", "credibility": 7, "feed_type": "rss"},
]

TECH_SOURCES = [
    # --- Technology ---
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/", "category": "tech", "credibility": 7, "feed_type": "rss"},
    {"name": "Hacker News", "url": "https://hnrss.org/frontpage", "category": "tech", "credibility": 7, "feed_type": "rss"},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml", "category": "tech", "credibility": 7, "feed_type": "rss"},
    {"name": "Wired", "url": "https://www.wired.com/feed/rss", "category": "tech", "credibility": 7, "feed_type": "rss"},
    {"name": "ZDNet", "url": "https://www.zdnet.com/news/rss.xml", "category": "tech", "credibility": 7, "feed_type": "rss"},
    {"name": "The Register", "url": "https://www.theregister.com/headlines.atom", "category": "tech", "credibility": 7, "feed_type": "atom"},
    {"name": "Engadget", "url": "https://www.engadget.com/rss.xml", "category": "tech", "credibility": 7, "feed_type": "rss"},
]

FINANCE_SOURCES = [
    # --- Finance ---
    {"name": "Financial Times", "url": "https://www.ft.com/rss/home", "category": "finance", "credibility": 7, "feed_type": "rss"},
    {"name": "MarketWatch", "url": "https://feeds.marketwatch.com/marketwatch/topstories/", "category": "finance", "credibility": 7, "feed_type": "rss"},
    {"name": "Yahoo Finance", "url": "https://finance.yahoo.com/news/rssindex", "category": "finance", "credibility": 7, "feed_type": "rss"},
    {"name": "Investopedia", "url": "https://www.investopedia.com/feedbuilder/feed/getfeed?feedName=rss_headline", "category": "finance", "credibility": 7, "feed_type": "rss"},
    {"name": "The Economist", "url": "https://www.economist.com/finance-and-economics/rss.xml", "category": "finance", "credibility": 7, "feed_type": "rss"},
    {"name": "Wall Street Journal", "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml", "category": "finance", "credibility": 7, "feed_type": "rss"},
    {"name": "Seeking Alpha", "url": "https://seekingalpha.com/feed.xml", "category": "finance", "credibility": 7, "feed_type": "rss"},
    {"name": "Barrons", "url": "https://www.barrons.com/feed", "category": "finance", "credibility": 7, "feed_type": "rss"},
    {"name": "The Solari Report", "url": "https://thesolarireport.substack.com/feed", "category": "finance", "credibility": 8, "feed_type": "rss"},
]

SCIENCE_SOURCES = [
    # --- Science ---
    {"name": "NASA", "url": "https://www.nasa.gov/rss/dyn/breaking_news.rss", "category": "science", "credibility": 7, "feed_type": "rss"},
    {"name": "NASA Spaceflight", "url": "https://www.nasaspaceflight.com/feed/", "category": "science", "credibility": 7, "feed_type": "rss"},
    {"name": "New Scientist", "url": "https://www.newscientist.com/feed/home/", "category": "science", "credibility": 7, "feed_type": "rss"},
    {"name": "Phys.org", "url": "https://phys.org/rss-feed/", "category": "science", "credibility": 7, "feed_type": "rss"},
    {"name": "Space.com", "url": "https://www.space.com/feeds/all", "category": "science", "credibility": 7, "feed_type": "rss"},
    {"name": "ScienceDaily", "url": "https://www.sciencedaily.com/rss/all.xml", "category": "science", "credibility": 7, "feed_type": "rss"},
]

EDUCATION_SOURCES = [
    # --- Education ---
    {"name": "Inside Higher Ed", "url": "https://www.insidehighered.com/rss/feed", "category": "education", "credibility": 7, "feed_type": "rss"},
    {"name": "Chronicle of Higher Ed", "url": "https://www.chronicle.com/feed", "category": "education", "credibility": 7, "feed_type": "rss"},
    {"name": "Education Week", "url": "https://www.edweek.org/feed", "category": "education", "credibility": 7, "feed_type": "rss"},
    {"name": "Times Higher Education", "url": "https://www.timeshighereducation.com/rss", "category": "education", "credibility": 7, "feed_type": "rss"},
    {"name": "EdSurge", "url": "https://www.edsurge.com/articles_rss", "category": "education", "credibility": 7, "feed_type": "rss"},
]

ENVIRONMENT_SOURCES = [
    # --- Environment & Climate ---
    {"name": "Climate.gov", "url": "https://www.climate.gov/feeds/all", "category": "environment", "credibility": 7, "feed_type": "rss"},
    {"name": "Carbon Brief", "url": "https://www.carbonbrief.org/feed/", "category": "environment", "credibility": 7, "feed_type": "rss"},
    {"name": "Mongabay", "url": "https://news.mongabay.com/feed/", "category": "environment", "credibility": 7, "feed_type": "rss"},
    {"name": "Yale Environment 360", "url": "https://e360.yale.edu/feed.xml", "category": "environment", "credibility": 7, "feed_type": "rss"},
]

POLITICS_SOURCES = [
    # --- Politics & Policy ---
    {"name": "Politico", "url": "https://www.politico.com/rss/politicopicks.xml", "category": "politics", "credibility": 7, "feed_type": "rss"},
    {"name": "The Hill", "url": "https://thehill.com/feed/", "category": "politics", "credibility": 7, "feed_type": "rss"},
    {"name": "FiveThirtyEight", "url": "https://fivethirtyeight.com/feed/", "category": "politics", "credibility": 7, "feed_type": "rss"},
    {"name": "ProPublica", "url": "https://www.propublica.org/feeds/propublica/main", "category": "politics", "credibility": 7, "feed_type": "rss"},
    {"name": "Brookings", "url": "https://www.brookings.edu/feed/", "category": "politics", "credibility": 7, "feed_type": "rss"},
    {"name": "RAND", "url": "https://www.rand.org/content/rand/blog.rss", "category": "politics", "credibility": 7, "feed_type": "rss"},
    {"name": "CRS Reports", "url": "https://crsreports.congress.gov/rss/reports", "category": "politics", "credibility": 7, "feed_type": "rss"},

    # --- Independent Commentary / Podcasts ---
    {"name": "Candace (Podcast)", "url": "https://feeds.megaphone.fm/candace", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Tucker Carlson (Podcast)", "url": "https://feeds.megaphone.fm/RSV1597324942", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Tim Pool - Timcast IRL", "url": "https://feeds.libsyn.com/574450/rss", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Tim Pool - Culture War", "url": "https://feeds.libsyn.com/552267/rss", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Ben Shapiro Show", "url": "https://feeds.megaphone.fm/BVDWV5370667266", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Louder with Crowder", "url": "https://feeds.libsyn.com/576250/rss", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Russell Brand - Stay Free", "url": "https://feeds.libsyn.com/576255/rss", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Joe Rogan Experience", "url": "https://feeds.megaphone.fm/GLT1412515089", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Robert F. Kennedy Jr.", "url": "https://robertfkennedyjr.substack.com/feed", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Ian Miles Cheong", "url": "https://stillgray.substack.com/feed", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Ian Miles Cheong (Midnight Directive)", "url": "https://midnightdirective.substack.com/feed", "category": "politics", "credibility": 8, "feed_type": "rss"},
]

MARKETS_SOURCES = [
    # --- Commodities & Metals ---
    {"name": "Gold Telegraph", "url": "https://goldtelegraph.com/feed", "category": "markets", "credibility": 8, "feed_type": "rss"},
    {"name": "Silver Doctors", "url": "https://www.silverdoctors.com/feed/", "category": "markets", "credibility": 8, "feed_type": "rss"},
    {"name": "Mining.com", "url": "https://www.mining.com/feed/", "category": "markets", "credibility": 7, "feed_type": "rss"},
    {"name": "OilPrice.com", "url": "https://oilprice.com/rss/main", "category": "markets", "credibility": 7, "feed_type": "rss"},

    # --- Markets & Trading ---
    {"name": "Zero Hedge (Markets)", "url": "https://feeds.feedburner.com/zerohedge/feed", "category": "markets", "credibility": 8, "feed_type": "rss"},
    {"name": "Wolf Street", "url": "https://wolfstreet.com/feed/", "category": "markets", "credibility": 8, "feed_type": "rss"},
    {"name": "Mish Talk", "url": "https://mishtalk.com/feed", "category": "markets", "credibility": 8, "feed_type": "rss"},
    {"name": "Investing.com", "url": "https://www.investing.com/rss/news.rss", "category": "markets", "credibility": 7, "feed_type": "rss"},

    # --- Cryptocurrency ---
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "category": "markets", "credibility": 7, "feed_type": "rss"},
    {"name": "CoinTelegraph", "url": "https://cointelegraph.com/rss", "category": "markets", "credibility": 7, "feed_type": "rss"},
    {"name": "Bitcoin Magazine", "url": "https://bitcoinmagazine.com/.rss/full/", "category": "markets", "credibility": 8, "feed_type": "rss"},
    {"name": "Decrypt", "url": "https://decrypt.co/feed", "category": "markets", "credibility": 7, "feed_type": "rss"},
    {"name": "The Block", "url": "https://www.theblock.co/rss.xml", "category": "markets", "credibility": 7, "feed_type": "rss"},
    {"name": "Blockworks", "url": "https://blockworks.co/feed", "category": "markets", "credibility": 7, "feed_type": "rss"},
]

ALL_SOURCES = (
    NEWS_SOURCES + MEDICAL_SOURCES + LEGAL_SOURCES + TECH_SOURCES +
    FINANCE_SOURCES + SCIENCE_SOURCES + EDUCATION_SOURCES +
    ENVIRONMENT_SOURCES + POLITICS_SOURCES + MARKETS_SOURCES
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
    "markets": {"label": "Markets", "description": "Commodities, Metals, Crypto & Trading", "icon": "coins", "color": "#f97316"},
}
