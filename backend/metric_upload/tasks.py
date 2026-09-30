import os
import logging
from celery import shared_task
from django.conf import settings
from .models import MetricFile

logger = logging.getLogger(__name__)


@shared_task(bind=True)
def scan_file_for_virus(self, file_id):
    """Scan uploaded file for viruses using ClamAV (no retries, minimal states)."""
    try:
        metric_file = MetricFile.objects.get(id=file_id)

        if metric_file.status == MetricFile.INCOMING:
            metric_file.start_scan()
        elif metric_file.status == MetricFile.SCANNING:
            pass
        else:
            return True

        metric_file.save()

        file_path = metric_file.storage_key
        full_path = os.path.join(settings.FILE_STORAGE_DIR, file_path)

        # The file may be missing here if it was deleted or moved after upload,
        # or if there was an error during the upload process that prevented it from being saved.
        # This check ensures we do not attempt to scan a file that does not exist on disk.
        if not os.path.exists(full_path):
            metric_file.mark_missing()
            metric_file.save()
            return False

        is_infected = perform_clamav_scan(full_path)

        if is_infected:
            metric_file.mark_infected()
        else:
            metric_file.mark_clean()

        metric_file.save()
        return not is_infected

    except MetricFile.DoesNotExist:
        return False
    except Exception:
        logger.exception("Virus scan failed for MetricFile %s", file_id)
        # On any error during scanning, mark error_scanning if we are scanning
        try:
            metric_file = MetricFile.objects.get(id=file_id)
            if metric_file.status == MetricFile.SCANNING:
                metric_file.mark_error_scanning()
                metric_file.save()
        except Exception:
            logger.exception(
                "Failed to mark MetricFile %s as error_scanning; it may stay in SCANNING",
                file_id,
            )
        return False


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
