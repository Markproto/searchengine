"""
Elasticsearch integration for Profoundd.
Handles indexing articles and executing search queries with custom ranking.
"""
import logging
from datetime import datetime, timezone
from elasticsearch import Elasticsearch, NotFoundError

logger = logging.getLogger(__name__)

# Index mapping for articles
ARTICLE_MAPPING = {
    "mappings": {
        "properties": {
            "title": {"type": "text", "analyzer": "english", "boost": 2.0},
            "summary": {"type": "text", "analyzer": "english", "boost": 1.5},
            "content": {"type": "text", "analyzer": "english"},
            "author": {"type": "keyword"},
            "category": {"type": "keyword"},
            "source_name": {"type": "keyword"},
            "source_credibility": {"type": "integer"},
            "url": {"type": "keyword"},
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

    def is_available(self):
        """Check if Elasticsearch is running."""
        try:
            return self.es.ping()
        except Exception:
            return False

    def create_index(self):
        """Create the articles index if it doesn't exist."""
        if not self.es.indices.exists(index=self.index_name):
            self.es.indices.create(index=self.index_name, body=ARTICLE_MAPPING)
            logger.info("Created index: %s", self.index_name)
        else:
            logger.info("Index already exists: %s", self.index_name)

    def index_article(self, article_data):
        """Index a single article."""
        doc_id = article_data.get("url", "")
        try:
            result = self.es.index(
                index=self.index_name,
                id=hash(doc_id),
                document=article_data,
            )
            return result.get("_id")
        except Exception as e:
            logger.error("Failed to index article: %s", e)
            return None

    def bulk_index(self, articles):
        """Bulk index multiple articles."""
        if not articles:
            return 0

        actions = []
        for article in articles:
            doc_id = hash(article.get("url", ""))
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

    def search(self, query, category=None, page=1, per_page=20, sort_by="relevance",
               source_filter=None, date_from=None, date_to=None):
        """
        Search articles with custom ranking.

        Ranking formula:
        - Text relevance (Elasticsearch BM25) * relevance_weight
        - Source credibility boost
        - Recency boost (newer = higher)
        """
        must_clauses = []
        filter_clauses = []

        # Main text query
        if query:
            must_clauses.append({
                "multi_match": {
                    "query": query,
                    "fields": ["title^3", "summary^2", "content"],
                    "type": "best_fields",
                    "fuzziness": "AUTO",
                }
            })

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

        # Build the query
        search_body = {
            "query": {
                "bool": {
                    "must": must_clauses if must_clauses else [{"match_all": {}}],
                    "filter": filter_clauses,
                }
            },
            "highlight": {
                "fields": {
                    "title": {"number_of_fragments": 0},
                    "summary": {"fragment_size": 200, "number_of_fragments": 1},
                    "content": {"fragment_size": 200, "number_of_fragments": 2},
                },
                "pre_tags": ["<mark>"],
                "post_tags": ["</mark>"],
            },
            "from": (page - 1) * per_page,
            "size": per_page,
        }

        # Sorting
        if sort_by == "date":
            search_body["sort"] = [{"published_at": {"order": "desc"}}, "_score"]
        elif sort_by == "credibility":
            search_body["sort"] = [{"source_credibility": {"order": "desc"}}, "_score"]
        else:
            # Default: relevance with recency boost
            search_body["query"]["bool"]["should"] = [
                {
                    "range": {
                        "published_at": {
                            "gte": "now-24h",
                            "boost": 5,
                        }
                    }
                },
                {
                    "range": {
                        "published_at": {
                            "gte": "now-7d",
                            "boost": 2,
                        }
                    }
                },
                {
                    "range": {
                        "source_credibility": {
                            "gte": 8,
                            "boost": 3,
                        }
                    }
                },
            ]

        try:
            result = self.es.search(index=self.index_name, body=search_body)
            hits = result["hits"]
            total = hits["total"]["value"]

            articles = []
            for hit in hits["hits"]:
                article = hit["_source"]
                article["_score"] = hit["_score"]
                article["_highlights"] = hit.get("highlight", {})
                articles.append(article)

            return {
                "articles": articles,
                "total": total,
                "page": page,
                "per_page": per_page,
                "pages": (total + per_page - 1) // per_page,
                "query": query,
                "category": category,
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

    def get_trending(self, category=None, hours=24, size=10):
        """Get trending/recent articles."""
        filter_clauses = [
            {"range": {"published_at": {"gte": f"now-{hours}h"}}}
        ]
        if category and category != "all":
            filter_clauses.append({"term": {"category": category}})

        body = {
            "query": {
                "bool": {
                    "filter": filter_clauses,
                    "should": [
                        {"range": {"source_credibility": {"gte": 8, "boost": 3}}}
                    ],
                }
            },
            "sort": [{"published_at": {"order": "desc"}}],
            "size": size,
        }

        try:
            result = self.es.search(index=self.index_name, body=body)
            return [hit["_source"] for hit in result["hits"]["hits"]]
        except Exception:
            return []

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
