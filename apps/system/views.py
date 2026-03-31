from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Q
from PIL import Image, ImageDraw, ImageFont
import random
import io
import base64
from .models import User, Role, Menu



def user_form_view(request):
    """返回用户表单页面"""
    return render(request, 'system/user_form.html')

def role_form_view(request):
    """返回角色表单页面"""
    return render(request, 'system/role_form.html')

def memu_form_view(request):
    """返回菜单表单页面"""
    return render(request, 'system/menu_form.html')

def core_down_report(request):
    """返回菜单表单页面"""
    return render(request, 'system/down_report.html')

# ==================== 认证相关视图 ====================

def generate_captcha():
    """
    生成验证码图片
    :return: 验证码文本和Base64编码的图片
    """
    # 生成4位随机验证码
    code = ''.join([str(random.randint(0, 9)) for _ in range(4)])
    
    # 创建图片
    width, height = 120, 40
    image = Image.new('RGB', (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    
    # 绘制验证码文本
    for i, char in enumerate(code):
        x = 20 + i * 25
        y = random.randint(5, 15)
        draw.text((x, y), char, fill=(random.randint(0, 100), random.randint(0, 100), random.randint(0, 100)))
    
    # 添加干扰线
    for _ in range(3):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        draw.line([(x1, y1), (x2, y2)], fill=(random.randint(100, 200), random.randint(100, 200), random.randint(100, 200)))
    
    # 转换为Base64
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    img_base64 = base64.b64encode(buffer.getvalue()).decode()
    
    return code, f'data:image/png;base64,{img_base64}'


def login_view(request):
    """
    登录页面视图
    :param request: HTTP请求对象
    :return: 登录页面或重定向到首页
    """
    if request.method == 'GET':
        # 生成验证码
        code, img = generate_captcha()
        request.session['captcha'] = code
        return render(request, 'login.html', {'captcha_img': img})
    
    elif request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        captcha = request.POST.get('captcha', '').strip()
        
        # 验证验证码
        if captcha.lower() != request.session.get('captcha', '').lower():
            code, img = generate_captcha()
            request.session['captcha'] = code
            return render(request, 'login.html', {
                'error': '验证码错误',
                'captcha_img': img
            })
        
        # 验证用户名和密码
        try:
            user = User.objects.get(username=username)
            if user.status != 'active':
                code, img = generate_captcha()
                request.session['captcha'] = code
                return render(request, 'login.html', {
                    'error': '账号已停用',
                    'captcha_img': img
                })
            
            if user.check_password(password):
                # 登录成功，保存session
                request.session['user_id'] = user.id
                request.session['username'] = user.username
                request.session['nickname'] = user.nickname
                return redirect(reverse('index'))
            else:
                code, img = generate_captcha()
                request.session['captcha'] = code
                return render(request, 'login.html', {
                    'error': '密码错误',
                    'captcha_img': img
                })
        except User.DoesNotExist:
            code, img = generate_captcha()
            request.session['captcha'] = code
            return render(request, 'login.html', {
                'error': '用户不存在',
                'captcha_img': img
            })


def logout_view(request):
    """
    退出登录视图
    :param request: HTTP请求对象
    :return: 重定向到登录页面
    """
    request.session.flush()
    return redirect(reverse('login'))


def captcha_view(request):
    """
    刷新验证码视图
    :param request: HTTP请求对象
    :return: JSON格式的验证码图片
    """
    code, img = generate_captcha()
    request.session['captcha'] = code
    return JsonResponse({'captcha_img': img})


# ==================== 首页视图 ====================

def index_view(request):
    """
    首页视图（主工作台）
    :param request: HTTP请求对象
    :return: 首页HTML
    """
    user_id = request.session.get('user_id')
    user = User.objects.get(id=user_id)
    
    # 获取用户的菜单
    menus = user.get_menus()
    
    # 构建菜单树
    menu_tree = []
    menu_dict = {}
    
    for menu in menus:
        menu_dict[menu.id] = {
            'id': menu.id,
            'name': menu.menu_name,
            'icon': menu.menu_icon,
            'route': menu.route_path,
            'children': []
        }
    
    for menu in menus:
        if menu.parent_id is None:
            menu_tree.append(menu_dict[menu.id])
        else:
            if menu.parent_id in menu_dict:
                menu_dict[menu.parent_id]['children'].append(menu_dict[menu.id])
    
    return render(request, 'index.html', {
        'user': user,
        'menus': menu_tree
    })


def about_view(request):
    """
    关于本站页面视图
    :param request: HTTP请求对象
    :return: 关于本站HTML
    """
    return render(request, 'about.html')


# ==================== 用户管理视图 ====================

def user_list_view(request):
    """
    用户列表视图
    :param request: HTTP请求对象
    :return: 用户列表HTML或JSON数据
    """
    if request.method == 'GET':
        # 判断是否是AJAX请求
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            # 获取分页参数
            page = int(request.GET.get('page', 1))
            limit = int(request.GET.get('limit', 10))
            
            # 获取搜索参数
            keyword = request.GET.get('keyword', '').strip()
            
            # 构建查询
            users = User.objects.all()
            if keyword:
                users = users.filter(
                    Q(username__icontains=keyword) |
                    Q(nickname__icontains=keyword) |
                    Q(phone__icontains=keyword)
                )
            
            total = users.count()
            users = users[(page - 1) * limit:page * limit]
            
            # 构建返回数据
            data = []
            for user in users:
                role_names = ', '.join([role.role_name for role in user.roles.all()])
                data.append({
                    'id': user.id,
                    'username': user.username,
                    'nickname': user.nickname,
                    'department': user.department,
                    'phone': user.phone,
                    'gender': user.get_gender_display(),
                    'status': user.get_status_display(),
                    'position': user.position,
                    'roles': role_names,
                    'created_at': user.created_at.strftime('%Y-%m-%d %H:%M:%S')
                })
            
            return JsonResponse({
                'code': 0,
                'msg': '',
                'count': total,
                'data': data
            })
        else:
            # 返回HTML页面
            return render(request, 'system/user_list.html')


def user_add_view(request):
    """
    添加用户视图
    :param request: HTTP请求对象
    :return: JSON响应
    """
    if request.method == 'POST':
        try:
            # 获取表单数据
            nickname = request.POST.get('nickname', '').strip()
            department = request.POST.get('department', '').strip()
            phone = request.POST.get('phone', '').strip()
            username = request.POST.get('username', '').strip()
            password = request.POST.get('password', '').strip()
            gender = request.POST.get('gender', 'male')
            status = request.POST.get('status', 'active')
            position = request.POST.get('position', '').strip()
            role_ids = request.POST.getlist('role_ids[]', [])
            
            # 检查用户名是否已存在
            if User.objects.filter(username=username).exists():
                return JsonResponse({'code': 1, 'msg': '用户名已存在'})
            
            # 创建用户
            user = User.objects.create(
                nickname=nickname,
                department=department,
                phone=phone,
                username=username,
                gender=gender,
                status=status,
                position=position
            )
            user.set_password(password)
            user.save()
            
            # 关联角色
            if role_ids:
                roles = Role.objects.filter(id__in=role_ids)
                user.roles.set(roles)
            
            return JsonResponse({'code': 0, 'msg': '添加成功'})
        except Exception as e:
            return JsonResponse({'code': 1, 'msg': f'添加失败: {str(e)}'})


def user_edit_view(request):
    """
    编辑用户视图
    :param request: HTTP请求对象
    :return: JSON响应或用户数据
    """
    user_id = request.GET.get('id') or request.POST.get('id')
    
    if request.method == 'GET':
        # 获取用户信息
        user = get_object_or_404(User, id=user_id)
        role_ids = list(user.roles.values_list('id', flat=True))
        
        return JsonResponse({
            'code': 0,
            'data': {
                'id': user.id,
                'nickname': user.nickname,
                'department': user.department,
                'phone': user.phone,
                'username': user.username,
                'gender': user.gender,
                'status': user.status,
                'position': user.position,
                'role_ids': role_ids
            }
        })
    
    elif request.method == 'POST':
        try:
            user = get_object_or_404(User, id=user_id)
            
            # 更新用户信息
            user.nickname = request.POST.get('nickname', '').strip()
            user.department = request.POST.get('department', '').strip()
            user.phone = request.POST.get('phone', '').strip()
            user.gender = request.POST.get('gender', 'male')
            user.status = request.POST.get('status', 'active')
            user.position = request.POST.get('position', '').strip()
            
            # 如果提供了新密码，则更新密码
            password = request.POST.get('password', '').strip()
            if password:
                user.set_password(password)
            
            user.save()
            
            # 更新角色
            role_ids = request.POST.getlist('role_ids[]', [])
            if role_ids:
                roles = Role.objects.filter(id__in=role_ids)
                user.roles.set(roles)
            else:
                user.roles.clear()
            
            return JsonResponse({'code': 0, 'msg': '修改成功'})
        except Exception as e:
            return JsonResponse({'code': 1, 'msg': f'修改失败: {str(e)}'})


def user_delete_view(request):
    """
    删除用户视图
    :param request: HTTP请求对象
    :return: JSON响应
    """
    if request.method == 'POST':
        try:
            user_id = request.POST.get('id')
            user = get_object_or_404(User, id=user_id)
            user.delete()
            return JsonResponse({'code': 0, 'msg': '删除成功'})
        except Exception as e:
            return JsonResponse({'code': 1, 'msg': f'删除失败: {str(e)}'})


# ==================== 角色管理视图 ====================

def role_list_view(request):
    """
    角色列表视图
    :param request: HTTP请求对象
    :return: 角色列表HTML或JSON数据
    """
    if request.method == 'GET':
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            # 获取分页参数
            page = int(request.GET.get('page', 1))
            limit = int(request.GET.get('limit', 10))
            
            # 获取搜索参数
            keyword = request.GET.get('keyword', '').strip()
            
            # 构建查询
            roles = Role.objects.all()
            if keyword:
                roles = roles.filter(role_name__icontains=keyword)
            
            total = roles.count()
            roles = roles[(page - 1) * limit:page * limit]
            
            # 构建返回数据
            data = []
            for role in roles:
                menu_names = ', '.join([menu.menu_name for menu in role.menus.all()])
                data.append({
                    'id': role.id,
                    'role_name': role.role_name,
                    'role_key': role.role_key,
                    'menus': menu_names,
                    'created_at': role.created_at.strftime('%Y-%m-%d %H:%M:%S')
                })
            
            return JsonResponse({
                'code': 0,
                'msg': '',
                'count': total,
                'data': data
            })
        else:
            return render(request, 'system/role_list.html')


def role_add_view(request):
    """
    添加角色视图
    :param request: HTTP请求对象
    :return: JSON响应
    """
    if request.method == 'POST':
        try:
            role_name = request.POST.get('role_name', '').strip()
            role_key = request.POST.get('role_key', '').strip()
            menu_ids = request.POST.getlist('menu_ids[]', [])
            
            # 检查角色名称是否已存在
            if Role.objects.filter(role_name=role_name).exists():
                return JsonResponse({'code': 1, 'msg': '角色名称已存在'})
            
            # 检查角色英文名称是否已存在
            if Role.objects.filter(role_key=role_key).exists():
                return JsonResponse({'code': 1, 'msg': '角色英文名称已存在'})
            
            # 创建角色
            role = Role.objects.create(
                role_name=role_name,
                role_key=role_key
            )
            
            # 关联菜单
            if menu_ids:
                menus = Menu.objects.filter(id__in=menu_ids)
                role.menus.set(menus)
            
            return JsonResponse({'code': 0, 'msg': '添加成功'})
        except Exception as e:
            return JsonResponse({'code': 1, 'msg': f'添加失败: {str(e)}'})


def role_edit_view(request):
    """
    编辑角色视图
    :param request: HTTP请求对象
    :return: JSON响应或角色数据
    """
    role_id = request.GET.get('id') or request.POST.get('id')
    
    if request.method == 'GET':
        role = get_object_or_404(Role, id=role_id)
        menu_ids = list(role.menus.values_list('id', flat=True))
        
        return JsonResponse({
            'code': 0,
            'data': {
                'id': role.id,
                'role_name': role.role_name,
                'role_key': role.role_key,
                'menu_ids': menu_ids
            }
        })
    
    elif request.method == 'POST':
        try:
            role = get_object_or_404(Role, id=role_id)
            
            role_name = request.POST.get('role_name', '').strip()
            role_key = request.POST.get('role_key', '').strip()
            menu_ids = request.POST.getlist('menu_ids[]', [])
            
            # 检查角色名称是否已被其他角色使用
            if Role.objects.filter(role_name=role_name).exclude(id=role_id).exists():
                return JsonResponse({'code': 1, 'msg': '角色名称已存在'})
            
            # 检查角色英文名称是否已被其他角色使用
            if Role.objects.filter(role_key=role_key).exclude(id=role_id).exists():
                return JsonResponse({'code': 1, 'msg': '角色英文名称已存在'})
            
            role.role_name = role_name
            role.role_key = role_key
            role.save()
            
            # 更新菜单
            if menu_ids:
                menus = Menu.objects.filter(id__in=menu_ids)
                role.menus.set(menus)
            else:
                role.menus.clear()
            
            return JsonResponse({'code': 0, 'msg': '修改成功'})
        except Exception as e:
            return JsonResponse({'code': 1, 'msg': f'修改失败: {str(e)}'})


def role_delete_view(request):
    """
    删除角色视图
    :param request: HTTP请求对象
    :return: JSON响应
    """
    if request.method == 'POST':
        try:
            role_id = request.POST.get('id')
            role = get_object_or_404(Role, id=role_id)
            role.delete()
            return JsonResponse({'code': 0, 'msg': '删除成功'})
        except Exception as e:
            return JsonResponse({'code': 1, 'msg': f'删除失败: {str(e)}'})


# ==================== 菜单管理视图 ====================

def menu_list_view(request):
    """
    菜单列表视图
    :param request: HTTP请求对象
    :return: 菜单列表HTML或JSON数据
    """
    if request.method == 'GET':
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            # 获取搜索参数
            keyword = request.GET.get('keyword', '').strip()
            
            # 构建查询
            menus = Menu.objects.all()
            if keyword:
                menus = menus.filter(menu_name__icontains=keyword)
            
            # 构建返回数据
            data = []
            for menu in menus:
                data.append({
                    'id': menu.id,
                    'menu_name': menu.menu_name,
                    'parent_name': menu.parent.menu_name if menu.parent else '主目录',
                    'menu_order': menu.menu_order,
                    'route_path': menu.route_path,
                    'menu_icon': menu.menu_icon,
                    'level': menu.get_level(),
                    'created_at': menu.created_at.strftime('%Y-%m-%d %H:%M:%S')
                })
            
            return JsonResponse({
                'code': 0,
                'msg': '',
                'count': len(data),
                'data': data
            })
        else:
            return render(request, 'system/menu_list.html')


def menu_tree_view(request):
    """
    获取菜单树（用于角色分配菜单权限）
    :param request: HTTP请求对象
    :return: JSON格式的菜单树
    """
    menus = Menu.objects.all()
    
    # 构建菜单树
    menu_tree = []
    menu_dict = {}
    
    for menu in menus:
        menu_dict[menu.id] = {
            'id': menu.id,
            'title': menu.menu_name,
            'icon': menu.menu_icon,
            'children': []
        }
    
    for menu in menus:
        if menu.parent_id is None:
            menu_tree.append(menu_dict[menu.id])
        else:
            if menu.parent_id in menu_dict:
                menu_dict[menu.parent_id]['children'].append(menu_dict[menu.id])
    
    return JsonResponse(menu_tree, safe=False)


def menu_parent_list_view(request):
    """
    获取可作为父菜单的菜单列表（只包含一级菜单）
    :param request: HTTP请求对象
    :return: JSON格式的菜单列表
    """
    # 只查询一级菜单
    menus = Menu.objects.filter(parent__isnull=True)
    
    data = [{'id': 0, 'menu_name': '主目录'}]
    for menu in menus:
        data.append({
            'id': menu.id,
            'menu_name': menu.menu_name
        })
    
    return JsonResponse({'code': 0, 'data': data})


def menu_add_view(request):
    """
    添加菜单视图
    :param request: HTTP请求对象
    :return: JSON响应
    """
    if request.method == 'POST':
        try:
            menu_name = request.POST.get('menu_name', '').strip()
            parent_id = request.POST.get('parent_id', '0')
            menu_order = int(request.POST.get('menu_order', 0))
            route_path = request.POST.get('route_path', '').strip()
            menu_icon = request.POST.get('menu_icon', 'layui-icon-app').strip()
            
            # 验证父菜单
            parent = None
            if parent_id != '0':
                parent = get_object_or_404(Menu, id=parent_id)
                # 检查父菜单级别，如果已经是二级菜单，则不能再添加子菜单
                if parent.get_level() >= 1:
                    return JsonResponse({'code': 1, 'msg': '不能在二级菜单下添加菜单'})
            
            # 创建菜单
            menu = Menu.objects.create(
                menu_name=menu_name,
                parent=parent,
                menu_order=menu_order,
                route_path=route_path,
                menu_icon=menu_icon
            )
            
            return JsonResponse({'code': 0, 'msg': '添加成功'})
        except Exception as e:
            return JsonResponse({'code': 1, 'msg': f'添加失败: {str(e)}'})


def menu_edit_view(request):
    """
    编辑菜单视图
    :param request: HTTP请求对象
    :return: JSON响应或菜单数据
    """
    menu_id = request.GET.get('id') or request.POST.get('id')
    
    if request.method == 'GET':
        menu = get_object_or_404(Menu, id=menu_id)
        
        return JsonResponse({
            'code': 0,
            'data': {
                'id': menu.id,
                'menu_name': menu.menu_name,
                'parent_id': menu.parent_id if menu.parent_id else 0,
                'menu_order': menu.menu_order,
                'route_path': menu.route_path,
                'menu_icon': menu.menu_icon
            }
        })
    
    elif request.method == 'POST':
        try:
            menu = get_object_or_404(Menu, id=menu_id)
            
            menu_name = request.POST.get('menu_name', '').strip()
            parent_id = request.POST.get('parent_id', '0')
            menu_order = int(request.POST.get('menu_order', 0))
            route_path = request.POST.get('route_path', '').strip()
            menu_icon = request.POST.get('menu_icon', 'layui-icon-app').strip()
            
            # 验证父菜单
            parent = None
            if parent_id != '0':
                parent = get_object_or_404(Menu, id=parent_id)
                # 检查父菜单级别
                if parent.get_level() >= 1:
                    return JsonResponse({'code': 1, 'msg': '不能在二级菜单下添加菜单'})
                # 不能将自己设置为自己的父菜单
                if parent.id == menu.id:
                    return JsonResponse({'code': 1, 'msg': '不能将自己设置为父菜单'})
            
            menu.menu_name = menu_name
            menu.parent = parent
            menu.menu_order = menu_order
            menu.route_path = route_path
            menu.menu_icon = menu_icon
            menu.save()
            
            return JsonResponse({'code': 0, 'msg': '修改成功'})
        except Exception as e:
            return JsonResponse({'code': 1, 'msg': f'修改失败: {str(e)}'})


def menu_delete_view(request):
    """
    删除菜单视图
    :param request: HTTP请求对象
    :return: JSON响应
    """
    if request.method == 'POST':
        try:
            menu_id = request.POST.get('id')
            menu = get_object_or_404(Menu, id=menu_id)
            
            # 检查是否有子菜单
            if menu.children.exists():
                return JsonResponse({'code': 1, 'msg': '该菜单下有子菜单，无法删除'})
            
            menu.delete()
            return JsonResponse({'code': 0, 'msg': '删除成功'})
        except Exception as e:
            return JsonResponse({'code': 1, 'msg': f'删除失败: {str(e)}'})


def get_all_roles_view(request):
    """
    获取所有角色列表（用于用户管理中的角色选择）
    :param request: HTTP请求对象
    :return: JSON格式的角色列表
    """
    roles = Role.objects.all()
    data = [{'id': role.id, 'role_name': role.role_name} for role in roles]
    return JsonResponse({'code': 0, 'data': data})
