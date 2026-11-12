"""Google Sheets cell-format payloads validated for the Sheets export endpoint."""

from rest_framework import serializers


class GoogleColorSerializer(serializers.Serializer):
    red = serializers.FloatField(min_value=0, max_value=1)
    green = serializers.FloatField(min_value=0, max_value=1)
    blue = serializers.FloatField(min_value=0, max_value=1)


class GoogleTextFormatSerializer(serializers.Serializer):
    bold = serializers.BooleanField(required=False)
    italic = serializers.BooleanField(required=False)
    strikethrough = serializers.BooleanField(required=False)
    fontFamily = serializers.CharField(required=False, allow_blank=False, max_length=100)
    fontSize = serializers.IntegerField(required=False, min_value=1, max_value=400)
    foregroundColor = GoogleColorSerializer(required=False)


class GoogleNumberFormatSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["NUMBER", "PERCENT", "CURRENCY"])
    pattern = serializers.CharField(max_length=120)


class GoogleUserEnteredFormatSerializer(serializers.Serializer):
    textFormat = GoogleTextFormatSerializer(required=False)
    backgroundColor = GoogleColorSerializer(required=False)
    numberFormat = GoogleNumberFormatSerializer(required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("At least one format field is required.")
        return attrs


class GoogleSheetExportFormatSerializer(serializers.Serializer):
    row = serializers.IntegerField(min_value=0)
    column = serializers.IntegerField(min_value=0)
    userEnteredFormat = GoogleUserEnteredFormatSerializer()