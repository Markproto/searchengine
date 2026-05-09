"""Profoundd Curator — autonomous source-quality monitor.

Runs nightly (APScheduler 03:00). Samples newly-ingested articles, scores
their domains via a ModernBERT classifier + rule layer, emits proposals
into the CuratorProposal review queue. Admin approves at
/admin/curator-queue; approved proposals are applied via curator.apply,
which captures before_value for Layer-2 rollback.
"""
