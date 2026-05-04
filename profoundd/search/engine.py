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
            "subcategory": {"type": "keyword"},
            "source_name": {"type": "keyword"},
            "source_credibility": {"type": "integer"},
            "source_sponsors": {"type": "keyword"},
            "admin_boost": {"type": "integer"},
            "url": {"type": "keyword"},
            "image_url": {"type": "keyword", "ignore_above": 2000},
            "tags": {"type": "keyword"},
            "published_at": {"type": "date"},
            "crawled_at": {"type": "date"},
            "result_type": {"type": "keyword"},
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


# Separate index for Epstein court documents (100K+ PDFs from DOJ release)
EPSTEIN_DOC_INDEX_NAME = "profoundd_epstein_docs"
CLIMATE_DOC_INDEX_NAME = "profoundd_climate_docs"
WEF_DOC_INDEX_NAME = "profoundd_wef_docs"

EPSTEIN_DOC_MAPPING = {
    "mappings": {
        "properties": {
            "title": {
                "type": "text",
                "analyzer": "english",
                "fields": {"raw": {"type": "keyword", "ignore_above": 512}},
            },
            "content": {"type": "text", "analyzer": "english"},
            "summary": {"type": "text", "analyzer": "english"},
            "bates_number": {"type": "keyword"},
            "dataset_number": {"type": "integer"},
            "page_count": {"type": "integer"},
            "source_name": {"type": "keyword"},
            "source_url": {"type": "keyword"},
            "category": {"type": "keyword"},
            "result_type": {"type": "keyword"},
            "file_path": {"type": "keyword"},
            "custodian": {"type": "keyword"},
            "doc_date": {
                "type": "date",
                "format": "yyyy-MM-dd||yyyy-MM||yyyy||epoch_millis",
                "ignore_malformed": True,
            },
            "indexed_at": {"type": "date"},
            "tags": {"type": "keyword"},
        }
    },
    "settings": {
        "number_of_shards": 2,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "english": {"type": "english"}
            }
        },
    },
}


BUSINESS_INDEX_NAME = "profoundd_businesses"

BUSINESS_MAPPING = {
    "mappings": {
        "properties": {
            "osm_id": {"type": "keyword"},
            "name": {
                "type": "text",
                "analyzer": "english",
                "fields": {
                    "raw": {"type": "keyword", "ignore_above": 256}
                }
            },
            "business_type": {"type": "keyword"},
            "business_category": {"type": "keyword"},
            "address": {"type": "text", "analyzer": "standard"},
            "street": {"type": "keyword"},
            "housenumber": {"type": "keyword"},
            "city": {"type": "keyword"},
            "state": {"type": "keyword"},
            "postcode": {"type": "keyword"},
            "phone": {"type": "keyword"},
            "website": {"type": "keyword"},
            "email": {"type": "keyword"},
            "opening_hours": {"type": "text"},
            "cuisine": {"type": "keyword"},
            "brand": {"type": "keyword"},
            "location": {"type": "geo_point"},
            "tags": {"type": "keyword"},
            "indexed_at": {"type": "date"},
        }
    },
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
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

    def create_epstein_doc_index(self):
        """Create the Epstein documents index if it doesn't exist."""
        if not self.es.indices.exists(index=EPSTEIN_DOC_INDEX_NAME):
            self.es.indices.create(
                index=EPSTEIN_DOC_INDEX_NAME,
                mappings=EPSTEIN_DOC_MAPPING["mappings"],
                settings=EPSTEIN_DOC_MAPPING["settings"],
            )
            logger.info("Created index: %s", EPSTEIN_DOC_INDEX_NAME)
        else:
            try:
                self.es.indices.put_mapping(
                    index=EPSTEIN_DOC_INDEX_NAME,
                    properties=EPSTEIN_DOC_MAPPING["mappings"]["properties"],
                )
            except Exception as e:
                logger.warning("Could not update epstein doc mapping: %s", e)
            logger.info("Index already exists: %s", EPSTEIN_DOC_INDEX_NAME)

    def bulk_index_epstein_docs(self, docs):
        """Bulk index Epstein court documents. Returns count indexed."""
        if not docs:
            return 0
        actions = []
        for doc in docs:
            doc_id = hashlib.md5(doc.get("bates_number", doc.get("file_path", "")).encode()).hexdigest()
            clean = {k: v for k, v in doc.items() if not k.startswith("_")}
            clean.setdefault("result_type", "document")
            clean.setdefault("category", "epstein-files")
            actions.append({"index": {"_index": EPSTEIN_DOC_INDEX_NAME, "_id": doc_id}})
            actions.append(clean)
        try:
            result = self.es.bulk(operations=actions, refresh=False)
            indexed = sum(1 for item in result["items"] if item["index"]["status"] in (200, 201))
            logger.info("Bulk indexed %d/%d epstein docs", indexed, len(docs))
            return indexed
        except Exception as e:
            logger.error("Bulk index epstein docs failed: %s", e)
            return 0

    def search_epstein_docs(self, query, page=1, per_page=20,
                            date_from=None, date_to=None,
                            custodian=None, dataset=None, sort_by="relevance"):
        """Search the Epstein documents index with optional filters."""
        from_offset = (page - 1) * per_page

        # Build query: simple_query_string supports AND/OR/NOT/"phrases" natively
        if query:
            main_query = {
                "simple_query_string": {
                    "query": query,
                    "fields": ["title^3", "summary^2", "content", "bates_number^5", "custodian^2"],
                    "default_operator": "AND",
                }
            }
        else:
            main_query = {"match_all": {}}

        # Build filter clauses
        filter_clauses = []
        if date_from or date_to:
            date_range = {}
            if date_from:
                date_range["gte"] = date_from
            if date_to:
                date_range["lte"] = date_to
            filter_clauses.append({"range": {"doc_date": date_range}})
        if custodian:
            filter_clauses.append({"term": {"custodian": custodian}})
        if dataset:
            filter_clauses.append({"term": {"dataset_number": int(dataset)}})

        # Wrap in bool query if filters exist
        if filter_clauses:
            es_query = {"bool": {"must": [main_query], "filter": filter_clauses}}
        else:
            es_query = main_query

        body = {
            "query": es_query,
            "from": from_offset,
            "size": per_page,
            "highlight": {
                "fields": {
                    "content": {"fragment_size": 200, "number_of_fragments": 2},
                    "title": {},
                },
                "pre_tags": ["<mark>"],
                "post_tags": ["</mark>"],
            },
        }

        # Sorting
        if sort_by == "date":
            body["sort"] = [{"doc_date": {"order": "desc", "missing": "_last"}}, "_score"]

        try:
            result = self.es.search(index=EPSTEIN_DOC_INDEX_NAME, body=body)
            docs = []
            for hit in result["hits"]["hits"]:
                doc = hit["_source"]
                doc["_score"] = hit["_score"]
                doc["_highlights"] = hit.get("highlight", {})
                # Ensure url field exists for template compatibility
                if not doc.get("url"):
                    bates = doc.get("bates_number", "")
                    doc["url"] = f"/epstein-docs/{bates}" if bates else doc.get("source_url", "#")
                docs.append(doc)
            return {
                "articles": docs,
                "total": result["hits"]["total"]["value"],
                "page": page,
                "per_page": per_page,
            }
        except Exception as e:
            logger.error("Epstein doc search failed: %s", e)
            return {"articles": [], "total": 0, "page": page, "per_page": per_page}

    def search_climate_docs(self, query, page=1, per_page=20, city=None, doc_id=None, sort_by="relevance"):
        """Search Oregon climate/planning docs (Ashland CEAP, Grants Pass SEAP, Medford CFA/TSP).

        Each ES doc is one PDF page, so results are page-level hits with highlights.
        Queries containing known labels like "agenda 2030" or "15-minute city" are
        transparently expanded to the corpus's actual terminology.
        """
        from profoundd.search.synonyms import expand_query
        from_offset = (page - 1) * per_page
        expansion_note = None
        effective_query = query
        if query:
            effective_query, expansion_note = expand_query(query)
            main_query = {
                "simple_query_string": {
                    "query": effective_query,
                    "fields": ["content", "title^2", "document_name^2", "city^2", "tags"],
                    "default_operator": "AND",
                }
            }
        else:
            main_query = {"match_all": {}}

        filter_clauses = []
        if city:
            filter_clauses.append({"term": {"city": city}})
        if doc_id:
            filter_clauses.append({"term": {"doc_id": doc_id}})

        if filter_clauses:
            es_query = {"bool": {"must": [main_query], "filter": filter_clauses}}
        else:
            es_query = main_query

        body = {
            "query": es_query,
            "from": from_offset,
            "size": per_page,
            "highlight": {
                "fields": {
                    "content": {"fragment_size": 220, "number_of_fragments": 3},
                },
                "pre_tags": ["<mark>"],
                "post_tags": ["</mark>"],
            },
        }
        if sort_by == "date":
            body["sort"] = [{"adopted_date": {"order": "desc", "missing": "_last"}}, "_score"]

        try:
            result = self.es.search(index=CLIMATE_DOC_INDEX_NAME, body=body)
            docs = []
            for hit in result["hits"]["hits"]:
                doc = hit["_source"]
                doc["_score"] = hit["_score"]
                doc["_highlights"] = hit.get("highlight", {})
                doc["url"] = f"/climate-docs/{doc.get('doc_id')}/{doc.get('page_number')}"
                docs.append(doc)
            return {
                "articles": docs,
                "total": result["hits"]["total"]["value"],
                "page": page,
                "per_page": per_page,
                "expansion": expansion_note,
            }
        except Exception as e:
            logger.error("Climate doc search failed: %s", e)
            return {"articles": [], "total": 0, "page": page, "per_page": per_page, "expansion": expansion_note}

    def get_climate_page(self, doc_id, page_number):
        """Fetch a single climate-doc page by doc_id + page_number."""
        es_id = f"{doc_id}-p{int(page_number):04d}"
        try:
            result = self.es.get(index=CLIMATE_DOC_INDEX_NAME, id=es_id)
            return result["_source"]
        except NotFoundError:
            return None
        except Exception as e:
            logger.error("Climate page fetch failed for %s p%s: %s", doc_id, page_number, e)
            return None

    def count_climate_docs(self):
        """Return the total number of climate-doc pages indexed."""
        try:
            result = self.es.count(index=CLIMATE_DOC_INDEX_NAME)
            return int(result.get("count", 0))
        except Exception as e:
            logger.debug("Climate count failed: %s", e)
            return 0

    def list_climate_docs(self):
        """Return one summary row per unique doc_id (for browse pages)."""
        body = {
            "size": 0,
            "aggs": {
                "docs": {
                    "terms": {"field": "doc_id", "size": 50},
                    "aggs": {"sample": {"top_hits": {"size": 1, "_source": ["city", "document_name", "adopted_date", "source_url", "doc_id"]}}},
                }
            },
        }
        try:
            r = self.es.search(index=CLIMATE_DOC_INDEX_NAME, body=body)
            out = []
            for b in r["aggregations"]["docs"]["buckets"]:
                src = b["sample"]["hits"]["hits"][0]["_source"]
                src["page_count"] = b["doc_count"]
                out.append(src)
            out.sort(key=lambda r: (r.get("city", ""), r.get("document_name", "")))
            return out
        except Exception as e:
            logger.error("list_climate_docs failed: %s", e)
            return []

    def search_wef_docs(self, query, page=1, per_page=20, doc_id=None, year=None, sort_by="relevance"):
        """Search WEF publications, one ES doc per PDF page."""
        from profoundd.search.synonyms import expand_query
        from_offset = (page - 1) * per_page
        expansion_note = None
        if query:
            effective_query, expansion_note = expand_query(query)
            main_query = {
                "simple_query_string": {
                    "query": effective_query,
                    "fields": ["content", "title^2", "document_name^3", "tags"],
                    "default_operator": "AND",
                }
            }
        else:
            main_query = {"match_all": {}}

        filter_clauses = []
        if doc_id:
            filter_clauses.append({"term": {"doc_id": doc_id}})
        if year:
            filter_clauses.append({"term": {"year": int(year)}})

        es_query = (
            {"bool": {"must": [main_query], "filter": filter_clauses}}
            if filter_clauses else main_query
        )

        body = {
            "query": es_query,
            "from": from_offset,
            "size": per_page,
            "highlight": {
                "fields": {"content": {"fragment_size": 220, "number_of_fragments": 3}},
                "pre_tags": ["<mark>"],
                "post_tags": ["</mark>"],
            },
        }
        if sort_by == "date":
            body["sort"] = [{"year": {"order": "desc", "missing": "_last"}}, "_score"]

        try:
            result = self.es.search(index=WEF_DOC_INDEX_NAME, body=body)
            docs = []
            for hit in result["hits"]["hits"]:
                doc = hit["_source"]
                doc["_score"] = hit["_score"]
                doc["_highlights"] = hit.get("highlight", {})
                doc["url"] = f"/wef-docs/{doc.get('doc_id')}/{doc.get('page_number')}"
                docs.append(doc)
            return {
                "articles": docs,
                "total": result["hits"]["total"]["value"],
                "page": page,
                "per_page": per_page,
                "expansion": expansion_note,
            }
        except Exception as e:
            logger.error("WEF doc search failed: %s", e)
            return {"articles": [], "total": 0, "page": page, "per_page": per_page, "expansion": expansion_note}

    def get_wef_page(self, doc_id, page_number):
        es_id = f"{doc_id}-p{int(page_number):04d}"
        try:
            result = self.es.get(index=WEF_DOC_INDEX_NAME, id=es_id)
            return result["_source"]
        except NotFoundError:
            return None
        except Exception as e:
            logger.error("WEF page fetch failed for %s p%s: %s", doc_id, page_number, e)
            return None

    def count_wef_docs(self):
        try:
            return int(self.es.count(index=WEF_DOC_INDEX_NAME).get("count", 0))
        except Exception as e:
            logger.debug("WEF count failed: %s", e)
            return 0

    def list_wef_docs(self, limit=500):
        body = {
            "size": 0,
            "aggs": {
                "docs": {
                    "terms": {"field": "doc_id", "size": limit},
                    "aggs": {"sample": {"top_hits": {"size": 1, "_source": ["doc_id", "document_name", "year", "source_url", "filename", "tags"]}}},
                }
            },
        }
        try:
            r = self.es.search(index=WEF_DOC_INDEX_NAME, body=body)
            out = []
            for b in r["aggregations"]["docs"]["buckets"]:
                src = b["sample"]["hits"]["hits"][0]["_source"]
                src["page_count"] = b["doc_count"]
                out.append(src)
            out.sort(key=lambda r: (-(r.get("year") or 0), r.get("document_name", "")))
            return out
        except Exception as e:
            logger.error("list_wef_docs failed: %s", e)
            return []

    def get_epstein_doc(self, bates_number):
        """Fetch a single Epstein document by Bates number."""
        doc_id = hashlib.md5(bates_number.encode()).hexdigest()
        try:
            result = self.es.get(index=EPSTEIN_DOC_INDEX_NAME, id=doc_id)
            return result["_source"]
        except NotFoundError:
            return None
        except Exception as e:
            logger.error("Epstein doc fetch failed for %s: %s", bates_number, e)
            return None

    def count_epstein_docs(self):
        """Return the total number of Epstein documents in the index."""
        try:
            result = self.es.count(index=EPSTEIN_DOC_INDEX_NAME)
            return int(result.get("count", 0))
        except Exception as e:
            logger.debug("Epstein count failed: %s", e)
            return 0

    def scroll_epstein_bates(self, chunk_size=50000, chunk_index=0):
        """Return (bates_number, indexed_at) tuples for sitemap chunk N.

        Uses search_after pagination keyed on bates_number. Page N is the
        N-th block of chunk_size documents sorted by bates_number.
        """
        # We need to skip chunk_index * chunk_size docs and return the next chunk_size.
        # ES limits "from" to 10,000 by default, so we paginate via search_after.
        page_size = 5000  # per-request page for search_after
        target_start = chunk_index * chunk_size
        target_end = target_start + chunk_size

        results = []
        fetched = 0
        search_after = None

        try:
            while fetched < target_end:
                body = {
                    "size": page_size,
                    "_source": ["bates_number", "indexed_at"],
                    "sort": [{"bates_number": "asc"}],
                    "query": {"exists": {"field": "bates_number"}},
                }
                if search_after:
                    body["search_after"] = search_after
                resp = self.es.search(index=EPSTEIN_DOC_INDEX_NAME, body=body)
                hits = resp["hits"]["hits"]
                if not hits:
                    break
                for hit in hits:
                    # Only collect hits in the target window
                    if target_start <= fetched < target_end:
                        src = hit["_source"]
                        bates = src.get("bates_number")
                        if bates:
                            results.append((bates, src.get("indexed_at", "")))
                    fetched += 1
                    if fetched >= target_end:
                        break
                # Advance search_after to last hit's sort key
                search_after = hits[-1]["sort"]
            return results
        except Exception as e:
            logger.error("scroll_epstein_bates failed (chunk %d): %s", chunk_index, e)
            return results

    # In-memory caches for expensive aggregation queries (TTL-based)
    _source_names_cache = {"data": None, "expires": 0}
    _epstein_facets_cache = {"data": None, "expires": 0}

    def get_source_names(self):
        """Return distinct source_name values from the articles index.
        Cached in memory for 1 hour (source list changes rarely).
        """
        import time
        now = time.time()
        if self._source_names_cache["data"] is not None and now < self._source_names_cache["expires"]:
            return self._source_names_cache["data"]
        try:
            result = self.es.search(
                index=self.index_name,
                body={"size": 0, "aggs": {"sources": {"terms": {"field": "source_name", "size": 500}}}},
            )
            data = sorted(b["key"] for b in result["aggregations"]["sources"]["buckets"])
            self._source_names_cache["data"] = data
            self._source_names_cache["expires"] = now + 3600
            return data
        except Exception as e:
            logger.debug("get_source_names failed: %s", e)
            return self._source_names_cache["data"] or []

    def get_epstein_facets(self):
        """Return custodian list and dataset numbers for Epstein filter dropdowns.
        Cached in memory for 1 hour (Epstein docs never change).
        """
        import time
        now = time.time()
        if self._epstein_facets_cache["data"] is not None and now < self._epstein_facets_cache["expires"]:
            return self._epstein_facets_cache["data"]
        try:
            result = self.es.search(
                index=EPSTEIN_DOC_INDEX_NAME,
                body={
                    "size": 0,
                    "aggs": {
                        "custodians": {"terms": {"field": "custodian", "size": 200}},
                        "datasets": {"terms": {"field": "dataset_number", "size": 20}},
                    },
                },
            )
            custodians = sorted(
                b["key"] for b in result["aggregations"]["custodians"]["buckets"] if b["key"]
            )
            datasets = sorted(
                b["key"] for b in result["aggregations"]["datasets"]["buckets"]
            )
            data = {"custodians": custodians, "datasets": datasets}
            self._epstein_facets_cache["data"] = data
            self._epstein_facets_cache["expires"] = now + 3600
            return data
        except Exception as e:
            logger.debug("get_epstein_facets failed: %s", e)
            return self._epstein_facets_cache["data"] or {"custodians": [], "datasets": []}

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

    def update_subcategory_by_source(self, source_name, subcategory):
        """Update subcategory on ALL articles from a given source."""
        try:
            result = self.es.update_by_query(
                index=self.index_name,
                body={
                    "query": {"term": {"source_name": source_name}},
                    "script": {
                        "source": "ctx._source.subcategory = params.subcat",
                        "lang": "painless",
                        "params": {"subcat": subcategory},
                    },
                },
                refresh=True,
            )
            updated = result.get("updated", 0)
            return updated
        except Exception as e:
            logger.error("Failed to update subcategory for '%s': %s", source_name, e)
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

    def add_category(self, article_url, category):
        """Copy an article into an additional category by indexing a duplicate with a new category-prefixed URL."""
        article = self.get_article(article_url)
        if not article:
            return False
        new_doc = dict(article)
        new_doc["category"] = category
        new_doc["url"] = f"{article_url}#cat-{category}"
        try:
            self.index_article(new_doc)
            return True
        except Exception as e:
            logger.error("Failed to add category %s for %s: %s", category, article_url, e)
            return False

    def bulk_index(self, articles):
        """Bulk index multiple articles."""
        if not articles:
            return 0

        actions = []
        for article in articles:
            doc_id = hashlib.md5(article.get("url", "").encode()).hexdigest()
            actions.append({"index": {"_index": self.index_name, "_id": doc_id}})
            # Strip internal metadata fields (prefixed with _) before indexing
            doc = {k: v for k, v in article.items() if not k.startswith("_")}
            actions.append(doc)

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

    def search(self, query, category=None, subcategory=None, page=1, per_page=20,
               sort_by="relevance", source_filter=None, date_from=None, date_to=None,
               exclude_sponsored=False):
        """
        Search articles with custom ranking.

        Ranking formula: BM25 relevance * dampened_credibility * admin_boost * recency_decay
        Credibility is dampened (1.0–1.6x) so relevance dominates.
        Multi-word queries get phrase boosts for exact matches.
        Duplicate titles are collapsed to show only the best-scoring version.
        """
        must_clauses = []
        filter_clauses = []

        # Main text query — simple_query_string supports boolean operators
        # (AND, OR, NOT, -term, "exact phrase", parentheses) natively.
        phrase_boosts = []
        name_expansions = []
        if query:
            from profoundd.search.name_synonyms import expand_names
            effective_query, name_expansions = expand_names(query)
            must_clauses.append({
                "simple_query_string": {
                    "query": effective_query,
                    "fields": ["title^3", "summary^2", "content"],
                    "default_operator": "AND",
                }
            })

            # Phrase boost still uses the ORIGINAL query so the user's exact
            # spelling stays the strongest signal.
            clean_query = query.replace(" AND ", " ").replace(" OR ", " ").replace(" NOT ", " ")
            clean_query = clean_query.replace("+", "").replace("-", "").replace("|", "")
            clean_query = clean_query.replace("(", "").replace(")", "").strip()
            clean_terms = clean_query.split()
            if len(clean_terms) >= 2 and '"' not in query:
                phrase_boosts = [
                    {"match_phrase": {"title": {"query": clean_query, "boost": 5}}},
                    {"match_phrase": {"summary": {"query": clean_query, "boost": 3}}},
                    {"match_phrase": {"content": {"query": clean_query, "boost": 1}}},
                ]

        # Category filter
        if category and category != "all":
            filter_clauses.append({"term": {"category": category}})

        # Subcategory filter (e.g. executive-orders, house, senate)
        if subcategory and subcategory != "all":
            filter_clauses.append({"term": {"subcategory": subcategory}})

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

        # Exclude Pfizer-sponsored sources
        if exclude_sponsored:
            filter_clauses.append({"bool": {"must_not": {"exists": {"field": "source_sponsors"}}}})

        # Build the inner bool query
        bool_query = {
            "bool": {
                "must": must_clauses if must_clauses else [{"match_all": {}}],
                "filter": filter_clauses,
            }
        }

        # Wrap in function_score: credibility, admin boost, and recency
        # Credibility is dampened to a 1.0–1.6x range so relevance dominates.
        # Admin boost: 5=neutral, 10=2x, 1=0.2x.
        # Recency: Gaussian decay — full score for 1h, halves every 3 days.
        scored_query = {
            "function_score": {
                "query": bool_query,
                "functions": [
                    {
                        "script_score": {
                            "script": {
                                "source": "double cred = doc['source_credibility'].size() > 0 ? doc['source_credibility'].value : 5; return 1.0 + (cred - 1) * 0.067;"
                            }
                        }
                    },
                    {
                        "script_score": {
                            "script": {
                                "source": "Math.max(0.1, (doc['admin_boost'].size() > 0 ? doc['admin_boost'].value : 5) / 5.0)"
                            }
                        }
                    },
                    {
                        "gauss": {
                            "published_at": {
                                "origin": "now",
                                "scale": "3d",
                                "offset": "1h",
                                "decay": 0.3
                            }
                        }
                    },
                ],
                "boost_mode": "multiply",
                "score_mode": "multiply",
            }
        }

        # Phrase boosts (optional — only for multi-word queries)
        bool_query["bool"]["should"] = phrase_boosts

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
                min_score = top_score * 0.15
                raw_articles = [a for a in raw_articles if a["_score"] >= min_score]
                # Keep ES total for pagination — don't reset to filtered count

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
                "name_expansions": name_expansions,
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
    def _ensure_credibility_floor(articles, size):
        """Ensure at least half the trending articles come from credibility 8+ sources.

        Splits articles into high-cred (8+) and lower-cred buckets, then
        interleaves: takes from high-cred first to fill at least half the
        slots, then fills the rest from lower-cred, preserving original order.
        """
        if not articles:
            return articles

        min_high = (size + 1) // 2  # at least half (rounded up)
        high = [a for a in articles if (a.get("source_credibility") or 5) >= 8]
        low = [a for a in articles if (a.get("source_credibility") or 5) < 8]

        # Already meets the floor?
        if len(high) >= min_high:
            return articles[:size]

        # Not enough high-cred articles exist — just return what we have
        if len(high) < min_high and len(high) + len(low) <= size:
            return articles[:size]

        # Rebuild: take min_high from high, fill rest from low
        result = high[:min_high]
        remaining_slots = size - len(result)
        result.extend(low[:remaining_slots])

        # If we still have room and more high-cred articles, add them
        if len(result) < size:
            result.extend(high[min_high:size - len(result) + min_high])

        return result[:size]

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

            # Credibility floor: ensure at least half come from 8+ rated sources
            articles = self._ensure_credibility_floor(articles, size)

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

    def get_spelling_suggestion(self, query):
        """
        Use ES suggest API to find spelling corrections.
        Returns corrected query string, or None if no correction found.
        """
        try:
            result = self.es.search(
                index=self.index_name,
                body={
                    "suggest": {
                        "text": query,
                        "title_suggest": {
                            "phrase": {
                                "field": "title",
                                "size": 1,
                                "gram_size": 3,
                                "direct_generator": [{
                                    "field": "title",
                                    "suggest_mode": "popular",
                                }],
                                "highlight": {
                                    "pre_tag": "",
                                    "post_tag": "",
                                },
                            }
                        },
                    },
                    "size": 0,
                },
            )
            suggestions = result.get("suggest", {}).get("title_suggest", [])
            if suggestions and suggestions[0].get("options"):
                corrected = suggestions[0]["options"][0]["text"]
                # Only suggest if it's actually different
                if corrected.lower().strip() != query.lower().strip():
                    return corrected
        except Exception as e:
            logger.debug("Spelling suggestion failed: %s", e)
        return None

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
                "source_sponsors", "subcategory", "category", "published_at", "image_url", "tags",
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

    # ------------------------------------------------------------------ #
    #  Local Business Search (OpenStreetMap)                               #
    # ------------------------------------------------------------------ #

    def create_business_index(self):
        """Create the profoundd_businesses index if it doesn't exist."""
        try:
            if not self.es.indices.exists(index=BUSINESS_INDEX_NAME):
                self.es.indices.create(index=BUSINESS_INDEX_NAME, body=BUSINESS_MAPPING)
                logger.info("Created business index: %s", BUSINESS_INDEX_NAME)
        except Exception as e:
            logger.error("Failed to create business index: %s", e)

    def delete_business_index(self):
        """Delete the business index for clean re-imports."""
        try:
            if self.es.indices.exists(index=BUSINESS_INDEX_NAME):
                self.es.indices.delete(index=BUSINESS_INDEX_NAME)
                logger.info("Deleted business index: %s", BUSINESS_INDEX_NAME)
        except Exception as e:
            logger.error("Failed to delete business index: %s", e)

    def bulk_index_businesses(self, businesses):
        """Bulk index business listings. Returns count of indexed docs."""
        if not businesses:
            return 0
        actions = []
        for biz in businesses:
            doc_id = hashlib.md5(biz["osm_id"].encode()).hexdigest()
            doc = {k: v for k, v in biz.items() if not k.startswith("_")}
            actions.append({"index": {"_index": BUSINESS_INDEX_NAME, "_id": doc_id}})
            actions.append(doc)
        try:
            result = self.es.bulk(operations=actions, refresh=True)
            success = sum(
                1 for item in result["items"]
                if item.get("index", {}).get("status") in (200, 201)
            )
            logger.info("Bulk indexed %d/%d businesses", success, len(businesses))
            return success
        except Exception as e:
            logger.error("Bulk index businesses failed: %s", e)
            return 0

    def search_businesses(self, query, location=None, radius_km=50, page=1, per_page=5):
        """Search the business index.

        Args:
            query: Search text (business name, type, cuisine, etc.)
            location: dict with 'lat' and 'lon' keys, or None
            radius_km: Search radius in km (only used if location provided)
            page: Page number
            per_page: Results per page
        Returns:
            List of business dicts with _provider and _distance metadata.
        """
        must_clauses = [
            {
                "multi_match": {
                    "query": query,
                    "fields": ["name^3", "business_type^2", "cuisine^2", "tags", "address", "brand"],
                    "type": "best_fields",
                    "fuzziness": "AUTO",
                }
            }
        ]
        filter_clauses = []

        if location and "lat" in location and "lon" in location:
            filter_clauses.append({
                "geo_distance": {
                    "distance": f"{radius_km}km",
                    "location": {"lat": location["lat"], "lon": location["lon"]},
                }
            })

        body = {
            "query": {
                "bool": {
                    "must": must_clauses,
                    "filter": filter_clauses if filter_clauses else None,
                }
            },
            "size": per_page,
            "from": (page - 1) * per_page,
        }

        # Remove None filter
        if not filter_clauses:
            body["query"]["bool"].pop("filter", None)

        # Sort by distance if location provided, otherwise by relevance
        if location and "lat" in location and "lon" in location:
            body["sort"] = [
                {
                    "_geo_distance": {
                        "location": {"lat": location["lat"], "lon": location["lon"]},
                        "order": "asc",
                        "unit": "mi",
                        "distance_type": "arc",
                    }
                }
            ]
            body["script_fields"] = {
                "distance_mi": {
                    "script": {
                        "source": "doc['location'].arcDistance(params.lat, params.lon) * 0.000621371",
                        "params": {"lat": location["lat"], "lon": location["lon"]},
                    }
                }
            }
            # ES omits _source when script_fields is set unless requested explicitly.
            body["_source"] = True

        try:
            result = self.es.search(index=BUSINESS_INDEX_NAME, body=body)
            businesses = []
            for hit in result["hits"]["hits"]:
                biz = hit["_source"]
                biz["_provider"] = "local_business"
                biz["_enhanced"] = True
                biz["_score"] = hit.get("_score", 0)
                # Add distance if available
                if "fields" in hit and "distance_mi" in hit["fields"]:
                    biz["_distance_mi"] = round(hit["fields"]["distance_mi"][0], 1)
                elif "sort" in hit and location:
                    biz["_distance_mi"] = round(hit["sort"][0], 1)
                businesses.append(biz)
            return businesses
        except Exception as e:
            logger.error("search_businesses failed: %s", e)
            return []

    def get_business_count(self):
        """Get total count of indexed businesses."""
        try:
            result = self.es.count(index=BUSINESS_INDEX_NAME)
            return result["count"]
        except Exception:
            return 0

    def get_business_stats(self):
        """Get business count breakdown by state and category."""
        try:
            body = {
                "size": 0,
                "aggs": {
                    "by_state": {
                        "terms": {"field": "state", "size": 50}
                    },
                    "by_category": {
                        "terms": {"field": "business_category", "size": 20}
                    },
                    "by_city": {
                        "terms": {"field": "city", "size": 50}
                    },
                }
            }
            result = self.es.search(index=BUSINESS_INDEX_NAME, body=body)
            return {
                "total": self.get_business_count(),
                "by_state": {
                    b["key"]: b["doc_count"]
                    for b in result["aggregations"]["by_state"]["buckets"]
                },
                "by_category": {
                    b["key"]: b["doc_count"]
                    for b in result["aggregations"]["by_category"]["buckets"]
                },
                "by_city": {
                    b["key"]: b["doc_count"]
                    for b in result["aggregations"]["by_city"]["buckets"]
                },
            }
        except Exception as e:
            logger.error("get_business_stats failed: %s", e)
            return {"total": 0, "by_state": {}, "by_category": {}, "by_city": {}}
