import os
import hashlib
import logging
import mimetypes
import tempfile
import subprocess
import json
import io
from urllib.parse import unquote, urlparse
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from datetime import timedelta
from .models import TikTokCreative, AdDraft, AdGroup, PublicPreview
from .serializers import AdDraftSerializer, AdGroupSerializer, PublicPreviewSerializer
from django.db.models import Prefetch
from PIL import Image, UnidentifiedImageError
import uuid
import secrets

logger = logging.getLogger(__name__)

# ----------------------
# Centralized constants
# ----------------------
ALLOWED_IMAGE_MIME_TYPES = {
    'image/jpeg', 'image/png', 'image/webp'
}
ALLOWED_VIDEO_MIME_TYPES = {
    'video/mp4',            # MP4 container
    'video/quicktime',      # MOV (QuickTime)
}
IMAGE_MAX_BYTES = 10 * 1024 * 1024  # 10MB limit for images
VIDEO_MAX_BYTES = 100 * 1024 * 1024  # 100MB limit for videos
MIN_DIMENSION = 1
ASPECT_RATIO_TOLERANCE = 0.01
# Minimum dimensions per allowed aspect ratio
MIN_SIZE_1_91_1 = (1200, 628)  # width, height
MIN_SIZE_1_1 = (640, 640)
MIN_SIZE_9_16 = (720, 1280)


def ensure_creative_file_available(creative, file_content, fallback_storage_path):
    storage_path = creative.storage_path or fallback_storage_path

    if not default_storage.exists(storage_path):
        storage_path = default_storage.save(storage_path, ContentFile(file_content))

    preview_url = f"{settings.MEDIA_URL}{storage_path}"
    update_fields = []
    if creative.storage_path != storage_path:
        creative.storage_path = storage_path
        update_fields.append('storage_path')
    if creative.preview_url != preview_url:
        creative.preview_url = preview_url
        update_fields.append('preview_url')
    if update_fields:
        update_fields.append('updated_at')
        creative.save(update_fields=update_fields)

    return creative


def creative_file_exists(creative):
    storage_path = getattr(creative, 'storage_path', None)
    if storage_path and default_storage.exists(storage_path):
        return True

    preview_url = getattr(creative, 'preview_url', None)
    if not preview_url:
        return False

    parsed = urlparse(preview_url)
    parsed_path = unquote(parsed.path or preview_url)
    media_url_setting = getattr(settings, 'MEDIA_URL', '/media/') or '/media/'
    media_url_path = urlparse(media_url_setting).path or media_url_setting
    if not media_url_path.startswith('/'):
        media_url_path = '/' + media_url_path

    if parsed_path.startswith(media_url_path) or parsed_path.startswith('/media/'):
        return False

    return parsed.scheme in ('http', 'https')


def build_material_payload(creative):
    item = {
        'id': creative.id,
        'type': creative.type,
        'name': creative.name,
        'preview_url': creative.preview_url,
        'file_url': creative.preview_url,
        'storage_path': creative.storage_path,
        'width': creative.width,
        'height': creative.height,
        'created_at': creative.created_at.isoformat() if creative.created_at else None,
    }

    if creative.type == 'video' and creative.duration_sec is not None:
        item['duration_sec'] = creative.duration_sec

    return item


def sanitize_assets_for_available_media(raw_assets, user):
    def sanitize_material(value):
        if not isinstance(value, dict):
            return None

        creative_id = value.get('id')
        if creative_id is None:
            return value

        try:
            creative = TikTokCreative.objects.get(id=creative_id, uploaded_by=user)
        except (TikTokCreative.DoesNotExist, ValueError, TypeError):
            return None

        if not creative_file_exists(creative):
            return None

        return {
            **value,
            'id': creative.id,
            'type': creative.type,
            'url': creative.preview_url,
            'previewUrl': creative.preview_url,
            'fileUrl': creative.preview_url,
            'title': creative.name,
            'width': creative.width,
            'height': creative.height,
        }

    def sanitize_asset_group(asset_group):
        if not isinstance(asset_group, dict):
            return asset_group

        sanitized = dict(asset_group)
        if 'primaryCreative' in sanitized:
            sanitized['primaryCreative'] = sanitize_material(sanitized.get('primaryCreative'))

        if isinstance(sanitized.get('images'), list):
            sanitized['images'] = [
                item
                for item in (sanitize_material(image) for image in sanitized['images'])
                if item is not None
            ]

        if sanitized.get('primaryCreative') is None and not sanitized.get('images'):
            return None

        return sanitized

    if isinstance(raw_assets, list):
        return [asset for asset in (sanitize_asset_group(item) for item in raw_assets) if asset is not None]
    if isinstance(raw_assets, dict):
        return sanitize_asset_group(raw_assets) or {}
    return raw_assets


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_video_ad(request):
    """Upload video advertisement content."""
    try:
        # Validate required fields
        if 'file' not in request.FILES:
            return Response({
                'error': 'file is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if 'name' not in request.data:
            return Response({
                'error': 'name is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        file = request.FILES['file']
        name = request.data['name']
        original_filename = request.data.get('original_filename', file.name)
        
        # Validate file type using real uploaded Content-Type
        mime_type = file.content_type or ''
        if mime_type not in ALLOWED_VIDEO_MIME_TYPES:
            return Response({
                'error': f'Only {", ".join(sorted(ALLOWED_VIDEO_MIME_TYPES))} files are supported'
            }, status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        
        # Validate file size
        if file.size > VIDEO_MAX_BYTES:
            return Response({
                'error': f'File exceeds max size: {VIDEO_MAX_BYTES // (1024*1024)}MB'
            }, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        
        # Generate MD5 hash
        file_content = file.read()
        file.seek(0)  # Reset file pointer
        md5_hash = hashlib.md5(file_content).hexdigest()
        file_extension = os.path.splitext(file.name)[1]
        storage_filename = f"tiktok/videos/{md5_hash}{file_extension}"
        
        # Check for duplicate files
        existing_creative = TikTokCreative.objects.filter(md5=md5_hash).first()
        if existing_creative:
            existing_creative = ensure_creative_file_available(
                existing_creative,
                file_content,
                storage_filename,
            )
            return Response({
                'id': existing_creative.id,
                'name': existing_creative.name,
                'type': existing_creative.type,
                'storage_path': existing_creative.storage_path,
                'original_filename': existing_creative.original_filename,
                'mime_type': existing_creative.mime_type,
                'size_bytes': existing_creative.size_bytes,
                'width': existing_creative.width,
                'height': existing_creative.height,
                'duration_sec': existing_creative.duration_sec,
                'md5': existing_creative.md5,
                'preview_url': existing_creative.preview_url,
                'scan_status': existing_creative.scan_status,
                'uploaded_by': existing_creative.uploaded_by_id,
                'created_at': existing_creative.created_at,
                'updated_at': existing_creative.updated_at
            }, status=status.HTTP_201_CREATED)
        
        # Save file to storage
        storage_path = default_storage.save(storage_filename, ContentFile(file_content))
        
        # Extract video metadata using mediainfo
        try:
            temp_path = None
            # Save file temporarily for metadata extraction
            # Keep original suffix for better codec detection (e.g. .mov)
            with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension or '.mp4') as temp_file:
                temp_file.write(file_content)
                temp_file.flush()
                temp_path = temp_file.name

            def probe_duration_ms(path: str) -> float:
                try:
                    res = subprocess.run(
                        ['mediainfo', '--Inform=General;%Duration%', path],
                        capture_output=True, text=True, check=True
                    )
                    s = (res.stdout or '').strip()
                    # Expect a number in milliseconds, e.g. "10023"
                    return float(s) if s and s.replace('.', '', 1).isdigit() else 0.0
                except Exception:
                    return 0.0
            
            # Extract video metadata using mediainfo
            result = subprocess.run([
                'mediainfo',
                '--Output=JSON',
                temp_path
            ], capture_output=True, text=True, check=True)
            
            metadata = json.loads(result.stdout)
            media_info = metadata.get('media', {})
            tracks = media_info.get('track', [])
            
            # Find tracks
            video_track = next((t for t in tracks if t.get('@type') == 'Video'), None)
            general_track = next((t for t in tracks if t.get('@type') == 'General'), None)
            
            if not video_track:
                return Response({
                    'error': 'No video stream found in file'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            def _to_int(val):
                if isinstance(val, (int, float)):
                    return int(val)
                if isinstance(val, str):
                    digits = ''.join(ch for ch in val if ch.isdigit())
                    return int(digits) if digits else 0
                return 0
            
            def _to_float(val):
                try:
                    return float(val)
                except Exception:
                    return 0.0
            
            width = _to_int(video_track.get('Width', 0))
            height = _to_int(video_track.get('Height', 0))
            
            # Prefer numeric milliseconds from --Inform (most reliable), fallback to JSON parsing
            dur_ms_primary = probe_duration_ms(temp_path)
            if dur_ms_primary and dur_ms_primary > 0:
                duration_sec = dur_ms_primary / 1000.0
            else:
                dur_ms = _to_float((general_track or {}).get('Duration', 0))
                duration_sec = dur_ms / 1000.0  # Convert from milliseconds
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    # Best-effort temp cleanup inside finally: an unlink error must not
                    # replace the in-flight response or the mediainfo exception.
                    logger.warning(
                        "TikTok video upload: failed to remove temp file %s", temp_path, exc_info=True
                    )
        
        # Validate duration (5-600 seconds)
        violations = []
        if duration_sec < 5 or duration_sec > 600:
            violations.append({
                'field': 'duration_sec',
                'rule': 'min/max',
                'message': 'Duration must be between 5 and 600 seconds',
                'actual': str(duration_sec),
                'expected': '5–600'
            })
        
        # Validate aspect ratio and resolution with 1% tolerance
        aspect_ratio = (width / height) if height > 0 else 0.0
        tolerance = 0.01
        valid_1_1 = abs(aspect_ratio - 1.0) <= tolerance and width >= 640 and height >= 640
        valid_9_16 = abs(aspect_ratio - (9/16)) <= tolerance and width >= 540 and height >= 960
        valid_16_9 = abs(aspect_ratio - (16/9)) <= tolerance and width >= 960 and height >= 540
        
        if not (valid_1_1 or valid_9_16 or valid_16_9):
            violations.append({
                'field': 'resolution/aspect_ratio',
                'rule': 'allowed',
                'message': 'Resolution too low or aspect ratio not allowed',
                'actual': f'{width}x{height} (AR={aspect_ratio:.3f})',
                'expected': '1:1≥640x640 OR 9:16≥540x960 OR 16:9≥960x540'
            })
        
        # Return validation errors if any
        if violations:
            return Response({
                'error': 'Spec validation failed',
                'violations': violations
            }, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        
        # Generate preview URL
        preview_url = f"{settings.MEDIA_URL}{storage_path}"
        
        # Create TikTokCreative record
        creative = TikTokCreative.objects.create(
            type='video',
            name=name,
            storage_path=storage_path,
            original_filename=original_filename,
            mime_type=mime_type,
            size_bytes=file.size,
            width=width,
            height=height,
            duration_sec=duration_sec,
            md5=md5_hash,
            preview_url=preview_url,
            scan_status=TikTokCreative.INCOMING,
            uploaded_by=request.user
        )
        
        # Virus scanning will be triggered automatically by signal
        
        return Response({
            'id': creative.id,
            'name': creative.name,
            'type': creative.type,
            'storage_path': creative.storage_path,
            'original_filename': creative.original_filename,
            'mime_type': creative.mime_type,
            'size_bytes': creative.size_bytes,
            'width': creative.width,
            'height': creative.height,
            'duration_sec': creative.duration_sec,
            'md5': creative.md5,
            'preview_url': creative.preview_url,
            'scan_status': creative.scan_status,
            'uploaded_by': creative.uploaded_by_id,
            'created_at': creative.created_at,
            'updated_at': creative.updated_at
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        return Response({
            'error': f'Upload failed: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_image_ad(request):
    """Upload image advertisement content."""
    try:
        # Validate required fields
        if 'file' not in request.FILES:
            return Response({
                'error': 'file is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        file = request.FILES['file']
        name = request.data.get('name', file.name)  # Use filename as default name
        original_filename = request.data.get('original_filename', file.name)
        
        # Validate file type using real uploaded Content-Type
        mime_type = file.content_type or ''
        if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
            return Response({
                'error': f'Only {", ".join(sorted(ALLOWED_IMAGE_MIME_TYPES))} files are supported'
            }, status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        
        # Validate file size (10MB limit for images)
        if file.size > IMAGE_MAX_BYTES:
            return Response({
                'error': 'File exceeds max size: 10MB'
            }, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        
        # Generate MD5 hash
        file_content = file.read()
        file.seek(0)  # Reset file pointer
        md5_hash = hashlib.md5(file_content).hexdigest()
        file_extension = os.path.splitext(file.name)[1]
        storage_filename = f"tiktok/images/{md5_hash}{file_extension}"
        
        # Check for duplicate files
        existing_creative = TikTokCreative.objects.filter(md5=md5_hash).first()
        if existing_creative:
            existing_creative = ensure_creative_file_available(
                existing_creative,
                file_content,
                storage_filename,
            )
            return Response({
                'id': existing_creative.id,
                'name': existing_creative.name,
                'type': existing_creative.type,
                'storage_path': existing_creative.storage_path,
                'original_filename': existing_creative.original_filename,
                'mime_type': existing_creative.mime_type,
                'size_bytes': existing_creative.size_bytes,
                'width': existing_creative.width,
                'height': existing_creative.height,
                'duration_sec': existing_creative.duration_sec,
                'md5': existing_creative.md5,
                'preview_url': existing_creative.preview_url,
                'scan_status': existing_creative.scan_status,
                'uploaded_by': existing_creative.uploaded_by_id,
                'created_at': existing_creative.created_at,
                'updated_at': existing_creative.updated_at
            }, status=status.HTTP_201_CREATED)
        
        # Read image dimensions using Pillow (faster/lighter than mediainfo)
        try:
            with Image.open(io.BytesIO(file_content)) as img:
                width, height = img.size
        except UnidentifiedImageError:
            return Response({
                'error': 'Invalid image file'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Reject zero or invalid dimensions explicitly
        if (width or 0) < MIN_DIMENSION or (height or 0) < MIN_DIMENSION or width == 0 or height == 0:
            return Response({
                'error': 'Image dimensions must be greater than 0'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        duration_sec = None  # Images don't have duration
        
        # Generate storage path and save file
        storage_path = default_storage.save(storage_filename, ContentFile(file_content))
        
        # Validate image dimensions and aspect ratios
        violations = []
        if width > 0 and height > 0:
            aspect_ratio = width / height
            
            # Check valid aspect ratios and dimensions with tolerance
            valid_1_91_1 = (abs(aspect_ratio - (MIN_SIZE_1_91_1[0]/MIN_SIZE_1_91_1[1])) <= ASPECT_RATIO_TOLERANCE 
                            and width >= MIN_SIZE_1_91_1[0] and height >= MIN_SIZE_1_91_1[1])
            valid_1_1 = (abs(aspect_ratio - 1.0) <= ASPECT_RATIO_TOLERANCE 
                         and width >= MIN_SIZE_1_1[0] and height >= MIN_SIZE_1_1[1])
            valid_9_16 = (abs(aspect_ratio - (MIN_SIZE_9_16[0]/MIN_SIZE_9_16[1])) <= ASPECT_RATIO_TOLERANCE 
                          and width >= MIN_SIZE_9_16[0] and height >= MIN_SIZE_9_16[1])
            
            if not (valid_1_91_1 or valid_1_1 or valid_9_16):
                violations.append({
                    'field': 'resolution/aspect_ratio',
                    'rule': 'allowed',
                    'message': 'Resolution or aspect ratio not allowed',
                    'actual': f'{width}x{height} (AR={aspect_ratio:.3f})',
                    'expected': '1200x628 (1.91:1) OR 640x640 (1:1) OR 720x1280 (9:16)'
                })
        
        # Return validation errors if any
        if violations:
            return Response({
                'error': 'Spec validation failed',
                'violations': violations
            }, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        
        # Generate preview URL
        preview_url = f"{settings.MEDIA_URL}{storage_path}"
        
        # Create TikTokCreative record
        creative = TikTokCreative.objects.create(
            type='image',
            name=name,
            storage_path=storage_path,
            original_filename=original_filename,
            mime_type=mime_type,
            size_bytes=file.size,
            width=width,
            height=height,
            duration_sec=duration_sec,
            md5=md5_hash,
            preview_url=preview_url,
            scan_status=TikTokCreative.INCOMING,
            uploaded_by=request.user
        )
        
        # Virus scanning will be triggered automatically by signal
        
        return Response({
            'id': creative.id,
            'name': creative.name,
            'type': creative.type,
            'storage_path': creative.storage_path,
            'original_filename': creative.original_filename,
            'mime_type': creative.mime_type,
            'size_bytes': creative.size_bytes,
            'width': creative.width,
            'height': creative.height,
            'duration_sec': creative.duration_sec,
            'md5': creative.md5,
            'preview_url': creative.preview_url,
            'scan_status': creative.scan_status,
            'uploaded_by': creative.uploaded_by_id,
            'created_at': creative.created_at,
            'updated_at': creative.updated_at
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        return Response({
            'error': f'Upload failed: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def material_list(request):
    """Get list of uploaded creative materials with pagination and filtering."""
    try:
        # Get query parameters
        creative_type = request.query_params.get('type')
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))
        
        # Validate parameters
        if page < 1:
            return Response({
                'error': 'page must be >= 1'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if page_size < 1 or page_size > 100:
            return Response({
                'error': 'page_size must be between 1 and 100'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Validate type filter
        if creative_type and creative_type not in ['image', 'video']:
            return Response({
                'error': 'type must be image or video'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Build queryset
        queryset = TikTokCreative.objects.filter(uploaded_by=request.user)
        
        # Apply type filter
        if creative_type:
            queryset = queryset.filter(type=creative_type)
        
        available_creatives = [
            creative
            for creative in queryset.order_by('-created_at')
            if creative_file_exists(creative)
        ]

        # Calculate pagination
        total = len(available_creatives)
        start = (page - 1) * page_size
        end = start + page_size
        
        # Get items for current page
        items_queryset = available_creatives[start:end]
        
        # Build response items
        items = [build_material_payload(creative) for creative in items_queryset]
        
        # Build response
        response_data = {
            'items': items,
            'page': page,
            'page_size': page_size,
            'total': total
        }
        
        return Response(response_data, status=status.HTTP_200_OK)
        
    except ValueError as e:
        return Response({
            'error': f'Invalid parameter: {str(e)}'
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({
            'error': f'Failed to retrieve materials: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def material_info(request, id):
    """Get detailed information about a specific material."""
    try:
        # Get the creative by ID and ensure it belongs to the current user
        try:
            creative = TikTokCreative.objects.get(id=id, uploaded_by=request.user)
        except TikTokCreative.DoesNotExist:
            return Response({
                'error': 'Material not found'
            }, status=status.HTTP_404_NOT_FOUND)

        if not creative_file_exists(creative):
            return Response({
                'error': 'Material file not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Build response data
        response_data = {
            'id': creative.id,
            'name': creative.name,
            'type': creative.type,
            'storage_path': creative.storage_path,
            'original_filename': creative.original_filename,
            'mime_type': creative.mime_type,
            'size_bytes': creative.size_bytes,
            'width': creative.width,
            'height': creative.height,
            'duration_sec': creative.duration_sec,
            'md5': creative.md5,
            'preview_url': creative.preview_url,
            'scan_status': creative.scan_status,
            'uploaded_by': creative.uploaded_by_id,
            'created_at': creative.created_at.isoformat() if creative.created_at else None,
            'updated_at': creative.updated_at.isoformat() if creative.updated_at else None
        }
        
        return Response(response_data, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'error': f'Failed to retrieve material info: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def material_delete(request, id):
    """Delete a creative material by ID. Only the uploader can delete."""
    try:
        creative = TikTokCreative.objects.get(id=id, uploaded_by=request.user)
    except TikTokCreative.DoesNotExist:
        return Response({'error': 'Material not found'}, status=status.HTTP_404_NOT_FOUND)

    if creative.storage_path:
        try:
            default_storage.delete(creative.storage_path)
        except Exception:
            # FileSystemStorage.delete() already ignores missing files, so anything
            # caught here is a real failure. The row is still deleted to honour the
            # user's request; log the path so the orphaned file can be removed by hand.
            logger.error(
                "TikTok material %s: failed to delete stored file %s; file is now orphaned",
                creative.id, creative.storage_path, exc_info=True,
            )

    creative.delete()
    return Response({'success': True}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def ad_draft_save(request):
    """
    Save or update ad drafts in batch.
    Accepts adgroup_id and form_data_list to create/update multiple ad drafts.
    
    Request body:
    {
      "adgroup_id": "uuid",  // UUID of ad group (optional, can be null)
      "form_data_list": [
        {
          "id": "uuid",  // Optional: if provided, updates existing draft
          "name": "...",
          "ad_text": "...",
          "call_to_action_mode": "...",
          "call_to_action_label": "...",
          "assets": {...}
          // ... other ad draft fields
        },
        // ... more drafts
      ]
    }
    
    Response:
    {
      "data": {
        "ad-draft-id": ["uuid1", "uuid2", ...]
      },
      "msg": "success"
    }
    """
    try:
        adgroup_id = request.data.get('adgroup_id')
        form_data_list = request.data.get('form_data_list', [])
        
        if not form_data_list:
            return Response({
                'error': 'form_data_list is required and cannot be empty'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Validate and get ad group if provided
        ad_group = None
        if adgroup_id:
            try:
                ad_group = AdGroup.objects.get(id=adgroup_id, created_by=request.user)
            except AdGroup.DoesNotExist:
                return Response({
                    'error': 'Ad group not found or does not belong to current user'
                }, status=status.HTTP_404_NOT_FOUND)
            except (ValueError, TypeError):
                return Response({
                    'error': 'Invalid adgroup_id format'
                }, status=status.HTTP_400_BAD_REQUEST)
        
        saved_draft_ids = []
        errors = []
        
        # Process each form data item
        for index, form_data in enumerate(form_data_list):
            draft_id = form_data.get('id')
            
            try:
                # Accept unified CTA as single field semantics:
                # - key missing => switch OFF (store None)
                # - '' empty string => dynamic
                # - non-empty string => standard label
                if 'call_to_action' not in form_data:
                    form_data['call_to_action'] = None
                else:
                    v = form_data.get('call_to_action')
                    if v is None:
                        form_data['call_to_action'] = None
                    elif isinstance(v, str):
                        form_data['call_to_action'] = v
                    else:
                        # Coerce unexpected types to None
                        form_data['call_to_action'] = None
                if draft_id:
                    # Update existing draft
                    try:
                        draft = AdDraft.objects.get(id=draft_id, created_by=request.user)
                    except AdDraft.DoesNotExist:
                        errors.append({
                            'index': index,
                            'error': f'Ad draft not found: {draft_id}'
                        })
                        continue
                    except (ValueError, TypeError):
                        errors.append({
                            'index': index,
                            'error': f'Invalid draft id format: {draft_id}'
                        })
                        continue
                    
                    # Update draft data
                    form_data['ad_group'] = ad_group.id if ad_group else None
                    serializer = AdDraftSerializer(draft, data=form_data, partial=True, context={'request': request})
                else:
                    # Create new draft
                    form_data['ad_group'] = ad_group.id if ad_group else None
                    serializer = AdDraftSerializer(data=form_data, context={'request': request})
                
                if serializer.is_valid():
                    saved_draft = serializer.save()
                    saved_draft_ids.append(str(saved_draft.id))
                else:
                    errors.append({
                        'index': index,
                        'error': 'Validation failed',
                        'details': serializer.errors
                    })
                    
            except Exception as e:
                errors.append({
                    'index': index,
                    'error': f'Failed to save draft: {str(e)}'
                })
        
        # If there are errors and no successful saves, return error
        if errors and not saved_draft_ids:
            return Response({
                'error': 'Failed to save any drafts',
                'details': errors
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Return success response with saved draft IDs
        response_data = {
            'msg': 'success',
            'data': {
                'ad-draft-id': saved_draft_ids
            }
        }
        
        # Include errors in response if any (partial success)
        if errors:
            response_data['warnings'] = errors
        
        return Response(response_data, status=status.HTTP_200_OK)
            
    except Exception as e:
        return Response({
            'error': f'Failed to save ad drafts: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def ad_group_save(request):
    """
    Save or update an ad group.
    Real-time save endpoint for TikTok ad groups.
    Supports both create (no id) and update/rename (with id).
    
    Request body:
    {
      "id": "uuid",  // Optional: if provided, updates existing group
      "name": "..."  // Group name
    }
    
    Response:
    {
      "data": {
        "ad-group-id": "uuid"
      },
      "msg": "success"
    }
    """
    try:
        group_id = request.data.get('id')
        name = request.data.get('name')
        
        if not name:
            return Response({
                'error': 'name is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if group_id:
            # Update existing ad group (rename)
            try:
                # Validate UUID format first
                group_uuid = uuid.UUID(str(group_id))
            except (ValueError, TypeError, AttributeError):
                return Response({
                    'error': 'Invalid id format'
                }, status=status.HTTP_400_BAD_REQUEST)

            try:
                ad_group = AdGroup.objects.get(id=group_uuid, created_by=request.user)
            except AdGroup.DoesNotExist:
                return Response({
                    'error': 'Ad group not found or does not belong to current user'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # Update name
            ad_group.name = name
            ad_group.save()
            
            return Response({
                'msg': 'success',
                'data': {
                    'ad-group-id': str(ad_group.id)
                }
            }, status=status.HTTP_200_OK)
        else:
            # Create new ad group
            serializer = AdGroupSerializer(data={'name': name}, context={'request': request})
            
            if serializer.is_valid():
                ad_group = serializer.save()
                return Response({
                    'msg': 'success',
                    'data': {
                        'ad-group-id': str(ad_group.id)
                    }
                }, status=status.HTTP_201_CREATED)
            else:
                return Response({
                    'error': 'Validation failed',
                    'details': serializer.errors
                }, status=status.HTTP_400_BAD_REQUEST)
            
    except Exception as e:
        return Response({
            'error': f'Failed to save ad group: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def ad_group_delete(request):
    """
    Delete ad groups in batch.
    Accepts ad_group_ids list to delete multiple ad groups.
    
    Request body:
    {
      "ad_group_ids": ["uuid1", "uuid2", ...]  // List of ad group UUIDs
    }
    
    Response:
    {
      "data": {
        "deleted_ids": ["uuid1", "uuid2", ...]
      },
      "msg": "success"
    }
    """
    try:
        ad_group_ids = request.data.get('ad_group_ids', [])

        if not ad_group_ids:
            return Response({
                'error': 'ad_group_ids is required and cannot be empty'
            }, status=status.HTTP_400_BAD_REQUEST)

        if len(ad_group_ids) > 200:
            return Response({
                'error': 'Too many ad_group_ids in one request (max 200)'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Convert string UUIDs to UUID objects
        group_uuids = []
        for id_str in ad_group_ids:
            try:
                group_uuids.append(uuid.UUID(str(id_str)))
            except (ValueError, TypeError):
                return Response({
                    'error': f'Invalid UUID format in ad_group_ids: {id_str}'
                }, status=status.HTTP_400_BAD_REQUEST)

        from django.db import transaction

        # Get ad groups that belong to the user
        ad_groups = AdGroup.objects.filter(id__in=group_uuids, created_by=request.user)
        found_ids = [str(ag.id) for ag in ad_groups]
        not_found_ids = [str(i) for i in ad_group_ids if str(i) not in found_ids]

        with transaction.atomic():
            deleted_count, _ = ad_groups.delete()

        deleted_ids = found_ids

        if deleted_count == 0:
            return Response({
                'error': 'No ad groups found or no permission to delete',
                'not_found_ids': not_found_ids
            }, status=status.HTTP_404_NOT_FOUND)

        resp = {
            'msg': 'success',
            'data': {
                'deleted_ids': deleted_ids
            }
        }
        if not_found_ids:
            resp['warnings'] = {'not_found_ids': not_found_ids}
        return Response(resp, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({
            'error': f'Failed to delete ad groups: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def ad_draft_delete(request):
    """
    Delete ad drafts in batch.
    Accepts ad_draft_ids list to delete multiple ad drafts.
    
    Request body:
    {
      "ad_draft_ids": ["uuid1", "uuid2", ...]  // List of ad draft UUIDs
    }
    
    Response:
    {
      "data": {
        "deleted_ids": ["uuid1", "uuid2", ...]
      },
      "msg": "success"
    }
    """
    try:
        ad_draft_ids = request.data.get('ad_draft_ids', [])

        if not ad_draft_ids:
            return Response({
                'error': 'ad_draft_ids is required and cannot be empty'
            }, status=status.HTTP_400_BAD_REQUEST)

        if len(ad_draft_ids) > 200:
            return Response({
                'error': 'Too many ad_draft_ids in one request (max 200)'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Convert string UUIDs to UUID objects
        draft_uuids = []
        for id_str in ad_draft_ids:
            try:
                draft_uuids.append(uuid.UUID(str(id_str)))
            except (ValueError, TypeError):
                return Response({
                    'error': f'Invalid UUID format in ad_draft_ids: {id_str}'
                }, status=status.HTTP_400_BAD_REQUEST)

        from django.db import transaction

        drafts = AdDraft.objects.filter(id__in=draft_uuids, created_by=request.user)
        found_ids = [str(d.id) for d in drafts]
        not_found_ids = [str(i) for i in ad_draft_ids if str(i) not in found_ids]

        with transaction.atomic():
            deleted_count, _ = drafts.delete()

        deleted_ids = found_ids

        if deleted_count == 0:
            return Response({
                'error': 'No ad drafts found or no permission to delete',
                'not_found_ids': not_found_ids
            }, status=status.HTTP_404_NOT_FOUND)

        resp = {
            'msg': 'success',
            'data': {
                'deleted_ids': deleted_ids
            }
        }
        if not_found_ids:
            resp['warnings'] = {'not_found_ids': not_found_ids}
        return Response(resp, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({
            'error': f'Failed to delete ad drafts: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



@api_view(['POST'])
@permission_classes([IsAuthenticated])
def share_ad_draft(request, id):
    """Create a public preview snapshot for an ad draft and return its slug."""
    try:
        try:
            draft = AdDraft.objects.get(id=id, created_by=request.user)
        except (AdDraft.DoesNotExist, ValueError, TypeError):
            return Response({'error': 'Ad draft not found'}, status=status.HTTP_404_NOT_FOUND)

        # Build snapshot: minimal state for rendering
        assets = draft.assets or {}
        snapshot = {
            'name': draft.name or '',
            'ad_text': draft.ad_text or '',
            'call_to_action': getattr(draft, 'call_to_action', None),
            'creative_type': draft.creative_type or 'UNKNOWN',
            'assets': assets,
            'created_at': draft.created_at.isoformat() if draft.created_at else None,
            'updated_at': draft.updated_at.isoformat() if draft.updated_at else None,
        }

        # Version id: use updated_at timestamp string
        version_id = str(int(draft.updated_at.timestamp())) if draft.updated_at else str(int(uuid.uuid1().time))

        # Generate slug
        slug = secrets.token_urlsafe(18)  # ~24 chars
        
        # Set expiration to 7 days from now (default expiration)
        expires_at = timezone.now() + timedelta(days=7)
        
        preview = PublicPreview.objects.create(
            slug=slug,
            ad_draft=draft,
            version_id=version_id,
            snapshot_json=snapshot,
            expires_at=expires_at,
        )

        return Response({'msg': 'success', 'data': {'slug': preview.slug}}, status=status.HTTP_201_CREATED)
    except Exception as e:
        return Response({'error': f'Failed to create shareable link: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([])
def get_public_preview(request, slug):
    """Public read-only endpoint to fetch a snapshot by slug."""
    try:
        try:
            preview = PublicPreview.objects.get(slug=slug)
        except PublicPreview.DoesNotExist:
            return Response({'error': 'Preview not found'}, status=status.HTTP_404_NOT_FOUND)

        # Check if preview has expired
        if preview.expires_at < timezone.now():
            return Response(
                {'error': 'Preview has expired', 'code': 'PREVIEW_EXPIRED'},
                status=status.HTTP_410_GONE
            )

        data = PublicPreviewSerializer(preview).data
        return Response({'msg': 'success', 'data': data}, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({'error': f'Failed to fetch preview: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def creation_detail(request):
    """
    Aggregated query endpoint for creation details.
    Accepts one or more of:
      - ad_draft_ids: AdDraft UUIDs (preferred)
      - aids: external AdDraft IDs (backward-compat)
      - ad_group_ids: AdGroup UUIDs
    Returns:
      {
        "ad_drafts": [ { ...normalized_draft... }, ... ],
        "ad_groups": [ { ...group..., "ad_drafts": [ ...brief items... ] }, ... ]
      }
    """
    try:
        data = request.data or {}
        ad_draft_ids = data.get('ad_draft_ids', []) or []
        legacy_aids = data.get('aids', []) or []  # backward-compat
        ad_group_ids = data.get('ad_group_ids', []) or []

        # --- helpers ---
        def _coerce_uuid_list(str_list):
            out = []
            for s in (str_list or []):
                try:
                    out.append(uuid.UUID(str(s)))
                except (ValueError, TypeError):
                    # Skip invalid UUID, we'll report later if nothing resolved
                    continue
            return out

        def _normalize_assets(raw_assets):
            # Ensure assets is always a list; tolerate None or dict legacy payloads.
            raw_assets = sanitize_assets_for_available_media(raw_assets, request.user)
            if isinstance(raw_assets, list):
                return raw_assets
            if isinstance(raw_assets, dict):
                return [raw_assets]
            return []

        def _normalize_draft(d):
            # unified draft payload for FE Ad Detail
            cta = getattr(d, 'call_to_action', None)
            return {
                "id": str(d.id),
                "ad_draft_id": d.aid or str(d.id),
                "group_id": str(d.ad_group_id) if d.ad_group_id else None,
                "name": d.name or "",
                "ad_text": d.ad_text or "",
                "call_to_action": cta,
                "landing_page_url": getattr(d, "landing_page_url", None),
                "creative_type": d.creative_type or "UNKNOWN",
                "assets": _normalize_assets(getattr(d, "assets", None)),
                "preview": getattr(d, "preview", {}) or {},
                "status": getattr(d, "status", "DRAFT_SAVED"),
                "created_at": d.created_at.isoformat() if getattr(d, "created_at", None) else None,
                "updated_at": d.updated_at.isoformat() if getattr(d, "updated_at", None) else None
            }

        # --- resolve drafts by aids and/or ad_draft_ids ---
        drafts_qs = AdDraft.objects.filter(created_by=request.user)

        drafts_by_aid = drafts_qs.none()
        if legacy_aids:
            drafts_by_aid = drafts_qs.filter(aid__in=[str(a) for a in legacy_aids])

        drafts_by_uuid = drafts_qs.none()
        if ad_draft_ids:
            uuid_list = _coerce_uuid_list(ad_draft_ids)
            if uuid_list:
                drafts_by_uuid = drafts_qs.filter(id__in=uuid_list)

        # union without duplicates
        drafts = list({d.id: d for d in list(drafts_by_aid) + list(drafts_by_uuid)}.values())

        # build drafts payload
        ad_drafts_payload = [_normalize_draft(d) for d in drafts]

        # --- resolve groups (optional) ---
        ad_groups_payload = []
        if ad_group_ids:
            group_uuid_list = _coerce_uuid_list(ad_group_ids)
            if group_uuid_list:
                groups_qs = AdGroup.objects.filter(id__in=group_uuid_list, created_by=request.user)
                # For each group, attach brief draft items (not full payload to keep lightweight)
                user_drafts_qs = AdDraft.objects.filter(ad_group_id__in=[g.id for g in groups_qs], created_by=request.user).order_by('-created_at')
                # bucket drafts by group_id
                bucket = {}
                for d in user_drafts_qs:
                    bucket.setdefault(d.ad_group_id, []).append(d)

                for g in groups_qs:
                    brief_items = []
                    for d in bucket.get(g.id, []):
                        brief_items.append({
                            "ad_draft_id": d.aid or str(d.id),
                            "name": d.name or "",
                            "creative_type": d.creative_type or "UNKNOWN",
                            "create_timestamp": int(d.created_at.timestamp()) if d.created_at else 0
                        })
                    ad_groups_payload.append({
                        "id": str(g.id),
                        "gid": getattr(g, "gid", None) or str(g.id),
                        "name": g.name or "",
                        "created_at": g.created_at.isoformat() if g.created_at else None,
                        "updated_at": g.updated_at.isoformat() if g.updated_at else None,
                        "ad_drafts": brief_items
                    })

        # If no valid inputs were resolved, return 400
        if not ad_drafts_payload and not ad_groups_payload:
            return Response({
                "error": "No valid ad_draft_ids/aids/ad_group_ids found or accessible"
            }, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'msg': 'success',
            'data': {
                'ad_drafts': ad_drafts_payload,
                'ad_groups': ad_groups_payload,
            }
        }, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({
            "error": f"Failed to retrieve creation details: {str(e)}"
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def brief_info_list(request):
    """
    Get brief info list for sidebar (no side effects).
    Returns ad groups with their associated ad drafts (brief items).
    Each brief item includes ad_draft_id (not aid).
    Supports optional pagination:
      - limit_groups (default 50), offset_groups (default 0)
      - limit_items_per_group (default 20)
    Response:
    {
      "code": 0,
      "msg": "success",
      "data": {
        "ad_group_brief_info_list": [...],
        "total_groups": 123,
        "limit_groups": 50,
        "offset_groups": 0
      }
    }
    """
    try:
        # --- Read and validate pagination query params (safe defaults) ---
        def _to_int(v, default):
            try:
                return int(v)
            except (TypeError, ValueError):
                return default

        limit_groups = _to_int(request.query_params.get('limit_groups'), 50)
        offset_groups = _to_int(request.query_params.get('offset_groups'), 0)
        limit_items = _to_int(request.query_params.get('limit_items_per_group'), 20)

        # Clamp to reasonable bounds
        if limit_groups <= 0 or limit_groups > 200:
            limit_groups = 50
        if offset_groups < 0:
            offset_groups = 0
        if limit_items <= 0 or limit_items > 100:
            limit_items = 20

        # --- Build queryset with prefetch to avoid N+1 and enforce user scope ---
        drafts_qs = (
            AdDraft.objects
            .filter(created_by=request.user)
            .order_by('-created_at')
        )
        campaign_id = request.query_params.get('campaign_id')
        if campaign_id:
            drafts_qs = drafts_qs.filter(media_campaign_id=campaign_id)
        ad_groups_base_qs = AdGroup.objects.filter(created_by=request.user).order_by('-created_at')

        total_groups = ad_groups_base_qs.count()
        ad_groups = (
            ad_groups_base_qs
            .prefetch_related(Prefetch('ad_drafts', queryset=drafts_qs, to_attr='prefetched_drafts'))
            [offset_groups:offset_groups + limit_groups]
        )

        ad_group_brief_info_list = []

        for ad_group in ad_groups:
            # Use prefetched drafts and apply per-group item limit in Python (already ordered)
            group_drafts = (getattr(ad_group, 'prefetched_drafts', []) or [])[:limit_items]

            creative_brief_info_item_list = []
            for draft in group_drafts:
                # DO NOT write to DB in GET; only compute a temp creative_type
                ctype = draft.creative_type or draft.infer_creative_type_from_assets()
                creative_item = {
                    'id': str(draft.id),  # UUID for API calls
                    'ad_draft_id': draft.aid or str(draft.id),  # External display ID
                    'name': draft.name or '',
                    'creative_type': ctype or 'UNKNOWN',
                    'opt_status': getattr(draft, 'opt_status', 0) or 0,
                    'create_timestamp': int(draft.created_at.timestamp()) if draft.created_at else 0,
                }
                creative_brief_info_item_list.append(creative_item)

            ad_group_info = {
                'id': str(ad_group.id),  # UUID for API calls
                'gid': ad_group.gid or str(ad_group.id),  # External display ID
                'name': ad_group.name or '',
                'create_timestamp': int(ad_group.created_at.timestamp()) if ad_group.created_at else 0,
                'creative_brief_info_item_list': creative_brief_info_item_list,
            }
            ad_group_brief_info_list.append(ad_group_info)

        return Response({
            'msg': 'success',
            'data': {
                'ad_group_brief_info_list': ad_group_brief_info_list,
                'total_groups': total_groups,
                'limit_groups': limit_groups,
                'offset_groups': offset_groups
            }
        }, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({
            'msg': f'Failed to retrieve brief info list: {str(e)}',
            'data': None
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
