from rest_framework import serializers

from .models import Module, Surface, SurfaceType


class ModuleSerializer(serializers.ModelSerializer):
    route_prefixes = serializers.SlugRelatedField(many=True, read_only=True, slug_field="path")

    class Meta:
        model = Module
        fields = ["key", "label", "default_visible", "default_order", "route_prefixes"]


class SurfaceTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = SurfaceType
        fields = ["key", "label"]


class SurfaceSerializer(serializers.ModelSerializer):
    key = serializers.CharField(read_only=True)
    route_prefixes = serializers.SlugRelatedField(many=True, read_only=True, slug_field="path")
    parent = serializers.SerializerMethodField()

    class Meta:
        model = Surface
        fields = ["key", "label", "default_visible", "default_order", "route_prefixes", "parent"]

    def get_parent(self, surface):
        return surface.parent.key if surface.parent else None
