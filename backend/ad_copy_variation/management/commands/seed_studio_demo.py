"""Seed local Variations Studio drafts so AI Drafts can be walked without Gemini."""

from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from ad_copy_variation.models import AdCopyVariation
from core.models import Project, ProjectMember
from core.services.tenant import slug_to_schema_name
from meta_ads.models import MetaAdCreative

SEED_INSTRUCTION = '[studio-demo-seed]'
User = get_user_model()


class Command(BaseCommand):
    help = (
        'Insert demo AdCopyVariation rows for local QA. '
        'Does not call Gemini. Safe to re-run with --reset.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--project-id', type=int, default=1)
        parser.add_argument('--user-email', default='qa-med246@example.com')
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete previous rows tagged with the seed instruction, then insert.',
        )

    def handle(self, *args, **options):
        project_id = options['project_id']
        email = options['user_email']
        reset = options['reset']

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist as exc:
            raise CommandError(f'User {email} not found.') from exc

        org = getattr(user, 'current_organization', None) or getattr(user, 'organization', None)
        schema = slug_to_schema_name(org.slug) if org else 'public'
        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO %s, public', [schema])

        try:
            self._seed(project_id, user, reset)
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SET search_path TO public')

    def _seed(self, project_id, user, reset):
        try:
            project = Project.objects.get(pk=project_id, is_deleted=False)
        except Project.DoesNotExist as exc:
            raise CommandError(f'Project {project_id} not found.') from exc

        if not ProjectMember.objects.filter(
            user=user, project=project, is_active=True
        ).exists():
            raise CommandError(f'{user.email} is not an active member of project {project_id}.')

        creative = (
            MetaAdCreative.objects.filter(ad_account__project=project)
            .order_by('id')
            .first()
        )

        if reset:
            deleted, _ = AdCopyVariation.objects.filter(
                project=project,
                instruction=SEED_INSTRUCTION,
            ).delete()
            self.stdout.write(f'Deleted {deleted} previous seed row(s).')

        latest_batch = uuid.uuid4()
        older_batch = uuid.uuid4()
        creative_batch = uuid.uuid4()

        rows = [
            *_custom_zh_drafts(latest_batch),
            *_custom_en_drafts(older_batch),
            *_existing_creative_rows(creative_batch, creative),
            *_external_url_reviewed(),
        ]

        created = []
        for payload in rows:
            created.append(
                AdCopyVariation.objects.create(
                    project=project,
                    created_by=user,
                    instruction=SEED_INSTRUCTION,
                    model_name='gemini-2.5-flash-lite',
                    prompt_version='v1',
                    **payload,
                )
            )

        draft_n = sum(1 for row in created if row.status == AdCopyVariation.STATUS_DRAFT)
        reviewed_n = len(created) - draft_n
        self.stdout.write(self.style.SUCCESS(
            f'Seeded {len(created)} variations for project {project_id} '
            f'({draft_n} draft, {reviewed_n} reviewed). latest_batch={latest_batch}'
        ))
        if creative:
            self.stdout.write(f'Linked existing-creative rows to creative id={creative.id} slug={creative.slug}')
        else:
            self.stdout.write(self.style.WARNING('No Meta creative on this project; existing-creative rows have creative=null.'))
        self.stdout.write(
            'Open Variations Studio → AI Drafts (Generate still needs Gemini).'
        )


def _custom_zh_drafts(batch_id):
    copies = [
        ('换季先护屏障', '一晚修护干皮', '神经酰胺面霜，换季紧绷起皮也能用。无香精。', 'SHOP_NOW'),
        ('皮肤在喊干燥', '修护不必等三月', '针对换季干痒紧绷。敏感肌可用，无香精。', 'SHOP_NOW'),
        ('干到起皮了吗', '今晚开始修屏障', '一款神经酰胺面霜，先舒缓再锁水。无香精。', 'LEARN_MORE'),
        ('换季别硬扛', '屏障修护就今晚', '干敏肌换季方案：无香精，质地清爽不闷。', 'SHOP_NOW'),
        ('先救干燥屏障', '修护可以很简单', '针对紧绷起皮。无香精，敏感肌友好。', 'SHOP_NOW'),
    ]
    return [
        {
            'source_mode': 'custom',
            'source_ref': '',
            'creative': None,
            'hook': hook,
            'headline': headline,
            'description': description,
            'cta': cta,
            'batch_id': batch_id,
            'batch_position': index,
            'status': AdCopyVariation.STATUS_DRAFT,
        }
        for index, (hook, headline, description, cta) in enumerate(copies)
    ]


def _custom_en_drafts(batch_id):
    copies = [
        ('Your barrier needs help', 'Repair overnight', 'Ceramide cream for tight, dry skin. No fragrance.', 'SHOP_NOW'),
        ('Skin feels tight?', 'Overnight repair', 'Dermatologist-tested cream. No invented prices.', 'LEARN_MORE'),
    ]
    return [
        {
            'source_mode': 'custom',
            'source_ref': '',
            'creative': None,
            'hook': hook,
            'headline': headline,
            'description': description,
            'cta': cta,
            'batch_id': batch_id,
            'batch_position': index,
            'status': AdCopyVariation.STATUS_DRAFT,
        }
        for index, (hook, headline, description, cta) in enumerate(copies)
    ]


def _existing_creative_rows(batch_id, creative):
    copies = [
        ('QA hook, shorter', 'QA headline v2', 'Rollover creative rewritten shorter for feed.', 'SHOP_NOW', AdCopyVariation.STATUS_DRAFT),
        ('Keep scrolling?', 'QA offer, tight', 'Same offer as the synced creative. No new claims.', 'LEARN_MORE', AdCopyVariation.STATUS_REVIEWED),
        ('Dry season is here', 'Fix the barrier', 'Mapped from existing Meta creative title/body.', 'SHOP_NOW', AdCopyVariation.STATUS_REVIEWED),
    ]
    return [
        {
            'source_mode': 'existing',
            'source_ref': '',
            'creative': creative,
            'hook': hook,
            'headline': headline,
            'description': description,
            'cta': cta,
            'batch_id': batch_id,
            'batch_position': index,
            'status': status,
        }
        for index, (hook, headline, description, cta, status) in enumerate(copies)
    ]


def _external_url_reviewed():
    return [
        {
            'source_mode': 'external_url',
            'source_ref': 'https://www.facebook.com/ads/library/?id=demo-seed-1',
            'creative': None,
            'hook': 'Seen in Ad Library',
            'headline': 'Same offer, new angle',
            'description': 'Variation extracted from a public Ad Library URL.',
            'cta': 'LEARN_MORE',
            'batch_id': uuid.uuid4(),
            'batch_position': 0,
            'status': AdCopyVariation.STATUS_REVIEWED,
        },
        {
            'source_mode': 'external_url',
            'source_ref': 'https://www.facebook.com/ads/library/?id=demo-seed-2',
            'creative': None,
            'hook': 'Stop the scroll',
            'headline': 'Proof, then the CTA',
            'description': 'Reviewed draft from an external URL source.',
            'cta': 'SHOP_NOW',
            'batch_id': uuid.uuid4(),
            'batch_position': 0,
            'status': AdCopyVariation.STATUS_REVIEWED,
        },
    ]
