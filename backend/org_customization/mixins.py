class ConfigurableOptionsMixin:
    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_values = dict(zip(field_names, values))
        return instance

    def save(self, *args, **kwargs):
        from .services import ensure_options_enabled

        ensure_options_enabled(self, kwargs.get('update_fields'))
        super().save(*args, **kwargs)
        self._loaded_values = {field.attname: getattr(self, field.attname) for field in self._meta.concrete_fields}
