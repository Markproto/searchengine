# Open Source Search Engine Projects Research

> Compiled: 2026-02-09
> Purpose: Evaluate open-source projects for building a custom news/medical/law search engine

---

## Table of Contents

1. [Brave Search](#1-brave-search)
2. [Open Source News Search Engines & Aggregators](#2-open-source-news-search-engines--aggregators)
3. [Open Source Web Crawlers](#3-open-source-web-crawlers)
4. [Meta Search Engines (SearXNG)](#4-meta-search-engines-searxng)
5. [Full-Stack Search (Crawling + Indexing + UI)](#5-full-stack-search-crawling--indexing--ui)
6. [Lightweight Search Alternatives (Meilisearch, Typesense, etc.)](#6-lightweight-search-alternatives)
7. [Medical Search Tools](#7-medical-search-tools)
8. [Legal Search Tools](#8-legal-search-tools)
9. [Recommendations Summary](#9-recommendations-summary)

---

## 1. Brave Search

**Verdict: Brave Search's backend is NOT open source. They provide API wrappers and peripheral tools only.**

| Project | GitHub URL | Stars | License | Description |
|---------|-----------|-------|---------|-------------|
| brave/brave-browser | https://github.com/brave/brave-browser | ~21,300 | MPL-2.0 | The browser itself, not the search engine backend |
| brave/brave-core | https://github.com/brave/brave-core | ~2,970 | MPL-2.0 | Core engine for Brave browser (C++) |
| brave/brave-search-mcp-server | https://github.com/brave/brave-search-mcp-server | N/A | MIT | MCP server for Brave Search API (web, local, image, video, news search) |
| brave/brave-search-extension | https://github.com/brave/brave-search-extension | N/A | N/A | Browser extension for Brave Search |
| kayvane1/brave-api | https://github.com/kayvane1/brave-api | N/A | N/A | Third-party Python wrapper for Brave Search API |

**Usefulness for our project:** LOW. Brave Search's actual crawling, indexing, and ranking engine is proprietary. The API could be used as a data source (like SearXNG uses multiple engines), but we cannot self-host or modify the search logic. The MCP server could be useful if we want to integrate Brave results as one of many sources.

---

## 2. Open Source News Search Engines & Aggregators

| Project | GitHub URL | Stars | Language | License | Description |
|---------|-----------|-------|----------|---------|-------------|
| newspaper3k/newspaper4k | https://github.com/codelucas/newspaper | ~14,900 | Python | MIT | News article extraction & NLP (metadata, full text, summaries, keywords) |
| FreshRSS | https://github.com/FreshRSS/FreshRSS | ~13,700 | PHP | AGPL-3.0 | Self-hosted RSS feed aggregator, multi-user, API for mobile clients |
| Trafilatura | https://github.com/adbar/trafilatura | ~5,200 | Python | Apache-2.0 | Web text extraction & crawling; outputs CSV, JSON, HTML, MD, TXT, XML |
| auto-news | https://github.com/finaldie/auto-news | N/A | Python | N/A | LLM-powered aggregator (Tweets, RSS, YouTube, Reddit, web articles) |
| news-please | https://github.com/fhamborg/news-please | N/A | Python | Apache-2.0 | Integrated web crawler + extractor for news; stores to Elasticsearch/PostgreSQL |
| RISJbot | https://github.com/pmyteh/RISJbot | N/A | Python | N/A | Scrapy-based project for extracting text/metadata from news websites |
| raggregate | https://github.com/sjuxax/raggregate | N/A | Python | N/A | Reddit-like open source news aggregator |

**Usefulness for our project:** HIGH.
- **news-please** is the most directly useful -- it's a ready-made news crawler that extracts structured data and stores to Elasticsearch/PostgreSQL.
- **Trafilatura** is excellent for content extraction from any web page (news, medical, legal).
- **newspaper3k/4k** is the go-to for article metadata extraction.
- **FreshRSS** could serve as a model for RSS-based news ingestion.

---

## 3. Open Source Web Crawlers

| Project | GitHub URL | Stars | Language | License | Description |
|---------|-----------|-------|----------|---------|-------------|
| Crawl4AI | https://github.com/unclecode/crawl4ai | ~58,000 | Python | Apache-2.0 | LLM-friendly crawler; outputs clean markdown/JSON; semantic search built-in |
| Scrapy | https://github.com/scrapy/scrapy | ~53,000+ | Python | BSD | The standard Python crawling/scraping framework |
| Crawlee (Apify) | https://github.com/apify/crawlee-python | ~6,000+ | Python/JS | Apache-2.0 | Browser fingerprint rotation, proxy management, session management |
| Apache StormCrawler | https://github.com/apache/stormcrawler | N/A | Java | Apache-2.0 | Stream-based crawling (Apache TLP since June 2025); used by CommonCrawl for news |
| Apache Nutch | https://github.com/apache/nutch | N/A | Java | Apache-2.0 | Highly modular architecture; plug-ins for parsing, retrieval, querying, clustering |
| mediacrawl | https://github.com/opensanctions/mediacrawl | N/A | Python | N/A | Lightweight crawler for journalistic articles (storyweb ecosystem) |
| Sky | https://github.com/kootenpv/sky | N/A | Python | N/A | Fast async news crawler with boilerplate removal and NLP keyword detection |

**Usefulness for our project:** VERY HIGH.
- **Scrapy** is the proven foundation -- extensible, well-documented, massive ecosystem.
- **Crawl4AI** is ideal if we want LLM-ready output (markdown/JSON) for RAG pipelines.
- **news-please** (built on Scrapy) handles news-specific crawling out of the box.
- **Apache StormCrawler** is the enterprise-grade option for high-throughput crawling.
- **mediacrawl** is interesting for investigative/legal journalism content.

---

## 4. Meta Search Engines (SearXNG)

| Project | GitHub URL | Stars | Language | License | Description |
|---------|-----------|-------|----------|---------|-------------|
| SearXNG | https://github.com/searxng/searxng | ~24,700 | Python | AGPL-3.0 | Meta search aggregating 70+ engines; privacy-focused; self-hostable |

**Key Details:**
- Supports 70+ search engines (Google, Bing, DuckDuckGo, Qwant, etc.)
- Categories: Web, Images, Videos, **News**, Social Media, Music, Files, IT, **Science**
- No user tracking or profiling
- Docker deployment available
- Active development (last updated Feb 6, 2026)
- Growing ecosystem: MCP server integrations, LLM integrations (LiteLLM)

**Usefulness for our project:** HIGH.
- Could serve as the initial search backend while we build our own index.
- The "News" and "Science" categories are directly relevant.
- Can be self-hosted for privacy/control.
- Perplexica (see below) uses SearXNG as its search backend -- proving this pattern works.
- However, it's a meta-engine (queries other engines), not its own index.

---

## 5. Full-Stack Search (Crawling + Indexing + UI)

| Project | GitHub URL | Stars | Language | License | Description |
|---------|-----------|-------|----------|---------|-------------|
| **Perplexica** | https://github.com/ItzCrazyKns/Perplexica | ~27,700 | TypeScript | MIT | AI-powered answer engine; uses SearXNG + LLMs; search modes; source citations |
| **Stract** | https://github.com/StractOrg/stract | N/A | Rust | AGPL-3.0 | Full independent search engine with own crawler + index; "Optics" for custom rankings |
| **Marginalia Search** | https://github.com/MarginaliaSearch/MarginaliaSearch | ~1,489 | Java | N/A | Independent index; text-focused; can run as white-label search for your own data |
| **mwmbl** | https://github.com/mwmbl/mwmbl | ~1,732 | Python | AGPL-3.0 | Non-profit search engine; distributed crawling via browser extension; community rankings |
| **OpenSearchServer** | https://github.com/jaeksoft/opensearchserver | N/A | Java | GPL-3.0 | Enterprise search on Lucene; web UI; crawlers for web/file/database |
| **Gigablast** | https://github.com/gigablast/open-source-search-engine | N/A | C/C++ | N/A | Distributed search engine + spider; older project (2017) |
| **YourSearch** | https://github.com/fifthsegment/yoursearch | N/A | N/A | N/A | Simple web indexer + search; crawler + server; designed for caching sites |

**Usefulness for our project:** VERY HIGH.
- **Perplexica** is the strongest candidate to fork/adapt. It has AI-powered search with source citations, uses SearXNG as backend, supports multiple LLM providers, and has a polished UI. MIT-licensed.
- **Stract** is impressive as a fully independent search engine (own crawler + index + UI) written in Rust. Its "Optics" feature for custom ranking is exactly what we'd need for domain-specific search.
- **Marginalia Search** can be run as a white-label search engine for your own crawled data -- directly applicable for building a domain-specific search.
- **mwmbl** demonstrates distributed crawling + centralized indexing in Python -- good architecture reference.

---

## 6. Lightweight Search Alternatives

| Project | GitHub URL | Stars | Language | License | Description |
|---------|-----------|-------|----------|---------|-------------|
| **Meilisearch** | https://github.com/meilisearch/meilisearch | ~55,700 | Rust | MIT | Typo-tolerant, instant search; hybrid (keyword + semantic); dashboard UI |
| **Typesense** | https://github.com/typesense/typesense | ~25,100 | C++ | GPL-3.0 | In-memory search; sub-50ms responses; query-time configuration; HA clustering |
| **ZincSearch** | https://github.com/zincsearch/zincsearch | N/A | Go | Apache-2.0 | Elasticsearch drop-in replacement; <100MB RAM; Vue-based web UI; beta |
| **OpenSearch** | https://github.com/opensearch-project/OpenSearch | N/A | Java | Apache-2.0 | AWS fork of Elasticsearch; full-featured distributed search |
| **Apache Solr** | https://github.com/apache/solr | N/A | Java | Apache-2.0 | Mature enterprise search platform built on Lucene |

**Comparison for our use case:**

| Feature | Meilisearch | Typesense | ZincSearch | OpenSearch |
|---------|------------|-----------|------------|-----------|
| Setup complexity | Very low | Low | Very low | High |
| RAM requirement | Moderate (LMDB) | High (all in RAM) | Low (<100MB) | High |
| License | MIT | GPL-3.0 | Apache-2.0 | Apache-2.0 |
| Hybrid search (keyword + semantic) | Yes | Yes | No | Yes (with plugins) |
| Scaling | Single node (enterprise sharding) | Replicated clusters | Single node | Distributed shards |
| Best for | Fast prototyping, CMS, apps | High-traffic, e-commerce | Log analytics, lightweight | Enterprise, large scale |

**Usefulness for our project:** VERY HIGH.
- **Meilisearch** is the top recommendation for a startup phase: MIT-licensed, instant setup, typo-tolerant search, built-in dashboard, and hybrid search (keyword + semantic). Ideal for indexing news/medical/legal articles.
- **Typesense** is better if we need query-time flexibility and high-availability clustering.
- **ZincSearch** is a fallback option if we want absolute minimum resource usage.
- **OpenSearch** is the path if we need Elasticsearch-level features without the Elastic license concerns.

---

## 7. Medical Search Tools

| Project | GitHub URL | Stars | Language | Description |
|---------|-----------|-------|----------|-------------|
| **paperscraper** | https://github.com/jannisborn/paperscraper | N/A | Python | Scrapes PubMed, arXiv, medRxiv, bioRxiv, chemRxiv; full-text via BioC-PMC API; supports publisher APIs (Wiley, Elsevier) |
| **async-pubmed-scraper** | https://github.com/IliaZenkov/async-pubmed-scraper | N/A | Python | Async PubMed search + concurrent extraction; outputs DataFrame/CSV |
| **ScrapeMed** | https://github.com/danielfrees/scrapemed | N/A | Python | PubMed Central scraper; used by Duke University; LangChain + ChromaDB integration for embeddings |
| **PubMed-MCP-Server** | https://github.com/Augmented-Nature/PubMed-MCP-Server | N/A | Python | 16 MCP tools for PubMed access; 36M+ citations; search by author/journal/MeSH terms |
| **health-data-scraper** | https://github.com/gasci/health-data-scraper | N/A | Python | Mines health data from social media + validates against PubMed |
| **AMG-RAG** | (GitHub Topics: pubmed) | N/A | Python | Agentic Medical Graph-RAG; automates Medical Knowledge Graph construction |

**Usefulness for our project:** HIGH.
- **paperscraper** is the most mature tool -- handles multiple sources, publisher APIs, and keyword queries. Ideal as a medical content pipeline.
- **ScrapeMed** with LangChain/ChromaDB integration is valuable if we want semantic search over medical literature.
- **PubMed-MCP-Server** provides ready-made structured access to 36M+ biomedical citations.
- We could combine paperscraper (ingestion) + Meilisearch/Typesense (indexing) for a medical search vertical.

---

## 8. Legal Search Tools

| Project | GitHub URL | Stars | Language | License | Description |
|---------|-----------|-------|----------|---------|-------------|
| **CourtListener** | https://github.com/freelawproject/courtlistener | ~843 | Python | AGPL | Fully-searchable archive: opinions, oral arguments, judges, financial records, federal filings |
| **Juriscraper** | https://github.com/freelawproject/juriscraper | ~534 | Python | BSD-2-Clause | API to scrape American court websites for metadata |
| **RECAP** | (Free Law Project) | N/A | JS | N/A | Browser extension to access/archive PACER federal court documents for free |
| **Open Legal Data Platform (OLDP)** | https://github.com/openlegaldata/oldp | N/A | Python | N/A | Open Legal Data Platform with legal text processing resources |
| **Awesome Legal Data** | https://github.com/openlegaldata/awesome-legal-data | N/A | N/A | N/A | Curated datasets and resources for legal text processing |
| **LawGlance** | https://github.com/lawglance/lawglance | N/A | N/A | N/A | Free open-source RAG-based AI legal assistant |
| **Open Law Library** | https://github.com/openlawlibrary | N/A | N/A | N/A | Tools for legal code/statute publishing (active Oct 2025) |
| **Eyecite** | (Free Law Project) | N/A | Python | N/A | Legal citation finder/extractor |

**Free Law Project Semantic Search API** (launched Nov 2025): Free Law Project now offers a semantic search API for legal documents, which could be integrated directly.

**Usefulness for our project:** VERY HIGH.
- **CourtListener + Juriscraper** from Free Law Project is the gold standard for US legal data. It already provides a searchable archive with its own API.
- **Juriscraper** can feed our own index with court metadata.
- **OLDP** provides a model for building a legal data platform.
- **LawGlance** demonstrates RAG-based legal search, which is the modern approach.
- The Free Law Project's **Semantic Search API** could be integrated as a legal data source.

---

## 9. Recommendations Summary

### Recommended Architecture

```
[Data Sources / Crawlers]
        |
        v
[Content Extraction & Processing]
        |
        v
[Search Index (Meilisearch or Typesense)]
        |
        v
[AI Layer (LLM for summarization/RAG)]
        |
        v
[Search UI (Perplexica-style or custom)]
```

### Top Picks by Component

#### Crawling & Ingestion
| Component | Recommended Tool | Why |
|-----------|-----------------|-----|
| News crawling | **news-please** | Purpose-built for news; Elasticsearch/PostgreSQL output |
| General crawling | **Scrapy** or **Crawl4AI** | Scrapy for control; Crawl4AI for LLM-ready output |
| Medical content | **paperscraper** + **PubMed E-utilities** | Covers PubMed, arXiv, medRxiv, bioRxiv |
| Legal content | **Juriscraper** + **CourtListener API** | US court system coverage |
| Content extraction | **Trafilatura** | Best-in-class web text extraction |

#### Indexing & Search
| Component | Recommended Tool | Why |
|-----------|-----------------|-----|
| Primary search index | **Meilisearch** | MIT license, instant setup, hybrid search, typo-tolerant |
| Alternative | **Typesense** | Better clustering, query-time config |
| Full-text + scale | **OpenSearch** | When you outgrow Meilisearch |

#### Search UI & AI
| Component | Recommended Tool | Why |
|-----------|-----------------|-----|
| AI-powered search UI | **Perplexica** (fork/adapt) | MIT license, SearXNG backend, LLM integration, polished UI |
| Meta-search backend | **SearXNG** | 70+ engines, privacy-focused, self-hostable |
| Independent search | **Stract** | Full stack (crawler + index + UI) in Rust; "Optics" for custom ranking |

### Quick-Start Path (MVP)

1. Deploy **SearXNG** for immediate meta-search across news/science
2. Deploy **Meilisearch** for our own index
3. Set up **news-please** to crawl target news sources into Meilisearch
4. Set up **paperscraper** for medical literature ingestion
5. Integrate **Juriscraper** / CourtListener API for legal content
6. Fork **Perplexica** for the AI-powered search UI
7. Connect LLMs for summarization and RAG

### Enterprise Path (Scale)

1. Replace Meilisearch with **OpenSearch** for distributed indexing
2. Replace meta-search with **Stract** or **Marginalia** (own index)
3. Build custom crawlers on **Scrapy** / **StormCrawler**
4. Add semantic/vector search via embeddings
