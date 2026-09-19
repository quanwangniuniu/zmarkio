from rest_framework import serializers

from .models import AdCopyVariation


class AdCopyVariationSerializer(serializers.ModelSerializer):
    creative_slug = serializers.SlugRelatedField(
        source='creative', slug_field='slug', read_only=True
    )

    class Meta:
        model = AdCopyVariation
        fields = ['slug',
            'id', 'project', 'creative', 'creative_slug', 'source_mode', 'source_ref',
            'hook', 'headline', 'description', 'cta', 'validation_warnings',
            'instruction', 'model_name', 'prompt_version',
            'batch_id', 'batch_position', 'status',
            'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['slug', 'id', 'project', 'created_by', 'created_at', 'updated_at', 'validation_warnings',]
