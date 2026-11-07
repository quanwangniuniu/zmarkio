from django.contrib import admin

from .models import Module, RoutePrefix, Surface, SurfaceType


class ModuleRoutePrefixInline(admin.TabularInline):
    model = RoutePrefix
    fk_name = "module"
    fields = ("path",)
    extra = 0


class SurfaceRoutePrefixInline(admin.TabularInline):
    model = RoutePrefix
    fk_name = "surface"
    fields = ("path",)
    extra = 0


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ("key", "label", "default_visible", "default_order")
    search_fields = ("key", "label")
    inlines = [ModuleRoutePrefixInline]


@admin.register(SurfaceType)
class SurfaceTypeAdmin(admin.ModelAdmin):
    list_display = ("key", "label")
    search_fields = ("key", "label")


@admin.register(Surface)
class SurfaceAdmin(admin.ModelAdmin):
    list_display = ("__str__", "label", "default_visible", "default_order", "parent")
    list_filter = ("module", "surface_type")
    search_fields = ("name", "label")
    inlines = [SurfaceRoutePrefixInline]
