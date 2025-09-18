import os
from datetime import datetime
from urllib.parse import unquote
import logging

from django.shortcuts import get_object_or_404
from ninja import Schema

from .meet_auth import data_permission
from .meet_ninja import MeetFilters
from .meet_response import BusinessCode, MeetError, MeetResponse
from .usual import get_user_info_from_token

logger = logging.getLogger(__name__)


class ImportSchema(Schema):
    path: str


def create(request, data, model):
    """
    创建新记录的函数。

    参数:
    - request: 请求对象，用于获取用户信息。
    - data: 创建记录的数据，可以是字典或者支持dict()方法的对象。
    - model: Django ORM模型，指定要创建的对象类型。

    返回值:
    - 创建成功的对象查询集合。
    """
    if not isinstance(data, dict):
        # 如果data不是字典类型，则转换为字典
        data = data.dict()
    user_info = get_user_info_from_token(request)
    # 从请求中提取用户信息，并添加到数据中作为创建人、修改者和所属部门信息
    data['creator_id'] = user_info['id']
    data['modifier'] = user_info['name']
    data['belong_dept'] = user_info['dept']
    # 使用提供的模型和数据创建新记录
    query_set = model.objects.create(**data)
    return query_set


def batch_create(request, data, model):
    """
    批量创建模型实例。

    参数:
    - request: HTTP请求对象，用于获取用户信息。
    - data: 一个包含创建数据的列表，每个元素可以是字典或者具有dict方法的对象。
    - model: Django模型类，用于实例化和批量创建。

    返回值:
    - query_set: 批量创建后的模型实例查询集。
    """
    user_info = get_user_info_from_token(request)  # 从请求中获取用户信息
    data_list = []
    for item in data:
        if not isinstance(item, dict):
            item = item.dict()  # 如果item不是字典，则转换为字典

        # 为每个创建的数据项添加默认的创建人、修改者和所属部门信息
        item['creator_id'] = user_info['id']
        item['modifier'] = user_info['name']
        item['belong_dept'] = user_info['dept']
        data_list.append(model(**item))  # 根据字典内容实例化模型对象并添加到列表中
    query_set = model.objects.bulk_create(data_list)  # 批量创建模型实例
    return query_set


def delete(id, model):
    """
    根据提供的ID和模型删除对象实例。

    参数:
    - id: 要删除的对象的ID。
    - model: 对象所属的模型类。

    返回值:
    - 无返回值。
    """
    try:
        instance = model.objects.get(id=id)
    except model.DoesNotExist:
        raise MeetError("对象不存在", BusinessCode.INSTANCE_NOT_FOUND)
    try:
        instance.delete()
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"删除对象失败: {e}")
        raise MeetError("删除对象失败", BusinessCode.SERVER_ERROR)


def update(request, id, data, model):
    """
    更新给定模型实例的数据。

    参数:
    - request: HTTP请求对象，用于获取用户信息。
    - id: 要更新的模型实例的ID。
    - data: 包含更新数据的实例，应能转换为字典格式。
    - model: 要更新的模型类。

    返回值:
    - 更新后的模型实例。
    """
    if not isinstance(data, dict):
        # 如果data不是字典类型，则转换为字典
        data = data.dict()
    # dict_data = data.dict()  # 将data转换为字典格式
    user_info = get_user_info_from_token(request)  # 从请求中获取用户信息
    # 为更新的数据添加修改者信息
    data['modifier'] = user_info['name']
    try:
        instance = model.objects.get(id=id)
    except model.DoesNotExist:
        raise MeetError("对象不存在", BusinessCode.INSTANCE_NOT_FOUND.value)
    # 遍历字典，将更新的数据设置到模型实例上
    for attr, value in data.items():
        setattr(instance, attr, value)
    instance.save()  # 保存更新
    return instance  # 返回更新后的实例


def retrieve(request, model, filters: MeetFilters = MeetFilters()):
    """
    根据提供的过滤条件从数据库中检索模型实例。

    参数:
    - request: HttpRequest对象，用于获取请求信息。
    - model: Django模型类，指定要检索的数据模型。
    - filters: MeetFilters类的实例，包含过滤条件。默认为MeetFilters()，即无条件过滤。

    返回值:
    - query_set: 一个Django QuerySet对象，包含根据过滤条件检索到的模型实例。
    """
    # 根据请求和过滤条件应用数据权限控制
    filters = data_permission(request, filters)
    if filters is not None:
        # 将filters中的空字符串值转换为None
        for attr, value in filters.__dict__.items():
            if getattr(filters, attr) == '':
                setattr(filters, attr, None)
        # 使用过滤条件查询模型实例
        query_set = model.objects.filter(**filters.dict(exclude_none=True))
    else:
        # 如果没有有效的过滤条件，则返回所有模型实例
        query_set = model.objects.all()
    return query_set

