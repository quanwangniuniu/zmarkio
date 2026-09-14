from django.apps import AppConfig


class NotionEditorConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'notion_editor'
    verbose_name = 'Notion Style Editor'
    
    def ready(self):
        """Import signal handlers when the app is ready"""
        # Import admin configuration to ensure it's registered
        try:
            import notion_editor.admin
        except ImportError:
            pass

        import notion_editor.signals  # noqa: F401


