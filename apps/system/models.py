from django.db import models
from django.contrib.auth.hashers import make_password, check_password


class Menu(models.Model):
    """
    菜单模型
    用于管理系统菜单，支持最多二级菜单
    """
    menu_name = models.CharField(max_length=50, verbose_name='菜单名称')
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, 
                               related_name='children', verbose_name='父级菜单')
    menu_order = models.IntegerField(default=0, verbose_name='显示顺序')
    route_path = models.CharField(max_length=200, blank=True, verbose_name='路由地址')
    menu_icon = models.CharField(max_length=50, blank=True, default='layui-icon-app', verbose_name='菜单图标')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        db_table = 'sys_menu'
        verbose_name = '菜单'
        verbose_name_plural = verbose_name
        ordering = ['menu_order', 'id']

    def __str__(self):
        return self.menu_name

    def get_level(self):
        """
        获取菜单级别
        :return: 菜单级别（0为一级菜单，1为二级菜单）
        """
        if self.parent is None:
            return 0
        elif self.parent.parent is None:
            return 1
        else:
            return 2


class Role(models.Model):
    """
    角色模型
    用于管理系统角色及其关联的菜单权限
    """
    role_name = models.CharField(max_length=50, unique=True, verbose_name='角色名称')
    role_key = models.CharField(max_length=50, unique=True, verbose_name='角色英文名称')
    menus = models.ManyToManyField(Menu, blank=True, related_name='roles', verbose_name='关联菜单')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        db_table = 'sys_role'
        verbose_name = '角色'
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.role_name


class User(models.Model):
    """
    用户模型
    用于管理系统用户信息
    """
    GENDER_CHOICES = (
        ('male', '男'),
        ('female', '女'),
    )
    
    STATUS_CHOICES = (
        ('active', '正常'),
        ('inactive', '停用'),
    )

    nickname = models.CharField(max_length=50, verbose_name='用户昵称')
    department = models.CharField(max_length=100, verbose_name='归属部门')
    phone = models.CharField(max_length=11, blank=True, verbose_name='手机号码')
    username = models.CharField(max_length=50, unique=True, verbose_name='用户名称')
    password = models.CharField(max_length=255, verbose_name='密码')
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, default='male', verbose_name='用户性别')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active', verbose_name='用户状态')
    position = models.CharField(max_length=50, blank=True, verbose_name='岗位')
    roles = models.ManyToManyField(Role, blank=True, related_name='users', verbose_name='关联角色')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        db_table = 'sys_user'
        verbose_name = '用户'
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.username

    def set_password(self, raw_password):
        """
        设置密码（加密）
        :param raw_password: 原始密码
        """
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        """
        验证密码
        :param raw_password: 原始密码
        :return: 密码是否正确
        """
        return check_password(raw_password, self.password)

    def get_menus(self):
        """
        获取用户关联的所有菜单
        :return: 菜单QuerySet
        """
        menu_ids = set()
        for role in self.roles.all():
            menu_ids.update(role.menus.values_list('id', flat=True))
        return Menu.objects.filter(id__in=menu_ids).order_by('menu_order', 'id')
