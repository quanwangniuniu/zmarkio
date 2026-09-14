"""
Test cases for facebook_meta API views
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status
from django.utils import timezone
from datetime import timedelta
from facebook_meta.models import (
    AdLabel, AdCreative, AdCreativePhotoData,
    AdCreativeTextData, AdCreativePreview, AdCreativeVideoData
)
import json

User = get_user_model()


class GetAdCreativeViewTest(TestCase):
    """Test cases for get_ad_creative API view"""
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        
        self.ad_creative = AdCreative.objects.create(
            id='123456789',
            actor=self.user,
            name='Test Ad Creative',
            status=AdCreative.STATUS_ACTIVE,
            body='Test body content',
            title='Test Title'
        )
    
    def test_get_ad_creative_success(self):
        """Test successful ad creative retrieval"""
        response = self.client.get('/api/facebook_meta/123456789/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], '123456789')
        self.assertEqual(response.data['name'], 'Test Ad Creative')
        self.assertEqual(response.data['status'], 'ACTIVE')
        self.assertEqual(response.data['body'], 'Test body content')
        self.assertEqual(response.data['title'], 'Test Title')
    
    def test_get_ad_creative_invalid_id_format(self):
        """A non-numeric id is treated as a slug; unknown -> 404."""
        response = self.client.get('/api/facebook_meta/abc123/')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
    
    def test_get_ad_creative_not_found(self):
        """Test ad creative retrieval when not found"""
        response = self.client.get('/api/facebook_meta/999999999/')
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('error', response.data)
        self.assertIn('AdCreative not found', response.data['error'])
        self.assertEqual(response.data['code'], 'NOT_FOUND')
    
    def test_get_ad_creative_with_fields_filter(self):
        """Test ad creative retrieval with fields filter"""
        response = self.client.get('/api/facebook_meta/123456789/?fields=id,name,status')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 3)
        self.assertIn('id', response.data)
        self.assertIn('name', response.data)
        self.assertIn('status', response.data)
        self.assertNotIn('body', response.data)
        self.assertNotIn('title', response.data)
    
    def test_get_ad_creative_invalid_fields(self):
        """Test ad creative retrieval with invalid fields"""
        response = self.client.get('/api/facebook_meta/123456789/?fields=id,invalid_field,status')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertIn('Invalid field', response.data['error'])
        self.assertEqual(response.data['code'], 'INVALID_FIELDS')
    
    def test_get_ad_creative_with_thumbnail_dimensions(self):
        """Test ad creative retrieval with thumbnail dimensions"""
        response = self.client.get('/api/facebook_meta/123456789/?thumbnail_width=100&thumbnail_height=200')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], '123456789')
    
    def test_get_ad_creative_invalid_thumbnail_dimensions(self):
        """Test ad creative retrieval with invalid thumbnail dimensions"""
        response = self.client.get('/api/facebook_meta/123456789/?thumbnail_width=0&thumbnail_height=200')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertIn('thumbnail_width must be between 1 and 10000', response.data['error'])
        self.assertEqual(response.data['code'], 'INVALID_THUMBNAIL_DIMENSIONS')
    
    def test_get_ad_creative_authentication_required(self):
        """Test that authentication is required"""
        self.client.logout()
        
        response = self.client.get('/api/facebook_meta/123456789/')
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class AdCreativesViewTest(TestCase):
    """Test cases for AdCreativesView"""
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        
        self.ad_creative1 = AdCreative.objects.create(
            id='creative_1',
            actor=self.user,
            name='Creative 1',
            status=AdCreative.STATUS_ACTIVE
        )
        
        self.ad_creative2 = AdCreative.objects.create(
            id='creative_2',
            actor=self.user,
            name='Creative 2',
            status=AdCreative.STATUS_ACTIVE
        )
    
    def test_get_ad_creatives_success(self):
        """Test successful ad creatives retrieval"""
        response = self.client.get('/api/facebook_meta/adcreatives/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)
        self.assertEqual(len(response.data['results']), 2)
        
        # Check that both creatives are returned
        creative_ids = [result['id'] for result in response.data['results']]
        self.assertIn('creative_1', creative_ids)
        self.assertIn('creative_2', creative_ids)
    
    def test_create_ad_creative_success(self):
        """Test successful ad creative creation"""
        data = {
            'name': 'New Ad Creative',
            'authorization_category': 'NONE'
        }
        
        response = self.client.post('/api/facebook_meta/adcreatives/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('data', response.data)
        self.assertIn('id', response.data['data'])
        
        # Verify the creative was created
        created_creative = AdCreative.objects.get(id=response.data['data']['id'])
        self.assertEqual(created_creative.name, 'New Ad Creative')
        self.assertEqual(created_creative.actor, self.user)
    
    def test_create_ad_creative_with_object_story_spec(self):
        """Test ad creative creation with object_story_spec"""
        data = {
            'name': 'New Ad Creative with Story',
            'object_story_spec': {
                'instagram_user_id': 'instagram_123',
                'page_id': 'page_123',
                'product_data': [{'product': 'test'}],
                'link_data': {
                    'name': 'Test Link',
                    'link': 'https://example.com',
                    'message': 'Test link message'
                },
                'photo_data': [{
                    'caption': 'Test photo caption',
                    'image_hash': 'abc123',
                    'url': 'https://example.com/image.jpg'
                }],
                'text_data': {
                    'message': 'Test text message'
                }
            }
        }
        
        response = self.client.post('/api/facebook_meta/adcreatives/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('data', response.data)
        self.assertIn('id', response.data['data'])
        
        # Verify the creative was created with object story spec
        created_creative = AdCreative.objects.get(id=response.data['data']['id'])
        self.assertEqual(created_creative.name, 'New Ad Creative with Story')
        self.assertEqual(created_creative.object_story_spec_instagram_user_id, 'instagram_123')
        self.assertEqual(created_creative.object_story_spec_page_id, 'page_123')
        self.assertEqual(created_creative.object_story_spec_product_data, [{'product': 'test'}])
        
        # Verify related objects were created
        self.assertIsNotNone(created_creative.object_story_spec_link_data)
        self.assertEqual(created_creative.object_story_spec_photo_data.count(), 1)
        self.assertIsNotNone(created_creative.object_story_spec_text_data)
    
    def test_create_ad_creative_invalid_data(self):
        """Test ad creative creation with invalid data"""
        data = {
            'name': '',  # Empty name
            'authorization_category': 'INVALID_CATEGORY'
        }
        
        response = self.client.post('/api/facebook_meta/adcreatives/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], 'INVALID_DATA')
    
    def test_create_ad_creative_missing_required_fields(self):
        """Test ad creative creation with missing required fields"""
        data = {
            'authorization_category': 'NONE'
            # Missing required 'name' field
        }
        
        response = self.client.post('/api/facebook_meta/adcreatives/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], 'INVALID_DATA')
    
    def test_create_ad_creative_authentication_required(self):
        """Test that authentication is required for creation"""
        self.client.logout()
        
        data = {
            'name': 'New Ad Creative',
            'authorization_category': 'NONE'
        }
        
        response = self.client.post('/api/facebook_meta/adcreatives/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class AdCreativeUpdateViewTest(TestCase):
    """Test cases for AdCreativeUpdateView"""
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        
        self.ad_creative = AdCreative.objects.create(
            id='123456789',
            actor=self.user,
            name='Original Name',
            status=AdCreative.STATUS_ACTIVE
        )
    
    def test_update_ad_creative_success(self):
        """Test successful ad creative update"""
        data = {
            'name': 'Updated Name',
            'status': 'IN_PROCESS'
        }
        
        response = self.client.patch('/api/facebook_meta/123456789/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['success'], True)
        
        # Verify the creative was updated
        self.ad_creative.refresh_from_db()
        self.assertEqual(self.ad_creative.name, 'Updated Name')
        self.assertEqual(self.ad_creative.status, 'IN_PROCESS')
    
    def test_update_ad_creative_invalid_id_format(self):
        """Test ad creative update with invalid ID format"""
        data = {
            'name': 'Updated Name'
        }
        
        response = self.client.patch('/api/facebook_meta/abc123/', data, format='json')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
    
    def test_update_ad_creative_not_found(self):
        """Test ad creative update when not found"""
        data = {
            'name': 'Updated Name'
        }
        
        response = self.client.patch('/api/facebook_meta/999999999/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('error', response.data)
        self.assertIn('AdCreative not found', response.data['error'])
        self.assertEqual(response.data['code'], 'NOT_FOUND')
    
    def test_update_ad_creative_invalid_data(self):
        """Test ad creative update with invalid data"""
        data = {
            'name': '',  # Empty name
            'status': 'INVALID_STATUS'
        }
        
        response = self.client.patch('/api/facebook_meta/123456789/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], 'INVALID_DATA')
    
    def test_update_ad_creative_with_adlabels(self):
        """Test ad creative update with ad labels"""
        # Create ad labels
        ad_label1 = AdLabel.objects.create(
            id='label_1',
            name='Label 1'
        )
        
        ad_label2 = AdLabel.objects.create(
            id='label_2',
            name='Label 2'
        )
        
        data = {
            'name': 'Updated Name',
            'adlabels': ['Label 1', 'Label 2']
        }
        
        response = self.client.patch('/api/facebook_meta/123456789/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['success'], True)
        
        # Verify the creative was updated
        self.ad_creative.refresh_from_db()
        self.assertEqual(self.ad_creative.name, 'Updated Name')
        
        # Verify labels were added
        labels = self.ad_creative.ad_labels.all()
        self.assertEqual(labels.count(), 2)
        label_names = [label.name for label in labels]
        self.assertIn('Label 1', label_names)
        self.assertIn('Label 2', label_names)
    
    def test_update_ad_creative_invalid_adlabels(self):
        """Test ad creative update with invalid ad labels"""
        data = {
            'name': 'Updated Name',
            'adlabels': ['Label 1', '', 'Label 2']  # Empty string in labels
        }
        
        response = self.client.patch('/api/facebook_meta/123456789/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], 'INVALID_DATA')
    
    def test_update_ad_creative_authentication_required(self):
        """Test that authentication is required for update"""
        self.client.logout()
        
        data = {
            'name': 'Updated Name'
        }
        
        response = self.client.patch('/api/facebook_meta/123456789/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class AdCreativeDeleteViewTest(TestCase):
    """Test cases for AdCreativeDeleteView"""
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        
        self.ad_creative = AdCreative.objects.create(
            id='123456789',
            actor=self.user,
            name='Test Ad Creative',
            status=AdCreative.STATUS_ACTIVE
        )
    
    def test_delete_ad_creative_success(self):
        """Test successful ad creative deletion"""
        response = self.client.delete('/api/facebook_meta/123456789/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['success'], True)
        
        # Verify the creative was deleted
        self.assertFalse(AdCreative.objects.filter(id='123456789').exists())
    
    def test_delete_ad_creative_invalid_id_format(self):
        """A non-numeric id is treated as a slug; unknown -> 404."""
        response = self.client.delete('/api/facebook_meta/abc123/')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
    
    def test_delete_ad_creative_not_found(self):
        """Test ad creative deletion when not found"""
        response = self.client.delete('/api/facebook_meta/999999999/')
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('error', response.data)
        self.assertIn('AdCreative not found', response.data['error'])
        self.assertEqual(response.data['code'], 'NOT_FOUND')
    
    def test_delete_ad_creative_authentication_required(self):
        """Test that authentication is required for deletion"""
        self.client.logout()
        
        response = self.client.delete('/api/facebook_meta/123456789/')
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
    
    def test_delete_ad_creative_with_related_objects(self):
        """Test ad creative deletion with related objects"""
        # Create related objects
        photo_data = AdCreativePhotoData.objects.create(
            caption='Test photo caption',
            image_hash='abc123',
            url='https://example.com/image.jpg'
        )
        
        text_data = AdCreativeTextData.objects.create(
            message='Test text message'
        )
        
        # Set object story spec data
        # Use .set() for ManyToMany fields
        self.ad_creative.object_story_spec_photo_data.set([photo_data])
        self.ad_creative.object_story_spec_text_data = text_data
        self.ad_creative.save()
        
        # Delete the creative
        response = self.client.delete('/api/facebook_meta/123456789/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['success'], True)
        
        # Verify the creative was deleted
        self.assertFalse(AdCreative.objects.filter(id='123456789').exists())
        
        # Verify related objects still exist (SET_NULL behavior)
        self.assertTrue(AdCreativePhotoData.objects.filter(id=photo_data.id).exists())
        self.assertTrue(AdCreativeTextData.objects.filter(id=text_data.id).exists())


class ViewIntegrationTest(TestCase):
    """Test integration between different views"""
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
    
    def test_create_read_update_delete_flow(self):
        """Test complete CRUD flow"""
        # 1. Create ad creative
        create_data = {
            'name': 'Integration Test Creative',
            'authorization_category': 'NONE'
        }
        
        create_response = self.client.post('/api/facebook_meta/adcreatives/', create_data, format='json')
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        creative_id = create_response.data['data']['id']
        
        # 2. Read ad creative
        read_response = self.client.get(f'/api/facebook_meta/{creative_id}/')
        self.assertEqual(read_response.status_code, status.HTTP_200_OK)
        self.assertEqual(read_response.data['name'], 'Integration Test Creative')
        
        # 3. Update ad creative
        update_data = {
            'name': 'Updated Integration Test Creative',
            'status': 'IN_PROCESS'
        }
        
        update_response = self.client.patch(f'/api/facebook_meta/{creative_id}/', update_data, format='json')
        self.assertEqual(update_response.status_code, status.HTTP_200_OK)
        self.assertEqual(update_response.data['success'], True)
        
        # 4. Verify update
        read_response = self.client.get(f'/api/facebook_meta/{creative_id}/')
        self.assertEqual(read_response.status_code, status.HTTP_200_OK)
        self.assertEqual(read_response.data['name'], 'Updated Integration Test Creative')
        self.assertEqual(read_response.data['status'], 'IN_PROCESS')
        
        # 5. Delete ad creative
        delete_response = self.client.delete(f'/api/facebook_meta/{creative_id}/')
        self.assertEqual(delete_response.status_code, status.HTTP_200_OK)
        self.assertEqual(delete_response.data['success'], True)
        
        # 6. Verify deletion
        read_response = self.client.get(f'/api/facebook_meta/{creative_id}/')
        print(read_response.data)
        self.assertEqual(read_response.status_code, status.HTTP_404_NOT_FOUND)
    
    def test_list_and_filter_flow(self):
        """Test list and filter flow"""
        # Create multiple creatives
        for i in range(3):
            AdCreative.objects.create(
                id=f'creative_{i}',
                actor=self.user,
                name=f'Creative {i}',
                status=AdCreative.STATUS_ACTIVE
            )
        
        # Create labels
        label1 = AdLabel.objects.create(
            id='label_1',
            name='Label 1'
        )
        
        label2 = AdLabel.objects.create(
            id='label_2',
            name='Label 2'
        )
        
        # Add labels to creatives
        creative1 = AdCreative.objects.get(id='creative_1')
        creative2 = AdCreative.objects.get(id='creative_2')
        creative1.ad_labels.add(label1)  # creative_1 has Label 1
        creative2.ad_labels.add(label2)  # creative_2 has Label 2
        creative2.ad_labels.add(label1)  # creative_2 also has Label 1
        
        # 1. List all creatives
        list_response = self.client.get('/api/facebook_meta/adcreatives/')
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_response.data['results']), 3)
        
        # 2. Test field filtering
        field_filter_response = self.client.get('/api/facebook_meta/adcreatives/?fields=id,name')
        self.assertEqual(field_filter_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(field_filter_response.data['results']), 3)
        
        for result in field_filter_response.data['results']:
            self.assertEqual(len(result), 2)  # Only id and name
            self.assertIn('id', result)
            self.assertIn('name', result)
            self.assertNotIn('status', result)
    
    def test_error_handling_flow(self):
        """Test error handling across different views"""
        # 1. Test invalid ID format across views
        invalid_id_views = [
            '/api/facebook_meta/abc123/',
            '/api/facebook_meta/abc123/',
            '/api/facebook_meta/abc123/'
        ]
        
        for view_url in invalid_id_views:
            with self.subTest(view_url=view_url):
                if 'update' in view_url:
                    response = self.client.patch(view_url, {'name': 'test'}, format='json')
                elif 'delete' in view_url:
                    response = self.client.delete(view_url)
                else:
                    response = self.client.get(view_url)

                # Non-numeric ids are slugs now; unknown -> 404.
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # 2. Test non-existent resources
        non_existent_views = [
            '/api/facebook_meta/999999999/',
            '/api/facebook_meta/999999999/',
            '/api/facebook_meta/999999999/'
        ]
        
        for view_url in non_existent_views:
            with self.subTest(view_url=view_url):
                if 'update' in view_url:
                    response = self.client.patch(view_url, {'name': 'test'}, format='json')
                elif 'delete' in view_url:
                    response = self.client.delete(view_url)
                else:
                    response = self.client.get(view_url)
                
                self.assertIn(response.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST])
        
        # 3. Test authentication required
        self.client.logout()
        
        auth_required_views = [
            '/api/facebook_meta/123456789/',
            '/api/facebook_meta/adcreatives/',
            '/api/facebook_meta/123456789/',
            '/api/facebook_meta/123456789/'
        ]
        
        for view_url in auth_required_views:
            with self.subTest(view_url=view_url):
                if 'update' in view_url:
                    response = self.client.patch(view_url, {'name': 'test'}, format='json')
                elif 'delete' in view_url:
                    response = self.client.delete(view_url)
                else:
                    response = self.client.get(view_url)
                
                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class ViewsEdgeCasesTest(TestCase):
    """Cover edge branches and error codes in views"""

    def setUp(self):
        self.user = User.objects.create_user('edge@example.com', 'password')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.creative = AdCreative.objects.create(
            id='555555555', actor=self.user, name='Edge', status=AdCreative.STATUS_ACTIVE
        )

    def test_get_ad_creative_invalid_thumbnail_types(self):
        r = self.client.get(f'/api/facebook_meta/{self.creative.id}/?thumbnail_width=abc')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(r.data.get('code'), 'INVALID_THUMBNAIL_WIDTH')
        r = self.client.get(f'/api/facebook_meta/{self.creative.id}/?thumbnail_height=abc')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(r.data.get('code'), 'INVALID_THUMBNAIL_HEIGHT')

    def test_update_invalid_payload(self):
        r = self.client.patch(f'/api/facebook_meta/{self.creative.id}/', {'status': 'INVALID'}, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(r.data.get('code'), 'INVALID_DATA')

    def test_create_invalid_payload(self):
        r = self.client.post(f'/api/facebook_meta/adcreatives/', {'name': ''}, format='json')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(r.data.get('code'), 'INVALID_DATA')


class SharePreviewViewTest(TestCase):
    """Test cases for SharePreviewView (POST, GET, DELETE)"""

    def setUp(self):
        self.user = User.objects.create_user(
            username='shareuser',
            email='share@example.com',
            password='testpass123'
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.ad_creative = AdCreative.objects.create(
            id='999888777',
            actor=self.user,
            name='Share Test Creative',
            status=AdCreative.STATUS_ACTIVE
        )

    def test_create_share_preview_by_slug(self):
        url = f'/api/facebook_meta/{self.ad_creative.slug}/'
        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)

        response = self.client.post(f'{url}share-preview/', {'days': 7}, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        preview = AdCreativePreview.objects.get(ad_creative=self.ad_creative)
        self.assertEqual(response.data['link'], preview.link)
        self.assertEqual(preview.days_active, 7)

    def test_get_share_preview_by_slug(self):
        created = self.client.post(
            f'/api/facebook_meta/{self.ad_creative.id}/share-preview/',
            {'days': 7}, format='json',
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)

        response = self.client.get(f'/api/facebook_meta/{self.ad_creative.slug}/share-preview/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['link'], created.data['link'])

    def test_delete_share_preview_by_slug(self):
        created = self.client.post(
            f'/api/facebook_meta/{self.ad_creative.id}/share-preview/',
            {'days': 7}, format='json',
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)

        response = self.client.delete(f'/api/facebook_meta/{self.ad_creative.slug}/share-preview/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(AdCreativePreview.objects.filter(ad_creative=self.ad_creative).exists())

    def test_share_preview_requires_owner_for_slug_and_id(self):
        other_user = User.objects.create_user(username='other', email='other@example.com')
        self.client.force_authenticate(user=other_user)

        for identifier in (self.ad_creative.slug, self.ad_creative.id):
            for method in ('post', 'get', 'delete'):
                with self.subTest(identifier=identifier, method=method):
                    response = getattr(self.client, method)(
                        f'/api/facebook_meta/{identifier}/share-preview/',
                        {'days': 7}, format='json',
                    )
                    self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
                    self.assertEqual(response.data['code'], 'AD_CREATIVE_NOT_FOUND')
        self.assertFalse(AdCreativePreview.objects.filter(ad_creative=self.ad_creative).exists())

    def test_create_share_preview_success(self):
        """Test successful share preview creation"""
        data = {
            'days': 30
        }
        
        response = self.client.post(f'/api/facebook_meta/{self.ad_creative.id}/share-preview/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('link', response.data)
        self.assertEqual(response.data['days_active'], 30)
        self.assertGreaterEqual(response.data['days_left'], 0)
        
        # Verify preview was created
        preview = AdCreativePreview.objects.get(ad_creative=self.ad_creative)
        self.assertEqual(preview.days_active, 30)
    
    def test_create_share_preview_with_7_days(self):
        """Test share preview creation with 7 days"""
        data = {
            'days': 7
        }
        
        response = self.client.post(f'/api/facebook_meta/{self.ad_creative.id}/share-preview/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['days_active'], 7)
        
        # Verify preview was created with 7 days
        preview = AdCreativePreview.objects.get(ad_creative=self.ad_creative)
        self.assertEqual(preview.days_active, 7)
    
    def test_create_share_preview_with_14_days(self):
        """Test share preview creation with 14 days"""
        data = {
            'days': 14
        }
        
        response = self.client.post(f'/api/facebook_meta/{self.ad_creative.id}/share-preview/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['days_active'], 14)
        
        # Verify preview was created with 14 days
        preview = AdCreativePreview.objects.get(ad_creative=self.ad_creative)
        self.assertEqual(preview.days_active, 14)
    
    def test_create_share_preview_already_exists(self):
        """Test that creating a preview when one already exists returns error"""
        # Create first preview
        data1 = {'days': 30}
        response1 = self.client.post(f'/api/facebook_meta/{self.ad_creative.id}/share-preview/', data1, format='json')
        self.assertEqual(response1.status_code, status.HTTP_201_CREATED)
        
        # Try to create second preview (should return error)
        data2 = {'days': 14}
        response2 = self.client.post(f'/api/facebook_meta/{self.ad_creative.id}/share-preview/', data2, format='json')
        self.assertEqual(response2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response2.data)
        self.assertEqual(response2.data['code'], 'PREVIEW_ALREADY_EXISTS')
        
        # Verify only one preview exists
        previews = AdCreativePreview.objects.filter(ad_creative=self.ad_creative)
        self.assertEqual(previews.count(), 1)
        self.assertEqual(previews.first().days_active, 30)
    
    def test_create_share_preview_missing_days(self):
        """Test share preview creation without days parameter"""
        data = {}
        
        response = self.client.post(f'/api/facebook_meta/{self.ad_creative.id}/share-preview/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], 'MISSING_DAYS')
    
    def test_create_share_preview_invalid_days(self):
        """Test share preview creation with invalid days"""
        data = {'days': 60}  # 60 is not in choices anymore
        
        response = self.client.post(f'/api/facebook_meta/{self.ad_creative.id}/share-preview/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], 'INVALID_DAYS')
    
    def test_create_share_preview_ad_creative_not_found(self):
        """Test share preview creation with non-existent ad creative"""
        data = {'days': 30}
        
        response = self.client.post('/api/facebook_meta/999999999/share-preview/', data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], 'AD_CREATIVE_NOT_FOUND')
    
    def test_get_share_preview_success(self):
        """Test successful share preview retrieval"""
        # Create a preview first
        preview = AdCreativePreview.objects.create(
            ad_creative=self.ad_creative,
            token='test-token-123',
            link='https://example.com/preview/123',
            expires_at=timezone.now() + timedelta(days=30),
            days_active=30,
            json_spec={'test': 'data'}
        )
        
        response = self.client.get(f'/api/facebook_meta/{self.ad_creative.id}/share-preview/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('link', response.data)
        self.assertEqual(response.data['days_active'], 30)
        self.assertGreaterEqual(response.data['days_left'], 0)
    
    def test_get_share_preview_not_found(self):
        """Test share preview retrieval when no preview exists"""
        response = self.client.get(f'/api/facebook_meta/{self.ad_creative.id}/share-preview/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data['link'])
        self.assertIsNone(response.data['days_active'])
        self.assertIsNone(response.data['days_left'])
    
    def test_delete_share_preview_success(self):
        """Test successful share preview deletion"""
        # Create a preview first
        preview = AdCreativePreview.objects.create(
            ad_creative=self.ad_creative,
            token='test-token-456',
            link='https://example.com/preview/456',
            expires_at=timezone.now() + timedelta(days=30),
            days_active=30
        )
        
        response = self.client.delete(f'/api/facebook_meta/{self.ad_creative.id}/share-preview/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('message', response.data)
        
        # Verify preview was deleted
        self.assertFalse(AdCreativePreview.objects.filter(ad_creative=self.ad_creative).exists())
    
    def test_delete_share_preview_not_found(self):
        """Test share preview deletion when no preview exists"""
        response = self.client.delete(f'/api/facebook_meta/{self.ad_creative.id}/share-preview/')
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], 'PREVIEW_NOT_FOUND')
    
    def test_share_preview_authentication_required(self):
        """Test that authentication is required for share preview operations"""
        self.client.logout()
        
        # Test POST
        response = self.client.post(f'/api/facebook_meta/{self.ad_creative.id}/share-preview/', 
                                  {'days': 30}, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        
        # Test GET
        response = self.client.get(f'/api/facebook_meta/{self.ad_creative.id}/share-preview/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        
        # Test DELETE
        response = self.client.delete(f'/api/facebook_meta/{self.ad_creative.id}/share-preview/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class AssociateMediaToAdCreativeViewTest(TestCase):
    """Test cases for AssociateMediaToAdCreativeView"""

    def setUp(self):
        self.user = User.objects.create_user(
            username='mediauser',
            email='media@example.com',
            password='testpass123'
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        
        self.ad_creative = AdCreative.objects.create(
            id='777666555',
            actor=self.user,
            name='Media Test Creative',
            status=AdCreative.STATUS_ACTIVE
        )
        
        # Create test photo and video data
        self.photo_data1 = AdCreativePhotoData.objects.create(
            caption='Test Photo 1',
            image_hash='hash1',
            url='https://example.com/photo1.jpg'
        )
        
        self.photo_data2 = AdCreativePhotoData.objects.create(
            caption='Test Photo 2',
            image_hash='hash2',
            url='https://example.com/photo2.jpg'
        )
        
        self.video_data1 = AdCreativeVideoData.objects.create(
            title='Test Video 1',
            message='Message 1',
            video_id='video1',
            image_url='https://example.com/video1.jpg'
        )
        
        self.video_data2 = AdCreativeVideoData.objects.create(
            title='Test Video 2',
            message='Message 2',
            video_id='video2',
            image_url='https://example.com/video2.jpg'
        )
    
    def test_associate_photos_success(self):
        """Test successful photo association"""
        data = {
            'photo_ids': [self.photo_data1.id, self.photo_data2.id]
        }
        
        response = self.client.post(f'/api/facebook_meta/{self.ad_creative.id}/associate-media/', 
                                  data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['success'], True)
        self.assertEqual(response.data['associated_photos'], 2)
        self.assertEqual(response.data['associated_videos'], 0)
        
        # Verify association
        self.ad_creative.refresh_from_db()
        photos = self.ad_creative.object_story_spec_photo_data.all()
        self.assertEqual(photos.count(), 2)
        self.assertIn(self.photo_data1, photos)
        self.assertIn(self.photo_data2, photos)
    
    def test_associate_videos_success(self):
        """Test successful video association"""
        data = {
            'video_ids': [self.video_data1.id, self.video_data2.id]
        }
        
        response = self.client.post(f'/api/facebook_meta/{self.ad_creative.id}/associate-media/', 
                                  data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['success'], True)
        self.assertEqual(response.data['associated_photos'], 0)
        self.assertEqual(response.data['associated_videos'], 2)
        
        # Verify association
        self.ad_creative.refresh_from_db()
        videos = self.ad_creative.object_story_spec_video_data.all()
        self.assertEqual(videos.count(), 2)
        self.assertIn(self.video_data1, videos)
        self.assertIn(self.video_data2, videos)
    
    def test_associate_mixed_media_success(self):
        """Test successful mixed media association"""
        data = {
            'photo_ids': [self.photo_data1.id],
            'video_ids': [self.video_data1.id]
        }
        
        response = self.client.post(f'/api/facebook_meta/{self.ad_creative.id}/associate-media/', 
                                  data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['associated_photos'], 1)
        self.assertEqual(response.data['associated_videos'], 1)
        
        # Verify association
        self.ad_creative.refresh_from_db()
        photos = self.ad_creative.object_story_spec_photo_data.all()
        videos = self.ad_creative.object_story_spec_video_data.all()
        self.assertEqual(photos.count(), 1)
        self.assertEqual(videos.count(), 1)
    
    def test_clear_photos_association(self):
        """Test clearing photo associations by sending empty array"""
        # First associate some photos
        self.ad_creative.object_story_spec_photo_data.add(self.photo_data1, self.photo_data2)
        
        # Now clear them
        data = {'photo_ids': []}
        
        response = self.client.post(f'/api/facebook_meta/{self.ad_creative.id}/associate-media/', 
                                  data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['associated_photos'], 0)
        
        # Verify photos are cleared
        self.ad_creative.refresh_from_db()
        photos = self.ad_creative.object_story_spec_photo_data.all()
        self.assertEqual(photos.count(), 0)
    
    def test_clear_videos_association(self):
        """Test clearing video associations by sending empty array"""
        # First associate some videos
        self.ad_creative.object_story_spec_video_data.add(self.video_data1, self.video_data2)
        
        # Now clear them
        data = {'video_ids': []}
        
        response = self.client.post(f'/api/facebook_meta/{self.ad_creative.id}/associate-media/', 
                                  data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['associated_videos'], 0)
        
        # Verify videos are cleared
        self.ad_creative.refresh_from_db()
        videos = self.ad_creative.object_story_spec_video_data.all()
        self.assertEqual(videos.count(), 0)
    
    def test_associate_media_no_media_provided(self):
        """Test association with no media provided in request"""
        data = {}
        
        response = self.client.post(f'/api/facebook_meta/{self.ad_creative.id}/associate-media/', 
                                  data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], 'NO_MEDIA_PROVIDED')
    
    def test_associate_media_invalid_photo_ids(self):
        """Test association with invalid photo IDs"""
        data = {
            'photo_ids': [99999, 99998]  # Non-existent IDs
        }
        
        response = self.client.post(f'/api/facebook_meta/{self.ad_creative.id}/associate-media/', 
                                  data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], 'PHOTOS_NOT_FOUND')
    
    def test_associate_media_invalid_video_ids(self):
        """Test association with invalid video IDs"""
        data = {
            'video_ids': [99999, 99998]  # Non-existent IDs
        }
        
        response = self.client.post(f'/api/facebook_meta/{self.ad_creative.id}/associate-media/', 
                                  data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], 'VIDEOS_NOT_FOUND')
    
    def test_associate_media_invalid_photo_ids_format(self):
        """Test association with invalid photo IDs format"""
        data = {
            'photo_ids': 'not_a_list'  # Should be a list
        }
        
        response = self.client.post(f'/api/facebook_meta/{self.ad_creative.id}/associate-media/', 
                                  data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], 'INVALID_PHOTO_IDS_FORMAT')
    
    def test_associate_media_invalid_video_ids_format(self):
        """Test association with invalid video IDs format"""
        data = {
            'video_ids': 'not_a_list'  # Should be a list
        }
        
        response = self.client.post(f'/api/facebook_meta/{self.ad_creative.id}/associate-media/', 
                                  data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], 'INVALID_VIDEO_IDS_FORMAT')
    
    def test_associate_media_ad_creative_not_found(self):
        """Test association with non-existent ad creative"""
        data = {'photo_ids': [self.photo_data1.id]}
        
        response = self.client.post('/api/facebook_meta/999999999/associate-media/', 
                                  data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], 'AD_CREATIVE_NOT_FOUND')
    
    def test_associate_media_invalid_ad_creative_id_format(self):
        """Test association with invalid ad creative ID format"""
        data = {'photo_ids': [self.photo_data1.id]}
        
        response = self.client.post('/api/facebook_meta/abc123/associate-media/', 
                                  data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], 'INVALID_AD_CREATIVE_ID')
    
    def test_associate_media_authentication_required(self):
        """Test that authentication is required for media association"""
        self.client.logout()
        
        data = {'photo_ids': [self.photo_data1.id]}
        
        response = self.client.post(f'/api/facebook_meta/{self.ad_creative.id}/associate-media/', 
                                  data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class GetPreviewByTokenPublicViewTest(TestCase):
    """Test cases for public preview retrieval by token"""
    
    def setUp(self):
        self.user = User.objects.create_user(
            username='publicuser',
            email='public@example.com',
            password='testpass123'
        )
        
        self.ad_creative = AdCreative.objects.create(
            id='444333222',
            actor=self.user,
            name='Public Preview Creative',
            status=AdCreative.STATUS_ACTIVE
        )
        
        # Create separate ad creative for expired preview (unique constraint)
        self.ad_creative_expired = AdCreative.objects.create(
            id='444333223',
            actor=self.user,
            name='Expired Preview Creative',
            status=AdCreative.STATUS_ACTIVE
        )
        
        # Create a valid preview
        self.valid_preview = AdCreativePreview.objects.create(
            ad_creative=self.ad_creative,
            token='valid-token-123',
            link='https://example.com/preview/123',
            expires_at=timezone.now() + timedelta(days=30),
            days_active=30,
            json_spec={'test': 'data'}
        )
        
        # Create an expired preview
        self.expired_preview = AdCreativePreview.objects.create(
            ad_creative=self.ad_creative_expired,
            token='expired-token-456',
            link='https://example.com/preview/456',
            expires_at=timezone.now() - timedelta(days=1),
            days_active=30
        )
    
    def test_get_public_preview_success(self):
        """Test successful public preview retrieval"""
        response = self.client.get('/api/facebook_meta/preview/valid-token-123/public/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], '444333222')
        self.assertEqual(response.data['name'], 'Public Preview Creative')
        self.assertGreaterEqual(response.data['days_left'], 0)
    
    def test_get_public_preview_not_found(self):
        """Test public preview retrieval with non-existent token"""
        response = self.client.get('/api/facebook_meta/preview/nonexistent-token/public/')
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], 'PREVIEW_NOT_FOUND')
    
    def test_get_public_preview_expired(self):
        """Test public preview retrieval with expired token"""
        response = self.client.get('/api/facebook_meta/preview/expired-token-456/public/')
        
        self.assertEqual(response.status_code, status.HTTP_410_GONE)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], 'PREVIEW_EXPIRED')
    
    def test_get_public_preview_no_authentication_required(self):
        """Test that public preview retrieval doesn't require authentication"""
        # This should work without authentication
        response = self.client.get('/api/facebook_meta/preview/valid-token-123/public/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class PhotoUploadViewTest(TestCase):
    """Test cases for PhotoUploadView"""
    
    def setUp(self):
        self.user = User.objects.create_user(
            username='photouser',
            email='photo@example.com',
            password='testpass123'
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
    
    def test_photo_upload_success(self):
        """Test successful photo upload"""
        # Create a test image file
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        import io
        
        # Create a simple test image
        image = Image.new('RGB', (100, 100), color='red')
        image_io = io.BytesIO()
        image.save(image_io, format='JPEG')
        image_io.seek(0)
        
        uploaded_file = SimpleUploadedFile(
            "test_image.jpg",
            image_io.getvalue(),
            content_type="image/jpeg"
        )
        
        data = {
            'file': uploaded_file,
            'caption': 'Test photo caption'
        }
        
        response = self.client.post('/api/facebook_meta/photos/upload/', data, format='multipart')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['success'], True)
        
        # Verify photo was created
        self.assertTrue(AdCreativePhotoData.objects.filter(caption='Test photo caption').exists())
    
    def test_photo_upload_missing_file(self):
        """Test photo upload without file"""
        data = {
            'caption': 'Test photo caption'
        }
        
        response = self.client.post('/api/facebook_meta/photos/upload/', data, format='multipart')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], 'MISSING_FILE')
    
    def test_photo_upload_authentication_required(self):
        """Test that authentication is required for photo upload"""
        self.client.logout()
        
        from django.core.files.uploadedfile import SimpleUploadedFile
        uploaded_file = SimpleUploadedFile(
            "test_image.jpg",
            b"fake image data",
            content_type="image/jpeg"
        )
        
        data = {
            'file': uploaded_file,
            'caption': 'Test photo caption'
        }
        
        response = self.client.post('/api/facebook_meta/photos/upload/', data, format='multipart')
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class PhotoListViewTest(TestCase):
    """Test cases for PhotoListView"""
    
    def setUp(self):
        self.user = User.objects.create_user(
            username='photouser',
            email='photo@example.com',
            password='testpass123'
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        
        # Create test photos
        self.photo1 = AdCreativePhotoData.objects.create(
            caption='Photo 1',
            image_hash='hash1',
            url='https://example.com/photo1.jpg'
        )
        
        self.photo2 = AdCreativePhotoData.objects.create(
            caption='Photo 2',
            image_hash='hash2',
            url='https://example.com/photo2.jpg'
        )
    
    def test_photo_list_success(self):
        """Test successful photo list retrieval"""
        response = self.client.get('/api/facebook_meta/photos/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)
        self.assertEqual(len(response.data['results']), 2)
        
        # Check that photos are returned
        photo_ids = [photo['id'] for photo in response.data['results']]
        self.assertIn(self.photo1.id, photo_ids)
        self.assertIn(self.photo2.id, photo_ids)
    
    def test_photo_list_authentication_required(self):
        """Test that authentication is required for photo list"""
        self.client.logout()
        
        response = self.client.get('/api/facebook_meta/photos/')
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class VideoUploadViewTest(TestCase):
    """Test cases for VideoUploadView"""
    
    def setUp(self):
        self.user = User.objects.create_user(
            username='videouser',
            email='video@example.com',
            password='testpass123'
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
    
    def test_video_upload_success(self):
        """Test successful video upload"""
        from django.core.files.uploadedfile import SimpleUploadedFile
        
        # Create a test video file (simulate)
        video_file = SimpleUploadedFile(
            "test_video.mp4",
            b"fake video data",
            content_type="video/mp4"
        )
        
        data = {
            'file': video_file,
            'title': 'Test Video Title',
            'message': 'Test video message'
        }
        
        response = self.client.post('/api/facebook_meta/videos/upload/', data, format='multipart')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['success'], True)
        
        # Verify video was created
        self.assertTrue(AdCreativeVideoData.objects.filter(title='Test Video Title').exists())
    
    def test_video_upload_missing_file(self):
        """Test video upload without file"""
        data = {
            'title': 'Test Video Title',
            'message': 'Test video message'
        }
        
        response = self.client.post('/api/facebook_meta/videos/upload/', data, format='multipart')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertEqual(response.data['code'], 'MISSING_FILE')
    
    def test_video_upload_authentication_required(self):
        """Test that authentication is required for video upload"""
        self.client.logout()
        
        from django.core.files.uploadedfile import SimpleUploadedFile
        uploaded_file = SimpleUploadedFile(
            "test_video.mp4",
            b"fake video data",
            content_type="video/mp4"
        )
        
        data = {
            'file': uploaded_file,
            'title': 'Test Video Title',
            'message': 'Test video message'
        }
        
        response = self.client.post('/api/facebook_meta/videos/upload/', data, format='multipart')
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class VideoListViewTest(TestCase):
    """Test cases for VideoListView"""
    
    def setUp(self):
        self.user = User.objects.create_user(
            username='videouser',
            email='video@example.com',
            password='testpass123'
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        
        # Create test videos
        self.video1 = AdCreativeVideoData.objects.create(
            title='Video 1',
            video_id='video1',
            message='Message 1',
            image_url='https://example.com/video1.jpg'
        )
        
        self.video2 = AdCreativeVideoData.objects.create(
            title='Video 2',
            video_id='video2',
            message='Message 2',
            image_url='https://example.com/video2.jpg'
        )
    
    def test_video_list_success(self):
        """Test successful video list retrieval"""
        response = self.client.get('/api/facebook_meta/videos/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)
        self.assertEqual(len(response.data['results']), 2)
        
        # Check that videos are returned
        video_ids = [video['id'] for video in response.data['results']]
        self.assertIn(self.video1.id, video_ids)
        self.assertIn(self.video2.id, video_ids)
    
    def test_video_list_authentication_required(self):
        """Test that authentication is required for video list"""
        self.client.logout()
        
        response = self.client.get('/api/facebook_meta/videos/')
        
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
