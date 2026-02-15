"""
Elasticsearch integration for Profoundd.
Handles indexing articles and executing search queries with custom ranking.
"""
import hashlib
import logging
from datetime import datetime, timezone
from elasticsearch import Elasticsearch, NotFoundError

logger = logging.getLogger(__name__)

# Index mapping for articles
ARTICLE_MAPPING = {
    "mappings": {
        "properties": {
            "title": {
                "type": "text",
                "analyzer": "english",
                "fields": {
                    "raw": {"type": "keyword", "ignore_above": 512}
                }
            },
            "summary": {"type": "text", "analyzer": "english"},
            "content": {"type": "text", "analyzer": "english"},
            "author": {"type": "keyword"},
            "category": {"type": "keyword"},
            "source_name": {"type": "keyword"},
            "source_credibility": {"type": "integer"},
            "source_sponsors": {"type": "keyword"},
            "admin_boost": {"type": "integer"},
            "url": {"type": "keyword"},
            "image_url": {"type": "keyword", "ignore_above": 2000},
            "tags": {"type": "keyword"},
            "published_at": {"type": "date"},
            "crawled_at": {"type": "date"},
        }
    },
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "english": {
                    "type": "english",
                }
            }
        }
    }
}


class SearchEngine:
    """Manages Elasticsearch operations for Profoundd."""

    def __init__(self, elasticsearch_url="http://localhost:9200"):
        self.es = Elasticsearch(elasticsearch_url)
        self.index_name = "profoundd_articles"
        try:
            if self.is_available():
                self.create_index()
        except Exception as e:
            logger.warning("Could not ensure index on startup: %s", e)

    def is_available(self):
        """Check if Elasticsearch is running."""
        try:
            return self.es.ping()
        except Exception:
            return False

    def create_index(self):
        """Create the articles index if it doesn't exist, and ensure mapping is correct."""
        if not self.es.indices.exists(index=self.index_name):
            self.es.indices.create(
                index=self.index_name,
                mappings=ARTICLE_MAPPING["mappings"],
                settings=ARTICLE_MAPPING["settings"],
            )
            logger.info("Created index: %s", self.index_name)
        else:
            # Ensure title.raw mapping exists (may be missing if index was auto-created)
            try:
                self.es.indices.put_mapping(
                    index=self.index_name,
                    properties=ARTICLE_MAPPING["mappings"]["properties"],
                )
            except Exception as e:
                logger.warning("Could not update mapping: %s", e)
            logger.info("Index already exists: %s", self.index_name)

    def index_article(self, article_data):
        """Index a single article."""
        doc_id = article_data.get("url", "")
        try:
            result = self.es.index(
                index=self.index_name,
                id=hashlib.md5(doc_id.encode()).hexdigest(),
                document=article_data,
            )
            return result.get("_id")
        except Exception as e:
            logger.error("Failed to index article: %s", e)
            return None

    def update_credibility(self, article_url, credibility):
        """Update the source_credibility of an article in ES by its URL."""
        es_id = hashlib.md5(article_url.encode()).hexdigest()
        try:
            self.es.update(
                index=self.index_name,
                id=es_id,
                doc={"source_credibility": credibility},
            )
            return True
        except Exception as e:
            logger.error("Failed to update credibility for %s: %s", article_url, e)
            return False

    def update_credibility_by_source(self, source_name, credibility):
        """Update source_credibility on ALL articles from a given source."""
        try:
            result = self.es.update_by_query(
                index=self.index_name,
                body={
                    "query": {"term": {"source_name": source_name}},
                    "script": {
                        "source": "ctx._source.source_credibility = params.cred",
                        "lang": "painless",
                        "params": {"cred": credibility},
                    },
                },
                refresh=True,
            )
            updated = result.get("updated", 0)
            logger.info("Updated credibility to %d for %d articles from '%s'",
                        credibility, updated, source_name)
            return updated
        except Exception as e:
            logger.error("Failed to bulk-update credibility for '%s': %s", source_name, e)
            return 0

    def update_sponsors_by_source(self, source_name, sponsors):
        """Update source_sponsors on ALL articles from a given source."""
        try:
            result = self.es.update_by_query(
                index=self.index_name,
                body={
                    "query": {"term": {"source_name": source_name}},
                    "script": {
                        "source": "ctx._source.source_sponsors = params.sponsors",
                        "lang": "painless",
                        "params": {"sponsors": sponsors},
                    },
                },
                refresh=True,
            )
            updated = result.get("updated", 0)
            return updated
        except Exception as e:
            logger.error("Failed to update sponsors for '%s': %s", source_name, e)
            return 0

    def get_article(self, article_url):
        """Fetch an article from ES by its URL."""
        es_id = hashlib.md5(article_url.encode()).hexdigest()
        try:
            result = self.es.get(index=self.index_name, id=es_id)
            return result["_source"]
        except Exception as e:
            logger.error("Failed to get article %s: %s", article_url, e)
            return None

    def update_boost(self, article_url, boost):
        """Update the admin_boost of an article in ES by its URL (1-10 scale, 5=neutral)."""
        boost = max(1, min(10, boost))
        es_id = hashlib.md5(article_url.encode()).hexdigest()
        try:
            self.es.update(
                index=self.index_name,
                id=es_id,
                doc={"admin_boost": boost},
            )
            return True
        except Exception as e:
            logger.error("Failed to update boost for %s: %s", article_url, e)
            return False

    def bulk_index(self, articles):
        """Bulk index multiple articles."""
        if not articles:
            return 0

        actions = []
        for article in articles:
            doc_id = hashlib.md5(article.get("url", "").encode()).hexdigest()
            actions.append({"index": {"_index": self.index_name, "_id": doc_id}})
            actions.append(article)

        try:
            result = self.es.bulk(operations=actions, refresh=True)
            indexed = sum(1 for item in result["items"] if item["index"]["status"] in (200, 201))
            logger.info("Bulk indexed %d/%d articles", indexed, len(articles))
            return indexed
        except Exception as e:
            logger.error("Bulk index failed: %s", e)
            return 0

    def delete_article(self, url):
        """Delete an article from the index by its URL. Returns True if deleted."""
        doc_id = hashlib.md5(url.encode()).hexdigest()
        try:
            self.es.delete(index=self.index_name, id=doc_id, ignore=[404])
            logger.info("Deleted article from ES: %s", url)
            return True
        except Exception as e:
            logger.error("Failed to delete article %s: %s", url, e)
            return False

    def article_exists(self, url):
        """Check if an article with this URL already exists in the index."""
        doc_id = hashlib.md5(url.encode()).hexdigest()
        try:
            return self.es.exists(index=self.index_name, id=doc_id)
        except Exception:
            return False

    def title_exists(self, title):
        """Check if an article with this exact title already exists in the index.

        Uses the title.raw keyword field for exact matching — first article
        indexed with a given headline wins (first-come-first-serve dedup).
        """
        try:
            result = self.es.count(
                index=self.index_name,
                query={"term": {"title.raw": title}},
            )
            return result["count"] > 0
        except Exception:
            return False

    def search(self, query, category=None, page=1, per_page=20, sort_by="relevance",
               source_filter=None, date_from=None, date_to=None):
        """
        Search articles with custom ranking.

        Ranking formula: (BM25 relevance + recency boost) * source_credibility
        Credibility is the dominant factor — it multiplies the entire score.
        Duplicate titles are collapsed to show only the best-scoring version.
        """
        must_clauses = []
        filter_clauses = []

        # Main text query
        if query:
            multi_match_query = {
                "query": query,
                "fields": ["title^3", "summary^2", "content"],
                "type": "best_fields",
            }
            query_terms = query.split()
            if len(query_terms) >= 3:
                # For multi-word queries: no fuzziness and require 75% of terms
                # to match. Stemming still causes false positives at 50%
                # (e.g. "electric"→"electr" matches "electricity" in energy articles).
                multi_match_query["minimum_should_match"] = "75%"
            else:
                # Short queries: allow fuzziness for typo correction
                multi_match_query["fuzziness"] = "AUTO"
            must_clauses.append({"multi_match": multi_match_query})

        # Category filter
        if category and category != "all":
            filter_clauses.append({"term": {"category": category}})

        # Source filter
        if source_filter:
            filter_clauses.append({"term": {"source_name": source_filter}})

        # Date range filter
        if date_from or date_to:
            date_range = {}
            if date_from:
                date_range["gte"] = date_from
            if date_to:
                date_range["lte"] = date_to
            filter_clauses.append({"range": {"published_at": date_range}})

        # Build the inner bool query
        bool_query = {
            "bool": {
                "must": must_clauses if must_clauses else [{"match_all": {}}],
                "filter": filter_clauses,
            }
        }

        # Wrap in function_score: credibility multiplies the relevance score
        # A credibility-8 source scores 8x, credibility-7 scores 7x, etc.
        # Admin boost adds a second multiplier: 0=neutral, +5=2x, -5=0.1x
        scored_query = {
            "function_score": {
                "query": bool_query,
                "functions": [
                    {
                        "field_value_factor": {
                            "field": "source_credibility",
                            "factor": 1,
                            "modifier": "none",
                            "missing": 5,
                        }
                    },
                    {
                        "script_score": {
                            "script": {
                                "source": "Math.max(0.1, (doc['admin_boost'].size() > 0 ? doc['admin_boost'].value : 5) / 5.0)"
                            }
                        }
                    }
                ],
                "boost_mode": "multiply",
                "score_mode": "multiply",
            }
        }

        # Add recency boosts inside the bool query
        bool_query["bool"]["should"] = [
            {"range": {"published_at": {"gte": "now-24h", "boost": 3}}},
            {"range": {"published_at": {"gte": "now-7d", "boost": 1}}},
        ]

        search_body = {
            "query": scored_query,
            "highlight": {
                "fields": {
                    "title": {"number_of_fragments": 0},
                    "summary": {"fragment_size": 200, "number_of_fragments": 1},
                    "content": {"fragment_size": 200, "number_of_fragments": 2},
                },
                "pre_tags": ["<mark>"],
                "post_tags": ["</mark>"],
            },
            # Collapse on title to deduplicate the same story from multiple sources
            # Keeps the highest-scoring version (highest credibility * relevance)
            "collapse": {
                "field": "title.raw",
            },
            "from": (page - 1) * per_page,
            "size": per_page,
        }

        # Sorting overrides
        if sort_by == "date":
            search_body["sort"] = [{"published_at": {"order": "desc"}}, "_score"]
        elif sort_by == "credibility":
            search_body["sort"] = [{"source_credibility": {"order": "desc"}}, "_score"]

        try:
            # Fetch extra results so we can diversify sources across credibility tiers
            search_body["size"] = per_page * 2
            result = self.es.search(index=self.index_name, body=search_body)
            hits = result["hits"]
            total = hits["total"]["value"]

            raw_articles = []
            for hit in hits["hits"]:
                article = hit["_source"]
                article["_score"] = hit["_score"]
                article["_highlights"] = hit.get("highlight", {})
                raw_articles.append(article)

            # Score-based filtering: drop articles scoring far below the top result.
            # Catches remaining noise from stemming false positives
            # (e.g. "electr" matching both "electric" and "electricity").
            top_score = raw_articles[0]["_score"] if raw_articles else 0
            if query and raw_articles:
                min_score = top_score * 0.3
                raw_articles = [a for a in raw_articles if a["_score"] >= min_score]
                total = len(raw_articles)

            # Diversify: group by credibility tier, interleave 3 high then 1 lower
            articles = self._diversify_results(raw_articles, per_page)

            # Final pass: admin boost shifts articles up/down in the list
            articles = self._apply_admin_boost_reorder(articles)

            return {
                "articles": articles,
                "total": total,
                "page": page,
                "per_page": per_page,
                "pages": (total + per_page - 1) // per_page,
                "query": query,
                "category": category,
                "top_score": top_score,
            }
        except Exception as e:
            logger.error("Search failed: %s", e)
            return {
                "articles": [],
                "total": 0,
                "page": page,
                "per_page": per_page,
                "pages": 0,
                "query": query,
                "category": category,
                "error": str(e),
            }

    @staticmethod
    def _diversify_results(articles, limit):
        """
        Interleave results so credibility tiers are mixed naturally.
        Shows 3 from the top credibility tier, then 1 from the next tier, repeat.
        Prevents results from looking uniform (all 8s then all 7s).
        """
        if not articles:
            return []

        # Group articles by credibility score, preserving order within each group
        tiers = {}
        for a in articles:
            cred = a.get("source_credibility", 5)
            tiers.setdefault(cred, []).append(a)

        # Sort tiers by credibility descending
        sorted_tiers = sorted(tiers.keys(), reverse=True)
        if len(sorted_tiers) <= 1:
            return articles[:limit]

        top_tier = sorted_tiers[0]
        other_tiers = sorted_tiers[1:]

        result = []
        top_idx = 0
        other_positions = {t: 0 for t in other_tiers}
        current_other = 0

        while len(result) < limit:
            # Add up to 3 from the top tier
            added = 0
            while added < 3 and top_idx < len(tiers[top_tier]) and len(result) < limit:
                result.append(tiers[top_tier][top_idx])
                top_idx += 1
                added += 1

            # Add 1 from the next lower tier (round-robin across lower tiers)
            if len(result) < limit and other_tiers:
                placed = False
                for _ in range(len(other_tiers)):
                    tier = other_tiers[current_other % len(other_tiers)]
                    pos = other_positions[tier]
                    if pos < len(tiers[tier]):
                        result.append(tiers[tier][pos])
                        other_positions[tier] = pos + 1
                        current_other += 1
                        placed = True
                        break
                    current_other += 1
                if not placed and top_idx >= len(tiers[top_tier]):
                    break

            # If top tier exhausted, fill remaining from other tiers in order
            if top_idx >= len(tiers[top_tier]):
                for tier in other_tiers:
                    pos = other_positions[tier]
                    while pos < len(tiers[tier]) and len(result) < limit:
                        result.append(tiers[tier][pos])
                        pos += 1
                    other_positions[tier] = pos
                break

        return result[:limit]

    @staticmethod
    def _diversify_by_source(articles, limit):
        """
        Ensure no single source dominates trending results.
        Caps each source to at most *max_per_source* articles, then
        round-robins across sources (ordered by their best article's
        position) to fill the remaining slots organically.
        """
        if not articles:
            return []

        # For small result sets (landing page) cap tighter than category pages
        max_per_source = 2 if limit <= 10 else 3

        # Group articles by source, preserving ES sort order within each group
        source_buckets = {}
        source_order = []
        for a in articles:
            src = a.get("source_name", "Unknown")
            if src not in source_buckets:
                source_buckets[src] = []
                source_order.append(src)
            source_buckets[src].append(a)

        # Round-robin across sources, taking one article at a time from each
        result = []
        pointers = {src: 0 for src in source_order}
        counts = {src: 0 for src in source_order}

        while len(result) < limit:
            added_this_round = False
            for src in source_order:
                if len(result) >= limit:
                    break
                idx = pointers[src]
                if idx < len(source_buckets[src]) and counts[src] < max_per_source:
                    result.append(source_buckets[src][idx])
                    pointers[src] = idx + 1
                    counts[src] += 1
                    added_this_round = True
            if not added_this_round:
                break

        # If we still haven't filled the page (all sources hit their cap),
        # relax the cap and fill remaining slots in original order
        if len(result) < limit:
            used_urls = {a.get("url") for a in result}
            for a in articles:
                if len(result) >= limit:
                    break
                if a.get("url") not in used_urls:
                    result.append(a)
                    used_urls.add(a.get("url"))

        return result[:limit]

    @staticmethod
    def _apply_admin_boost_reorder(articles):
        """
        Final pass: shift articles up/down based on admin_boost.
        Each boost point away from 5 shifts the article by 2 positions.
        boost 7 → moves up 4 spots, boost 3 → moves down 4 spots, boost 5 → stays.
        """
        if not articles:
            return articles
        indexed = list(enumerate(articles))
        indexed.sort(key=lambda item: item[0] - ((item[1].get("admin_boost") or 5) - 5) * 2)
        return [a for _, a in indexed]

    def get_latest(self, category=None, size=20):
        """Get latest articles with no time filter — reliable fallback."""
        filter_clauses = []
        if category and category != "all":
            filter_clauses.append({"term": {"category": category}})

        inner_query = {
            "bool": {
                "filter": filter_clauses,
            }
        } if filter_clauses else {"match_all": {}}

        body = {
            "query": {
                "function_score": {
                    "query": inner_query,
                    "functions": [
                        {
                            "field_value_factor": {
                                "field": "source_credibility",
                                "factor": 1,
                                "modifier": "none",
                                "missing": 5,
                            }
                        },
                        {
                            "script_score": {
                                "script": {
                                    "source": "Math.max(0.1, (doc['admin_boost'].size() > 0 ? doc['admin_boost'].value : 5) / 5.0)"
                                }
                            }
                        }
                    ],
                    "boost_mode": "multiply",
                    "score_mode": "multiply",
                }
            },
            "sort": [
                {"published_at": {"order": "desc"}},
                {"_score": {"order": "desc"}},
            ],
            "size": size * 3,  # fetch extra so dedup still fills the page
        }

        try:
            result = self.es.search(index=self.index_name, body=body)
            raw = [hit["_source"] for hit in result["hits"]["hits"]]
            deduped = self._dedup_by_title(raw, size * 3)
            diversified = self._diversify_by_source(deduped, size)
            return self._apply_admin_boost_reorder(diversified)
        except Exception as e:
            logger.error("get_latest failed (category=%s): %s", category, e)
            return []

    @staticmethod
    def _dedup_by_title(articles, limit):
        """Remove articles with duplicate titles (Python-level fallback for collapse)."""
        seen = set()
        result = []
        for a in articles:
            key = (a.get("title") or "").strip().lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(a)
            if len(result) >= limit:
                break
        return result

    def get_stats(self):
        """Get index statistics."""
        try:
            stats = self.es.indices.stats(index=self.index_name)
            count = self.es.count(index=self.index_name)
            return {
                "total_articles": count["count"],
                "index_size": stats["_all"]["primaries"]["store"]["size_in_bytes"],
            }
        except Exception:
            return {"total_articles": 0, "index_size": 0}

    def delete_old_articles(self, days=30):
        """Delete articles older than N days to save space."""
        try:
            result = self.es.delete_by_query(
                index=self.index_name,
                body={
                    "query": {
                        "range": {
                            "published_at": {
                                "lt": f"now-{days}d"
                            }
                        }
                    }
                }
            )
            deleted = result.get("deleted", 0)
            logger.info("Deleted %d articles older than %d days", deleted, days)
            return deleted
        except Exception as e:
            logger.error("Failed to delete old articles: %s", e)
            return 0

    def recategorize_by_keywords(self, keywords, new_category):
        """Re-categorize all articles matching any of the keywords to a new category."""
        should_clauses = []
        for kw in keywords:
            should_clauses.append({"match_phrase": {"title": kw}})
            should_clauses.append({"match_phrase": {"summary": kw}})
            should_clauses.append({"match_phrase": {"content": kw}})
            should_clauses.append({"match_phrase": {"tags": kw}})
        try:
            result = self.es.update_by_query(
                index=self.index_name,
                body={
                    "query": {"bool": {"should": should_clauses, "minimum_should_match": 1}},
                    "script": {
                        "source": "ctx._source.category = params.cat",
                        "lang": "painless",
                        "params": {"cat": new_category},
                    },
                },
                refresh=True,
            )
            updated = result.get("updated", 0)
            logger.info("Recategorized %d articles to '%s'", updated, new_category)
            return updated
        except Exception as e:
            logger.error("Failed to recategorize to '%s': %s", new_category, e)
            return 0

    # Sub-topic guarantees: ensure minimum representation of specific topics
    # within a category.  Keys = category name, values = list of
    # (min_count, keywords_list) tuples.
    CATEGORY_SUBTOPIC_GUARANTEES = {
        "markets": [
            (2, ["gold", "silver", "platinum", "palladium", "precious metals",
                  "bullion", "mining", "gold price", "silver price"]),
        ],
    }

    def get_trending(self, category=None, hours=24, size=10):
        """Get trending/recent articles with source diversity."""
        filter_clauses = [
            {"range": {"published_at": {"gte": f"now-{hours}h"}}}
        ]
        if category and category != "all":
            filter_clauses.append({"term": {"category": category}})

        # Fetch extra results so source-diversity filtering still fills the page
        fetch_size = size * 3

        body = {
            "query": {
                "function_score": {
                    "query": {
                        "bool": {
                            "filter": filter_clauses,
                        }
                    },
                    "functions": [
                        {
                            "field_value_factor": {
                                "field": "source_credibility",
                                "factor": 1,
                                "modifier": "none",
                                "missing": 5,
                            }
                        },
                        {
                            "script_score": {
                                "script": {
                                    "source": "Math.max(0.1, (doc['admin_boost'].size() > 0 ? doc['admin_boost'].value : 5) / 5.0)"
                                }
                            }
                        }
                    ],
                    "boost_mode": "multiply",
                    "score_mode": "multiply",
                }
            },
            "collapse": {
                "field": "title.raw",
            },
            "sort": [
                {"_score": {"order": "desc"}},
                {"published_at": {"order": "desc"}},
            ],
            "size": fetch_size,
        }

        try:
            result = self.es.search(index=self.index_name, body=body)
            articles = [hit["_source"] for hit in result["hits"]["hits"]]

            # Diversify: cap per-source articles so no single source dominates
            articles = self._diversify_by_source(articles, size)

            # Enforce sub-topic guarantees for this category
            if category and category in self.CATEGORY_SUBTOPIC_GUARANTEES:
                articles = self._enforce_subtopic_guarantees(
                    articles, category, filter_clauses, size
                )

            # Final pass: admin boost shifts articles up/down in the list
            articles = self._apply_admin_boost_reorder(articles)

            return articles
        except Exception as e:
            logger.error("get_trending failed (category=%s, hours=%d): %s", category, hours, e)
            # Fallback: try without collapse in case title.raw has issues
            try:
                del body["collapse"]
                body["size"] = size * 3
                result = self.es.search(index=self.index_name, body=body)
                raw = [hit["_source"] for hit in result["hits"]["hits"]]
                articles = self._dedup_by_title(raw, size)
                logger.info("get_trending fallback (no collapse) returned %d articles", len(articles))
                return articles
            except Exception as e2:
                logger.error("get_trending fallback also failed: %s", e2)
                return []

    def _enforce_subtopic_guarantees(self, articles, category, base_filters, size):
        """
        Ensure minimum representation of specific sub-topics in results.
        If the main results don't include enough articles matching the
        sub-topic keywords, fetch targeted articles and inject them.
        """
        guarantees = self.CATEGORY_SUBTOPIC_GUARANTEES.get(category, [])
        seen_urls = {a.get("url") for a in articles}

        for min_count, keywords in guarantees:
            # Count how many existing articles match this sub-topic
            matching = [
                a for a in articles
                if self._matches_subtopic(a, keywords)
            ]

            needed = min_count - len(matching)
            if needed <= 0:
                continue

            # Fetch targeted articles for this sub-topic
            try:
                subtopic_body = {
                    "query": {
                        "bool": {
                            "must": [
                                {
                                    "multi_match": {
                                        "query": " ".join(keywords[:5]),
                                        "fields": ["title^3", "summary^2", "content"],
                                        "type": "best_fields",
                                    }
                                }
                            ],
                            "filter": base_filters,
                        }
                    },
                    "collapse": {"field": "title.raw"},
                    "sort": [
                        {"source_credibility": {"order": "desc"}},
                        {"published_at": {"order": "desc"}},
                    ],
                    "size": needed + 5,
                }

                result = self.es.search(index=self.index_name, body=subtopic_body)
                candidates = [hit["_source"] for hit in result["hits"]["hits"]]

                # Insert non-duplicate subtopic articles into the results
                inserted = 0
                insert_pos = min(2, len(articles))  # After the first 2 results
                for candidate in candidates:
                    if candidate.get("url") in seen_urls:
                        continue
                    articles.insert(insert_pos, candidate)
                    seen_urls.add(candidate.get("url"))
                    insert_pos += 1
                    inserted += 1
                    if inserted >= needed:
                        break

                # Trim back to requested size
                articles = articles[:size]

            except Exception as e:
                logger.warning("Subtopic guarantee fetch failed: %s", e)

        return articles

    @staticmethod
    def _matches_subtopic(article, keywords):
        """Check if an article's title or summary mentions any of the sub-topic keywords."""
        text = (
            (article.get("title") or "") + " " + (article.get("summary") or "")
        ).lower()
        return any(kw in text for kw in keywords)

    def get_suggestions(self, query, size=5):
        """Get search suggestions based on article titles."""
        try:
            body = {
                "query": {
                    "multi_match": {
                        "query": query,
                        "fields": ["title"],
                        "type": "phrase_prefix",
                    }
                },
                "_source": ["title", "category", "source_name"],
                "size": size,
            }
            result = self.es.search(index=self.index_name, body=body)
            return [
                {
                    "title": hit["_source"]["title"],
                    "category": hit["_source"].get("category", ""),
                    "source": hit["_source"].get("source_name", ""),
                }
                for hit in result["hits"]["hits"]
            ]
        except Exception:
            return []

    def get_category_counts(self):
        """Get article count per category."""
        try:
            body = {
                "size": 0,
                "aggs": {
                    "categories": {
                        "terms": {"field": "category", "size": 20}
                    }
                }
            }
            result = self.es.search(index=self.index_name, body=body)
            buckets = result["aggregations"]["categories"]["buckets"]
            return {b["key"]: b["doc_count"] for b in buckets}
        except Exception:
            return {}

    def get_topic_feed(self, keywords, categories=None, hours=72, size=30):
        """Get recent articles matching topic keywords, optionally filtered by categories.

        Used by the /api/feed/ endpoints to serve topic-specific feeds
        (e.g. stocks, crypto, metals) from the existing article index.
        """
        filter_clauses = [
            {"range": {"published_at": {"gte": f"now-{hours}h"}}},
        ]
        if categories:
            filter_clauses.append({"terms": {"category": categories}})

        # Match any of the keywords in title, summary, or content
        should_clauses = []
        for kw in keywords:
            should_clauses.append({"match_phrase": {"title": {"query": kw, "boost": 3}}})
            should_clauses.append({"match_phrase": {"summary": {"query": kw, "boost": 2}}})
            should_clauses.append({"match_phrase": {"content": kw}})

        # Also match source names known to be topic-specific
        body = {
            "query": {
                "bool": {
                    "must": [
                        {"bool": {"should": should_clauses, "minimum_should_match": 1}},
                    ],
                    "filter": filter_clauses,
                }
            },
            "sort": [
                {"published_at": {"order": "desc"}},
            ],
            "collapse": {"field": "title.raw"},
            "size": size,
            "_source": [
                "title", "summary", "url", "source_name", "source_credibility",
                "source_sponsors", "category", "published_at", "image_url", "tags",
            ],
        }

        try:
            result = self.es.search(index=self.index_name, body=body)
            articles = [hit["_source"] for hit in result["hits"]["hits"]]
            return self._dedup_by_title(articles, size)
        except Exception as e:
            logger.error("get_topic_feed failed: %s", e)
            return []

    def deduplicate_titles(self):
        """Remove duplicate articles by title, keeping the earliest crawled version.

        First-come-first-serve cleanup: for each title that appears more than
        once, the oldest article (by crawled_at) is kept and all others are
        deleted from the index.
        """
        try:
            body = {
                "size": 0,
                "aggs": {
                    "dup_titles": {
                        "terms": {
                            "field": "title.raw",
                            "min_doc_count": 2,
                            "size": 10000,
                        },
                        "aggs": {
                            "keep": {
                                "top_hits": {
                                    "size": 1,
                                    "sort": [{"crawled_at": {"order": "asc"}}],
                                    "_source": ["url"],
                                }
                            }
                        }
                    }
                }
            }

            result = self.es.search(index=self.index_name, body=body)
            buckets = result["aggregations"]["dup_titles"]["buckets"]

            total_deleted = 0
            for bucket in buckets:
                title = bucket["key"]
                keep_id = bucket["keep"]["hits"]["hits"][0]["_id"]

                del_result = self.es.delete_by_query(
                    index=self.index_name,
                    body={
                        "query": {
                            "bool": {
                                "must": [{"term": {"title.raw": title}}],
                                "must_not": [{"ids": {"values": [keep_id]}}],
                            }
                        }
                    },
                )
                deleted = del_result.get("deleted", 0)
                total_deleted += deleted
                if deleted > 0:
                    logger.info("Dedup: kept 1, deleted %d for: %s", deleted, title[:80])

            if total_deleted:
                self.es.indices.refresh(index=self.index_name)
            logger.info("Title dedup complete: %d duplicate groups, %d articles removed",
                        len(buckets), total_deleted)
            return total_deleted, len(buckets)
        except Exception as e:
            logger.error("deduplicate_titles failed: %s", e)
            return 0, 0
