"""
Copy historical AdCopyVariation rows from public into each org schema.

Run AFTER migrate_all_tenants so org_xxx.ad_copy_variation_adcopyvariation
already exists. Destination project is matched by slug, not by public pk.

    python manage.py migrate_ad_copy_variations_to_tenants --dry-run
    python manage.py migrate_ad_copy_variations_to_tenants
    python manage.py migrate_ad_copy_variations_to_tenants --slug acme-corp
"""

from django.core.management.base import BaseCommand

from ad_copy_variation.tenant_backfill import backfill_variations_to_tenants


class Command(BaseCommand):
    help = (
        'Copy public AdCopyVariation rows into the matching org schema. '
        'Idempotent: already-copied slugs are skipped. Does not delete public rows.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Count what would be copied without writing.',
        )
        parser.add_argument(
            '--slug',
            dest='slugs',
            action='append',
            metavar='SLUG',
            help='Limit to this organization slug. Repeatable.',
        )

    def handle(self, *args, **options):
        dry_run: bool = options['dry_run']
        slugs = options.get('slugs')
        prefix = '[DRY-RUN] ' if dry_run else ''

        result = backfill_variations_to_tenants(org_slugs=slugs, dry_run=dry_run)

        self.stdout.write(
            f'{prefix}copied={result.copied} skipped={result.skipped}'
        )
        if result.reasons:
            for reason, count in sorted(result.reasons.items()):
                self.stdout.write(f'  skip {reason}: {count}')

        if result.reasons.get('missing_variation_table'):
            self.stdout.write(
                self.style.WARNING(
                    'Some orgs are missing the variation table. '
                    'Run migrate_all_tenants first.'
                )
            )
