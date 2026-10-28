"""
Tests for notion_editor views and API endpoints
"""
import json
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from notion_editor.models import Draft, ContentBlock, BlockAction, DraftRevision

User = get_user_model()


class DraftViewSetTest(TestCase):
    """Test cases for DraftViewSet"""
    
    def setUp(self):
        """Set up test data"""
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.other_user = User.objects.create_user(
            username='otheruser',
            email='other@example.com',
            password='testpass123'
        )
        self.draft = Draft.objects.create(
            title='Test Draft',
            user=self.user,
            status='draft',
            content_blocks=[
                {
                    'type': 'text',
                    'content': 'Test content',
                    'id': 'block_1'
                }
            ]
        )
        self.content_block = ContentBlock.objects.create(
            draft=self.draft,
            block_type='text',
            content={'text': 'Test content'},
            order=1
        )
    
    
    def test_list_drafts_authenticated(self):
        """Test listing drafts for authenticated user"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['title'], 'Test Draft')
        self.assertEqual(response.data['results'][0]['user_email'], 'test@example.com')
    
    def test_list_drafts_unauthenticated(self):
        """Test listing drafts without authentication"""
        url = reverse('notion_editor:draft-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
    
    def test_list_drafts_user_isolation(self):
        """Test that users only see their own drafts"""
        # Create draft for other user
        Draft.objects.create(
            title='Other Draft',
            user=self.other_user,
            status='draft'
        )
        
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['title'], 'Test Draft')
    
    def test_retrieve_draft(self):
        """Test retrieving a specific draft"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-detail', kwargs={'pk': self.draft.slug})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['title'], 'Test Draft')
        self.assertEqual(response.data['content_blocks_count'], 1)
        self.assertEqual(len(response.data['blocks']), 1)
    
    def test_retrieve_draft_other_user(self):
        """Test retrieving draft from another user"""
        self.client.force_authenticate(user=self.other_user)
        url = reverse('notion_editor:draft-detail', kwargs={'pk': self.draft.slug})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
    
    def test_create_draft(self):
        """Test creating a new draft"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-list')
        data = {
            'title': 'New Draft',
            'status': 'draft',
            'content_blocks': [
                {
                    'type': 'text',
                    'content': 'New content',
                    'id': 'block_1'
                }
            ]
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['title'], 'New Draft')
        self.assertTrue(response.data['slug'])
        # User field might not be included in CreateDraftSerializer response
        # Check that the draft was created successfully
        self.assertEqual(len(response.data['content_blocks']), 1)
    
    def test_update_draft(self):
        """Test updating a draft"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-detail', kwargs={'pk': self.draft.slug})
        data = {
            'title': 'Updated Draft',
            'status': 'published',
            'content_blocks': [
                {
                    'type': 'text',
                    'content': 'Updated content',
                    'id': 'block_1'
                }
            ]
        }
        
        response = self.client.put(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['title'], 'Updated Draft')
        self.assertEqual(response.data['status'], 'published')
    
    def test_partial_update_draft(self):
        """Test partially updating a draft"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-detail', kwargs={'pk': self.draft.slug})
        data = {
            'title': 'Partially Updated Draft'
        }
        
        response = self.client.patch(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['title'], 'Partially Updated Draft')
        self.assertEqual(response.data['status'], 'draft')  # Should remain unchanged
    
    def test_delete_draft(self):
        """Test soft deleting a draft"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-detail', kwargs={'pk': self.draft.slug})
        response = self.client.delete(url)
        
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        
        # Verify soft delete
        self.draft.refresh_from_db()
        self.assertTrue(self.draft.is_deleted)
        
        # Should not appear in list
        list_url = reverse('notion_editor:draft-list')
        list_response = self.client.get(list_url)
        self.assertEqual(list_response.data['count'], 0)
        self.assertEqual(len(list_response.data['results']), 0)
    
    def test_add_block_to_draft(self):
        """Test adding a block to a draft"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-add-block', kwargs={'pk': self.draft.slug})
        data = {
            'type': 'heading',
            'content': 'New heading',
            'level': 1
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('block_id', response.data)
        self.assertIn('content_block_id', response.data)
        self.assertEqual(response.data['message'], 'Block added successfully')
        
        # Verify block was added
        self.draft.refresh_from_db()
        self.assertEqual(len(self.draft.content_blocks), 2)
        self.assertEqual(self.draft.content_blocks[1]['type'], 'heading')
    
    def test_update_block_in_draft(self):
        """Test updating a block in a draft"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-update-block', kwargs={'pk': self.draft.slug})
        block_id = self.draft.content_blocks[0]['id']
        data = {
            'block_id': block_id,
            'block_data': {
                'type': 'text',
                'content': 'Updated content',
                'id': block_id
            }
        }
        
        response = self.client.put(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['message'], 'Block updated successfully')
        
        # Verify block was updated
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.content_blocks[0]['content'], 'Updated content')
    
    def test_delete_block_from_draft(self):
        """Test deleting a block from a draft"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-delete-block', kwargs={'pk': self.draft.slug})
        block_id = self.draft.content_blocks[0]['id']
        data = {
            'block_id': block_id
        }
        
        response = self.client.delete(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['message'], 'Block deleted successfully')
        
        # Verify block was deleted
        self.draft.refresh_from_db()
        self.assertEqual(len(self.draft.content_blocks), 0)
    
    def test_add_block_invalid_data(self):
        """Test adding block with invalid data"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-add-block', kwargs={'pk': self.draft.slug})
        data = 'invalid data'  # Should be a dictionary
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
    
    def test_update_block_missing_data(self):
        """Test updating block with missing data"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-update-block', kwargs={'pk': self.draft.slug})
        data = {
            'block_id': 'some_id'
            # Missing block_data
        }
        
        response = self.client.put(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
    
    def test_delete_block_missing_data(self):
        """Test deleting block with missing data"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-delete-block', kwargs={'pk': self.draft.slug})
        data = {}  # Missing block_id
        
        response = self.client.delete(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)


class ContentBlockViewSetTest(TestCase):
    """Test cases for ContentBlockViewSet"""
    
    def setUp(self):
        """Set up test data"""
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='testuser2',
            email='test2@example.com',
            password='testpass123'
        )
        self.other_user = User.objects.create_user(
            username='otheruser2',
            email='other2@example.com',
            password='testpass123'
        )
        self.draft = Draft.objects.create(
            title='Test Draft',
            user=self.user
        )
        self.content_block = ContentBlock.objects.create(
            draft=self.draft,
            block_type='text',
            content={'text': 'Test content'},
            order=1
        )
    
    
    def test_list_content_blocks(self):
        """Test listing content blocks"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:contentblock-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['block_type'], 'text')
        self.assertEqual(response.data['results'][0]['text_content'], 'Test content')
    
    def test_list_content_blocks_user_isolation(self):
        """Test that users only see their own content blocks"""
        # Create draft and block for other user
        other_draft = Draft.objects.create(
            title='Other Draft',
            user=self.other_user
        )
        ContentBlock.objects.create(
            draft=other_draft,
            block_type='text',
            content={'text': 'Other content'},
            order=1
        )
        
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:contentblock-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['text_content'], 'Test content')
    
    def test_retrieve_content_block(self):
        """Test retrieving a specific content block"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:contentblock-detail', kwargs={'pk': self.content_block.pk})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['block_type'], 'text')
        self.assertEqual(response.data['text_content'], 'Test content')
        self.assertEqual(response.data['draft'], self.draft.id)
    
    def test_create_content_block(self):
        """Test creating a new content block"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:contentblock-list')
        data = {
            'draft': self.draft.id,
            'block_type': 'heading',
            'content': {'text': 'New heading', 'level': 1},
            'order': 2
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['block_type'], 'heading')
        self.assertEqual(response.data['draft'], self.draft.id)
    
    def test_create_content_block_other_user_draft(self):
        """Test creating content block for another user's draft"""
        other_draft = Draft.objects.create(
            title='Other Draft',
            user=self.other_user
        )
        
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:contentblock-list')
        data = {
            'draft': other_draft.id,
            'block_type': 'text',
            'content': {'text': 'Unauthorized content'},
            'order': 1
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
    
    def test_update_content_block(self):
        """Test updating a content block"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:contentblock-detail', kwargs={'pk': self.content_block.pk})
        data = {
            'draft': self.draft.id,
            'block_type': 'rich_text',
            'content': {'text': 'Updated content', 'formatting': ['bold']},
            'order': 1
        }
        
        response = self.client.put(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['block_type'], 'rich_text')
    
    def test_delete_content_block(self):
        """Test deleting a content block"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:contentblock-detail', kwargs={'pk': self.content_block.pk})
        response = self.client.delete(url)
        
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        
        # Verify block was deleted
        self.assertFalse(ContentBlock.objects.filter(pk=self.content_block.pk).exists())


class BlockActionViewSetTest(TestCase):
    """Test cases for BlockActionViewSet"""
    
    def setUp(self):
        """Set up test data"""
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='testuser3',
            email='test3@example.com',
            password='testpass123'
        )
        self.other_user = User.objects.create_user(
            username='otheruser3',
            email='other3@example.com',
            password='testpass123'
        )
        self.draft = Draft.objects.create(
            title='Test Draft',
            user=self.user
        )
        self.content_block = ContentBlock.objects.create(
            draft=self.draft,
            block_type='text',
            content={'text': 'Test content'},
            order=1
        )
        self.block_action = BlockAction.objects.create(
            block=self.content_block,
            action_type='edit',
            label='Edit',
            icon='edit',
            order=1
        )
    
    
    def test_list_block_actions(self):
        """Test listing block actions"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:blockaction-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['action_type'], 'edit')
        self.assertEqual(response.data['results'][0]['label'], 'Edit')
    
    def test_list_block_actions_user_isolation(self):
        """Test that users only see their own block actions"""
        # Create draft, block, and action for other user
        other_draft = Draft.objects.create(
            title='Other Draft',
            user=self.other_user
        )
        other_block = ContentBlock.objects.create(
            draft=other_draft,
            block_type='text',
            content={'text': 'Other content'},
            order=1
        )
        BlockAction.objects.create(
            block=other_block,
            action_type='delete',
            label='Delete',
            order=1
        )
        
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:blockaction-list')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['action_type'], 'edit')
    
    def test_retrieve_block_action(self):
        """Test retrieving a specific block action"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:blockaction-detail', kwargs={'pk': self.block_action.pk})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['action_type'], 'edit')
        self.assertEqual(response.data['label'], 'Edit')
        self.assertEqual(response.data['icon'], 'edit')
    
    def test_create_block_action(self):
        """Test creating a new block action"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:blockaction-list')
        data = {
            'block': self.content_block.id,
            'action_type': 'preview',
            'label': 'Preview',
            'icon': 'eye',
            'is_enabled': True,
            'order': 2
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['action_type'], 'preview')
        self.assertEqual(response.data['label'], 'Preview')
    
    def test_create_block_action_other_user_block(self):
        """Test creating block action for another user's block"""
        other_draft = Draft.objects.create(
            title='Other Draft',
            user=self.other_user
        )
        other_block = ContentBlock.objects.create(
            draft=other_draft,
            block_type='text',
            content={'text': 'Other content'},
            order=1
        )
        
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:blockaction-list')
        data = {
            'block': other_block.id,
            'action_type': 'edit',
            'label': 'Edit'
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('non_field_errors', response.data)
    
    def test_update_block_action(self):
        """Test updating a block action"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:blockaction-detail', kwargs={'pk': self.block_action.pk})
        data = {
            'block': self.content_block.id,
            'action_type': 'edit',
            'label': 'Edit Block',
            'icon': 'edit-alt',
            'is_enabled': True,
            'order': 1
        }
        
        response = self.client.put(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['label'], 'Edit Block')
        self.assertEqual(response.data['icon'], 'edit-alt')
    
    def test_delete_block_action(self):
        """Test deleting a block action"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:blockaction-detail', kwargs={'pk': self.block_action.pk})
        response = self.client.delete(url)
        
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        
        # Verify action was deleted
        self.assertFalse(BlockAction.objects.filter(pk=self.block_action.pk).exists())


class DraftBlocksViewTest(TestCase):
    """Test cases for DraftBlocksView"""
    
    def setUp(self):
        """Set up test data"""
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='testuser4',
            email='test4@example.com',
            password='testpass123'
        )
        self.other_user = User.objects.create_user(
            username='otheruser4',
            email='other4@example.com',
            password='testpass123'
        )
        self.draft = Draft.objects.create(
            title='Test Draft',
            user=self.user
        )
        self.content_block1 = ContentBlock.objects.create(
            draft=self.draft,
            block_type='text',
            content={'text': 'First block'},
            order=1
        )
        self.content_block2 = ContentBlock.objects.create(
            draft=self.draft,
            block_type='heading',
            content={'text': 'Second block', 'level': 1},
            order=2
        )
    
    
    def test_get_draft_blocks(self):
        """Test getting blocks for a draft"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-blocks', kwargs={'draft_id': self.draft.slug})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['draft_id'], self.draft.id)
        self.assertEqual(response.data['draft_title'], 'Test Draft')
        self.assertEqual(len(response.data['blocks']), 2)
        self.assertEqual(response.data['blocks'][0]['block_type'], 'text')
        self.assertEqual(response.data['blocks'][1]['block_type'], 'heading')
    
    def test_get_draft_blocks_other_user(self):
        """Test getting blocks for another user's draft"""
        self.client.force_authenticate(user=self.other_user)
        url = reverse('notion_editor:draft-blocks', kwargs={'draft_id': self.draft.slug})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
    
    def test_get_draft_blocks_nonexistent(self):
        """Test getting blocks for non-existent draft"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-blocks', kwargs={'draft_id': 99999})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ExportDraftViewTest(TestCase):
    """Test cases for ExportDraftView"""
    
    def setUp(self):
        """Set up test data"""
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='testuser5',
            email='test5@example.com',
            password='testpass123'
        )
        self.other_user = User.objects.create_user(
            username='otheruser5',
            email='other5@example.com',
            password='testpass123'
        )
        self.draft = Draft.objects.create(
            title='Test Draft',
            user=self.user,
            status='draft',
            content_blocks=[
                {
                    'type': 'text',
                    'content': 'Test content',
                    'id': 'block_1'
                }
            ]
        )
    
    
    def test_export_draft(self):
        """Test exporting a draft"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:export-draft', kwargs={'draft_id': self.draft.slug})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('Test Draft_export.json', response['Content-Disposition'])
        
        # Parse response content
        content = json.loads(response.content)
        self.assertEqual(content['title'], 'Test Draft')
        self.assertEqual(content['status'], 'draft')
        self.assertEqual(len(content['content_blocks']), 1)
        self.assertIn('created_at', content)
        self.assertIn('updated_at', content)
        self.assertIn('exported_at', content)
    
    def test_export_draft_other_user(self):
        """Test exporting another user's draft"""
        self.client.force_authenticate(user=self.other_user)
        url = reverse('notion_editor:export-draft', kwargs={'draft_id': self.draft.slug})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
    
    def test_export_draft_nonexistent(self):
        """Test exporting non-existent draft"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:export-draft', kwargs={'draft_id': 99999})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class DuplicateDraftViewTest(TestCase):
    """Test cases for DuplicateDraftView"""
    
    def setUp(self):
        """Set up test data"""
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='testuser6',
            email='test6@example.com',
            password='testpass123'
        )
        self.other_user = User.objects.create_user(
            username='otheruser6',
            email='other6@example.com',
            password='testpass123'
        )
        self.draft = Draft.objects.create(
            title='Test Draft',
            user=self.user,
            status='draft',
            content_blocks=[
                {
                    'type': 'text',
                    'content': 'Test content',
                    'id': 'block_1'
                }
            ]
        )
        self.content_block = ContentBlock.objects.create(
            draft=self.draft,
            block_type='text',
            content={'text': 'Test content'},
            order=1
        )
        self.block_action = BlockAction.objects.create(
            block=self.content_block,
            action_type='edit',
            label='Edit',
            icon='edit',
            order=1
        )
    
    
    def test_duplicate_draft(self):
        """Test duplicating a draft"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:duplicate-draft', kwargs={'draft_id': self.draft.slug})
        data = {
            'title': 'Duplicated Draft'
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['title'], 'Duplicated Draft')
        self.assertEqual(response.data['user'], self.user.id)
        self.assertEqual(response.data['status'], 'draft')
        
        # Verify original draft still exists
        self.assertTrue(Draft.objects.filter(pk=self.draft.id).exists())
        
        # Verify new draft was created
        new_draft = Draft.objects.get(title='Duplicated Draft')
        self.assertEqual(new_draft.user, self.user)
        self.assertEqual(len(new_draft.content_blocks), 1)
        
        # Verify content blocks were duplicated
        new_blocks = new_draft.blocks.all()
        self.assertEqual(new_blocks.count(), 1)
        self.assertEqual(new_blocks[0].block_type, 'text')
        
        # Verify block actions were duplicated
        new_actions = new_blocks[0].actions.all()
        self.assertEqual(new_actions.count(), 1)
        self.assertEqual(new_actions[0].action_type, 'edit')
    
    def test_duplicate_draft_default_title(self):
        """Test duplicating a draft with default title"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:duplicate-draft', kwargs={'draft_id': self.draft.slug})
        data = {}  # No title provided
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['title'], 'Test Draft (Copy)')
    
    def test_duplicate_draft_other_user(self):
        """Test duplicating another user's draft"""
        self.client.force_authenticate(user=self.other_user)
        url = reverse('notion_editor:duplicate-draft', kwargs={'draft_id': self.draft.slug})
        data = {
            'title': 'Unauthorized Copy'
        }
        
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
    
    def test_duplicate_draft_nonexistent(self):
        """Test duplicating non-existent draft"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:duplicate-draft', kwargs={'draft_id': 99999})
        data = {
            'title': 'Copy of Nonexistent'
        }

        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class DraftRevisionViewSetTest(TestCase):
    """Test cases for DraftRevisionViewSet"""

    def setUp(self):
        """Set up test data"""
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='testuser7',
            email='test7@example.com',
            password='testpass123'
        )
        self.other_user = User.objects.create_user(
            username='otheruser7',
            email='other7@example.com',
            password='testpass123'
        )
        self.draft = Draft.objects.create(
            title='Test Draft',
            user=self.user,
            status='draft',
            content_blocks=[
                {
                    'type': 'text',
                    'content': 'Initial content',
                    'id': 'block_1'
                }
            ]
        )
        self.revision1 = DraftRevision.objects.create(
            draft=self.draft,
            title='Test Draft',
            content_blocks=self.draft.content_blocks.copy(),
            status='draft',
            revision_number=1,
            change_summary='Initial version',
            created_by=self.user
        )
        self.revision2 = DraftRevision.objects.create(
            draft=self.draft,
            title='Updated Draft',
            content_blocks=[{'type': 'text', 'content': 'Updated content', 'id': 'block_1'}],
            status='draft',
            revision_number=2,
            change_summary='Updated content',
            created_by=self.user
        )

    def test_list_revisions_authenticated(self):
        """Test listing revisions for authenticated user"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draftrevision-list')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(len(response.data['results']), 2)

    def test_list_revisions_unauthenticated(self):
        """Test listing revisions without authentication"""
        url = reverse('notion_editor:draftrevision-list')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_revisions_user_isolation(self):
        """Test that users only see revisions for their own drafts"""
        # Create draft and revision for other user
        other_draft = Draft.objects.create(
            title='Other Draft',
            user=self.other_user,
            status='draft'
        )
        DraftRevision.objects.create(
            draft=other_draft,
            title='Other Draft',
            content_blocks=[],
            status='draft',
            revision_number=1,
            created_by=self.other_user
        )

        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draftrevision-list')
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 2)
        # Should only see revisions for self.draft

    def test_retrieve_revision(self):
        """Test retrieving a specific revision"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draftrevision-detail', kwargs={'pk': self.revision1.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['revision_number'], 1)
        self.assertEqual(response.data['title'], 'Test Draft')
        self.assertEqual(response.data['change_summary'], 'Initial version')

    def test_retrieve_revision_other_user(self):
        """Test retrieving revision from another user's draft"""
        self.client.force_authenticate(user=self.other_user)
        url = reverse('notion_editor:draftrevision-detail', kwargs={'pk': self.revision1.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_revision_not_allowed(self):
        """Test that creating revisions manually is not allowed (read-only viewset)"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draftrevision-list')
        data = {
            'draft': self.draft.id,
            'title': 'Manual Revision',
            'content_blocks': [],
            'status': 'draft',
            'revision_number': 3
        }

        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_update_revision_not_allowed(self):
        """Test that updating revisions is not allowed (read-only viewset)"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draftrevision-detail', kwargs={'pk': self.revision1.pk})
        data = {
            'title': 'Updated Title'
        }

        response = self.client.put(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_delete_revision_not_allowed(self):
        """Test that deleting revisions is not allowed (read-only viewset)"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draftrevision-detail', kwargs={'pk': self.revision1.pk})
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_restore_revision(self):
        """Test restoring a draft to a specific revision"""
        # Update draft to different content
        self.draft.title = 'Latest Version'
        self.draft.content_blocks = [{'type': 'text', 'content': 'Latest content'}]
        self.draft.status = 'published'
        self.draft.save()

        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draftrevision-restore', kwargs={'pk': self.revision1.pk})
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('message', response.data)
        self.assertIn('draft', response.data)

        # Verify draft was restored
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.title, 'Test Draft')
        self.assertEqual(self.draft.status, 'draft')
        self.assertEqual(len(self.draft.content_blocks), 1)
        self.assertEqual(self.draft.content_blocks[0]['content'], 'Initial content')

        # Verify a new revision was created marking the restoration
        latest_revision = self.draft.revisions.first()
        self.assertIn('Restored to revision', latest_revision.change_summary)

    def test_restore_revision_other_user(self):
        """Test that other users cannot restore revisions"""
        self.client.force_authenticate(user=self.other_user)
        url = reverse('notion_editor:draftrevision-restore', kwargs={'pk': self.revision1.pk})
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_restore_revision_creates_new_revision(self):
        """Test that restoring creates a new revision"""
        initial_count = self.draft.revisions.count()

        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draftrevision-restore', kwargs={'pk': self.revision1.pk})
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify new revision was created
        self.assertEqual(self.draft.revisions.count(), initial_count + 1)


class DraftRevisionsListViewTest(TestCase):
    """Test cases for DraftRevisionsListView"""

    def setUp(self):
        """Set up test data"""
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='testuser8',
            email='test8@example.com',
            password='testpass123'
        )
        self.other_user = User.objects.create_user(
            username='otheruser8',
            email='other8@example.com',
            password='testpass123'
        )
        self.draft = Draft.objects.create(
            title='Test Draft',
            user=self.user,
            status='draft'
        )
        self.revision1 = DraftRevision.objects.create(
            draft=self.draft,
            title='Version 1',
            content_blocks=[{'type': 'text', 'content': 'Content v1'}],
            status='draft',
            revision_number=1,
            change_summary='Initial version',
            created_by=self.user
        )
        self.revision2 = DraftRevision.objects.create(
            draft=self.draft,
            title='Version 2',
            content_blocks=[{'type': 'text', 'content': 'Content v2'}],
            status='draft',
            revision_number=2,
            change_summary='Updated content',
            created_by=self.user
        )
        self.revision3 = DraftRevision.objects.create(
            draft=self.draft,
            title='Version 3',
            content_blocks=[{'type': 'text', 'content': 'Content v3'}],
            status='published',
            revision_number=3,
            change_summary='Published',
            created_by=self.user
        )

    def test_get_draft_revisions(self):
        """Test getting all revisions for a draft"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-revisions', kwargs={'draft_id': self.draft.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['draft_id'], self.draft.id)
        self.assertEqual(response.data['draft_title'], 'Test Draft')
        self.assertEqual(response.data['total_revisions'], 3)
        self.assertEqual(len(response.data['revisions']), 3)

        # Verify ordering (most recent first)
        self.assertEqual(response.data['revisions'][0]['revision_number'], 3)
        self.assertEqual(response.data['revisions'][1]['revision_number'], 2)
        self.assertEqual(response.data['revisions'][2]['revision_number'], 1)

    def test_get_draft_revisions_other_user(self):
        """Test getting revisions for another user's draft"""
        self.client.force_authenticate(user=self.other_user)
        url = reverse('notion_editor:draft-revisions', kwargs={'draft_id': self.draft.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_get_draft_revisions_nonexistent(self):
        """Test getting revisions for non-existent draft"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-revisions', kwargs={'draft_id': 99999})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_get_draft_revisions_unauthenticated(self):
        """Test getting revisions without authentication"""
        url = reverse('notion_editor:draft-revisions', kwargs={'draft_id': self.draft.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class DraftVersioningIntegrationTest(TestCase):
    """Integration tests for draft versioning functionality"""

    def setUp(self):
        """Set up test data"""
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='testuser9',
            email='test9@example.com',
            password='testpass123'
        )

    def test_create_draft_creates_initial_revision(self):
        """Test that creating a draft creates an initial revision"""
        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-list')
        data = {
            'title': 'New Draft',
            'status': 'draft',
            'content_blocks': [
                {
                    'type': 'text',
                    'content': 'Initial content',
                    'id': 'block_1'
                }
            ]
        }

        response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # CreateDraftSerializer may not return 'id', so get the draft by title
        draft = Draft.objects.get(title='New Draft', user=self.user)

        # Verify initial revision was created
        self.assertEqual(draft.revisions.count(), 1)

        revision = draft.revisions.first()
        self.assertEqual(revision.revision_number, 1)
        self.assertEqual(revision.title, 'New Draft')
        self.assertIn('Initial', revision.change_summary)

    def test_update_draft_creates_new_revision(self):
        """Test that updating a draft creates a new revision"""
        # Create initial draft
        draft = Draft.objects.create(
            title='Original Title',
            user=self.user,
            status='draft',
            content_blocks=[{'type': 'text', 'content': 'Original content'}]
        )
        DraftRevision.objects.create(
            draft=draft,
            title=draft.title,
            content_blocks=draft.content_blocks.copy(),
            status=draft.status,
            revision_number=1,
            change_summary='Initial version',
            created_by=self.user
        )

        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-detail', kwargs={'pk': draft.slug})
        data = {
            'title': 'Updated Title',
            'status': 'published',
            'content_blocks': [{'type': 'text', 'content': 'Updated content'}],
            'change_summary': 'Made updates and published'
        }

        response = self.client.put(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify new revision was created
        draft.refresh_from_db()
        self.assertEqual(draft.revisions.count(), 2)

        latest_revision = draft.revisions.first()
        self.assertEqual(latest_revision.revision_number, 2)
        self.assertEqual(latest_revision.title, 'Updated Title')
        self.assertEqual(latest_revision.status, 'published')
        self.assertIn('Made updates', latest_revision.change_summary)

    def test_partial_update_draft_creates_revision(self):
        """Test that partial update creates a revision"""
        # Create initial draft
        draft = Draft.objects.create(
            title='Original Title',
            user=self.user,
            status='draft',
            content_blocks=[{'type': 'text', 'content': 'Original content'}]
        )
        DraftRevision.objects.create(
            draft=draft,
            title=draft.title,
            content_blocks=draft.content_blocks.copy(),
            status=draft.status,
            revision_number=1,
            created_by=self.user
        )

        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-detail', kwargs={'pk': draft.slug})
        data = {
            'title': 'Partially Updated Title'
        }

        response = self.client.patch(url, data, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify new revision was created
        draft.refresh_from_db()
        self.assertEqual(draft.revisions.count(), 2)

        latest_revision = draft.revisions.first()
        self.assertEqual(latest_revision.title, 'Partially Updated Title')

    def test_multiple_updates_create_multiple_revisions(self):
        """Test that multiple updates create sequential revisions"""
        # Create initial draft
        draft = Draft.objects.create(
            title='Version 1',
            user=self.user,
            status='draft',
            content_blocks=[{'type': 'text', 'content': 'Content v1'}]
        )
        DraftRevision.objects.create(
            draft=draft,
            title=draft.title,
            content_blocks=draft.content_blocks.copy(),
            status=draft.status,
            revision_number=1,
            created_by=self.user
        )

        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-detail', kwargs={'pk': draft.slug})

        # Update 1
        response1 = self.client.patch(url, {'title': 'Version 2'}, format='json')
        self.assertEqual(response1.status_code, status.HTTP_200_OK)

        # Update 2
        response2 = self.client.patch(url, {'title': 'Version 3'}, format='json')
        self.assertEqual(response2.status_code, status.HTTP_200_OK)

        # Update 3
        response3 = self.client.patch(url, {'title': 'Version 4'}, format='json')
        self.assertEqual(response3.status_code, status.HTTP_200_OK)

        # Verify all revisions were created
        draft.refresh_from_db()
        self.assertEqual(draft.revisions.count(), 4)

        # Verify revision numbers are sequential
        revisions = list(draft.revisions.all().order_by('revision_number'))
        for i, revision in enumerate(revisions, start=1):
            self.assertEqual(revision.revision_number, i)

    def test_revision_snapshot_accuracy(self):
        """Test that revisions accurately capture draft state at time of save"""
        # Create initial draft
        draft = Draft.objects.create(
            title='Version 1',
            user=self.user,
            status='draft',
            content_blocks=[{'type': 'text', 'content': 'Content v1', 'id': 'block_1'}]
        )
        DraftRevision.objects.create(
            draft=draft,
            title=draft.title,
            content_blocks=draft.content_blocks.copy(),
            status=draft.status,
            revision_number=1,
            created_by=self.user
        )

        self.client.force_authenticate(user=self.user)
        url = reverse('notion_editor:draft-detail', kwargs={'pk': draft.slug})

        # Update to version 2
        data_v2 = {
            'title': 'Version 2',
            'status': 'draft',
            'content_blocks': [{'type': 'text', 'content': 'Content v2', 'id': 'block_1'}]
        }
        self.client.put(url, data_v2, format='json')

        # Update to version 3
        data_v3 = {
            'title': 'Version 3',
            'status': 'published',
            'content_blocks': [{'type': 'heading', 'content': 'Content v3', 'id': 'block_1'}]
        }
        self.client.put(url, data_v3, format='json')

        # Verify each revision has correct snapshot
        revisions = list(draft.revisions.all().order_by('revision_number'))

        # Revision 1 snapshot
        self.assertEqual(revisions[0].title, 'Version 1')
        self.assertEqual(revisions[0].status, 'draft')
        self.assertEqual(revisions[0].content_blocks[0]['content'], 'Content v1')

        # Revision 2 snapshot
        self.assertEqual(revisions[1].title, 'Version 2')
        self.assertEqual(revisions[1].status, 'draft')
        self.assertEqual(revisions[1].content_blocks[0]['content'], 'Content v2')

        # Revision 3 snapshot
        self.assertEqual(revisions[2].title, 'Version 3')
        self.assertEqual(revisions[2].status, 'published')
        self.assertEqual(revisions[2].content_blocks[0]['type'], 'heading')
