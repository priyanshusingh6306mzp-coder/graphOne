"""Flatten nested record dicts into exact dot-notation Sheet columns (stdlib only).

Pydantic's serialization_alias does not flatten nested models, so we dump the model
(`record.model_dump(mode="json")`) and flatten the resulting dict here.
"""
from typing import Any, Dict, List

COLUMNS: Dict[str, List[str]] = {
    "STARTUP": ["schemaVersion", "recordType", "source.name", "source.url",
                "content.entityName", "content.data.employeeCount", "collectedAt"],
    "PRODUCT": ["schemaVersion", "recordType", "source.name", "source.url",
                "content.startupName", "content.pricingModel", "collectedAt"],
    "RESEARCH_PAPER": ["schemaVersion", "recordType", "source.name", "source.url",
                       "content.title", "content.authors", "content.paper_url",
                       "content.github_url", "content.github_stars",
                       "content.published_date", "collectedAt"],
    "JOB": ["schemaVersion", "recordType", "source.name", "source.url",
            "content.company", "content.title", "content.date", "content.is_remote",
            "content.role_family", "collectedAt"],
    "NEWS": ["schemaVersion", "recordType", "source.name", "source.url",
             "content.title", "content.date", "content.summary", "content.entities",
             "collectedAt"],
    "ENTITY_MAPPING_LOG": ["raw_name", "canonical_name", "match_method", "score",
                           "entity_type", "source.url", "collectedAt"],
}


def flatten(data: Dict[str, Any], parent: str = "", sep: str = ".") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in data.items():
        name = f"{parent}{sep}{key}" if parent else key
        if isinstance(value, dict):
            out.update(flatten(value, name, sep))
        elif isinstance(value, (list, tuple)):
            out[name] = "; ".join(str(v) for v in value)
        else:
            out[name] = value
    return out


def to_row(record: Dict[str, Any], record_type: str) -> List[Any]:
    """Return values in the exact column order for the given tab; None -> empty cell."""
    flat = flatten(record)
    return ["" if flat.get(col) is None else flat.get(col) for col in COLUMNS[record_type]]
