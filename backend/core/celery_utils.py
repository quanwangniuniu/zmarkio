"""
Timeout protection for Celery tasks running under --pool=threads, where
Celery's native time_limit/soft_time_limit are silently inert. 
TimeoutEnforcedTask is wired in once as the app's default Task class — tasks 
just declare the native `time_limit=N` kwarg; no per-task decorator or import.
"""
import concurrent.futures
import logging
import os

from celery import Task

logger = logging.getLogger(__name__)


class TaskTimeoutError(TimeoutError):
    """Raised by TimeoutEnforcedTask when a task exceeds its time budget."""


# Centralizes every task's timeout in ONE place — tasks don't declare
# anything themselves. Add/change a task's protection by editing this
# dict only; the 47 task files below are untouched.
TASK_TIMEOUTS = {
    # chat
    'chat.tasks.deliver_message_task': 30,
    'chat.tasks.finalize_presence_offline': 10,
    'chat.tasks.send_typing_indicator': 10,
    'chat.tasks.update_message_status_task': 10,
    'chat.tasks.notify_new_message': 10,
    'chat.tasks.notify_message_recipients': 20,
    'chat.tasks.notify_pin_update': 10,
    'chat.tasks.notify_reaction_update': 10,
    'chat.tasks.send_scheduled_message': 30,
    'chat.tasks.fetch_link_preview_task': 35,
    'chat.tasks.prune_link_previews': 35,
    # meta_ads
    'meta_ads.tasks.sync_all_meta_connections': 300,
    'meta_ads.tasks.sync_recent_meta': 300,
    'meta_ads.tasks.sync_single_ad_account': 60,
    'meta_ads.tasks.sync_all_active_ad_accounts': 60,
    # google_calendar_integration
    'google_calendar_integration.tasks.import_for_connection_task': 90,
    'google_calendar_integration.tasks.sync_all_google_calendar_imports': 300,
    'google_calendar_integration.tasks.export_event_to_google_task': 60,
    # stripe_meta
    'stripe_meta.tasks.report_overage_to_stripe': 300,
    'stripe_meta.tasks.settle_final_overage': 60,
    'stripe_meta.tasks.aggregate_monthly_llm_cost': 90,
    'stripe_meta.tasks.check_fair_use_alerts': 60,
    'stripe_meta.tasks.reset_daily_usage': 60,
    # tiktok
    'tiktok.tasks.scan_tiktok_creative_for_virus': 30,
    'tiktok.tasks.cleanup_expired_previews': 60,
    # csm
    'csm.tasks.auto_resolve_pending_tickets': 60,
    'csm.tasks.notify_sla_breaches': 60,
    # tracking
    'tracking.tasks.emit_tracking_event': 30,
    'tracking.tasks.expire_stale_sessions': 30,
    'tracking.tasks.purge_old_data': 300,
    # notifications (already registered with an explicit name=)
    'notifications.tasks.fire_calendar_reminders': 60,
    'notifications.tasks.fire_task_overdue_notifications': 60,
    'notifications.tasks.fire_decision_deadline_notifications': 60,
    'notifications.tasks.fire_meeting_starting_soon_notifications': 60,
    'notifications.tasks.fire_message_reminders': 60,
    # agent (2 auto-named, 4 explicit name=)
    'agent.tasks.generate_miro_board_for_workflow_run_task': 120,
    'agent.tasks.handle_chat_message_for_agent': 120,
    'agent.tasks.check_polling_triggers': 90,
    'agent.tasks.check_scheduled_triggers': 90,
    'agent.tasks.cleanup_old_trigger_logs': 60,
    'agent.tasks.execute_workflow_async': 90,
    # retrospective
    'retrospective.tasks.generate_retrospective': 120,
    'retrospective.tasks.generate_mock_kpi_data': 30,
    'retrospective.tasks.generate_insights_for_retrospective': 90,
    'retrospective.tasks.generate_report_for_retrospective': 90,
    'retrospective.tasks.cleanup_old_retrospectives': 90,
    'retrospective.tasks.update_kpi_data_from_external_sources': 90,
}


class TimeoutEnforcedTask(Task):
    """
    Default Task class for this app (wired in backend/backend/celery.py).
    Protection is driven entirely by TASK_TIMEOUTS above — add a task's
    full dotted name (e.g. 'chat.tasks.deliver_message_task') there to
    protect it, with whatever timeout it needs. Nothing to declare on
    the task itself, and tasks not listed in TASK_TIMEOUTS run
    unprotected, same as before this class existed.
    """

    def __call__(self, *args, **kwargs):
        seconds = self.time_limit or TASK_TIMEOUTS.get(self.name)
        if not seconds or os.environ.get('PYTEST_CURRENT_TEST'):
            return self.run(*args, **kwargs)

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self.run, *args, **kwargs)
        try:
            result = future.result(timeout=seconds)
        except concurrent.futures.TimeoutError:
            logger.error(
                "%s exceeded its %ss timeout — abandoning. The Celery "
                "worker is freed to pick up new work, but the "
                "underlying call may still be running in the "
                "background (Python cannot forcibly stop a thread).",
                self.name,
                seconds,
            )
            # wait=False: do NOT block here waiting for the orphaned
            # thread to finish — that would defeat the entire point of
            # timing out. Let it run its course in the background.
            executor.shutdown(wait=False)
            raise TaskTimeoutError(
                f"{self.name} exceeded {seconds}s timeout"
            ) from None
        except BaseException:
            # self.run() itself raised (a real error, not a timeout). It
            # has already finished running by the time future.result()
            # re-raises here, so this shutdown is fast, not a wait on
            # still-running work.
            executor.shutdown(wait=True)
            raise
        else:
            executor.shutdown(wait=True)
            return result