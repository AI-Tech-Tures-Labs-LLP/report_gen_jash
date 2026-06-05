"""Schema / relationships introspection endpoints: /schema, /relationships."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/schema")
def schema_endpoint():
    from db.schema import get_schema
    return get_schema()


@router.get("/relationships")
def relationships_endpoint():
    from db.relationships import discover_relationships
    rels = discover_relationships()
    return [
        {
            "table_a": r.table_a, "column_a": r.column_a,
            "table_b": r.table_b, "column_b": r.column_b,
            "confidence": r.confidence, "source": r.source,
        }
        for r in rels
    ]
