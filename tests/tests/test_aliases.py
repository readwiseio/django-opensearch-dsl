from io import StringIO
from unittest import TestCase
from unittest.mock import MagicMock, call, patch

from django.core.management import call_command
from opensearchpy.exceptions import NotFoundError

from django_opensearch_dsl.aliases import (
    activate_alias,
    alias_exists,
    create_alias,
    generate_versioned_name,
    get_active_index,
    get_alias_info,
    get_newest_unaliased_index,
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
