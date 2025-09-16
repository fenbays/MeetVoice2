from django.db import migrations, models
import uuid

def populate_file_uuids(apps, schema_editor):
    File = apps.get_model('system', 'File')
    existing_uuids = set(f.uuid for f in File.objects.all() if f.uuid)
    for row in File.objects.all():
        while not row.uuid or row.uuid in existing_uuids:
            row.uuid = uuid.uuid4()
        existing_uuids.add(row.uuid)
        row.save(update_fields=['uuid'])

class Migration(migrations.Migration):

    dependencies = [
        ('system', '0005_alter_file_url_alter_users_user_type'),
    ]

    operations = [
        # 1️⃣ 添加 uuid 字段，先允许 null
        migrations.AddField(
            model_name='file',
            name='uuid',
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                null=True,  # 允许为空，保证迁移执行不会冲突
                verbose_name='文件UUID',
                help_text='文件唯一标识符',
            ),
        ),
        # 2️⃣ 填充已有数据
        migrations.RunPython(populate_file_uuids, reverse_code=migrations.RunPython.noop),
        # 3️⃣ 修改字段为 unique 且 null=False
        migrations.AlterField(
            model_name='file',
            name='uuid',
            field=models.UUIDField(
                default=uuid.uuid4,
                unique=True,
                editable=False,
                null=False,
                verbose_name='文件UUID',
                help_text='文件唯一标识符',
            ),
        ),
    ]
