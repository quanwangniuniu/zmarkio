import os
import logging
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.contrib.auth import get_user_model
from .models import Task, TaskAttachment
from django_fsm import can_proceed
from core.models import ProjectMember
from utils.virus_scanner import perform_clamav_scan

logger = logging.getLogger(__name__)


@shared_task(bind=True)
def scan_task_attachment(self, attachment_id):
    """Scan uploaded task attachment for viruses using ClamAV"""
    try:
        attachment = TaskAttachment.objects.get(id=attachment_id)

        if attachment.scan_status == TaskAttachment.PENDING:
            attachment.start_scan()
        elif attachment.scan_status == TaskAttachment.SCANNING:
            pass
        else:
            return True

        attachment.save()

        # Get file path (using MEDIA_ROOT)
        if not attachment.file:
            logger.warning(f"Attachment {attachment_id} has no file")
            return False

        file_path = attachment.file.path

        # Check if file exists
        if not os.path.exists(file_path):
            logger.warning(f"File not found: {file_path}")
            attachment.mark_error_scanning()
            attachment.save()
            return False

        # Perform virus scan
        is_infected = perform_clamav_scan(file_path)

        if is_infected:
            attachment.mark_infected()
        else:
            attachment.mark_clean()

        attachment.save()
        return not is_infected

    except TaskAttachment.DoesNotExist:
        logger.error(f"TaskAttachment {attachment_id} not found")
        return False
    except Exception as e:
        # On any error during scanning, mark error_scanning if we are scanning
        try:
            attachment = TaskAttachment.objects.get(id=attachment_id)
            if attachment.scan_status == TaskAttachment.SCANNING:
                attachment.mark_error_scanning()
                attachment.save()
        except Exception:
            logger.exception(
                "Failed to mark task attachment %s as error_scanning; it may stay in SCANNING",
                attachment_id,
            )
        logger.error(f"Error scanning task attachment {attachment_id}: {str(e)}")
        return False


@shared_task(bind=True, max_retries=3)
def process_bulk_action(self, task_ids, action, payload, user_id):
    """
    Process bulk action on multiple tasks with per-task DB locking.
    Returns { "succeeded": [...], "failed": [...] }
    """
    User = get_user_model()
    succeeded = []
    failed = []

    for task_id in task_ids:
        try:
            with transaction.atomic():
                task = Task.objects.select_for_update().get(id=task_id)
                user = User.objects.get(id=user_id)

                has_membership = ProjectMember.objects.filter(
                    user=user,
                    project=task.project,
                    is_active=True
                ).exists()
                if not has_membership:
                    failed.append({
                        'task_id': task_id,
                        'reason': 'Permission denied'
                    })
                    continue

                if action == 'submit':
                    if not can_proceed(task.submit):
                        failed.append({
                            'task_id': task_id,
                            'reason': f'Cannot submit task in {task.status} status (must be DRAFT)'
                        })
                        continue
                    task.submit()
                    task.save()

                elif action == 'assign_approver':
                    approver_id = payload.get('approver_id')
                    if not approver_id:
                        failed.append({
                            'task_id': task_id,
                            'reason': 'approver_id is required'
                        })
                        continue
                    approver = User.objects.get(id=approver_id)
                    task.current_approver = approver
                    task.save()

                elif action == 'change_status':
                    new_status = payload.get('status')
                    if not new_status:
                        failed.append({
                            'task_id': task_id,
                            'reason': 'status is required'
                        })
                        continue

                    # Map target status to the correct FSM transition method
                    TRANSITION_MAP = {
                        Task.Status.DRAFT:        task.revise,
                        Task.Status.UNDER_REVIEW: task.start_review,
                        Task.Status.APPROVED:     task.approve,
                        Task.Status.REJECTED:     task.reject,
                        Task.Status.LOCKED:       task.lock,
                        'UNLOCK':                 task.unlock,
                        Task.Status.CANCELLED:    task.cancel,
                    }

                    transition_method = TRANSITION_MAP.get(new_status)

                    if not transition_method:
                        failed.append({
                            'task_id': task_id,
                            'reason': f'Unsupported target status: {new_status}'
                        })
                        continue

                    try:
                        if not can_proceed(transition_method):
                            failed.append({
                                'task_id': task_id,
                                'reason': f'Cannot transition from {task.status} to {new_status}'
                            })
                            continue
                        transition_method()
                        task.save()
                    except Exception as fsm_error:
                        failed.append({
                            'task_id': task_id,
                            'reason': f'Transition failed: {str(fsm_error)}'
                        })
                        continue

                else:
                    failed.append({
                        'task_id': task_id,
                        'reason': f'Unknown action: {action}'
                    })
                    continue

                succeeded.append(task_id)

        except Task.DoesNotExist:
            failed.append({'task_id': task_id, 'reason': 'Task not found'})
        except Exception as e:
            failed.append({'task_id': task_id, 'reason': str(e)})

    return {'succeeded': succeeded, 'failed': failed}
