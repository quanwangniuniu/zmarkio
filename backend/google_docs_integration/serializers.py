from rest_framework import serializers


class GoogleDocsStatusSerializer(serializers.Serializer):
    connected = serializers.BooleanField()
    google_email = serializers.CharField(allow_null=True, required=False)


class GoogleDocsConnectSerializer(serializers.Serializer):
    auth_url = serializers.URLField()
    state = serializers.CharField()


class GoogleDocsImportSerializer(serializers.Serializer):
    document_id = serializers.CharField()
    decision_id = serializers.IntegerField(required=False)


class GoogleDocsExportSerializer(serializers.Serializer):
    decision_id = serializers.IntegerField()
    title = serializers.CharField(required=False, allow_blank=True)


class GoogleDocsRawExportSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True)
    content = serializers.CharField(required=True, allow_blank=True)


class GoogleSheetsImportSerializer(serializers.Serializer):
    sheet_url = serializers.CharField()


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


class GoogleSheetsExportSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True)
    matrix = serializers.ListField(
        child=serializers.ListField(child=serializers.CharField(allow_blank=True)),
        required=True,
    )
    formats = GoogleSheetExportFormatSerializer(many=True, required=False, default=list)
    spreadsheet_id = serializers.CharField(required=False, allow_blank=True, max_length=255)
    sheet_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)