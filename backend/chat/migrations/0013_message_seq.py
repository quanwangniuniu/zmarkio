from django.db import migrations, models


def backfill_message_sequences(apps, schema_editor):
    Message = apps.get_model('chat', 'Message')
    chat_ids = Message.objects.order_by().values_list('chat_id', flat=True).distinct()

    for chat_id in chat_ids.iterator():
        batch = []
        for seq, message in enumerate(
            Message.objects.filter(chat_id=chat_id).order_by('created_at', 'id').iterator(),
            start=1,
        ):
            message.seq = seq
            batch.append(message)
            if len(batch) == 1000:
                Message.objects.bulk_update(batch, ['seq'])
                batch = []
        if batch:
            Message.objects.bulk_update(batch, ['seq'])


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0012_message_client_message_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='message',
            name='seq',
            field=models.PositiveBigIntegerField(
                editable=False,
                help_text='Monotonic sequence number within the chat, used for deterministic ordering.',
                null=True,
            ),
        ),
        migrations.RunPython(backfill_message_sequences, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='message',
            name='seq',
            field=models.PositiveBigIntegerField(
                editable=False,
                help_text='Monotonic sequence number within the chat, used for deterministic ordering.',
            ),
        ),
        migrations.AddConstraint(
            model_name='message',
            constraint=models.UniqueConstraint(
                fields=('chat', 'seq'),
                name='chat_message_chat_seq_uniq',
            ),
        ),
    ]
