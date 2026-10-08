"""
Generic virus scanning utilities for all modules
"""
import os
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def perform_clamav_scan(file_path):
    """
    Perform virus scan using ClamAV over TCP by streaming file content.
    Returns True if file is infected, False if clean.
    Raises RuntimeError if scanner is unavailable or an unexpected error occurs.
    """
    import clamd

    host = os.getenv("CLAMAV_HOST", getattr(settings, "CLAMAV_HOST", "clamav"))
    port = int(os.getenv("CLAMAV_PORT", getattr(settings, "CLAMAV_PORT", 3310)))

    cd = clamd.ClamdNetworkSocket(host=host, port=port)
    
    # Stream file content to ClamAV instead of passing file path
    with open(file_path, "rb") as fh:
        result = cd.instream(fh)
    
    # Parse instream result format: {'stream': ('OK'|'FOUND'|'ERROR', message)}
    if isinstance(result, dict) and 'stream' in result:
        status, message = result['stream']
        if status == "FOUND":
            return True
        elif status == "ERROR":
            raise RuntimeError(f"ClamAV scan error: {message}")
        # status == "OK" or anything else is considered clean
        return False
    else:
        raise RuntimeError(f"Unexpected ClamAV response format: {result}")


def scan_file_generic(file_path, model_class, file_id, status_field='scan_status'):
    """
    Generic file scanning function that can work with any model.
    
    Args:
        file_path: Full path to the file to scan
        model_class: Django model class (e.g., MetricFile, TikTokCreative)
        file_id: ID of the model instance
        status_field: Name of the status field (default: 'scan_status')
    
    Returns:
        bool: True if file is clean, False if infected or error
    """
    try:
        instance = model_class.objects.get(id=file_id)
        
        # Get status constants from model
        INCOMING = getattr(model_class, 'INCOMING', 'incoming')
        SCANNING = getattr(model_class, 'SCANNING', 'scanning')
        READY = getattr(model_class, 'READY', 'ready')
        INFECTED = getattr(model_class, 'INFECTED', 'infected')
        MISSING = getattr(model_class, 'MISSING', 'missing')
        ERROR_SCANNING = getattr(model_class, 'ERROR_SCANNING', 'error_scanning')
        
        current_status = getattr(instance, status_field)
        
        # Check if already processed
        if current_status == READY:
            return True
        elif current_status == INFECTED:
            return False
        elif current_status not in [INCOMING, SCANNING]:
            return False

        # Update status to scanning
        if current_status == INCOMING:
            if hasattr(instance, 'start_scan'):
                instance.start_scan()
            else:
                setattr(instance, status_field, SCANNING)
            instance.save()

        # Check if file exists
        if not os.path.exists(file_path):
            if hasattr(instance, 'mark_missing'):
                instance.mark_missing()
            else:
                setattr(instance, status_field, MISSING)
            instance.save()
            logger.warning(f"File not found: {file_path}")
            return False

        # Perform virus scan
        is_infected = perform_clamav_scan(file_path)

        if is_infected:
            if hasattr(instance, 'mark_infected'):
                instance.mark_infected()
            else:
                setattr(instance, status_field, INFECTED)
            logger.warning(f"File infected: {file_id}")
        else:
            if hasattr(instance, 'mark_clean'):
                instance.mark_clean()
            else:
                setattr(instance, status_field, READY)
            logger.info(f"File clean: {file_id}")

        instance.save()
        return not is_infected

    except model_class.DoesNotExist:
        logger.error(f"Model instance not found: {file_id}")
        return False
    except Exception as e:
        # On any error during scanning, mark error_scanning
        try:
            instance = model_class.objects.get(id=file_id)
            current_status = getattr(instance, status_field)
            if current_status == SCANNING:
                if hasattr(instance, 'mark_error_scanning'):
                    instance.mark_error_scanning()
                else:
                    setattr(instance, status_field, ERROR_SCANNING)
                instance.save()
        except Exception:
            logger.exception(
                "Failed to mark %s %s as error_scanning; it may stay in SCANNING",
                model_class.__name__, file_id,
            )
        logger.error(f"Error scanning file {file_id}: {str(e)}")
        return False
