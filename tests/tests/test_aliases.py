import time
from io import StringIO
from unittest import TestCase
from unittest.mock import MagicMock, call, patch

from django.core.management import call_command
from opensearchpy.exceptions import NotFoundError

from django_opensearch_dsl.aliases import (
    _pending_index_cache,
    activate_alias,
    alias_exists,
    clear_pending_index_cache,
    create_alias,
    generate_versioned_name,
    get_active_index,
    get_alias_info,
    get_newest_unaliased_index,
    get_pending_indices,
    get_unaliased_indices,
    get_versioned_indices,
)


class GenerateVersionedNameTestCase(TestCase):
    @patch("django_opensearch_dsl.aliases.datetime")
    def test_generate_versioned_name(self, mock_dt):
        mock_dt.now.return_value.strftime.return_value = "20260223143052"
        result = generate_versioned_name("products")
        self.assertEqual(result, "products_20260223143052")

    @patch("django_opensearch_dsl.aliases.datetime")
    def test_generate_versioned_name_with_dashes(self, mock_dt):
        mock_dt.now.return_value.strftime.return_value = "20260223143052"
        result = generate_versioned_name("my-index")
        self.assertEqual(result, "my-index_20260223143052")


class AliasExistsTestCase(TestCase):
    def test_alias_exists_true(self):
        client = MagicMock()
        client.indices.exists_alias.return_value = True
        self.assertTrue(alias_exists(client, "products"))
        client.indices.exists_alias.assert_called_once_with(name="products")

    def test_alias_exists_false(self):
        client = MagicMock()
        client.indices.exists_alias.return_value = False
        self.assertFalse(alias_exists(client, "products"))


class GetAliasInfoTestCase(TestCase):
    def test_returns_info(self):
        client = MagicMock()
        expected = {"products_20260223143052": {"aliases": {"products": {}}}}
        client.indices.get_alias.return_value = expected
        result = get_alias_info(client, "products")
        self.assertEqual(result, expected)

    def test_returns_empty_on_error(self):
        client = MagicMock()
        client.indices.get_alias.side_effect = NotFoundError(404, "not found")
        result = get_alias_info(client, "products")
        self.assertEqual(result, {})


class GetActiveIndexTestCase(TestCase):
    def test_returns_active(self):
        client = MagicMock()
        client.indices.get_alias.return_value = {"products_20260223143052": {"aliases": {"products": {}}}}
        result = get_active_index(client, "products")
        self.assertEqual(result, "products_20260223143052")

    def test_returns_none_when_no_alias(self):
        client = MagicMock()
        client.indices.get_alias.side_effect = NotFoundError(404, "not found")
        result = get_active_index(client, "products")
        self.assertIsNone(result)


class GetVersionedIndicesTestCase(TestCase):
    def test_returns_sorted_indices(self):
        client = MagicMock()
        client.indices.get.return_value = {
            "products_20260223150000": {},
            "products_20260223143052": {},
            "products_20260223160000": {},
        }
        result = get_versioned_indices(client, "products")
        self.assertEqual(result, ["products_20260223143052", "products_20260223150000", "products_20260223160000"])
        client.indices.get.assert_called_once_with(index="products_*")

    def test_returns_empty_on_error(self):
        client = MagicMock()
        client.indices.get.side_effect = NotFoundError(404, "not found")
        result = get_versioned_indices(client, "products")
        self.assertEqual(result, [])


class GetUnaliasedIndicesTestCase(TestCase):
    def test_returns_unaliased(self):
        client = MagicMock()
        client.indices.get_alias.return_value = {"products_20260223143052": {"aliases": {"products": {}}}}
        client.indices.get.return_value = {
            "products_20260223143052": {},
            "products_20260223150000": {},
        }
        result = get_unaliased_indices(client, "products")
        self.assertEqual(result, ["products_20260223150000"])

    def test_returns_all_when_no_alias(self):
        client = MagicMock()
        client.indices.get_alias.side_effect = NotFoundError(404, "not found")
        client.indices.get.return_value = {
            "products_20260223143052": {},
            "products_20260223150000": {},
        }
        result = get_unaliased_indices(client, "products")
        self.assertEqual(result, ["products_20260223143052", "products_20260223150000"])


class GetNewestUnaliasedIndexTestCase(TestCase):
    def test_returns_newest(self):
        client = MagicMock()
        client.indices.get_alias.return_value = {"products_20260223143052": {"aliases": {"products": {}}}}
        client.indices.get.return_value = {
            "products_20260223143052": {},
            "products_20260223150000": {},
            "products_20260223160000": {},
        }
        result = get_newest_unaliased_index(client, "products")
        self.assertEqual(result, "products_20260223160000")

    def test_returns_none_when_all_aliased(self):
        client = MagicMock()
        client.indices.get_alias.return_value = {"products_20260223143052": {"aliases": {"products": {}}}}
        client.indices.get.return_value = {"products_20260223143052": {}}
        result = get_newest_unaliased_index(client, "products")
        self.assertIsNone(result)


class ActivateAliasTestCase(TestCase):
    def test_switches_alias(self):
        client = MagicMock()
        client.indices.get_alias.return_value = {"products_20260223143052": {"aliases": {"products": {}}}}
        client.indices.update_aliases.return_value = {"acknowledged": True}

        result = activate_alias(client, "products", "products_20260223150000")

        client.indices.update_aliases.assert_called_once_with(
            body={
                "actions": [
                    {"remove": {"index": "products_20260223143052", "alias": "products"}},
                    {"add": {"index": "products_20260223150000", "alias": "products"}},
                ]
            }
        )
        self.assertEqual(result, {"acknowledged": True})

    def test_creates_alias_when_none_exists(self):
        client = MagicMock()
        client.indices.get_alias.side_effect = NotFoundError(404, "not found")
        client.indices.update_aliases.return_value = {"acknowledged": True}

        result = activate_alias(client, "products", "products_20260223150000")

        client.indices.update_aliases.assert_called_once_with(
            body={
                "actions": [
                    {"add": {"index": "products_20260223150000", "alias": "products"}},
                ]
            }
        )


class CreateAliasTestCase(TestCase):
    def test_creates_alias(self):
        client = MagicMock()
        client.indices.update_aliases.return_value = {"acknowledged": True}

        result = create_alias(client, "products", "products_20260223143052")

        client.indices.update_aliases.assert_called_once_with(
            body={
                "actions": [
                    {"add": {"index": "products_20260223143052", "alias": "products"}},
                ]
            }
        )
        self.assertEqual(result, {"acknowledged": True})


class MigrateIndexCommandTestCase(TestCase):
    """Tests for the 'index migrate' management command."""

    @patch("django_opensearch_dsl.management.commands.opensearch.connection")
    @patch("django_opensearch_dsl.management.commands.opensearch.registry")
    @patch("django_opensearch_dsl.management.commands.opensearch.generate_versioned_name")
    @patch("django_opensearch_dsl.management.commands.opensearch.alias_exists")
    @patch("django_opensearch_dsl.management.commands.opensearch.create_alias")
    def test_migrate_legacy_index(self, mock_create_alias, mock_alias_exists, mock_gen_name, mock_registry, mock_conn):
        """Legacy index is cloned, deleted, and alias created."""
        client = MagicMock()
        mock_conn.return_value = client

        index = MagicMock()
        index._name = "products"
        mock_registry.get_indices.return_value = [index]

        mock_alias_exists.return_value = False
        client.indices.exists.return_value = True
        mock_gen_name.return_value = "products_20260223143052"

        out = StringIO()
        call_command("opensearch", "index", "migrate", "--force", stdout=out, verbosity=1)

        client.indices.put_settings.assert_any_call(index="products", body={"index.blocks.write": True})
        client.indices.clone.assert_called_once_with(index="products", target="products_20260223143052")
        client.indices.delete.assert_called_once_with(index="products")
        mock_create_alias.assert_called_once_with(client, "products", "products_20260223143052")
        client.indices.put_settings.assert_any_call(
            index="products_20260223143052", body={"index.blocks.write": False}
        )

    @patch("django_opensearch_dsl.management.commands.opensearch.connection")
    @patch("django_opensearch_dsl.management.commands.opensearch.registry")
    @patch("django_opensearch_dsl.management.commands.opensearch.alias_exists")
    def test_migrate_skips_already_aliased(self, mock_alias_exists, mock_registry, mock_conn):
        """Already-aliased index is skipped."""
        client = MagicMock()
        mock_conn.return_value = client

        index = MagicMock()
        index._name = "products"
        mock_registry.get_indices.return_value = [index]

        mock_alias_exists.return_value = True

        out = StringIO()
        call_command("opensearch", "index", "migrate", "--force", stdout=out, verbosity=1)

        client.indices.clone.assert_not_called()
        client.indices.delete.assert_not_called()
        self.assertIn("already using aliases", out.getvalue())

    @patch("django_opensearch_dsl.management.commands.opensearch.connection")
    @patch("django_opensearch_dsl.management.commands.opensearch.registry")
    @patch("django_opensearch_dsl.management.commands.opensearch.alias_exists")
    def test_migrate_skips_nonexistent_index(self, mock_alias_exists, mock_registry, mock_conn):
        """Non-existent index is skipped."""
        client = MagicMock()
        mock_conn.return_value = client

        index = MagicMock()
        index._name = "products"
        mock_registry.get_indices.return_value = [index]

        mock_alias_exists.return_value = False
        client.indices.exists.return_value = False

        out = StringIO()
        call_command("opensearch", "index", "migrate", "--force", stdout=out, verbosity=1)

        client.indices.clone.assert_not_called()
        client.indices.delete.assert_not_called()
        self.assertIn("does not exist", out.getvalue())


class GetPendingIndicesTestCase(TestCase):
    """Tests for the pending-index cache."""

    def setUp(self):
        clear_pending_index_cache()

    def tearDown(self):
        clear_pending_index_cache()

    @patch("django_opensearch_dsl.aliases.get_unaliased_indices")
    def test_first_call_queries_opensearch(self, mock_get):
        mock_get.return_value = ["products_20260223150000"]
        client = MagicMock()

        result = get_pending_indices(client, "products")

        self.assertEqual(result, ["products_20260223150000"])
        mock_get.assert_called_once_with(client, "products")

    @patch("django_opensearch_dsl.aliases.get_unaliased_indices")
    def test_second_call_within_ttl_uses_cache(self, mock_get):
        mock_get.return_value = ["products_20260223150000"]
        client = MagicMock()

        get_pending_indices(client, "products")
        result = get_pending_indices(client, "products")

        self.assertEqual(result, ["products_20260223150000"])
        mock_get.assert_called_once()  # only one call to OpenSearch

    @patch("django_opensearch_dsl.aliases.get_unaliased_indices")
    @patch("django_opensearch_dsl.aliases.time")
    def test_call_after_ttl_refreshes(self, mock_time, mock_get):
        mock_time.monotonic.return_value = 100.0
        mock_get.return_value = ["products_20260223150000"]
        client = MagicMock()

        get_pending_indices(client, "products")
        self.assertEqual(mock_get.call_count, 1)

        # Advance past TTL
        mock_time.monotonic.return_value = 131.0
        mock_get.return_value = ["products_20260223150000", "products_20260223160000"]

        result = get_pending_indices(client, "products")

        self.assertEqual(result, ["products_20260223150000", "products_20260223160000"])
        self.assertEqual(mock_get.call_count, 2)

    @patch("django_opensearch_dsl.aliases.get_unaliased_indices")
    def test_clear_cache_forces_refresh(self, mock_get):
        mock_get.return_value = ["products_20260223150000"]
        client = MagicMock()

        get_pending_indices(client, "products")
        clear_pending_index_cache()

        mock_get.return_value = []
        result = get_pending_indices(client, "products")

        self.assertEqual(result, [])
        self.assertEqual(mock_get.call_count, 2)

    @patch("django_opensearch_dsl.aliases.get_unaliased_indices")
    def test_returns_empty_list_when_no_pending(self, mock_get):
        mock_get.return_value = []
        client = MagicMock()

        result = get_pending_indices(client, "products")
        self.assertEqual(result, [])


class DualWriteTestCase(TestCase):
    """Tests for dual-write in Document.update()."""

    @patch("django_opensearch_dsl.documents.get_pending_indices")
    @patch("django_opensearch_dsl.apps.DODConfig.autosync_enabled")
    def test_dual_write_to_pending_index(self, mock_autosync, mock_pending):
        """When autosync is on and pending indices exist, _bulk is called for each."""
        from django_opensearch_dsl.documents import Document

        mock_autosync.return_value = True
        mock_pending.return_value = ["products_20260223160000"]

        doc = MagicMock(spec=Document)
        doc._index = MagicMock()
        doc._index._name = "products"
        doc._get_connection = MagicMock()

        # Track _bulk calls
        bulk_calls = []

        def fake_bulk(actions, **kwargs):
            # Consume generator to capture actions
            action_list = list(actions)
            bulk_calls.append((action_list, kwargs))
            return (1, [])

        doc._bulk = fake_bulk
        doc._get_actions = MagicMock(side_effect=[
            iter([{"_index": "products", "_id": 1, "_source": {}}]),
            iter([{"_index": "products", "_id": 1, "_source": {}}]),
        ])
        doc.should_index_object = MagicMock(return_value=True)

        thing = MagicMock()
        thing_list = [thing]

        # Call the real update method
        Document.update(doc, thing, action="index", refresh=False, parallel=False)

        # Primary write + one pending write
        self.assertEqual(len(bulk_calls), 2)
        # The pending write should have patched _index
        pending_actions = bulk_calls[1][0]
        self.assertEqual(pending_actions[0]["_index"], "products_20260223160000")
        # The pending write should have raise_on_error=False
        self.assertFalse(bulk_calls[1][1]["raise_on_error"])

    @patch("django_opensearch_dsl.documents.get_pending_indices")
    @patch("django_opensearch_dsl.apps.DODConfig.autosync_enabled")
    def test_no_dual_write_when_autosync_disabled(self, mock_autosync, mock_pending):
        """When autosync is off, no pending-index lookup happens."""
        from django_opensearch_dsl.documents import Document

        mock_autosync.return_value = False

        doc = MagicMock(spec=Document)
        doc._index = MagicMock()
        doc._index._name = "products"

        bulk_calls = []

        def fake_bulk(actions, **kwargs):
            list(actions)
            bulk_calls.append(kwargs)
            return (1, [])

        doc._bulk = fake_bulk
        doc._get_actions = MagicMock(return_value=iter([{"_index": "products", "_id": 1, "_source": {}}]))

        Document.update(doc, MagicMock(), action="index", refresh=False, parallel=False)

        self.assertEqual(len(bulk_calls), 1)  # only primary write
        mock_pending.assert_not_called()

    @patch("django_opensearch_dsl.documents.get_pending_indices")
    @patch("django_opensearch_dsl.apps.DODConfig.autosync_enabled")
    def test_no_dual_write_when_no_pending(self, mock_autosync, mock_pending):
        """When there are no pending indices, only primary write happens."""
        from django_opensearch_dsl.documents import Document

        mock_autosync.return_value = True
        mock_pending.return_value = []

        doc = MagicMock(spec=Document)
        doc._index = MagicMock()
        doc._index._name = "products"
        doc._get_connection = MagicMock()

        bulk_calls = []

        def fake_bulk(actions, **kwargs):
            list(actions)
            bulk_calls.append(kwargs)
            return (1, [])

        doc._bulk = fake_bulk
        doc._get_actions = MagicMock(return_value=iter([{"_index": "products", "_id": 1, "_source": {}}]))

        Document.update(doc, MagicMock(), action="index", refresh=False, parallel=False)

        self.assertEqual(len(bulk_calls), 1)  # only primary write

    @patch("django_opensearch_dsl.documents.get_pending_indices")
    @patch("django_opensearch_dsl.apps.DODConfig.autosync_enabled")
    def test_pending_write_failure_does_not_break_primary(self, mock_autosync, mock_pending):
        """If pending write raises, the primary result is still returned."""
        from django_opensearch_dsl.documents import Document

        mock_autosync.return_value = True
        mock_pending.return_value = ["products_20260223160000"]

        doc = MagicMock(spec=Document)
        doc._index = MagicMock()
        doc._index._name = "products"
        doc._get_connection = MagicMock()

        call_count = [0]

        def fake_bulk(actions, **kwargs):
            list(actions)
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("OpenSearch connection failed")
            return (1, [])

        doc._bulk = fake_bulk
        doc._get_actions = MagicMock(side_effect=[
            iter([{"_index": "products", "_id": 1, "_source": {}}]),
            iter([{"_index": "products", "_id": 1, "_source": {}}]),
        ])

        result = Document.update(doc, MagicMock(), action="index", refresh=False, parallel=False)

        # Primary write succeeded
        self.assertEqual(result, (1, []))
