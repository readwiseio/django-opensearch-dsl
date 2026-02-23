"""Stateless utility functions for OpenSearch alias management.

These functions query OpenSearch for alias/index state and perform
atomic alias operations to enable zero-downtime index deployments.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from opensearchpy import OpenSearch


def generate_versioned_name(alias_name: str) -> str:
    """Generate a timestamped physical index name.

    Parameters
    ----------
    alias_name : str
        The alias (logical) name, e.g. ``products``.

    Returns
    -------
    str
        A name like ``products_20260223143052``.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{alias_name}_{ts}"


def alias_exists(client: OpenSearch, alias_name: str) -> bool:
    """Check whether an alias exists.

    Parameters
    ----------
    client : OpenSearch
        The OpenSearch client.
    alias_name : str
        The alias name to check.

    Returns
    -------
    bool
    """
    return client.indices.exists_alias(name=alias_name)


def get_alias_info(client: OpenSearch, alias_name: str) -> dict[str, Any]:
    """Return the physical indices an alias points to.

    Parameters
    ----------
    client : OpenSearch
        The OpenSearch client.
    alias_name : str
        The alias name.

    Returns
    -------
    dict
        Mapping of physical index names to their alias metadata,
        e.g. ``{"products_20260223143052": {"aliases": {"products": {}}}}``.
        Empty dict if alias does not exist.
    """
    try:
        return client.indices.get_alias(name=alias_name)
    except Exception:
        return {}


def get_active_index(client: OpenSearch, alias_name: str) -> Optional[str]:
    """Return the physical index currently pointed to by the alias.

    Parameters
    ----------
    client : OpenSearch
        The OpenSearch client.
    alias_name : str
        The alias name.

    Returns
    -------
    str or None
        The physical index name, or ``None`` if no alias exists.
    """
    info = get_alias_info(client, alias_name)
    if not info:
        return None
    return next(iter(info))


def get_versioned_indices(client: OpenSearch, alias_name: str) -> list[str]:
    """Return all physical indices matching ``{alias_name}_*``, sorted ascending.

    Parameters
    ----------
    client : OpenSearch
        The OpenSearch client.
    alias_name : str
        The alias name.

    Returns
    -------
    list[str]
        Sorted list of physical index names.
    """
    try:
        indices = list(client.indices.get(index=f"{alias_name}_*").keys())
    except Exception:
        return []
    return sorted(indices)


def get_unaliased_indices(client: OpenSearch, alias_name: str) -> list[str]:
    """Return versioned indices NOT pointed to by the alias.

    Parameters
    ----------
    client : OpenSearch
        The OpenSearch client.
    alias_name : str
        The alias name.

    Returns
    -------
    list[str]
        Sorted list of unaliased physical index names.
    """
    aliased = set(get_alias_info(client, alias_name).keys())
    versioned = get_versioned_indices(client, alias_name)
    return [idx for idx in versioned if idx not in aliased]


def get_newest_unaliased_index(client: OpenSearch, alias_name: str) -> Optional[str]:
    """Return the newest versioned index not pointed to by the alias.

    Parameters
    ----------
    client : OpenSearch
        The OpenSearch client.
    alias_name : str
        The alias name.

    Returns
    -------
    str or None
    """
    unaliased = get_unaliased_indices(client, alias_name)
    return unaliased[-1] if unaliased else None


def activate_alias(client: OpenSearch, alias_name: str, new_index: str) -> dict[str, Any]:
    """Atomically switch an alias from old index(es) to a new one.

    Parameters
    ----------
    client : OpenSearch
        The OpenSearch client.
    alias_name : str
        The alias name.
    new_index : str
        The physical index to point the alias to.

    Returns
    -------
    dict
        The response from OpenSearch.
    """
    actions: list[dict[str, Any]] = []
    # Remove alias from all current targets
    current = get_alias_info(client, alias_name)
    for old_index in current:
        actions.append({"remove": {"index": old_index, "alias": alias_name}})
    # Add alias to new target
    actions.append({"add": {"index": new_index, "alias": alias_name}})
    return client.indices.update_aliases(body={"actions": actions})


def create_alias(client: OpenSearch, alias_name: str, index_name: str) -> dict[str, Any]:
    """Create an alias pointing to the given index.

    Parameters
    ----------
    client : OpenSearch
        The OpenSearch client.
    alias_name : str
        The alias name.
    index_name : str
        The physical index name.

    Returns
    -------
    dict
        The response from OpenSearch.
    """
    return client.indices.update_aliases(
        body={"actions": [{"add": {"index": index_name, "alias": alias_name}}]}
    )
