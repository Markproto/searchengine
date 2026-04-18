"""
Feed sources organized by domain/category.
Each source has: name, url, category, default_credibility (1-10), feed_type (rss/atom/api).
Expand this list as needed via the admin panel.
"""

NEWS_SOURCES = [
    # --- Oregon Corner (staff-written) ---
    {"name": "Oregon Corner", "url": "https://oregoncorner.com/api/feed", "category": "news", "credibility": 9, "feed_type": "rss"},

    # --- US News ---
    {"name": "Washington Post", "url": "https://feeds.washingtonpost.com/rss/national", "category": "news", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    {"name": "ABC News", "url": "https://abcnews.go.com/abcnews/topstories", "category": "news", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},

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
    {"name": "Vigilant Citizen", "url": "https://vigilantcitizen.com/feed/", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "True Pundit", "url": "https://www.truepundit.com/feed/", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "Before Its News", "url": "https://beforeitsnews.com/feed.xml", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "Neon Nettle", "url": "https://neonnettle.com/rss.xml", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "Hagmann Report", "url": "https://www.hagmannreport.com/feed/", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "Zuby", "url": "https://realtalkwithzuby.substack.com/feed", "category": "news", "credibility": 8, "feed_type": "rss"},

    # --- Unbiased / Centrist ---
    {"name": "Straight Arrow News", "url": "https://san.com/feed/", "category": "news", "credibility": 7, "feed_type": "rss"},

    # --- New Independent Sources (no Pfizer advertising) ---
    {"name": "Revolver News", "url": "https://www.revolver.news/feed/", "category": "news", "credibility": 7, "feed_type": "rss"},
    {"name": "National File", "url": "https://nationalfile.com/feed/", "category": "news", "credibility": 6, "feed_type": "rss"},
    {"name": "The Federalist", "url": "https://thefederalist.com/feed/", "category": "news", "credibility": 7, "feed_type": "rss"},
    {"name": "American Greatness", "url": "https://amgreatness.com/feed/", "category": "news", "credibility": 7, "feed_type": "rss"},
    {"name": "Just The News", "url": "https://justthenews.com/rss.xml", "category": "news", "credibility": 7, "feed_type": "rss"},
    {"name": "The Post Millennial", "url": "https://thepostmillennial.com/feed", "category": "news", "credibility": 7, "feed_type": "rss"},
    {"name": "Unlimited Hangout", "url": "https://unlimitedhangout.com/feed/", "category": "news", "credibility": 8, "feed_type": "rss"},
    {"name": "Corbett Report", "url": "https://www.corbettreport.com/feed/", "category": "news", "credibility": 7, "feed_type": "rss"},
    {"name": "The Last American Vagabond", "url": "https://www.thelastamericanvagabond.com/feed/", "category": "news", "credibility": 7, "feed_type": "rss"},
    {"name": "MintPress News", "url": "https://www.mintpressnews.com/feed/", "category": "news", "credibility": 7, "feed_type": "rss"},
    {"name": "The Grayzone", "url": "https://thegrayzone.com/feed/", "category": "news", "credibility": 7, "feed_type": "rss"},
    {"name": "Consortium News", "url": "https://consortiumnews.com/feed/", "category": "news", "credibility": 7, "feed_type": "rss"},

    # --- Business News ---
    {"name": "CNBC", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", "category": "news", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    {"name": "Bloomberg", "url": "https://feeds.bloomberg.com/markets/news.rss", "category": "news", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
]

MEDICAL_SOURCES = [
    {"name": "WHO News", "url": "https://www.who.int/rss-feeds/news-english.xml", "category": "medical", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    {"name": "Medical News Today", "url": "https://www.medicalnewstoday.com/newsfeeds/rss", "category": "medical", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    {"name": "The Lancet", "url": "https://www.thelancet.com/rssfeed/lancet_current.xml", "category": "medical", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    {"name": "NEJM", "url": "https://www.nejm.org/action/showFeed?jc=nejm&type=etoc&feed=rss", "category": "medical", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    {"name": "WebMD Health", "url": "https://rssfeeds.webmd.com/rss/rss.aspx?RSSSource=RSS_PUBLIC", "category": "medical", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    {"name": "FDA News", "url": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/fda-newsroom/rss.xml", "category": "medical", "credibility": 7, "feed_type": "rss"},

    # --- Independent Health / Substack ---
    {"name": "Dr. Robert Malone", "url": "https://rwmalonemd.substack.com/feed", "category": "medical", "credibility": 8, "feed_type": "rss"},
    {"name": "David Avocado Wolfe (Substack)", "url": "https://davidavocadowolfe.substack.com/feed", "category": "medical", "credibility": 8, "feed_type": "rss"},
    {"name": "David Avocado Wolfe (Blog)", "url": "https://shop.davidwolfe.com/blogs/learn.atom", "category": "medical", "credibility": 8, "feed_type": "atom"},
    {"name": "Dr. Judy Mikovits", "url": "https://therealdr.substack.com/feed", "category": "medical", "credibility": 8, "feed_type": "rss"},
    {"name": "Dr. Mercola", "url": "https://articles.mercola.com/sites/articles/rss.aspx", "category": "medical", "credibility": 8, "feed_type": "rss"},

    # --- Independent Medical (no pharma advertising) ---
    {"name": "Children's Health Defense", "url": "https://childrenshealthdefense.org/defender/feed/", "category": "medical", "credibility": 7, "feed_type": "rss"},
    {"name": "GreenMedInfo", "url": "https://greenmedinfo.com/rss.xml", "category": "medical", "credibility": 6, "feed_type": "rss"},
    {"name": "Retraction Watch", "url": "https://retractionwatch.com/feed/", "category": "medical", "credibility": 8, "feed_type": "rss"},
]

LEGAL_SOURCES = [
    # --- Legal News & Case Law ---
    {"name": "SCOTUSblog", "url": "https://www.scotusblog.com/feed/", "category": "legal", "credibility": 7, "feed_type": "rss"},
    {"name": "Law.com", "url": "https://feeds.law.com/law/headlines", "category": "legal", "credibility": 7, "feed_type": "rss"},
    {"name": "Jurist", "url": "https://www.jurist.org/news/feed/", "category": "legal", "credibility": 7, "feed_type": "rss"},
    {"name": "Cornell LII", "url": "https://www.law.cornell.edu/lii/rss.xml", "category": "legal", "credibility": 7, "feed_type": "rss"},
    {"name": "Courthouse News", "url": "https://www.courthousenews.com/feed/", "category": "legal", "credibility": 7, "feed_type": "rss"},
    {"name": "Reuters Legal", "url": "https://www.reuters.com/legal/rss", "category": "legal", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    {"name": "Lawfare", "url": "https://www.lawfaremedia.org/feed", "category": "legal", "credibility": 7, "feed_type": "rss"},
    {"name": "Above the Law", "url": "https://abovethelaw.com/feed/", "category": "legal", "credibility": 7, "feed_type": "rss"},
    {"name": "ABA Journal", "url": "https://www.abajournal.com/feed", "category": "legal", "credibility": 7, "feed_type": "rss"},

    # --- Independent Legal / Transparency (no pharma advertising) ---
    {"name": "Judicial Watch", "url": "https://www.judicialwatch.org/feed/", "category": "legal", "credibility": 7, "feed_type": "rss"},
    {"name": "Empower Oversight", "url": "https://empoweroversite.org/feed/", "category": "legal", "credibility": 7, "feed_type": "rss"},
]

TECH_SOURCES = [
    # --- Technology ---
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/", "category": "tech", "credibility": 7, "feed_type": "rss"},
    {"name": "Hacker News", "url": "https://hnrss.org/frontpage", "category": "tech", "credibility": 7, "feed_type": "rss"},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml", "category": "tech", "credibility": 7, "feed_type": "rss"},
    {"name": "Wired", "url": "https://www.wired.com/feed/rss", "category": "tech", "credibility": 7, "feed_type": "rss"},
    {"name": "The Register", "url": "https://www.theregister.com/headlines.atom", "category": "tech", "credibility": 7, "feed_type": "atom"},
    {"name": "Engadget", "url": "https://www.engadget.com/rss.xml", "category": "tech", "credibility": 7, "feed_type": "rss"},
]

FINANCE_SOURCES = [
    # --- Finance ---
    {"name": "Financial Times", "url": "https://www.ft.com/rss/home", "category": "finance", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    {"name": "MarketWatch", "url": "https://feeds.marketwatch.com/marketwatch/topstories/", "category": "finance", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    {"name": "Yahoo Finance", "url": "https://finance.yahoo.com/news/rssindex", "category": "finance", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    {"name": "Investopedia", "url": "https://www.investopedia.com/feedbuilder/feed/getfeed?feedName=rss_headline", "category": "finance", "credibility": 7, "feed_type": "rss"},
    {"name": "The Economist", "url": "https://www.economist.com/finance-and-economics/rss.xml", "category": "finance", "credibility": 7, "feed_type": "rss"},
    {"name": "Wall Street Journal", "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml", "category": "finance", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
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
    {"name": "Politico", "url": "https://www.politico.com/rss/politicopicks.xml", "category": "politics", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    {"name": "The Hill", "url": "https://thehill.com/feed/", "category": "politics", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    {"name": "FiveThirtyEight", "url": "https://fivethirtyeight.com/feed/", "category": "politics", "credibility": 7, "feed_type": "rss"},
    {"name": "ProPublica", "url": "https://www.propublica.org/feeds/propublica/main", "category": "politics", "credibility": 7, "feed_type": "rss"},
    {"name": "Brookings", "url": "https://www.brookings.edu/feed/", "category": "politics", "credibility": 7, "feed_type": "rss"},
    {"name": "RAND", "url": "https://www.rand.org/content/rand/blog.rss", "category": "politics", "credibility": 7, "feed_type": "rss"},

    # --- Independent Commentary / Podcasts ---
    {"name": "Candace (Podcast)", "url": "https://feeds.megaphone.fm/candace", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Tucker Carlson (Podcast)", "url": "https://feeds.megaphone.fm/RSV1597324942", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Tim Pool - Timcast IRL", "url": "https://feeds.libsyn.com/574450/rss", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Tim Pool - Culture War", "url": "https://feeds.libsyn.com/552267/rss", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Ben Shapiro Show", "url": "https://feeds.megaphone.fm/BVDWV5370667266", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Louder with Crowder", "url": "https://feeds.libsyn.com/576250/rss", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Russell Brand - Stay Free", "url": "https://feeds.libsyn.com/576255/rss", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Joe Rogan Experience", "url": "https://feeds.megaphone.fm/GLT1412515089", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Matt Gaetz Show", "url": "https://www.spreaker.com/show/6461238/episodes/feed", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "The Anchormen Show (Gaetz)", "url": "https://anchor.fm/s/ea4136c/podcast/rss", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Redacted", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCoJhK5kMc4LjBKdiYrDtzlA", "category": "politics", "credibility": 8, "feed_type": "atom"},
    {"name": "Robert F. Kennedy Jr.", "url": "https://robertfkennedyjr.substack.com/feed", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Nick Shirley", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC2Uioh1tYQkNuHLSBpzdfCg", "category": "politics", "credibility": 8, "feed_type": "atom"},
    {"name": "Ian Miles Cheong", "url": "https://stillgray.substack.com/feed", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Ian Miles Cheong (Midnight Directive)", "url": "https://midnightdirective.substack.com/feed", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Ian Carroll", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCXN75hqjDGF0sKsCRoAZ6fz", "category": "politics", "credibility": 8, "feed_type": "atom"},

    # --- YouTube Channels (RSS feeds) ---
    {"name": "Tim Pool (YouTube)", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCG749Dj4V2fKa143f8sE60Q", "category": "politics", "credibility": 8, "feed_type": "atom"},
    {"name": "Steven Crowder (YouTube)", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCIveFvW-ARp_B_RckhweNJw", "category": "politics", "credibility": 8, "feed_type": "atom"},
    {"name": "Breaking Points (YouTube)", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCDRIjKy6eZOvKtOELtTdeUA", "category": "politics", "credibility": 8, "feed_type": "atom"},
    {"name": "Candace Owens (YouTube)", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCL0u5uz7KZ9q-pe-VC8TY-w", "category": "politics", "credibility": 8, "feed_type": "atom"},
    {"name": "JRE Clips (YouTube)", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCnxGkOGNMqQEUMvroOWps6Q", "category": "politics", "credibility": 8, "feed_type": "atom"},
]

MARKETS_SOURCES = [
    # --- Commodities & Metals ---
    {"name": "Gold Telegraph", "url": "https://goldtelegraph.com/feed", "category": "markets", "credibility": 8, "feed_type": "rss"},
    {"name": "Silver Doctors", "url": "https://www.silverdoctors.com/feed/", "category": "markets", "credibility": 8, "feed_type": "rss"},
    {"name": "Mining.com", "url": "https://www.mining.com/feed/", "category": "markets", "credibility": 7, "feed_type": "rss"},
    {"name": "OilPrice.com", "url": "https://oilprice.com/rss/main", "category": "markets", "credibility": 7, "feed_type": "rss"},

    # --- Markets & Trading ---
    {"name": "Benzinga", "url": "https://feeds.benzinga.com/benzinga", "category": "markets", "credibility": 7, "feed_type": "rss"},
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

EPSTEIN_SOURCES = [
    # --- Epstein Files / Case Coverage ---
    # Investigative & Legal
    {"name": "Courthouse News", "url": "https://www.courthousenews.com/feed/", "category": "epstein-files", "credibility": 7, "feed_type": "rss"},
    {"name": "Lawfare", "url": "https://www.lawfaremedia.org/feed", "category": "epstein-files", "credibility": 7, "feed_type": "rss"},
    {"name": "Reuters Legal", "url": "https://www.reuters.com/legal/rss", "category": "epstein-files", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    {"name": "ProPublica", "url": "https://www.propublica.org/feeds/propublica/main", "category": "epstein-files", "credibility": 7, "feed_type": "rss"},
    {"name": "The Intercept", "url": "https://theintercept.com/feed/?rss", "category": "epstein-files", "credibility": 7, "feed_type": "rss"},
    # Tabloid / High-volume news
    {"name": "Daily Mail US", "url": "https://www.dailymail.co.uk/articles.rss", "category": "epstein-files", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    {"name": "New York Post", "url": "https://nypost.com/feed/", "category": "epstein-files", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    {"name": "Insider", "url": "https://www.businessinsider.com/sai/rss", "category": "epstein-files", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    {"name": "Fox News", "url": "https://moxie.foxnews.com/google-publisher/latest.xml", "category": "epstein-files", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    # Independent investigative
    {"name": "Andrew Murphy (Substack)", "url": "https://andrewmurphy950923.substack.com/feed", "category": "epstein-files", "credibility": 8, "feed_type": "rss"},
    {"name": "Unlimited Hangout", "url": "https://unlimitedhangout.com/feed/", "category": "epstein-files", "credibility": 8, "feed_type": "rss"},
    {"name": "MintPress News", "url": "https://www.mintpressnews.com/feed/", "category": "epstein-files", "credibility": 8, "feed_type": "rss"},
    {"name": "The Grayzone", "url": "https://thegrayzone.com/feed/", "category": "epstein-files", "credibility": 8, "feed_type": "rss"},
    {"name": "Consortium News", "url": "https://consortiumnews.com/feed/", "category": "epstein-files", "credibility": 8, "feed_type": "rss"},
    {"name": "Zero Hedge", "url": "https://feeds.feedburner.com/zerohedge/feed", "category": "epstein-files", "credibility": 8, "feed_type": "rss"},
    {"name": "Gateway Pundit", "url": "https://www.thegatewaypundit.com/feed/", "category": "epstein-files", "credibility": 8, "feed_type": "rss"},
]

CHARLIE_KIRK_SOURCES = [
    # --- Charlie Kirk / Turning Point USA / Conservative Movement ---
    {"name": "Charlie Kirk Show", "url": "https://feeds.megaphone.fm/charliekirkshow", "category": "charlie-kirk", "credibility": 8, "feed_type": "rss"},
    {"name": "Turning Point USA", "url": "https://www.tpusa.com/feed", "category": "charlie-kirk", "credibility": 8, "feed_type": "rss"},
    {"name": "Daily Wire", "url": "https://www.dailywire.com/feeds/rss.xml", "category": "charlie-kirk", "credibility": 8, "feed_type": "rss"},
    {"name": "Breitbart", "url": "https://feeds.feedburner.com/breitbart", "category": "charlie-kirk", "credibility": 8, "feed_type": "rss"},
    {"name": "The Federalist", "url": "https://thefederalist.com/feed/", "category": "charlie-kirk", "credibility": 8, "feed_type": "rss"},
    {"name": "Campus Reform", "url": "https://www.campusreform.org/rss/CampusReform.rss", "category": "charlie-kirk", "credibility": 7, "feed_type": "rss"},
    {"name": "Fox News", "url": "https://moxie.foxnews.com/google-publisher/latest.xml", "category": "charlie-kirk", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    {"name": "New York Post", "url": "https://nypost.com/feed/", "category": "charlie-kirk", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    {"name": "Newsmax", "url": "https://www.newsmax.com/rss/Newsfront/1/", "category": "charlie-kirk", "credibility": 5, "feed_type": "rss", "sponsors": "Pfizer"},
    {"name": "The Blaze", "url": "https://www.theblaze.com/rss", "category": "charlie-kirk", "credibility": 8, "feed_type": "rss"},
    {"name": "Epoch Times", "url": "https://www.theepochtimes.com/feed", "category": "charlie-kirk", "credibility": 8, "feed_type": "rss"},
    {"name": "Washington Examiner", "url": "https://www.washingtonexaminer.com/feed", "category": "charlie-kirk", "credibility": 7, "feed_type": "rss"},
]

LEGISLATIVE_SOURCES = [
    # --- Congress.gov Official Feeds ---
    {"name": "Senate Floor Today", "url": "https://www.congress.gov/rss/senate-floor-today.xml", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "senate"},
    {"name": "House Floor Today", "url": "https://www.congress.gov/rss/house-floor-today.xml", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "house"},
    {"name": "Most-Viewed Bills", "url": "https://www.congress.gov/rss/most-viewed-bills.xml", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "bills"},
    {"name": "Bills to the President", "url": "https://www.congress.gov/rss/presented-to-president.xml", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "enacted"},

    # --- GovInfo (GPO) Official Feeds ---
    {"name": "Congressional Bills", "url": "https://www.govinfo.gov/rss/bills.xml", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "bills"},
    {"name": "Public Laws", "url": "https://www.govinfo.gov/rss/plaw.xml", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "enacted"},
    {"name": "Congressional Hearings", "url": "https://www.govinfo.gov/rss/chrg.xml", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "hearings"},
    {"name": "Committee Reports", "url": "https://www.govinfo.gov/rss/crpt.xml", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "hearings"},
    {"name": "Committee Prints", "url": "https://www.govinfo.gov/rss/cprt.xml", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "hearings"},
    {"name": "Federal Register", "url": "https://www.govinfo.gov/rss/fr.xml", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "regulations"},
    {"name": "Code of Federal Regulations", "url": "https://www.govinfo.gov/rss/cfr.xml", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "regulations"},
    {"name": "Presidential Documents", "url": "https://www.govinfo.gov/rss/dcpd.xml", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "executive-orders"},
    {"name": "US Code Updates", "url": "https://www.govinfo.gov/rss/uscode.xml", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "enacted"},
    {"name": "Federal Budget", "url": "https://www.govinfo.gov/rss/budget.xml", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "budget"},
    {"name": "Economic Indicators", "url": "https://www.govinfo.gov/rss/econi.xml", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "budget"},

    # --- CRS & CBO ---
    {"name": "EveryCRSReport", "url": "https://www.everycrsreport.com/rss.xml", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "research"},
    {"name": "CBO Cost Estimates", "url": "https://www.cbo.gov/rss/119congress-cost-estimates.xml", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "research"},

    # --- Executive Branch ---
    {"name": "White House Presidential Actions", "url": "https://www.whitehouse.gov/presidential-actions/feed/", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "executive-orders"},

    # --- House Clerk ---
    {"name": "House Floor Proceedings", "url": "https://clerk.house.gov/floor/HDoc-119-2-FloorProceedings.xml", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "house"},

    # --- Committee-Specific Feeds ---
    {"name": "House Judiciary Committee", "url": "https://judiciary.house.gov/rss.xml", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "hearings"},
    {"name": "House Appropriations Committee", "url": "https://appropriations.house.gov/rss.xml", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "hearings"},

    # --- EPA / Regulatory Agencies ---
    {"name": "EPA News Releases", "url": "https://www.epa.gov/newsreleases/search/rss", "category": "legislative", "credibility": 7, "feed_type": "rss", "subcategory": "regulations"},
]

STATE_LEGISLATIVE_SOURCES = [
    # --- Oregon ---
    {"name": "Oregon Governor", "url": "https://www.oregon.gov/newsroom/Pages/rss.aspx", "category": "state-legislative", "credibility": 7, "feed_type": "rss", "subcategory": "oregon"},
    {"name": "My Oregon (Governor's Blog)", "url": "https://www.myoregon.gov/feed/", "category": "state-legislative", "credibility": 7, "feed_type": "rss", "subcategory": "oregon"},
    {"name": "Oregon Legislature Press", "url": "https://www.myoregon.gov/category/press-releases/feed/", "category": "state-legislative", "credibility": 7, "feed_type": "rss", "subcategory": "oregon"},
    {"name": "Oregon Firearms Federation", "url": "https://www.oregonfirearms.org/feed/", "category": "state-legislative", "credibility": 8, "feed_type": "rss", "subcategory": "oregon"},
    {"name": "Oregonians for Medical Freedom", "url": "https://www.oregoniansformedicalfreedom.com/feed/", "category": "state-legislative", "credibility": 8, "feed_type": "rss", "subcategory": "oregon"},

    # --- Washington ---
    {"name": "Washington AG", "url": "https://www.atg.wa.gov/rss.xml", "category": "state-legislative", "credibility": 7, "feed_type": "rss", "subcategory": "washington"},

    # --- California ---
    {"name": "California Governor", "url": "https://www.gov.ca.gov/category/press-releases/feed/", "category": "state-legislative", "credibility": 7, "feed_type": "rss", "subcategory": "california"},
    {"name": "CA Legislative Analyst", "url": "https://lao.ca.gov/RSS", "category": "state-legislative", "credibility": 7, "feed_type": "rss", "subcategory": "california"},
    {"name": "CA Attorney General", "url": "https://oag.ca.gov/news/feed", "category": "state-legislative", "credibility": 7, "feed_type": "rss", "subcategory": "california"},
]

ADVOCACY_SOURCES = [
    # --- Federal Gun Rights Advocacy ---
    {"name": "NRA-ILA", "url": "https://www.nraila.org/ilarss.aspx", "category": "legislative", "credibility": 8, "feed_type": "rss", "subcategory": "regulations"},
    {"name": "Gun Owners of America", "url": "https://www.gunowners.org/feed/", "category": "legislative", "credibility": 8, "feed_type": "rss", "subcategory": "regulations"},
    {"name": "Second Amendment Foundation", "url": "https://saf.org/feed/", "category": "legislative", "credibility": 8, "feed_type": "rss", "subcategory": "regulations"},
    {"name": "Firearms Policy Coalition", "url": "https://www.firearmspolicy.org/news?format=rss", "category": "legislative", "credibility": 8, "feed_type": "rss", "subcategory": "regulations"},

    # --- Federal Think Tanks / Limited Government ---
    {"name": "Heritage Foundation", "url": "https://www.heritage.org/rss", "category": "legislative", "credibility": 8, "feed_type": "rss", "subcategory": "research"},
    {"name": "Judicial Watch", "url": "https://www.judicialwatch.org/feed/", "category": "legislative", "credibility": 8, "feed_type": "rss", "subcategory": "research"},
    {"name": "Claremont Institute", "url": "https://claremontinstitute.substack.com/feed", "category": "legislative", "credibility": 8, "feed_type": "rss", "subcategory": "research"},

    # --- Federal Pro-Life / Family ---
    {"name": "Focus on the Family", "url": "https://dailycitizen.focusonthefamily.com/feed/", "category": "legislative", "credibility": 8, "feed_type": "rss", "subcategory": "regulations"},
    {"name": "Students for Life", "url": "https://studentsforlife.org/feed/", "category": "legislative", "credibility": 8, "feed_type": "rss", "subcategory": "regulations"},

    # --- Federal Religious Liberty (via OpenRSS) ---
    {"name": "Alliance Defending Freedom", "url": "https://openrss.org/adflegal.org/media", "category": "legal", "credibility": 8, "feed_type": "rss"},
    {"name": "First Liberty Institute", "url": "https://openrss.org/firstliberty.org/news", "category": "legal", "credibility": 8, "feed_type": "rss"},

    # --- Federal Immigration ---
    {"name": "Center for Immigration Studies", "url": "https://cis.org/blog/feed", "category": "legislative", "credibility": 8, "feed_type": "rss", "subcategory": "regulations"},
    {"name": "FAIR", "url": "https://immigrationreform.com/feed", "category": "legislative", "credibility": 8, "feed_type": "rss", "subcategory": "regulations"},
    {"name": "NumbersUSA", "url": "https://openrss.org/www.numbersusa.com/blog", "category": "legislative", "credibility": 8, "feed_type": "rss", "subcategory": "regulations"},

    # --- Free Speech ---
    {"name": "FIRE", "url": "https://www.thefire.org/feed/", "category": "legal", "credibility": 7, "feed_type": "rss"},

    # --- Oregon State ---
    {"name": "Oregon Catalyst", "url": "https://oregoncatalyst.com/feed/", "category": "state-legislative", "credibility": 8, "feed_type": "rss", "subcategory": "oregon"},

    # --- Washington State ---
    {"name": "Washington Policy Center", "url": "https://www.washingtonpolicy.org/rss", "category": "state-legislative", "credibility": 8, "feed_type": "rss", "subcategory": "washington"},

    # --- California ---
    {"name": "Pacific Legal Foundation", "url": "https://pacificlegal.org/feed/", "category": "state-legislative", "credibility": 8, "feed_type": "rss", "subcategory": "california"},
]

RUMBLE_SOURCES = [
    # --- Rumble Feeds (via OpenRSS) ---
    # These creators also have YouTube/podcast feeds in other categories;
    # Rumble copies are cross-posted into the 'rumble' section automatically.
    {"name": "Redacted (Rumble)", "url": "https://openrss.org/rumble.com/c/Redacted", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Nick Shirley (Rumble)", "url": "https://openrss.org/rumble.com/c/NickShirley", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Ian Carroll (Rumble)", "url": "https://openrss.org/rumble.com/c/IanCarrollShow", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Candace Owens (Rumble)", "url": "https://openrss.org/rumble.com/c/RealCandaceO", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Tucker Carlson (Rumble)", "url": "https://openrss.org/rumble.com/c/TuckerCarlson", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Timcast (Rumble)", "url": "https://openrss.org/rumble.com/c/Timcast", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Ben Shapiro (Rumble)", "url": "https://openrss.org/rumble.com/c/BenShapiro", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Steven Crowder (Rumble)", "url": "https://openrss.org/rumble.com/c/StevenCrowder", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Russell Brand (Rumble)", "url": "https://openrss.org/rumble.com/c/russellbrand", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Charlie Kirk (Rumble)", "url": "https://openrss.org/rumble.com/c/CharlieKirk", "category": "politics", "credibility": 8, "feed_type": "rss"},
    {"name": "Matt Gaetz (Rumble)", "url": "https://openrss.org/rumble.com/c/c-6007494", "category": "politics", "credibility": 8, "feed_type": "rss"},
]

ALL_SOURCES = (
    NEWS_SOURCES + MEDICAL_SOURCES + LEGAL_SOURCES + TECH_SOURCES +
    FINANCE_SOURCES + SCIENCE_SOURCES + EDUCATION_SOURCES +
    ENVIRONMENT_SOURCES + POLITICS_SOURCES + MARKETS_SOURCES +
    EPSTEIN_SOURCES + CHARLIE_KIRK_SOURCES + LEGISLATIVE_SOURCES +
    STATE_LEGISLATIVE_SOURCES + ADVOCACY_SOURCES + RUMBLE_SOURCES
)

SPECIAL_SECTION_KEYWORDS = {
    "epstein-files": [
        "epstein", "jeffrey epstein", "ghislaine maxwell",
        "epstein files", "epstein documents", "epstein list",
        "epstein island", "epstein client", "epstein victim",
        "epstein associate", "epstein flight", "lolita express",
        "little st. james", "little saint james",
        "jean-luc brunel", "brunel", "les wexner", "wexner",
        "epstein sealed", "epstein unsealed", "epstein deposition",
        "epstein trafficking", "epstein coverup", "epstein cover-up",
        "epstein conspiracy", "epstein blackmail", "epstein suicide",
        "epstein murder", "epstein plea deal", "epstein settlement",
        "virginia giuffre", "giuffre", "sarah ransome",
        "courtney wild", "epstein accuser", "epstein survivor",
    ],
    "charlie-kirk": [
        "charlie kirk", "charliekirk",
        "turning point usa", "turning point action", "tpusa",
        "amfest", "americafest",
        "kirk conservative", "kirk maga", "kirk trump",
        "kirk campus", "kirk university", "kirk students",
        "kirk podcast", "kirk show", "kirk rally",
        "kirk interview", "kirk debate", "kirk speech",
    ],
    "polymarket": [
        "polymarket", "prediction market", "prediction markets",
        "betting odds", "market odds", "forecast odds",
        "what are the odds", "probability of", "chances of",
        "will trump", "will biden", "election odds",
        "prediction contract", "event contract",
    ],
}

CATEGORIES = {
    "polymarket": {
        "label": "Prediction Markets",
        "description": "Live Odds & Forecasts from Polymarket",
        "icon": "chart-bar",
        "color": "#6366f1",
        "subcategories": {
            "elections": {"label": "Politics", "description": "Elections, policy, and political forecasts"},
            "global-conflicts": {"label": "Global Conflicts", "description": "Geopolitical events, wars & international disputes"},
            "crypto": {"label": "Crypto", "description": "Cryptocurrency price predictions and events"},
            "finance": {"label": "Finance", "description": "Financial markets, IPOs & economic forecasts"},
            "tech": {"label": "Tech", "description": "Technology predictions and AI forecasts"},
            "culture": {"label": "Culture", "description": "Pop culture, entertainment & media predictions"},
            "sports": {"label": "Sports", "description": "Sports betting odds and predictions"},
        },
    },
    "polls": {
        "label": "Polls",
        "description": "National & State Election Polls with Pollster Ratings",
        "icon": "chart-pie",
        "color": "#8b5cf6",
    },
    "news": {"label": "News", "description": "General & World News", "icon": "newspaper", "color": "#3b82f6"},
    "epstein-files": {
        "label": "Epstein Files",
        "description": "Jeffrey Epstein Case Documents & Coverage",
        "icon": "folder-open",
        "color": "#b91c1c",
        "subcategories": {
            "news": {"label": "News Coverage", "description": "Articles from investigative and news sources"},
            "court-docs": {"label": "Court Documents", "description": "DOJ case files, depositions, and evidence"},
            "flight-logs": {"label": "Flight Logs", "description": "Lolita Express manifests and travel records"},
        },
    },
    "charlie-kirk": {"label": "Charlie Kirk", "description": "Charlie Kirk Coverage & Commentary", "icon": "megaphone", "color": "#1d4ed8"},
    "medical": {"label": "Medical", "description": "Health, Medicine & Research", "icon": "heartbeat", "color": "#ef4444"},
    "legal": {"label": "Legal", "description": "Law, Courts & Regulations", "icon": "gavel", "color": "#a855f7"},
    "tech": {"label": "Technology", "description": "Innovation, AI & Digital Frontier", "icon": "microchip", "color": "#06b6d4"},
    "finance": {"label": "Finance", "description": "Banking, Economy & Business", "icon": "chart-line", "color": "#22c55e"},
    "science": {"label": "Science", "description": "Research, Space & Discovery", "icon": "flask", "color": "#f59e0b"},
    "education": {"label": "Education", "description": "Higher Ed, Research & Learning", "icon": "graduation-cap", "color": "#ec4899"},
    "environment": {"label": "Environment", "description": "Climate, Energy & Conservation", "icon": "leaf", "color": "#10b981"},
    "politics": {"label": "Politics", "description": "Policy, Government & Analysis", "icon": "landmark", "color": "#8b5cf6"},
    "markets": {"label": "Markets", "description": "Stocks, Crypto, Precious Metals & Trading", "icon": "coins", "color": "#f97316"},
    "legislative": {
        "label": "Legislative Watch",
        "description": "Federal Bills, Hearings, Regulations & Executive Orders",
        "icon": "scroll",
        "color": "#991b1b",
        "subcategories": {
            "executive-orders": {"label": "Executive Orders", "description": "Presidential executive orders, memoranda & proclamations"},
            "house": {"label": "House", "description": "Bills and proceedings on the House floor"},
            "senate": {"label": "Senate", "description": "Bills and proceedings on the Senate floor"},
            "bills": {"label": "Bills", "description": "Active bills moving through Congress"},
            "enacted": {"label": "Signed Into Law", "description": "Public laws signed by the President"},
            "hearings": {"label": "Committees & Hearings", "description": "Committee hearings, reports & prints"},
            "regulations": {"label": "Regulations", "description": "Federal Register rules & Code of Federal Regulations"},
            "research": {"label": "CRS & CBO Analysis", "description": "Congressional Research Service & budget analysis"},
            "budget": {"label": "Budget & Economy", "description": "Federal budget documents & economic indicators"},
        },
    },
    "youtube": {
        "label": "YouTube",
        "description": "Video Content from YouTube Creators",
        "icon": "youtube",
        "color": "#FF0000",
    },
    "rumble": {
        "label": "Rumble",
        "description": "Video Content from Rumble Creators",
        "icon": "rumble",
        "color": "#85c742",
    },
    "state-legislative": {
        "label": "State Watch",
        "description": "Oregon, Washington & California State Government",
        "icon": "building",
        "color": "#0e7490",
        "subcategories": {
            "oregon": {"label": "Oregon", "description": "Oregon state legislature, governor & agencies"},
            "washington": {"label": "Washington", "description": "Washington state legislature, governor & agencies"},
            "california": {"label": "California", "description": "California state legislature, governor & agencies"},
        },
    },
}
