from django.contrib import admin

from .models import AdsDraftPerformanceDaily, ReportTask, ReportTaskKeyAction


@admin.register(ReportTask)
class ReportTaskAdmin(admin.ModelAdmin):
    list_display = ("id", "task", "audience_type", "audience_prompt_version", "created_at", "updated_at")
    search_fields = ("id", "task__id", "audience_type")
    list_filter = ("audience_type",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(ReportTaskKeyAction)
class ReportTaskKeyActionAdmin(admin.ModelAdmin):
    list_display = ("id", "report_task", "order_index", "created_at")
    search_fields = ("id", "report_task__id", "action_text")
    list_filter = ("report_task",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(AdsDraftPerformanceDaily)
class AdsDraftPerformanceDailyAdmin(admin.ModelAdmin):
    list_display = ("project", "platform", "date", "updated_at")
    list_filter = ("platform",)
    search_fields = ("project__name",)
    readonly_fields = ("created_at", "updated_at")
