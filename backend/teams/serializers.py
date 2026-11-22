from rest_framework import serializers

from teams.services import TEAM_NAME_MAX_LENGTH


class _StrictCharField(serializers.CharField):
    """DRF's CharField coerces numbers to strings; this endpoint rejects them."""

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class TeamCreateSerializer(serializers.Serializer):
    name = _StrictCharField(max_length=TEAM_NAME_MAX_LENGTH)
    desc = _StrictCharField(required=False, allow_blank=True, allow_null=True, trim_whitespace=False)
