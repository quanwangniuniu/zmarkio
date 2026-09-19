from rest_framework import status, permissions, generics
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.http import Http404

from core.slug_mixins import resolve_pk_for  # resolve an ad id that may be a slug or a legacy pk
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.conf import settings
import base64
import json
import os
import hashlib
import re
import logging

logger = logging.getLogger(__name__)
from .models import Ad, CustomerAccount, AdPreview, GoogleAdsPhotoData, GoogleAdsVideoData
from .serializers import AdSerializer, AdListSerializer, GoogleAdsPhotoDataSerializer, GoogleAdsVideoDataSerializer
from .services import AdPreviewService

# ========== Global ads views (for frontend) ==========

class AdsListView(generics.ListCreateAPIView):
    """
    Get all ads for current user
    GET /google_ads/ads/
    POST /google_ads/ads/
    """
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['status', 'type']
    ordering_fields = ['created_at', 'updated_at', 'name']
    ordering = ['-created_at']

    def get_queryset(self):
        """Get all ads for current user"""
        queryset = Ad.objects.filter(
            Q(customer_account__created_by=self.request.user) |
            Q(created_by=self.request.user) |
            Q(created_by__isnull=True)
        )
        campaign_id = self.request.query_params.get('campaign_id')
        if campaign_id:
            queryset = queryset.filter(media_campaign_id=campaign_id)
        return queryset

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return AdSerializer
        return AdListSerializer

    def perform_create(self, serializer):
        """Automatically associate with current user"""
        serializer.save(created_by_id=self.request.user.id)


class AdDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    Get, update, or delete a single ad
    GET /google_ads/ads/{ad_id}/
    PATCH /google_ads/ads/{ad_id}/
    DELETE /google_ads/ads/{ad_id}/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = AdSerializer

    def get_queryset(self):
        """Only return ads for current user or ads without created_by (for testing)"""
        return Ad.objects.filter(
            Q(customer_account__created_by=self.request.user) |
            Q(created_by=self.request.user) |
            Q(created_by__isnull=True)
        )

    def get_object(self):
        """Get ad object"""
        ad_id = resolve_pk_for(Ad, self.kwargs['ad_id'])
        return get_object_or_404(
            self.get_queryset(),
            id=ad_id
        )
    
    def update(self, request, *args, **kwargs):
        """Override update method to add logging"""
        print(f"AdDetailView.update called with data: {request.data}")
        logger.info(f"AdDetailView.update called with data: {request.data}")
        return super().update(request, *args, **kwargs)


# ========== Views for operations by account ==========
class AdsByAccountView(generics.ListCreateAPIView):
    """
    Get ad list by Google Ads account
    """
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['status', 'type']
    search_fields = ['name', 'display_url']
    ordering_fields = ['created_at', 'updated_at', 'name']
    ordering = ['-created_at']

    def get_queryset(self):
        """Get ads for the account based on customer_id"""
        customer_id = self.kwargs['customer_id']
        return Ad.objects.filter(
            customer_account__customer_id=customer_id,
            customer_account__created_by=self.request.user
        )

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return AdSerializer
        return AdListSerializer

    def perform_create(self, serializer):
        """Automatically associate with current account when creating ad"""
        customer_id = self.kwargs['customer_id']
        customer_account = get_object_or_404(
            CustomerAccount,
            customer_id=customer_id,
            created_by=self.request.user
        )
        serializer.save(
            customer_account=customer_account,
            created_by=self.request.user
        )

class AdByAccountView(generics.RetrieveUpdateDestroyAPIView):
    """
    Get single ad by Google Ads account
    """
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        """Get specific ad based on customer_id and ad_id"""
        customer_id = self.kwargs['customer_id']
        ad_id = resolve_pk_for(Ad, self.kwargs['ad_id'])
        return get_object_or_404(
            Ad,
            customer_account__customer_id=customer_id,
            id=ad_id,
            customer_account__created_by=self.request.user
        )

    def get_serializer_class(self):
        return AdSerializer

# ========== Global operation view functions ==========

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def get_ad(request, ad_id):
    """
    Get single ad
    GET /google_ads/{ad_id}
    """
    ad_id = resolve_pk_for(Ad, ad_id)
    try:
        ad = get_object_or_404(
            Ad.objects.select_related('customer_account', 'created_by'),
            id=ad_id,
            customer_account__created_by=request.user
        )
        serializer = AdSerializer(ad)
        return Response(serializer.data)
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_404_NOT_FOUND
        )

class AdUpdateView(generics.UpdateAPIView):
    """
    Update ad
    POST /google_ads/{ad_id}/update/
    """
    queryset = Ad.objects.all()
    serializer_class = AdSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """Only return ads for current user"""
        return self.queryset.filter(
            customer_account__created_by=self.request.user
        )

    def get_object(self):
        """Get ad object to update"""
        ad_id = resolve_pk_for(Ad, self.kwargs['ad_id'])
        return get_object_or_404(
            self.get_queryset(),
            id=ad_id
        )

class AdDeleteView(generics.DestroyAPIView):
    """
    Delete ad
    DELETE /google_ads/{ad_id}/delete/
    """
    queryset = Ad.objects.all()
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """Only return ads for current user"""
        return self.queryset.filter(
            customer_account__created_by=self.request.user
        )

    def get_object(self):
        """Get ad object to delete"""
        ad_id = resolve_pk_for(Ad, self.kwargs['ad_id'])
        return get_object_or_404(
            self.get_queryset(),
            id=ad_id
        )

# ========== Preview related views ==========

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def create_preview_from_ad(request, ad_id):
    """
    Create preview from current ad
    POST /google_ads/{ad_id}/create_preview/
    """
    try:
        # Get ad
        ad = get_object_or_404(
            Ad.objects.filter(
                Q(customer_account__created_by=request.user) |
                Q(created_by=request.user) |
                Q(created_by__isnull=True)
            ),
            id=ad_id
        )
        
        # Get request parameters
        device_type = request.data.get('device_type', 'DESKTOP')
        
        # Create preview
        preview = AdPreviewService.generate_preview_from_ad(
            ad=ad,
            device_type=device_type
        )
        # The public page expects a URL-safe JSON payload, not a token path segment.
        share_payload = base64.urlsafe_b64encode(json.dumps({
            'previewToken': preview.token, 'device': preview.device_type,
        }).encode()).decode().rstrip('=')
        
        return Response({
            'token': preview.token,
            'ad_id': preview.ad.id,
            'device_type': preview.device_type,
            'preview_url': f'/google_ads/preview/share?share={share_payload}',
            'expiration_date_time': preview.expiration_date_time.isoformat()
        }, status=status.HTTP_201_CREATED)
        
    except ValidationError as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_400_BAD_REQUEST
        )
    except Http404:
        return Response(
            {'error': 'Ad draft not found', 'code': 'NOT_FOUND'},
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        return Response(
            {'error': 'Unauthorized access', 'code': 'UNAUTHORIZED'},
            status=status.HTTP_401_UNAUTHORIZED
        )

@api_view(['GET'])
def get_preview_data(request, token):
    """
    Get preview data by token
    GET /google_ads/preview/{token}/
    
    Supports two token formats:
    1. AdPreview token (from AdPreview model)
    2. Encoded share payload (base64 encoded JSON with ad_id)
    """
    try:
        import base64
        import json
        from django.utils import timezone
        from datetime import timedelta
        from urllib.parse import unquote
        
        # URL decode the token in case it was encoded
        token = unquote(token)
        
        preview = None
        
        # Try to find preview by token first (existing AdPreview token)
        try:
            preview = AdPreview.objects.get(token=token)
        except AdPreview.DoesNotExist:
            # If not found, try to decode as share payload
            try:
                # Decode base64 URL-safe token
                def from_url_safe(s):
                    s = s.replace('-', '+').replace('_', '/')
                    while len(s) % 4:
                        s += '='
                    return s
                
                decoded = base64.b64decode(from_url_safe(token)).decode('utf-8')
                payload = json.loads(decoded)
                
                # Extract ad_id from payload
                ad_id = payload.get('ad_id')
                if not ad_id:
                    raise ValueError("No ad_id in payload")
                
                # Get or create preview for this ad
                # Check if there's an existing non-expired preview
                existing_preview = AdPreview.objects.filter(
                    ad_id=ad_id,
                    expiration_date_time__gt=timezone.now()
                ).order_by('-created_at').first()
                
                if existing_preview:
                    preview = existing_preview
                else:
                    # Create new preview
                    ad = get_object_or_404(Ad, id=resolve_pk_for(Ad, ad_id))
                    # Map device from payload (MOBILE/DESKTOP) to device_type
                    device_payload = payload.get('device', 'MOBILE')
                    device_type = device_payload if device_payload in ['MOBILE', 'DESKTOP', 'TABLET', 'CONNECTED_TV', 'OTHER', 'UNSPECIFIED', 'UNKNOWN'] else 'DESKTOP'
                    
                    # Calculate expiration from payload or use default
                    exp_ms = payload.get('exp')
                    if exp_ms:
                        from datetime import datetime
                        expiration_time = datetime.fromtimestamp(exp_ms / 1000, tz=timezone.utc)
                    else:
                        expiration_time = timezone.now() + timedelta(days=7)
                    
                    preview = AdPreviewService.generate_preview_from_ad(ad, device_type)
                    preview.expiration_date_time = expiration_time
                    preview.save()
                    
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError, base64.binascii.Error) as e:
                # Not a valid encoded payload, log for debugging
                logger.debug(f"Failed to decode share token: {str(e)}")
                pass
            except Exception as decode_error:
                # Catch any other decoding errors
                logger.debug(f"Unexpected error decoding token: {str(decode_error)}")
                pass
        
        if not preview:
            return Response(
                {'error': 'Preview token not found', 'code': 'TOKEN_NOT_FOUND'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Check if expired
        if preview.expiration_date_time < timezone.now():
            return Response(
                {'error': 'Preview token has expired', 'code': 'TOKEN_EXPIRED'},
                status=status.HTTP_410_GONE
            )
        
        # Get preview data
        preview_data = AdPreviewService.get_preview_by_token(preview.token)
        
        return Response({
            'ad': {
                'id': preview.ad.id,
                'name': preview.ad.name,
                'type': preview.ad.type,
                'status': preview.ad.status
            },
            'device_type': preview.device_type,
            'preview_data': preview_data,
            'created_at': preview.created_at.isoformat(),
            'expiration_date_time': preview.expiration_date_time.isoformat()
        }, status=status.HTTP_200_OK)
        
    except AdPreview.DoesNotExist:
        return Response(
            {'error': 'Preview token not found', 'code': 'TOKEN_NOT_FOUND'},
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        logger.error(f"Error in get_preview_data: {str(e)}")
        return Response(
            {'error': 'Failed to load preview', 'code': 'SERVER_ERROR', 'detail': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ========== Media Upload and List Views (Aligned with Facebook Meta) ==========

class PhotoUploadView(APIView):
    """
    POST /google_ads/photos/upload/
    """
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    MAX_FILE_BYTES = 30 * 1024 * 1024  # 30 MB
    
    def post(self, request):
        try:
            # validate file exists
            if 'file' not in request.FILES:
                return Response(
                    {'error': 'No file provided'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            file = request.FILES['file']
            
            # validate file size
            if file.size > self.MAX_FILE_BYTES:
                return Response(
                    {'error': f'File size exceeds {self.MAX_FILE_BYTES // (1024*1024)}MB limit'},
                    status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
                )
            
            # validate file type
            allowed_types = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']
            if file.content_type not in allowed_types:
                return Response(
                    {'error': f'Invalid file type. Allowed types: {", ".join(allowed_types)}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # create storage directory
            storage_dir_path = 'google_ads_photos'
            storage_dir = getattr(settings, 'FILE_STORAGE_DIR', os.path.join(settings.BASE_DIR, 'media'))
            full_storage_path = os.path.join(storage_dir, storage_dir_path)
            os.makedirs(full_storage_path, exist_ok=True)
            
            # generate file name, handle conflicts
            original_name = file.name
            base_name, ext = os.path.splitext(original_name)
            
            final_filename = original_name
            counter = 1
            while os.path.exists(os.path.join(full_storage_path, final_filename)):
                final_filename = f"{base_name}({counter}){ext}"
                counter += 1
            
            storage_key = os.path.join(storage_dir_path, final_filename)
            full_path = os.path.join(full_storage_path, final_filename)
            
            # save file
            with open(full_path, 'wb') as destination:
                for chunk in file.chunks():
                    destination.write(chunk)
            
            # calculate SHA-256 checksum
            with open(full_path, 'rb') as f:
                file_content = f.read()
                checksum = hashlib.sha256(file_content).hexdigest()
            
            # generate relative URL - align with Facebook Meta
            file_url = f"/media/{storage_key}"
            
            # create GoogleAdsPhotoData record
            photo_data = GoogleAdsPhotoData.objects.create(
                caption=request.data.get('caption', ''),
                image_hash=checksum,
                url=file_url
            )
            
            return Response({
                'success': True,
                'photo': {
                    'id': photo_data.id,
                    'url': photo_data.url,
                    'caption': photo_data.caption,
                    'image_hash': photo_data.image_hash
                }
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            return Response(
                {'error': f'Upload failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class PhotoListView(generics.ListAPIView):
    """
    GET /google_ads/photos/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = GoogleAdsPhotoDataSerializer
    
    def get_queryset(self):
        return GoogleAdsPhotoData.objects.all().order_by('-id')
    
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        
        return Response({
            'count': queryset.count(),
            'next': None,
            'previous': None,
            'results': serializer.data
        })


class VideoCreateView(APIView):
    """
    POST /google_ads/videos/
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request):
        try:
            url = request.data.get('url', '').strip()
            
            if not url:
                return Response(
                    {'error': 'YouTube URL is required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # validate URL format and extract video_id
            video_id = self._extract_video_id(url)
            if not video_id:
                return Response(
                    {'error': 'Invalid YouTube URL format'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # create GoogleAdsVideoData record
            video_data = GoogleAdsVideoData.objects.create(
                title=f"YouTube Video {video_id}",  # placeholder
                video_id=video_id,
                image_url=f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",  # placeholder
                message=request.data.get('message', '')
            )
            
            return Response({
                'success': True,
                'video': {
                    'id': video_data.id,
                    'title': video_data.title,
                    'video_id': video_data.video_id,
                    'image_url': video_data.image_url,
                    'message': video_data.message
                }
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            return Response(
                {'error': f'Video creation failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _extract_video_id(self, url):
        """extract video_id from YouTube URL"""
        patterns = [
            r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
            r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
            r'youtube\.com/v/([a-zA-Z0-9_-]{11})'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None


class VideoListView(generics.ListAPIView):
    """
    GET /google_ads/videos/
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = GoogleAdsVideoDataSerializer
    
    def get_queryset(self):
        return GoogleAdsVideoData.objects.all().order_by('-id')
    
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        
        return Response({
            'count': queryset.count(),
            'next': None,
            'previous': None,
            'results': serializer.data
        })
