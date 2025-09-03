from meetvoice.settings import SECRET_KEY
from system.models import Dept

from .meet_jwt import MeetJwt

def get_user_info_from_token(request):
    """
    获取请求用户的token信息
    :param request: 请求对象
    :return: 用户信息
    """
    token = request.META.get("HTTP_AUTHORIZATION")
    token = token.split(" ")[1]
    jwt = MeetJwt(SECRET_KEY)
    value = jwt.decode(SECRET_KEY, token)
    user_info = value.payload
    return user_info

def get_dept(dept_id: int, dept_all_list=None, dept_list=None):
    """
    递归获取部门的所有下级部门
    :param dept_id: 需要获取的部门id
    :param dept_all_list: 所有部门列表
    :param dept_list: 递归部门list
    :return:
    """
    if not dept_all_list:
        dept_all_list = Dept.objects.all().values('id', 'parent')
    if dept_list is None:
        dept_list = [dept_id]
    for ele in dept_all_list:
        if ele.get('parent') == dept_id:
            dept_list.append(ele.get('id'))
            get_dept(ele.get('id'), dept_all_list, dept_list)
    return list(set(dept_list))