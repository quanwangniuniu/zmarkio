from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from asset.models import Asset
from core.models import Organization, Team, Project
from task.models import Task

User = get_user_model()


class AssetAuthenticationTest(APITestCase):
    """Test authentication requirements for Asset API endpoints"""
    
    def setUp(self):
        # Create test user
        self.user = User.objects.create_user(
            email='test@example.com',
            username='testuser',
            password='testpass123'
        )
        # Create test organization and team
        self.organization = Organization.objects.create(
            name="Test Organization"
        )
        self.team = Team.objects.create(
            organization=self.organization,
            name="Test Team"
        )

        # Create project and task
        self.project = Project.objects.create(name="Test Project", organization=self.organization)
        self.task = Task.objects.create(summary="Test Task", type="asset", project=self.project)
        
        # Create test asset
        self.asset = Asset.objects.create(
            task=self.task,
            owner=self.user,
            team=self.team,
            status=Asset.NOT_SUBMITTED,
            tags=['test']
        )
    
    def tearDown(self):
        """Clean up any created versions to avoid foreign key constraint issues"""
        from asset.models import AssetVersion
        AssetVersion.objects.all().delete()
    
    def test_asset_list_requires_authentication(self):
        """Test that asset list endpoint requires authentication"""
        url = reverse('asset:asset-list')
        
        # Test without authentication
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        
        # Test with authentication
        self.client.force_authenticate(user=self.user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_asset_detail_requires_authentication(self):
        """Test that asset detail endpoint requires authentication"""
        url = reverse('asset:asset-detail', kwargs={'pk': self.asset.pk})
        
        # Test without authentication
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        
        # Test with authentication
        self.client.force_authenticate(user=self.user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_asset_create_requires_authentication(self):
        """Test that asset creation requires authentication"""
        url = reverse('asset:asset-list')
        data = {
            'task': self.task.id,
            'team': self.team.id,
            'tags': ['new', 'asset']
        }
        
        # Test without authentication
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        
        # Test with authentication
        self.client.force_authenticate(user=self.user)
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
    
    def test_asset_update_requires_authentication(self):
        """Test that asset update requires authentication"""
        url = reverse('asset:asset-detail', kwargs={'pk': self.asset.pk})
        data = {
            'task': self.task.id,
            'team': self.team.id,
            'tags': ['updated', 'tags']
        }
        
        # Test without authentication
        response = self.client.put(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        
        # Test with authentication
        self.client.force_authenticate(user=self.user)
        response = self.client.put(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_asset_delete_requires_authentication(self):
        """Test that asset deletion requires authentication"""
        url = reverse('asset:asset-detail', kwargs={'pk': self.asset.pk})
        
        # Test without authentication
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        
        # Test with authentication
        self.client.force_authenticate(user=self.user)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
    
    def test_asset_submit_requires_authentication(self):
        """Test that asset submit requires authentication"""
        # Create a finalized version first
        from asset.models import AssetVersion
        version = AssetVersion.objects.create(
            asset=self.asset,
            version_number=1,
            uploaded_by=self.user,
            scan_status=AssetVersion.CLEAN
        )
        version.finalize(finalized_by=self.user)
        version.save()
        
        url = reverse('asset:asset-submit', kwargs={'pk': self.asset.pk})
        
        # Test without authentication
        response = self.client.put(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        
        # Test with authentication
        self.client.force_authenticate(user=self.user)
        response = self.client.put(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_asset_review_requires_authentication(self):
        """Test that asset review requires authentication"""
        # Create a finalized version and submit the asset first
        from asset.models import AssetVersion
        version = AssetVersion.objects.create(
            asset=self.asset,
            version_number=1,
            uploaded_by=self.user,
            scan_status=AssetVersion.CLEAN
        )
        version.finalize(finalized_by=self.user)
        version.save()
        
        self.client.force_authenticate(user=self.user)
        submit_url = reverse('asset:asset-submit', kwargs={'pk': self.asset.pk})
        self.client.put(submit_url)
        
        # Test review without authentication
        self.client.force_authenticate(user=None)
        review_url = reverse('asset:asset-review', kwargs={'pk': self.asset.pk})
        data = {'action': 'start_review'}
        
        response = self.client.patch(review_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        
        # Test review with authentication
        self.client.force_authenticate(user=self.user)
        response = self.client.patch(review_url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_asset_versions_require_authentication(self):
        """Test that asset versions endpoint requires authentication"""
        url = reverse('asset:asset-version-list', kwargs={'asset_id': self.asset.pk})
        
        # Test without authentication
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        
        # Test with authentication
        self.client.force_authenticate(user=self.user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_asset_comments_require_authentication(self):
        """Test that asset comments endpoint requires authentication"""
        url = reverse('asset:asset-comment-list', kwargs={'asset_id': self.asset.pk})
        
        # Test without authentication
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        
        # Test with authentication
        self.client.force_authenticate(user=self.user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_asset_history_requires_authentication(self):
        """Test that asset history endpoint requires authentication"""
        url = reverse('asset:asset-history', kwargs={'asset_id': self.asset.pk})
        
        # Test without authentication
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        
        # Test with authentication
        self.client.force_authenticate(user=self.user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_review_assignments_require_authentication(self):
        """Test that review assignments endpoint requires authentication"""
        url = reverse('asset:review-assignment-list', kwargs={'asset_id': self.asset.pk})
        
        # Test without authentication
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        
        # Test with authentication
        self.client.force_authenticate(user=self.user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_bulk_review_requires_authentication(self):
        """Test that bulk review endpoint requires authentication"""
        url = reverse('asset:bulk-review')
        data = {
            'reviews': [
                {
                    'asset_id': self.asset.id,
                    'action': 'approve'
                }
            ]
        }
        
        # Test without authentication
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        
        # Test with authentication
        self.client.force_authenticate(user=self.user)
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_asset_download_requires_authentication(self):
        """Test that asset download endpoint requires authentication"""
        # Create a version first
        from asset.models import AssetVersion
        version = AssetVersion.objects.create(
            asset=self.asset,
            version_number=1,
            uploaded_by=self.user,
            scan_status=AssetVersion.CLEAN
        )
        url = reverse('asset:asset-version-download', kwargs={'asset_id': self.asset.pk, 'version_id': version.pk})
        
        # Test without authentication
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        
        # Test with authentication
        self.client.force_authenticate(user=self.user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)  # Asset not approved, but auth works
    
    def test_invalid_token_returns_401(self):
        """Test that invalid authentication token returns 401"""
        url = reverse('asset:asset-list')
        
        # Test with invalid token
        self.client.credentials(HTTP_AUTHORIZATION='Bearer invalid_token')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
    
    def test_expired_token_returns_401(self):
        """Test that expired authentication token returns 401"""
        url = reverse('asset:asset-list')
        
        # Test with expired token (this would require JWT settings to be configured)
        # For now, we'll test with an obviously invalid token
        self.client.credentials(HTTP_AUTHORIZATION='Bearer expired_token_here')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
    
    def test_missing_token_returns_401(self):
        """Test that missing authentication token returns 401"""
        url = reverse('asset:asset-list')
        
        # Test with missing token
        self.client.credentials(HTTP_AUTHORIZATION='')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
    
    def test_different_user_can_access_assets(self):
        """Test that different authenticated users can access assets"""
        # Create another user
        other_user = User.objects.create_user(
            email='other@example.com',
            username='otheruser_auth',
            password='testpass123'
        )
        
        url = reverse('asset:asset-list')
        
        # Test with other user
        self.client.force_authenticate(user=other_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Test accessing specific asset
        detail_url = reverse('asset:asset-detail', kwargs={'pk': self.asset.pk})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_comprehensive_authentication_coverage(self):
        """Comprehensive test ensuring ALL endpoints require authentication"""
        
        # All endpoints from urls.py with their supported methods
        endpoint_configs = [
            # Asset CRUD
            ('asset:asset-list', ['GET', 'POST']),
            ('asset:asset-detail', ['GET', 'PUT', 'DELETE']),
            
            # Asset workflow
            ('asset:asset-submit', ['PUT']),
            ('asset:asset-review', ['PATCH']),
            
            # Asset versions
            ('asset:asset-version-list', ['GET', 'POST']),
            
            # Asset comments
            ('asset:asset-comment-list', ['GET', 'POST']),
            
            # Asset history
            ('asset:asset-history', ['GET']),
            
            # Review assignments
            ('asset:review-assignment-list', ['GET', 'POST']),
            
            # Bulk operations
            ('asset:bulk-review', ['POST']),
            
            # Download
            ('asset:asset-version-download', ['GET']),
        ]
        
        for endpoint_name, supported_methods in endpoint_configs:
            # Test each supported method
            for method in supported_methods:
                # Prepare URL and test data based on endpoint type
                if endpoint_name == 'asset:asset-list':
                    kwargs = {}
                elif endpoint_name == 'asset:asset-detail':
                    kwargs = {'pk': self.asset.pk}
                elif endpoint_name == 'asset:asset-submit':
                    kwargs = {'pk': self.asset.pk}
                elif endpoint_name == 'asset:asset-review':
                    kwargs = {'pk': self.asset.pk}
                elif endpoint_name == 'asset:asset-version-list':
                    kwargs = {'asset_id': self.asset.pk}
                elif endpoint_name == 'asset:asset-comment-list':
                    kwargs = {'asset_id': self.asset.pk}
                elif endpoint_name == 'asset:asset-history':
                    kwargs = {'asset_id': self.asset.pk}
                elif endpoint_name == 'asset:review-assignment-list':
                    kwargs = {'asset_id': self.asset.pk}
                elif endpoint_name == 'asset:bulk-review':
                    kwargs = {}
                elif endpoint_name == 'asset:asset-version-download':
                    # Create a version first
                    from asset.models import AssetVersion
                    version = AssetVersion.objects.create(
                        asset=self.asset,
                        version_number=1,
                        uploaded_by=self.user,
                        scan_status=AssetVersion.CLEAN
                    )
                    kwargs = {'asset_id': self.asset.pk, 'version_id': version.pk}
                else:
                    kwargs = {}
                
                url = reverse(endpoint_name, kwargs=kwargs)
                
                # Test without authentication - should return 401
                if method == 'GET':
                    response = self.client.get(url)
                elif method == 'POST':
                    response = self.client.post(url, {}, format='json')
                elif method == 'PUT':
                    response = self.client.put(url, {}, format='json')
                elif method == 'PATCH':
                    response = self.client.patch(url, {}, format='json')
                elif method == 'DELETE':
                    response = self.client.delete(url)
                
                self.assertEqual(
                    response.status_code, 
                    status.HTTP_401_UNAUTHORIZED,
                    f"Endpoint {endpoint_name} {method} should require authentication"
                )
                
                # Test with authentication - should not return 401
                self.client.force_authenticate(user=self.user)
                
                if method == 'GET':
                    response = self.client.get(url)
                elif method == 'POST':
                    response = self.client.post(url, {}, format='json')
                elif method == 'PUT':
                    response = self.client.put(url, {}, format='json')
                elif method == 'PATCH':
                    response = self.client.patch(url, {}, format='json')
                elif method == 'DELETE':
                    response = self.client.delete(url)
                
                self.assertNotEqual(
                    response.status_code, 
                    status.HTTP_401_UNAUTHORIZED,
                    f"Endpoint {endpoint_name} {method} should work when authenticated"
                )
                
                # Reset authentication
                self.client.force_authenticate(user=None)
    


    def test_all_endpoints_require_authentication(self):
        """Test that ALL asset endpoints require authentication"""
        
        # Define all endpoints with their methods and expected success status codes
        endpoints = [
            # Asset CRUD endpoints
            ('asset:asset-list', 'get', {}, status.HTTP_200_OK),
            ('asset:asset-list', 'post', {}, status.HTTP_201_CREATED),
            ('asset:asset-detail', 'get', {'pk': self.asset.pk}, status.HTTP_200_OK),
            ('asset:asset-detail', 'put', {'pk': self.asset.pk}, status.HTTP_200_OK),
            ('asset:asset-detail', 'delete', {'pk': self.asset.pk}, status.HTTP_204_NO_CONTENT),
            
            # Asset workflow endpoints - submit first, then review
            ('asset:asset-submit', 'put', {'pk': self.asset.pk}, status.HTTP_200_OK),
            ('asset:asset-review', 'patch', {'pk': self.asset.pk}, status.HTTP_200_OK),
            
            # Asset version endpoints
            ('asset:asset-version-list', 'get', {'asset_id': self.asset.pk}, status.HTTP_200_OK),
            ('asset:asset-version-list', 'post', {'asset_id': self.asset.pk}, status.HTTP_201_CREATED),
            
            # Asset comment endpoints
            ('asset:asset-comment-list', 'get', {'asset_id': self.asset.pk}, status.HTTP_200_OK),
            ('asset:asset-comment-list', 'post', {'asset_id': self.asset.pk}, status.HTTP_201_CREATED),
            
            # Asset history endpoint
            ('asset:asset-history', 'get', {'asset_id': self.asset.pk}, status.HTTP_200_OK),
            
            # Review assignment endpoints
            ('asset:review-assignment-list', 'get', {'asset_id': self.asset.pk}, status.HTTP_200_OK),
            ('asset:review-assignment-list', 'post', {'asset_id': self.asset.pk}, status.HTTP_201_CREATED),
            
            # Bulk review endpoint
            ('asset:bulk-review', 'post', {}, status.HTTP_200_OK),
            
            # Download endpoint
                            ('asset:asset-version-download', 'get', {'asset_id': self.asset.pk, 'version_id': 1}, status.HTTP_400_BAD_REQUEST),  # Asset not approved
        ]
        
        for endpoint_name, method, kwargs, expected_success_code in endpoints:
            # Create a fresh asset for each test to avoid state conflicts
            if endpoint_name in ['asset:asset-submit', 'asset:asset-review', 'asset:asset-version-list', 'asset:asset-comment-list', 'asset:asset-history', 'asset:review-assignment-list', 'asset:asset-version-download', 'asset:asset-detail']:
                # Create a fresh asset for tests that need a specific asset
                test_asset = Asset.objects.create(
                    task=self.task,
                    owner=self.user,
                    team=self.team,
                    status=Asset.NOT_SUBMITTED,
                    tags=['test']
                )
                # Update kwargs to use the fresh asset
                if 'pk' in kwargs:
                    kwargs['pk'] = test_asset.pk
                if 'asset_id' in kwargs:
                    kwargs['asset_id'] = test_asset.pk
            else:
                test_asset = self.asset
            
            url = reverse(endpoint_name, kwargs=kwargs)
            
            # Prepare test data for POST/PUT/PATCH requests
            test_data = {}
            if endpoint_name == 'asset:asset-list' and method == 'post':
                test_data = {
                    'task': self.task.id,
                    'team': self.team.id,
                    'tags': ['test']
                }
            elif endpoint_name == 'asset:asset-detail' and method == 'put':
                test_data = {
                    'task': self.task.id,
                    'team': self.team.id,
                    'tags': ['updated']
                }
            elif endpoint_name == 'asset:asset-submit' and method == 'put':
                # Create a finalized version first
                from asset.models import AssetVersion
                version = AssetVersion.objects.create(
                    asset=test_asset,
                    version_number=1,
                    uploaded_by=self.user,
                    scan_status=AssetVersion.CLEAN
                )
                version.finalize(finalized_by=self.user)
                version.save()
            elif endpoint_name == 'asset:asset-review' and method == 'patch':
                # First create a finalized version and submit the asset to make review possible
                from asset.models import AssetVersion
                version = AssetVersion.objects.create(
                    asset=test_asset,
                    version_number=1,
                    uploaded_by=self.user,
                    scan_status=AssetVersion.CLEAN
                )
                version.finalize(finalized_by=self.user)
                version.save()
                
                self.client.force_authenticate(user=self.user)
                submit_url = reverse('asset:asset-submit', kwargs={'pk': test_asset.pk})
                self.client.put(submit_url)
                self.client.force_authenticate(user=None)
                test_data = {'action': 'start_review'}
            elif endpoint_name == 'asset:asset-version-list' and method == 'post':
                # Create a fresh asset without any versions for version creation test
                clean_asset = Asset.objects.create(
                    task=self.task,
                    owner=self.user,
                    team=self.team,
                    status=Asset.NOT_SUBMITTED,
                    tags=['test']
                )
                kwargs = {'asset_id': clean_asset.pk}
                # Create a fresh test file for version creation (avoid file pointer issues)
                from django.core.files.uploadedfile import SimpleUploadedFile
                test_data = {'file': SimpleUploadedFile("test.pdf", b"file_content", content_type="application/pdf")}
            elif endpoint_name == 'asset:asset-version-download' and method == 'get':
                # Create a version first
                from asset.models import AssetVersion
                version = AssetVersion.objects.create(
                    asset=test_asset,
                    version_number=1,
                    uploaded_by=self.user,
                    scan_status=AssetVersion.CLEAN
                )
                kwargs = {'asset_id': test_asset.pk, 'version_id': version.pk}
                # Update the URL to use the correct version ID
                url = reverse(endpoint_name, kwargs=kwargs)
            elif endpoint_name == 'asset:asset-comment-list' and method == 'post':
                test_data = {'body': 'Test comment'}
            elif endpoint_name == 'asset:review-assignment-list' and method == 'post':
                test_data = {
                    'user': self.user.id,
                    'role': 'reviewer'
                }
            elif endpoint_name == 'asset:bulk-review' and method == 'post':
                test_data = {
                    'reviews': [
                        {
                            'asset_id': test_asset.id,
                            'action': 'approve'
                        }
                    ]
                }
            
            # Test without authentication - should return 401
            if method == 'get':
                response = self.client.get(url)
            elif method == 'post':
                # Use multipart format for file uploads, json for others
                if endpoint_name == 'asset:asset-version-list':
                    # Create a fresh file object for each request to avoid file pointer issues
                    from django.core.files.uploadedfile import SimpleUploadedFile
                    fresh_test_data = {'file': SimpleUploadedFile("test.pdf", b"file_content", content_type="application/pdf")}
                    response = self.client.post(url, fresh_test_data, format='multipart')
                else:
                    response = self.client.post(url, test_data, format='json')
            elif method == 'put':
                response = self.client.put(url, test_data, format='json')
            elif method == 'patch':
                response = self.client.patch(url, test_data, format='json')
            elif method == 'delete':
                response = self.client.delete(url)
            
            self.assertEqual(
                response.status_code, 
                status.HTTP_401_UNAUTHORIZED,
                f"Endpoint {endpoint_name} {method.upper()} should require authentication"
            )
            
            # Test with authentication - should return expected success code
            self.client.force_authenticate(user=self.user)
            
            if method == 'get':
                response = self.client.get(url)
            elif method == 'post':
                # Use multipart format for file uploads, json for others
                if endpoint_name == 'asset:asset-version-list':
                    # Create a fresh file object for each request to avoid file pointer issues
                    from django.core.files.uploadedfile import SimpleUploadedFile
                    fresh_test_data = {'file': SimpleUploadedFile("test.pdf", b"file_content", content_type="application/pdf")}
                    response = self.client.post(url, fresh_test_data, format='multipart')
                else:
                    response = self.client.post(url, test_data, format='json')
            elif method == 'put':
                response = self.client.put(url, test_data, format='json')
            elif method == 'patch':
                response = self.client.patch(url, test_data, format='json')
            elif method == 'delete':
                response = self.client.delete(url)
            
            self.assertEqual(
                response.status_code, 
                expected_success_code,
                f"Endpoint {endpoint_name} {method.upper()} should return {expected_success_code} when authenticated, but got {response.status_code}. Response: {response.data}"
            )
            
            # Reset authentication for next iteration
            self.client.force_authenticate(user=None)
    
    def test_http_methods_not_allowed_return_405(self):
        """Test that unsupported HTTP methods return 405 Method Not Allowed"""
        self.client.force_authenticate(user=self.user)
        
        # Test unsupported methods for each endpoint
        endpoints = [
            ('asset:asset-list', 'put', {}),  # PUT not allowed on list
            ('asset:asset-list', 'patch', {}),  # PATCH not allowed on list
            ('asset:asset-list', 'delete', {}),  # DELETE not allowed on list
            ('asset:asset-detail', 'post', {'pk': self.asset.pk}),  # POST not allowed on detail
            ('asset:asset-submit', 'get', {'pk': self.asset.pk}),  # GET not allowed on submit
            ('asset:asset-submit', 'post', {'pk': self.asset.pk}),  # POST not allowed on submit
            ('asset:asset-submit', 'patch', {'pk': self.asset.pk}),  # PATCH not allowed on submit
            ('asset:asset-submit', 'delete', {'pk': self.asset.pk}),  # DELETE not allowed on submit
            ('asset:asset-review', 'get', {'pk': self.asset.pk}),  # GET not allowed on review
            ('asset:asset-review', 'post', {'pk': self.asset.pk}),  # POST not allowed on review
            ('asset:asset-review', 'put', {'pk': self.asset.pk}),  # PUT not allowed on review
            ('asset:asset-review', 'delete', {'pk': self.asset.pk}),  # DELETE not allowed on review
            ('asset:asset-history', 'post', {'asset_id': self.asset.pk}),  # POST not allowed on history
            ('asset:asset-history', 'put', {'asset_id': self.asset.pk}),  # PUT not allowed on history
            ('asset:asset-history', 'patch', {'asset_id': self.asset.pk}),  # PATCH not allowed on history
            ('asset:asset-history', 'delete', {'asset_id': self.asset.pk}),  # DELETE not allowed on history
            ('asset:bulk-review', 'get', {}),  # GET not allowed on bulk-review
            ('asset:bulk-review', 'put', {}),  # PUT not allowed on bulk-review
            ('asset:bulk-review', 'patch', {}),  # PATCH not allowed on bulk-review
            ('asset:bulk-review', 'delete', {}),  # DELETE not allowed on bulk-review
            ('asset:asset-version-download', 'post', {'asset_id': self.asset.pk, 'version_id': 1}),  # POST not allowed on download
            ('asset:asset-version-download', 'put', {'asset_id': self.asset.pk, 'version_id': 1}),  # PUT not allowed on download
            ('asset:asset-version-download', 'patch', {'asset_id': self.asset.pk, 'version_id': 1}),  # PATCH not allowed on download
            ('asset:asset-version-download', 'delete', {'asset_id': self.asset.pk, 'version_id': 1}),  # DELETE not allowed on download
        ]
        
        for endpoint_name, method, kwargs in endpoints:
            url = reverse(endpoint_name, kwargs=kwargs)
            
            if method == 'get':
                response = self.client.get(url)
            elif method == 'post':
                response = self.client.post(url, {}, format='json')
            elif method == 'put':
                response = self.client.put(url, {}, format='json')
            elif method == 'patch':
                response = self.client.patch(url, {}, format='json')
            elif method == 'delete':
                response = self.client.delete(url)
        

            self.assertEqual(
                response.status_code, 
                status.HTTP_405_METHOD_NOT_ALLOWED,
                f"Endpoint {endpoint_name} {method.upper()} should return 405 Method Not Allowed"
            ) 
    
    def test_all_urls_can_be_reversed(self):
        """Test that all URLs can be properly reversed"""
        # Test all endpoints to ensure they can be reversed correctly
        url_tests = [
            ('asset:asset-list', {}),
            ('asset:asset-detail', {'pk': self.asset.pk}),
            ('asset:asset-submit', {'pk': self.asset.pk}),
            ('asset:asset-review', {'pk': self.asset.pk}),
            ('asset:asset-version-list', {'asset_id': self.asset.pk}),
            ('asset:asset-comment-list', {'asset_id': self.asset.pk}),
            ('asset:asset-history', {'asset_id': self.asset.pk}),
            ('asset:review-assignment-list', {'asset_id': self.asset.pk}),
            ('asset:bulk-review', {}),
            ('asset:asset-version-download', {'asset_id': self.asset.pk, 'version_id': 1}),
        ]
        
        for endpoint_name, kwargs in url_tests:
            try:
                url = reverse(endpoint_name, kwargs=kwargs)
                # If we get here, the URL was successfully reversed
                self.assertIsInstance(url, str)
                self.assertTrue(url.startswith('/api/assets/'))
            except Exception as e:
                self.fail(f"Failed to reverse URL for {endpoint_name} with kwargs {kwargs}: {e}") 