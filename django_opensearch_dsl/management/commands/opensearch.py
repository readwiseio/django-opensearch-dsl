import argparse
import functools
import operator
import sys
from argparse import ArgumentParser
from collections import defaultdict
from typing import Any, Callable

import opensearchpy
from django.core.exceptions import FieldError
from django.core.management import BaseCommand, CommandError
from django.db.models import Q
from opensearchpy import OpenSearch
from opensearchpy.connection.connections import connections

from ...aliases import (
    activate_alias,
    alias_exists,
    clear_pending_index_cache,
    create_alias,
    generate_versioned_name,
    get_active_index,
    get_alias_info,
    get_newest_unaliased_index,
    get_unaliased_indices,
    get_versioned_indices,
)
from ...apps import DODConfig
from ...enums import CommandAction
from ...registries import registry
from ..types import Values, parse


def connection(using: str = "default") -> opensearchpy.OpenSearch:
    """Return the OpenSearch connection for the given alias."""
    try:
        return connections.get_connection(using)
    except KeyError:
        raise CommandError(
            f"No OpenSearch connection found for alias '{using}', known connections are: {list(connections._kwargs.keys())}"
        )


class Command(BaseCommand):
    """Manage indices and documents."""

    help = (
        "Allow to create and delete indices, as well as indexing, updating or deleting specific "
        "documents from specific indices.\n"
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa
        super(Command, self).__init__()
        self.usage = ""

    def db_filter(self, parser: ArgumentParser) -> Callable[[str], Any]:
        """Return a function to parse the filters."""

        def wrap(value: str) -> tuple[str, Values]:
            v: Values
            try:
                lookup, v = value.split("=")
                v = parse(v)
            except ValueError:
                if parser._subparsers is not None:
                    sys.stderr.write(parser._subparsers._group_actions[0].choices["document"].format_usage())
                else:
                    sys.stderr.write("Error: Subparsers are not defined.\n")
                sys.stderr.write(
                    f"manage.py index: error: invalid filter: '{value}' (filter must be formatted as "
                    f"'[Field Lookups]=[value]')\n",
                )
                raise CommandError
            return lookup, v  # noqa

        return wrap

    def _get_given_indices(self, indices: list[str]) -> list:
        """Resolve and validate index names from registry."""
        known = registry.get_indices()
        if indices:
            known_name = [i._name for i in known]  # noqa
            unknown = set(indices) - set(known_name)
            if unknown:
                self.stderr.write(f"Unknown indices '{list(unknown)}', choices are: '{known_name}'")
                raise CommandError
            return [i for i in known if i._name in indices]
        return list(known)

    def _confirm(self, action: CommandAction, given_indices: list, force: bool, verbosity: int) -> None:
        """Display action summary and optionally ask for confirmation."""
        if verbosity or not force:
            self.stdout.write(f"The following indices will be {action.past}:")
            for index in given_indices:
                self.stdout.write(f"\t- {index._name}.")  # noqa
            self.stdout.write("")
        if not force:  # pragma: no cover
            while True:
                p = input("Continue ? [y]es [n]o : ")
                if p.lower() in ["yes", "y"]:
                    self.stdout.write("")
                    break
                elif p.lower() in ["no", "n"]:
                    raise CommandError

    def __list_index(self, using: OpenSearch, **options: Any) -> None:  # noqa pragma: no cover
        """List all known index and indicate whether they are created or not."""
        indices = registry.get_indices()
        result = defaultdict(list)
        for index in indices:
            alias_name = index._name  # noqa
            module = index._doc_types[0].__module__.split(".")[-2]  # noqa

            # Check if alias exists (new versioned mode)
            active = get_active_index(using, alias_name)
            if active:
                count = using.count(index=alias_name)["count"]
                line = f"[X] {alias_name} -> {active} ({count} documents)"
                # Show pending (unaliased) versions
                pending = get_unaliased_indices(using, alias_name)
                if pending:
                    line += f" [pending: {', '.join(pending)}]"
                result[module].append(line)
            else:
                # Fall back to legacy check (non-versioned index)
                exists = index.exists(using=using)
                checkbox = f"[{'X' if exists else ' '}]"
                count_str = f" ({index.search(using=using).count()} documents)" if exists else ""
                result[module].append(f"{checkbox} {alias_name}{count_str}")

        for app, indice_names in result.items():
            self.stdout.write(self.style.MIGRATE_LABEL(app))
            self.stdout.write("\n".join(indice_names))

    def _manage_index(
        self,
        action: CommandAction,
        indices: list[str],
        force: bool,
        verbosity: int,
        ignore_error: bool,
        using: OpenSearch,
        keep: int = 1,
        **options: Any,
    ) -> None:
        """Manage the creation and deletion of indices."""
        action = CommandAction(action)  # type: ignore[call-arg]
        given_indices = self._get_given_indices(indices)
        self._confirm(action, given_indices, force, verbosity)

        pp = action.present_participle.title()
        for index in given_indices:
            alias_name = index._name  # noqa
            if verbosity:
                self.stdout.write(
                    f"{pp} index '{alias_name}'...\r",
                    ending="",
                )
                self.stdout.flush()
            try:
                if action == CommandAction.CREATE:
                    self._create_versioned_index(index, alias_name, using, verbosity)
                elif action == CommandAction.DELETE:
                    self._delete_all_indices(alias_name, using)
                elif action == CommandAction.UPDATE:
                    self._update_mapping(index, alias_name, using)
                elif action == CommandAction.REBUILD:
                    self._rebuild_index(index, alias_name, using)
                elif action == CommandAction.ACTIVATE:
                    self._activate_index(alias_name, using, verbosity)
                elif action == CommandAction.CLEANUP:
                    self._cleanup_indices(alias_name, using, keep, verbosity)
                elif action == CommandAction.MIGRATE:
                    self._migrate_index(alias_name, using, verbosity)
            except opensearchpy.exceptions.TransportError as e:
                if verbosity or not ignore_error:
                    error = self.style.ERROR(f"Error: {e.error} - {e.info}")
                    self.stderr.write(f"{pp} index '{alias_name}'...\n{error}")
                if not ignore_error:
                    self.stderr.write("exiting...")
                    raise CommandError
            else:
                if verbosity:
                    self.stdout.write(f"{pp} index '{alias_name}'... {self.style.SUCCESS('OK')}")

    def _create_versioned_index(self, index: Any, alias_name: str, using: OpenSearch, verbosity: int) -> None:
        """Create a new versioned physical index. Set up alias if none exists."""
        versioned_name = generate_versioned_name(alias_name)
        new_index = index.clone(name=versioned_name)
        new_index.create(using=using)
        if not alias_exists(using, alias_name):
            create_alias(using, alias_name, versioned_name)
            if verbosity:
                self.stdout.write(f"  Created alias '{alias_name}' -> '{versioned_name}'")
        else:
            if verbosity:
                self.stdout.write(f"  Created new version '{versioned_name}' (alias unchanged)")

    def _delete_all_indices(self, alias_name: str, using: OpenSearch) -> None:
        """Delete alias and all versioned + legacy physical indices."""
        # Delete all versioned indices
        versioned = get_versioned_indices(using, alias_name)
        for idx in versioned:
            using.indices.delete(index=idx)
        # Delete alias (may fail if already gone, that's fine)
        try:
            if alias_exists(using, alias_name):
                info = get_alias_info(using, alias_name)
                for idx in info:
                    using.indices.delete_alias(index=idx, name=alias_name)
        except Exception:
            pass
        # Delete legacy non-versioned index
        try:
            using.indices.delete(index=alias_name)
        except opensearchpy.exceptions.NotFoundError:
            pass

    def _update_mapping(self, index: Any, alias_name: str, using: OpenSearch) -> None:
        """Put mapping on the active physical index."""
        active = get_active_index(using, alias_name)
        if active:
            using.indices.put_mapping(index=active, body=index.to_dict()["mappings"])
        else:
            # Legacy fallback
            index.put_mapping(using=using, body=index.to_dict()["mappings"])

    def _rebuild_index(self, index: Any, alias_name: str, using: OpenSearch) -> None:
        """Delete everything and create fresh versioned index + alias."""
        self._delete_all_indices(alias_name, using)
        versioned_name = generate_versioned_name(alias_name)
        new_index = index.clone(name=versioned_name)
        new_index.create(using=using)
        create_alias(using, alias_name, versioned_name)

    def _activate_index(self, alias_name: str, using: OpenSearch, verbosity: int) -> None:
        """Atomically switch alias to newest unaliased version."""
        new_index = get_newest_unaliased_index(using, alias_name)
        if not new_index:
            raise CommandError(f"No new version found for '{alias_name}'. Create one first with 'index create'.")
        old = get_active_index(using, alias_name)
        activate_alias(using, alias_name, new_index)
        clear_pending_index_cache()
        if verbosity:
            self.stdout.write(f"  Alias '{alias_name}': '{old}' -> '{new_index}'")

    def _cleanup_indices(self, alias_name: str, using: OpenSearch, keep: int, verbosity: int) -> None:
        """Delete unaliased versioned indices, keeping the N most recent."""
        unaliased = get_unaliased_indices(using, alias_name)
        # Keep the `keep` most recent unaliased indices
        to_delete = unaliased[:-keep] if keep > 0 else unaliased
        for idx in to_delete:
            using.indices.delete(index=idx)
            if verbosity:
                self.stdout.write(f"  Deleted '{idx}'")
        if to_delete:
            clear_pending_index_cache()
        if not to_delete and verbosity:
            self.stdout.write(f"  No old versions to clean up for '{alias_name}'")

    def _migrate_index(self, alias_name: str, using: OpenSearch, verbosity: int) -> None:
        """Migrate a legacy non-versioned index to the alias-based system.

        Clones the legacy index to a versioned name, deletes the legacy index,
        then creates an alias pointing to the versioned copy.
        """
        # Skip if already aliased
        if alias_exists(using, alias_name):
            if verbosity:
                self.stdout.write(f"  Skipping '{alias_name}': already using aliases")
            return

        # Skip if legacy index doesn't exist
        if not using.indices.exists(index=alias_name):
            if verbosity:
                self.stdout.write(f"  Skipping '{alias_name}': index does not exist")
            return

        versioned_name = generate_versioned_name(alias_name)

        # Block writes on legacy index
        using.indices.put_settings(index=alias_name, body={"index.blocks.write": True})

        # Clone legacy index to versioned name
        if verbosity:
            self.stdout.write(f"  Cloning '{alias_name}' -> '{versioned_name}'...")
        using.indices.clone(index=alias_name, target=versioned_name)

        # Delete legacy index (frees the name for the alias)
        if verbosity:
            self.stdout.write(f"  Deleting legacy index '{alias_name}'...")
        using.indices.delete(index=alias_name)

        # Create alias pointing to the versioned copy
        create_alias(using, alias_name, versioned_name)
        if verbosity:
            self.stdout.write(f"  Created alias '{alias_name}' -> '{versioned_name}'")

        # Remove write block inherited by the cloned index
        using.indices.put_settings(index=versioned_name, body={"index.blocks.write": False})

    def _manage_document(
        self,
        action: CommandAction,
        indices: list[str],
        force: bool,
        filters: list[tuple[str, str]],
        excludes: list[tuple[str, str]],
        verbosity: int,
        parallel: bool,
        count: bool,
        refresh: bool,
        missing: bool,
        using: OpenSearch,
        database: str,
        new_version: bool = False,
        **options: Any,
    ) -> None:
        """Manage the creation and deletion of indices."""
        action = CommandAction(action)  # type: ignore[call-arg]
        known = registry.get_indices()
        filter_ = functools.reduce(operator.and_, (Q(**{k: v}) for k, v in filters)) if filters else None
        exclude = functools.reduce(operator.and_, (Q(**{k: v}) for k, v in excludes)) if excludes else None

        # Filter indices
        if indices:
            # Ensure every given indices exists
            known_name = [i._name for i in known]  # noqa
            unknown = set(indices) - set(known_name)
            if unknown:
                self.stderr.write(f"Unknown indices '{list(unknown)}', choices are: '{known_name}'")
                raise CommandError

            # Only keep given indices
            given_indices = list(filter(lambda i: i._name in indices, known))  # type: ignore[arg-type]
        else:
            given_indices = list(known)

        # Resolve --new-version targets: map alias_name -> physical index name
        new_version_targets: dict[str, str] = {}
        if new_version:
            for index in given_indices:
                alias_name = index._name  # noqa
                target = get_newest_unaliased_index(using, alias_name)
                if not target:
                    self.stderr.write(
                        f"No new version found for '{alias_name}'. "
                        f"Create one first with 'opensearch index create'."
                    )
                    raise CommandError
                new_version_targets[alias_name] = target

        # Ensure every indices needed are created (check alias or physical index)
        not_created = []
        for i in given_indices:
            alias_name = i._name  # noqa
            if new_version and alias_name in new_version_targets:
                # Check that the target physical index exists
                if not using.indices.exists(index=new_version_targets[alias_name]):
                    not_created.append(alias_name)
            elif alias_exists(using, alias_name):
                continue  # alias exists, good
            elif not i.exists(using=using):
                not_created.append(alias_name)
        if not_created:
            self.stderr.write(f"The following indices are not created : {not_created}")
            self.stderr.write("Use 'python3 manage.py opensearch list' to list indices' state.")
            raise CommandError

        # Check field, preparing to display expected actions
        s = f"The following documents will be {action.past}:"
        kwargs_list = []
        for index in given_indices:
            # Handle --missing
            exclude_ = exclude
            if missing and action == CommandAction.INDEX:
                q = Q(pk__in=[h.meta.id for h in index.search(using=using).extra(stored_fields=[]).scan()])
                exclude_ = exclude_ & q if exclude_ is not None else q

            document = index._doc_types[0]()  # noqa
            try:
                kwargs_list.append({"filter_": filter_, "exclude": exclude_, "count": count})
                qs = document.get_queryset(filter_=filter_, exclude=exclude_, count=count, alias=database).count()
            except FieldError as e:
                model = index._doc_types[0].django.model.__name__  # noqa
                self.stderr.write(f"Error while filtering on '{model}' (from index '{index._name}'):\n{e}'")  # noqa
                raise CommandError
            else:
                s += f"\n\t- {qs} {document.django.model.__name__}."

        # Display expected actions
        if verbosity or not force:
            self.stdout.write(s + "\n\n")

        # Ask for confirmation to continue
        if not force:  # pragma: no cover
            while True:
                p = input("Continue ? [y]es [n]o : ")
                if p.lower() in ["yes", "y"]:
                    self.stdout.write("\n")
                    break
                elif p.lower() in ["no", "n"]:
                    raise CommandError

        result = "\n"
        for index, kwargs in zip(given_indices, kwargs_list):
            document = index._doc_types[0]()  # noqa
            alias_name = index._name  # noqa

            # If --new-version, temporarily override _index._name to write to physical index
            original_index_name = None
            if new_version and alias_name in new_version_targets:
                original_index_name = document._index._name  # noqa
                document._index._name = new_version_targets[alias_name]  # noqa

            try:
                qs = document.get_indexing_queryset(
                    stdout=self.stdout._out, verbose=verbosity, action=action, alias=database, **kwargs
                )
                success, errors = document.update(
                    qs, parallel=parallel, refresh=refresh, action=action, raise_on_error=False, using=using
                )
            finally:
                # Restore original name
                if original_index_name is not None:
                    document._index._name = original_index_name  # noqa

            success_str = self.style.SUCCESS(success) if success else success
            errors_str = self.style.ERROR(len(errors)) if errors else len(errors)
            model = document.django.model.__name__

            if verbosity == 1:
                result += f"{success_str} {model} successfully {action.past}, {errors_str} errors:\n"
                reasons: defaultdict[str, int] = defaultdict(int)
                for err in errors:  # Count occurrence of each error
                    error = err.get(action, {"result": "unknown error"}).get("result", "unknown error")
                    reasons[error] += 1
                for reason, total in reasons.items():
                    result += f"    - {reason} : {total}\n"

            if verbosity > 1:
                result += f"{success_str} {model} successfully {action}d, {errors_str} errors:\n {errors}\n"

        if verbosity:
            self.stdout.write(result + "\n")

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Add arguments to parser."""
        parser.formatter_class = argparse.RawTextHelpFormatter
        subparsers = parser.add_subparsers()

        # 'list' subcommand
        subparser = subparsers.add_parser(
            "list",
            help="Show all available indices (and their state) for the current project.",
            description="Show all available indices (and their state) for the current project.",
        )
        subparser.set_defaults(func=self.__list_index)
        subparser.add_argument(
            "-u",
            "--using",
            type=connection,
            default=connection("default"),
            help="Alias of the OpenSearch connection to use. Default to 'default'.",
        )

        # 'index' subcommand
        subparser = subparsers.add_parser(
            "index",
            help="Manage the creation and deletion of indices.",
            description="Manage the creation and deletion of indices.",
        )
        subparser.set_defaults(func=self._manage_index)
        subparser.add_argument(
            "-u",
            "--using",
            type=connection,
            default=connection("default"),
            help="Alias of the OpenSearch connection to use. Default to 'default'.",
        )
        subparser.add_argument(
            "action",
            type=str,
            help=(
                "Whether you want to create, update, delete, rebuild, activate, cleanup or migrate the indices.\n"
                "  create   - Create a new versioned index and set up alias if needed.\n"
                "  delete   - Delete alias and all versioned physical indices.\n"
                "  rebuild  - Delete everything and create fresh versioned index + alias.\n"
                "  update   - Update mappings on the active physical index.\n"
                "  activate - Atomically switch alias to newest unaliased version.\n"
                "  cleanup  - Delete old unaliased versioned indices.\n"
                "  migrate  - Convert legacy non-versioned indices to alias-based system."
            ),
            choices=[
                CommandAction.CREATE.value,
                CommandAction.DELETE.value,
                CommandAction.REBUILD.value,
                CommandAction.UPDATE.value,
                CommandAction.ACTIVATE.value,
                CommandAction.CLEANUP.value,
                CommandAction.MIGRATE.value,
            ],
        )
        subparser.add_argument("--force", action="store_true", default=False, help="Do not ask for confirmation.")
        subparser.add_argument("--ignore-error", action="store_true", default=False, help="Do not stop on error.")
        subparser.add_argument(
            "--keep",
            type=int,
            default=1,
            help="Number of old versions to keep during cleanup. Default: 1.",
        )
        subparser.add_argument(
            "indices",
            type=str,
            nargs="*",
            metavar="INDEX",
            help="Only manage the given indices.",
        )

        # 'document' subcommand
        subparser = subparsers.add_parser(
            "document",
            help="Manage the indexation and creation of documents.",
            description="Manage the indexation and creation of documents.",
            formatter_class=argparse.RawTextHelpFormatter,
        )
        subparser.set_defaults(func=self._manage_document)
        subparser.add_argument(
            "action",
            type=str,
            help="Whether you want to create, delete or rebuild the indices.",
            choices=[
                CommandAction.INDEX.value,
                CommandAction.DELETE.value,
                CommandAction.UPDATE.value,
            ],
        )
        subparser.add_argument(
            "-u",
            "--using",
            type=connection,
            default=connection("default"),
            help="Alias of the OpenSearch connection to use. Default to 'default'.",
        )
        subparser.add_argument("-d", "--database", default=None, dest="database", help="Nominates a database.")
        subparser.add_argument(
            "-f",
            "--filters",
            type=self.db_filter(parser),
            nargs="*",
            help=(
                "Filter object in the queryset. Argument must be formatted as '[lookup]=[value]', e.g. "
                "'document_date__gte=2020-05-21.\n"
                "The accepted value type are:\n"
                "  - 'None' ('[lookup]=')\n"
                "  - 'float' ('[lookup]=1.12')\n"
                "  - 'int' ('[lookup]=23')\n"
                "  - 'datetime.date' ('[lookup]=2020-10-08')\n"
                "  - 'list' ('[lookup]=1,2,3,4') Value between comma ',' can be of any other accepted value type\n"
                "  - 'str' ('[lookup]=week') Value that didn't match any type above will be interpreted as a str\n"
                "The list of lookup function can be found here: "
                "https://docs.djangoproject.com/en/dev/ref/models/querysets/#field-lookups"
            ),
        )
        subparser.add_argument(
            "-e",
            "--excludes",
            type=self.db_filter(parser),
            nargs="*",
            help=(
                "Exclude objects from the queryset. Argument must be formatted as '[lookup]=[value]', see '--filters' "
                "for more information"
            ),
        )
        subparser.add_argument("--force", action="store_true", default=False, help="Do not ask for confirmation.")
        subparser.add_argument(
            "-i", "--indices", type=str, nargs="*", help="Only update documents on the given indices."
        )
        subparser.add_argument(
            "-c", "--count", type=int, default=None, help="Update at most COUNT objects (0 to index everything)."
        )
        refresh = subparser.add_mutually_exclusive_group()
        refresh.add_argument(
            "-r",
            "--refresh",
            action="store_true",
            default=DODConfig.auto_refresh_enabled(),
            help=(
                "Whether the operations performed on the indices are immediately available for search. Default to "
                "`OPENSEARCH_DSL_AUTO_REFRESH` (which default to `False`)"
            ),
        )
        refresh.add_argument("--no-refresh", action="store_false")
        parallel = subparser.add_mutually_exclusive_group()
        parallel.add_argument(
            "-p",
            "--parallel",
            action="store_true",
            default=DODConfig.parallel_enabled(),
            help=(
                "Whether to run bulk operations in parallel. Default to `OPENSEARCH_DSL_PARALLEL` (which default to "
                "`False`)"
            ),
        )
        refresh.add_argument("--no-parallel", action="store_false")
        subparser.add_argument(
            "-m",
            "--missing",
            action="store_true",
            default=False,
            help="When used with 'index' action, only index documents not indexed yet.",
        )
        subparser.add_argument(
            "--new-version",
            action="store_true",
            default=False,
            dest="new_version",
            help="Write documents to the newest unaliased version instead of through the alias.",
        )

        self.usage = parser.format_usage()

    def handle(self, *args: Any, **options: Any) -> None:
        """Run the command according to `options`."""
        if "func" not in options:  # pragma: no cover
            self.stderr.write(self.usage)
            self.stderr.write(f"manage.py opensearch: error: no subcommand provided.")
            raise CommandError

        options["func"](**options)
