"""Paradigm shift detection — Cypher query."""

PARADIGM_SHIFT_QUERY = """
MATCH (parent:Finding)<-[:CONTRADICTS]-(challenger:Finding)
WHERE parent.status = 'active'
WITH parent, challenger,
     sum([(parent)<-[:SUPPORTS]-(s) | s.weight]) as parent_weight,
     sum([(challenger)<-[:SUPPORTS]-(s) | s.weight]) as challenger_weight
WHERE challenger_weight > parent_weight * 1.5
RETURN parent, challenger, parent_weight, challenger_weight
ORDER BY (challenger_weight - parent_weight) DESC
"""
