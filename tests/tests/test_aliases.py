from unittest import TestCase
from unittest.mock import MagicMock, call, patch

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
