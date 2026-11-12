from rest_framework import serializers
from .format_serializers import GoogleSheetExportFormatSerializer

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


class GoogleSheetsExportSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True)
    matrix = serializers.ListField(
        child=serializers.ListField(child=serializers.CharField(allow_blank=True)),
        required=True,
    )
    formats = GoogleSheetExportFormatSerializer(many=True, required=False, default=list)
    spreadsheet_id = serializers.CharField(required=False, allow_blank=True, max_length=255)
    sheet_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)