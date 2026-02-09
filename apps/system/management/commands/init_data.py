from django.core.management.base import BaseCommand
from system.models import User, Role, Menu


class Command(BaseCommand):
    """
    初始化测试数据命令
    使用方法：python manage.py init_data
    """
    help = '初始化测试数据'

    def handle(self, *args, **options):
        """
        执行初始化数据
        """
        self.stdout.write('开始初始化测试数据...')
        
        # 清空现有数据
        User.objects.all().delete()
        Role.objects.all().delete()
        Menu.objects.all().delete()
        
        # 创建菜单
        self.stdout.write('创建菜单...')
        
        # 一级菜单：系统管理
        menu_system = Menu.objects.create(
            menu_name='系统管理',
            menu_order=1,
            route_path=''
        )
        
        # 二级菜单
        menu_user = Menu.objects.create(
            menu_name='用户管理',
            parent=menu_system,
            menu_order=1,
            route_path='/system/user/list/'
        )
        
        menu_role = Menu.objects.create(
            menu_name='角色管理',
            parent=menu_system,
            menu_order=2,
            route_path='/system/role/list/'
        )
        
        menu_menu = Menu.objects.create(
            menu_name='菜单管理',
            parent=menu_system,
            menu_order=3,
            route_path='/system/menu/list/'
        )
        
        # 创建角色
        self.stdout.write('创建角色...')
        
        # 管理员角色（拥有所有菜单权限）
        role_admin = Role.objects.create(
            role_name='系统管理员',
            role_key='admin'
        )
        role_admin.menus.add(menu_system, menu_user, menu_role, menu_menu)
        
        # 普通用户角色（只有用户管理权限）
        role_user = Role.objects.create(
            role_name='普通用户',
            role_key='user'
        )
        role_user.menus.add(menu_system, menu_user)
        
        # 创建用户
        self.stdout.write('创建用户...')
        
        # 管理员账号
        admin_user = User.objects.create(
            nickname='系统管理员',
            department='总部',
            phone='13800138000',
            username='admin',
            gender='male',
            status='active',
            position='系统管理员'
        )
        admin_user.set_password('admin888')
        admin_user.save()
        admin_user.roles.add(role_admin)
        
        # 测试用户账号
        test_user = User.objects.create(
            nickname='测试用户',
            department='总部-研发',
            phone='13900139000',
            username='test',
            gender='female',
            status='active',
            position='测试工程师'
        )
        test_user.set_password('test123')
        test_user.save()
        test_user.roles.add(role_user)
        
        self.stdout.write(self.style.SUCCESS('测试数据初始化完成！'))
        self.stdout.write('管理员账号：admin / admin888')
        self.stdout.write('测试用户账号：test / test123')
